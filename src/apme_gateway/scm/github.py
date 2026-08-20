"""GitHub SCM provider — Phase 1 of ADR-050.

Uses the GitHub REST API v3 for branch creation, file push (via the
Git Trees/Commits API for atomic multi-file commits), and PR creation.
Supports both ``github.com`` and GitHub Enterprise Server via a
configurable ``api_base_url``.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from apme_gateway.scm._http import async_client, custom_ca_bundle, http_verify
from apme_gateway.scm.base import PullRequestResult

logger = logging.getLogger(__name__)

# GitHub secondary rate limits content-creation endpoints (e.g. POST /git/blobs)
# to roughly 80 requests/minute. Large remediations exceed that without pacing.
_BLOB_MIN_INTERVAL_S = 0.85
_BLOB_MAX_RETRIES = 6
_DEFAULT_RETRY_AFTER_S = 60.0
_MAX_RETRY_AFTER_S = 180.0
_PUSH_PER_REQUEST_TIMEOUT_S = 60.0
_PUSH_NON_BLOB_REQUEST_COUNT = 5
_PUSH_MAX_OPERATION_TIMEOUT_S = 1800.0

# Re-export under legacy private names for existing tests.
_custom_ca_bundle = custom_ca_bundle
_http_verify = http_verify


def _parse_owner_repo(repo_url: str) -> tuple[str, str]:
    """Extract ``(owner, repo)`` from an HTTPS clone URL.

    Args:
        repo_url: HTTPS URL like ``https://github.com/owner/repo.git``.

    Returns:
        Tuple of (owner, repo) with ``.git`` suffix stripped.

    Raises:
        ValueError: If the URL cannot be parsed.
    """
    parsed = urlparse(repo_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        msg = f"Cannot extract owner/repo from URL: {repo_url}"
        raise ValueError(msg)
    owner = parts[0]
    repo = parts[1].removesuffix(".git")
    return owner, repo


def _branch_ref_url(api: str, owner: str, repo: str, branch: str) -> str:
    """Build the GitHub git ref URL for a branch head (slashes URL-encoded).

    Args:
        api: GitHub API base URL.
        owner: Repository owner.
        repo: Repository name.
        branch: Branch name.

    Returns:
        Fully qualified REST URL for the branch ref.
    """
    return f"{api}/repos/{owner}/{repo}/git/ref/heads/{quote(branch, safe='')}"


def _branch_refs_update_url(api: str, owner: str, repo: str, branch: str) -> str:
    """Build the GitHub git refs update URL for a branch head.

    Args:
        api: GitHub API base URL.
        owner: Repository owner.
        repo: Repository name.
        branch: Branch name.

    Returns:
        Fully qualified REST URL for updating the branch ref.
    """
    return f"{api}/repos/{owner}/{repo}/git/refs/heads/{quote(branch, safe='')}"


class GitHubProvider:
    """GitHub REST API v3 implementation of :class:`ScmProvider`."""

    def __init__(self, api_base_url: str = "https://api.github.com") -> None:
        """Store the API base URL for subsequent requests.

        Args:
            api_base_url: Base URL for the GitHub API.
        """
        self._api = api_base_url.rstrip("/")

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    @staticmethod
    def _client(*, timeout: float) -> httpx.AsyncClient:
        """Build an HTTP client with the configured CA bundle.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            Configured ``httpx.AsyncClient`` instance.
        """
        return async_client(timeout=timeout)

    async def branch_head_sha(
        self,
        repo_url: str,
        branch: str,
        token: str,
    ) -> str | None:
        """Return the commit SHA at the tip of *branch*, if it exists.

        Args:
            repo_url: HTTPS clone URL.
            branch: Branch name.
            token: GitHub PAT.

        Returns:
            Commit SHA when the branch exists, else ``None``.
        """
        owner, repo = _parse_owner_repo(repo_url)
        async with self._client(timeout=30) as client:
            resp = await client.get(
                _branch_ref_url(self._api, owner, repo, branch),
                headers=self._headers(token),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return str(resp.json()["object"]["sha"])

    async def create_branch(
        self,
        repo_url: str,
        base_branch: str,
        new_branch: str,
        token: str,
    ) -> str:
        """Create a Git ref for *new_branch* from the HEAD of *base_branch*.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Source branch.
            new_branch: New branch name.
            token: GitHub PAT or app installation token.

        Returns:
            Commit SHA at the tip of *new_branch* after creation (or if it already exists).
        """
        owner, repo = _parse_owner_repo(repo_url)
        existing = await self.branch_head_sha(repo_url, new_branch, token)
        if existing:
            logger.info("Branch %s already exists on %s/%s", new_branch, owner, repo)
            return existing

        async with self._client(timeout=30) as client:
            ref_resp = await client.get(
                _branch_ref_url(self._api, owner, repo, base_branch),
                headers=self._headers(token),
            )
            ref_resp.raise_for_status()
            sha = str(ref_resp.json()["object"]["sha"])

            create_resp = await client.post(
                f"{self._api}/repos/{owner}/{repo}/git/refs",
                headers=self._headers(token),
                json={"ref": f"refs/heads/{new_branch}", "sha": sha},
            )
            create_resp.raise_for_status()
        logger.info("Created branch %s from %s@%s on %s/%s", new_branch, base_branch, sha[:8], owner, repo)
        return sha

    async def push_files(
        self,
        repo_url: str,
        branch: str,
        files: dict[str, bytes],
        commit_message: str,
        token: str,
        *,
        parent_commit_sha: str | None = None,
    ) -> str:
        """Push files atomically via the Git Trees + Commits API.

        Args:
            repo_url: HTTPS clone URL.
            branch: Target branch.
            files: Mapping of path → content.
            commit_message: Commit message.
            token: GitHub PAT.
            parent_commit_sha: Optional parent commit when the branch ref is not
                yet readable (e.g. immediately after :meth:`create_branch`).

        Returns:
            SHA of the new commit.

        Raises:
            TimeoutError: When the full operation exceeds the submission budget.
        """
        owner, repo = _parse_owner_repo(repo_url)
        # Per-request timeout caps a single hung blob/tree/commit call; operation
        # deadline bounds the full multi-file submission (pacing + retries).
        per_request_timeout = _PUSH_PER_REQUEST_TIMEOUT_S
        operation_timeout_s = _blob_operation_timeout_s(len(files))
        operation_deadline = time.monotonic() + operation_timeout_s
        commit_sha: str
        try:
            async with asyncio.timeout(operation_timeout_s):
                async with self._client(timeout=per_request_timeout) as client:
                    headers = self._headers(token)

                    if parent_commit_sha:
                        commit_sha_head = parent_commit_sha
                    else:
                        ref_resp = await client.get(
                            _branch_ref_url(self._api, owner, repo, branch),
                            headers=headers,
                        )
                        ref_resp.raise_for_status()
                        commit_sha_head = str(ref_resp.json()["object"]["sha"])

                    commit_detail = await client.get(
                        f"{self._api}/repos/{owner}/{repo}/git/commits/{commit_sha_head}",
                        headers=headers,
                    )
                    commit_detail.raise_for_status()
                    base_tree_sha = commit_detail.json()["tree"]["sha"]

                    tree_items = []
                    last_request_at: float | None = None
                    for path, content in files.items():
                        if _is_text(content):
                            blob_json = {"content": content.decode("utf-8"), "encoding": "utf-8"}
                        else:
                            blob_json = {
                                "content": base64.b64encode(content).decode(),
                                "encoding": "base64",
                            }

                        blob_resp, last_request_at = await _paced_post_json(
                            client,
                            url=f"{self._api}/repos/{owner}/{repo}/git/blobs",
                            headers=headers,
                            json=blob_json,
                            last_request_at=last_request_at,
                            operation_deadline=operation_deadline,
                        )
                        tree_items.append(
                            {
                                "path": path,
                                "mode": "100644",
                                "type": "blob",
                                "sha": blob_resp.json()["sha"],
                            }
                        )

                    tree_resp, last_request_at = await _paced_post_json(
                        client,
                        url=f"{self._api}/repos/{owner}/{repo}/git/trees",
                        headers=headers,
                        json={"base_tree": base_tree_sha, "tree": tree_items},
                        last_request_at=last_request_at,
                        operation_deadline=operation_deadline,
                    )
                    tree_sha = tree_resp.json()["sha"]

                    commit_resp, last_request_at = await _paced_post_json(
                        client,
                        url=f"{self._api}/repos/{owner}/{repo}/git/commits",
                        headers=headers,
                        json={
                            "message": commit_message,
                            "tree": tree_sha,
                            "parents": [commit_sha_head],
                        },
                        last_request_at=last_request_at,
                        operation_deadline=operation_deadline,
                    )
                    commit_sha = commit_resp.json()["sha"]

                    await _paced_request_json(
                        client,
                        method="PATCH",
                        url=_branch_refs_update_url(self._api, owner, repo, branch),
                        headers=headers,
                        json={"sha": commit_sha},
                        last_request_at=last_request_at,
                        operation_deadline=operation_deadline,
                    )
        except TimeoutError as exc:
            msg = f"GitHub push_files timed out after {operation_timeout_s:.0f}s ({len(files)} files)"
            raise TimeoutError(msg) from exc

        logger.info("Pushed %d files to %s/%s@%s (%s)", len(files), owner, repo, branch, commit_sha[:8])
        return commit_sha

    async def create_pull_request(
        self,
        repo_url: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequestResult:
        """Create a pull request via the GitHub Pulls API.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Target branch.
            head_branch: Source branch.
            title: PR title.
            body: PR body.
            token: GitHub PAT.

        Returns:
            PullRequestResult with the URL.
        """
        owner, repo = _parse_owner_repo(repo_url)
        async with self._client(timeout=30) as client:
            headers = self._headers(token)
            resp = await client.post(
                f"{self._api}/repos/{owner}/{repo}/pulls",
                headers=headers,
                json={
                    "title": title,
                    "body": body,
                    "head": head_branch,
                    "base": base_branch,
                },
            )
            if resp.status_code == 422:
                existing = await client.get(
                    f"{self._api}/repos/{owner}/{repo}/pulls",
                    headers=headers,
                    params={
                        "head": f"{owner}:{head_branch}",
                        "base": base_branch,
                        "state": "open",
                    },
                )
                existing.raise_for_status()
                pulls = existing.json()
                if pulls:
                    pr_url = pulls[0]["html_url"]
                    logger.info("Reusing existing PR %s on %s/%s", pr_url, owner, repo)
                    return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="github")
            resp.raise_for_status()
            data = resp.json()

        pr_url = data["html_url"]
        logger.info("Created PR %s on %s/%s", pr_url, owner, repo)
        return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="github")


def _github_error_message(response: httpx.Response) -> str | None:
    """Extract a human-readable error message from a GitHub API response.

    Args:
        response: HTTP response from the GitHub API.

    Returns:
        Message string when present in the JSON body, else ``None``.
    """
    try:
        payload = response.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        message = payload.get("message")
        if isinstance(message, str) and message:
            return message
    return None


def _is_secondary_rate_limit(response: httpx.Response) -> bool:
    """Return whether *response* indicates a GitHub secondary rate limit.

    Args:
        response: HTTP response from the GitHub API.

    Returns:
        True when GitHub reports a secondary rate limit or HTTP 429.
    """
    if response.status_code == 429:
        return True
    message = _github_error_message(response)
    return bool(message and "secondary rate limit" in message.lower())


def _max_rate_limit_retry_wait_s() -> float:
    """Return worst-case wait time across rate-limit retries for one blob upload.

    Returns:
        Total sleep budget in seconds before retries are exhausted.
    """
    total = 0.0
    for attempt in range(_BLOB_MAX_RETRIES - 1):
        total += max(
            _BLOB_MIN_INTERVAL_S,
            min(_MAX_RETRY_AFTER_S * (1.2**attempt), _MAX_RETRY_AFTER_S),
        )
    return total


def _blob_operation_timeout_s(file_count: int) -> float:
    """Return the push_files operation deadline for *file_count* blob uploads.

    Args:
        file_count: Number of files in the submission.

    Returns:
        Operation timeout in seconds, including pacing and worst-case retries.
    """
    per_file_pacing_and_retries = _BLOB_MIN_INTERVAL_S * (_BLOB_MAX_RETRIES + 1) + _max_rate_limit_retry_wait_s()
    blob_request_budget = file_count * _BLOB_MAX_RETRIES * _PUSH_PER_REQUEST_TIMEOUT_S
    non_blob_request_budget = _PUSH_NON_BLOB_REQUEST_COUNT * _PUSH_PER_REQUEST_TIMEOUT_S
    computed = file_count * per_file_pacing_and_retries + blob_request_budget + non_blob_request_budget
    floor = file_count * (_BLOB_MIN_INTERVAL_S + _PUSH_PER_REQUEST_TIMEOUT_S) + non_blob_request_budget
    return max(120.0, min(max(_PUSH_MAX_OPERATION_TIMEOUT_S, floor), computed))


def _remaining_operation_time_s(operation_deadline: float) -> float:
    """Return seconds remaining before *operation_deadline*.

    Args:
        operation_deadline: Monotonic timestamp when the operation must end.

    Returns:
        Remaining seconds (may be negative when the deadline has passed).
    """
    return operation_deadline - time.monotonic()


async def _paced_request_json(
    client: httpx.AsyncClient,
    *,
    method: str = "POST",
    url: str,
    headers: dict[str, str],
    json: dict[str, Any],
    last_request_at: float | None,
    operation_deadline: float | None = None,
) -> tuple[httpx.Response, float]:
    """Issue a paced GitHub JSON request with secondary rate-limit retries.

    Args:
        client: Active HTTP client.
        method: HTTP method (POST, PATCH, etc.).
        url: Request URL.
        headers: Request headers.
        json: JSON request body.
        last_request_at: Monotonic timestamp of the prior paced request, if any.
        operation_deadline: Optional monotonic deadline for the full push operation.

    Returns:
        Tuple of (response, monotonic timestamp after the successful request).

    Raises:
        RuntimeError: When the retry loop completes without producing a response.
        TimeoutError: When the operation deadline is exceeded before a retry can complete.
        httpx.HTTPStatusError: When the request fails after retries are exhausted.
    """
    if operation_deadline is not None and _remaining_operation_time_s(operation_deadline) <= 0:
        msg = "GitHub blob upload exceeded the push operation deadline"
        raise TimeoutError(msg)

    if last_request_at is not None:
        elapsed = time.monotonic() - last_request_at
        if elapsed < _BLOB_MIN_INTERVAL_S:
            sleep_s = _BLOB_MIN_INTERVAL_S - elapsed
            if operation_deadline is not None:
                remaining = _remaining_operation_time_s(operation_deadline)
                if remaining <= 0:
                    msg = "GitHub blob upload exceeded the push operation deadline"
                    raise TimeoutError(msg)
                sleep_s = min(sleep_s, remaining)
            await asyncio.sleep(sleep_s)

    response: httpx.Response | None = None
    for attempt in range(_BLOB_MAX_RETRIES):
        if operation_deadline is not None and _remaining_operation_time_s(operation_deadline) <= 0:
            msg = "GitHub blob upload exceeded the push operation deadline"
            raise TimeoutError(msg)

        response = await client.request(method, url, headers=headers, json=json)
        if response.is_success or not _is_secondary_rate_limit(response):
            response.raise_for_status()
            return response, time.monotonic()

        is_last_attempt = attempt == _BLOB_MAX_RETRIES - 1
        if is_last_attempt:
            break

        retry_after_raw = response.headers.get("retry-after", str(int(_DEFAULT_RETRY_AFTER_S)))
        try:
            retry_after = float(retry_after_raw)
        except ValueError:
            retry_after = _DEFAULT_RETRY_AFTER_S
        wait_s = max(
            _BLOB_MIN_INTERVAL_S,
            min(retry_after * (1.2**attempt), _MAX_RETRY_AFTER_S),
        )
        if operation_deadline is not None:
            remaining = _remaining_operation_time_s(operation_deadline)
            if remaining <= 0 or wait_s > remaining:
                await response.aclose()
                msg = "GitHub blob upload exceeded the push operation deadline during rate-limit retry"
                raise TimeoutError(msg)
        logger.warning(
            "GitHub secondary rate limit on %s (attempt %d/%d); waiting %.1fs",
            url.rsplit("/", 1)[-1],
            attempt + 1,
            _BLOB_MAX_RETRIES,
            wait_s,
        )
        await response.aclose()
        await asyncio.sleep(wait_s)

    if response is None:  # pragma: no cover - loop always performs one request
        msg = "GitHub request produced no response"
        raise RuntimeError(msg)
    message = _github_error_message(response)
    if message:
        raise httpx.HTTPStatusError(
            f"GitHub API error {response.status_code}: {message}",
            request=response.request,
            response=response,
        )
    response.raise_for_status()
    return response, time.monotonic()


async def _paced_post_json(
    client: httpx.AsyncClient,
    *,
    url: str,
    headers: dict[str, str],
    json: dict[str, Any],
    last_request_at: float | None,
    operation_deadline: float | None = None,
) -> tuple[httpx.Response, float]:
    """POST JSON to GitHub with pacing and secondary rate-limit retries.

    Args:
        client: Active HTTP client.
        url: Request URL.
        headers: Request headers.
        json: JSON request body.
        last_request_at: Monotonic timestamp of the prior paced request, if any.
        operation_deadline: Optional monotonic deadline for the full push operation.

    Returns:
        Tuple of (response, monotonic timestamp after the successful request).
    """
    return await _paced_request_json(
        client,
        method="POST",
        url=url,
        headers=headers,
        json=json,
        last_request_at=last_request_at,
        operation_deadline=operation_deadline,
    )


def _is_text(data: bytes) -> bool:
    """Heuristic: treat content as text if it decodes as UTF-8 without errors.

    Args:
        data: Raw bytes to check.

    Returns:
        True if the data is valid UTF-8 text.
    """
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    return True
