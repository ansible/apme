"""GitHub SCM provider — Phase 1 of ADR-050.

Uses the GitHub REST API v3 for branch creation, file push (via the
Git Trees/Commits API for atomic multi-file commits), and PR creation.
Supports both ``github.com`` and GitHub Enterprise Server via a
configurable ``api_base_url``.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote, urlparse

import httpx

from apme_gateway.scm._http import async_client, custom_ca_bundle, http_verify
from apme_gateway.scm.base import PullRequestResult

logger = logging.getLogger(__name__)

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
        """
        owner, repo = _parse_owner_repo(repo_url)
        async with self._client(timeout=60) as client:
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
            for path, content in files.items():
                if _is_text(content):
                    blob_json = {"content": content.decode("utf-8"), "encoding": "utf-8"}
                else:
                    blob_json = {"content": base64.b64encode(content).decode(), "encoding": "base64"}

                blob_resp = await client.post(
                    f"{self._api}/repos/{owner}/{repo}/git/blobs",
                    headers=headers,
                    json=blob_json,
                )
                blob_resp.raise_for_status()
                tree_items.append(
                    {
                        "path": path,
                        "mode": "100644",
                        "type": "blob",
                        "sha": blob_resp.json()["sha"],
                    }
                )

            tree_resp = await client.post(
                f"{self._api}/repos/{owner}/{repo}/git/trees",
                headers=headers,
                json={"base_tree": base_tree_sha, "tree": tree_items},
            )
            tree_resp.raise_for_status()
            tree_sha = tree_resp.json()["sha"]

            commit_resp = await client.post(
                f"{self._api}/repos/{owner}/{repo}/git/commits",
                headers=headers,
                json={
                    "message": commit_message,
                    "tree": tree_sha,
                    "parents": [commit_sha_head],
                },
            )
            commit_resp.raise_for_status()
            commit_sha: str = commit_resp.json()["sha"]

            update_resp = await client.patch(
                _branch_refs_update_url(self._api, owner, repo, branch),
                headers=headers,
                json={"sha": commit_sha},
            )
            update_resp.raise_for_status()

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
