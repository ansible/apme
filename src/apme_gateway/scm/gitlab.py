"""GitLab SCM provider — Phase 2 of ADR-050.

Uses the GitLab REST API v4 for branch creation, multi-file commits, and
merge-request creation. Supports ``gitlab.com`` and self-hosted instances via
a configurable ``api_base_url``.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote, urlparse

import httpx

from apme_gateway.scm._http import async_client
from apme_gateway.scm.base import PullRequestResult
from apme_gateway.scm.urls import DEFAULT_GITLAB_API_URL, split_user_pass_token

logger = logging.getLogger(__name__)


def _parse_project_path(repo_url: str) -> str:
    """Extract the URL-encoded GitLab project path from a clone URL.

    Args:
        repo_url: HTTPS URL like ``https://gitlab.com/group/sub/repo.git``.

    Returns:
        Path with ``/`` encoded as ``%2F`` (e.g. ``group%2Fsub%2Frepo``).

    Raises:
        ValueError: If the URL cannot be parsed.
    """
    parsed = urlparse(repo_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        msg = f"Cannot extract GitLab project path from URL: {repo_url}"
        raise ValueError(msg)
    parts[-1] = parts[-1].removesuffix(".git")
    return quote("/".join(parts), safe="")


class GitLabProvider:
    """GitLab REST API v4 implementation of :class:`ScmProvider`."""

    def __init__(self, api_base_url: str = DEFAULT_GITLAB_API_URL) -> None:
        """Store the API base URL for subsequent requests.

        Args:
            api_base_url: Base URL for the GitLab API (including ``/api/v4``).
        """
        self._api = api_base_url.rstrip("/")

    def _headers(self, token: str) -> dict[str, str]:
        """Build Authorization headers for GitLab API calls.

        Personal/project/group access tokens use Bearer + PRIVATE-TOKEN
        (older self-hosted GitLab prefers PRIVATE-TOKEN). Deploy tokens of
        the form ``username:token`` use HTTP Basic.

        Args:
            token: GitLab personal, project, group, or deploy token.

        Returns:
            Request headers including auth.
        """
        user_pass = split_user_pass_token(token)
        if user_pass is not None:
            user, password = user_pass
            encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
            return {
                "Authorization": f"Basic {encoded}",
                "Content-Type": "application/json",
            }
        return {
            "Authorization": f"Bearer {token}",
            "PRIVATE-TOKEN": token,
            "Content-Type": "application/json",
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

    def _project_url(self, project: str, *parts: str) -> str:
        """Build a project-scoped API URL.

        Args:
            project: URL-encoded project path.
            *parts: Additional path segments.

        Returns:
            Fully qualified API URL.
        """
        suffix = "/".join(parts)
        return f"{self._api}/projects/{project}/{suffix}"

    async def _file_exists(
        self,
        client: httpx.AsyncClient,
        project: str,
        branch: str,
        file_path: str,
        headers: dict[str, str],
    ) -> bool:
        """Return True when *file_path* exists on *branch*.

        Args:
            client: Active HTTP client for GitLab API calls.
            project: URL-encoded project path.
            branch: Branch name to inspect.
            file_path: Repository-relative file path.
            headers: Authenticated request headers.

        Returns:
            ``True`` when the file exists on the branch, else ``False``.
        """
        encoded_path = quote(file_path, safe="")
        resp = await client.head(
            self._project_url(project, f"repository/files/{encoded_path}"),
            headers=headers,
            params={"ref": branch},
        )
        return bool(resp.status_code == 200)

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
            token: GitLab access token.

        Returns:
            Commit SHA when the branch exists, else ``None``.
        """
        project = _parse_project_path(repo_url)
        async with self._client(timeout=30) as client:
            resp = await client.get(
                self._project_url(project, "repository/branches", quote(branch, safe="")),
                headers=self._headers(token),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            commit = resp.json().get("commit") or {}
            sha = commit.get("id")
            return str(sha) if sha else None

    async def create_branch(
        self,
        repo_url: str,
        base_branch: str,
        new_branch: str,
        token: str,
    ) -> str:
        """Create *new_branch* from *base_branch* (or return existing tip).

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Source branch.
            new_branch: New branch name.
            token: GitLab access token.

        Returns:
            Commit SHA at the tip of *new_branch*.
        """
        project = _parse_project_path(repo_url)
        existing = await self.branch_head_sha(repo_url, new_branch, token)
        if existing:
            logger.info("Branch %s already exists on GitLab project %s", new_branch, project)
            return existing

        async with self._client(timeout=30) as client:
            resp = await client.post(
                self._project_url(project, "repository/branches"),
                headers=self._headers(token),
                params={"branch": new_branch, "ref": base_branch},
            )
            if resp.status_code in {400, 409}:
                existing_after = await self.branch_head_sha(repo_url, new_branch, token)
                if existing_after:
                    logger.info(
                        "Branch %s already exists on GitLab project %s (create raced)",
                        new_branch,
                        project,
                    )
                    return existing_after
            resp.raise_for_status()
            commit = resp.json().get("commit") or {}
            sha = str(commit.get("id") or "")
        logger.info("Created GitLab branch %s from %s on %s", new_branch, base_branch, project)
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
        """Push files atomically via the GitLab Commits API.

        Remediation patches always update existing scanned files, so actions
        default to ``update`` (no per-file probe). When *parent_commit_sha* is
        provided, the branch tip must still match that SHA or the push fails
        (TOCTOU guard).

        Args:
            repo_url: HTTPS clone URL.
            branch: Target branch (must already exist).
            files: Mapping of relative path → file content.
            commit_message: Commit message for the push.
            token: GitLab access token.
            parent_commit_sha: Expected tip SHA; when set, must match branch tip.

        Returns:
            The SHA of the new commit.

        Raises:
            ValueError: If *parent_commit_sha* does not match the current tip.
        """
        project = _parse_project_path(repo_url)
        headers = self._headers(token)

        if parent_commit_sha:
            tip = await self.branch_head_sha(repo_url, branch, token)
            if tip is not None and tip != parent_commit_sha:
                msg = f"GitLab branch '{branch}' tip {tip[:8]} does not match expected parent {parent_commit_sha[:8]}"
                raise ValueError(msg)

        actions: list[dict[str, str]] = []
        for path, content in files.items():
            if _is_text(content):
                payload = content.decode("utf-8")
                encoding = "text"
            else:
                payload = base64.b64encode(content).decode()
                encoding = "base64"
            actions.append(
                {
                    "action": "update",
                    "file_path": path,
                    "content": payload,
                    "encoding": encoding,
                }
            )

        async with self._client(timeout=60) as client:
            resp = await client.post(
                self._project_url(project, "repository/commits"),
                headers=headers,
                json={
                    "branch": branch,
                    "commit_message": commit_message,
                    "actions": actions,
                },
            )
            # If some paths are new (create), retry with per-file create/update.
            if resp.status_code == 400:
                err_text = (resp.text or "").lower()
                if "does not exist" in err_text or "a file with this name doesn't exist" in err_text:
                    mixed_actions: list[dict[str, str]] = []
                    for action in actions:
                        exists = await self._file_exists(
                            client,
                            project,
                            branch,
                            action["file_path"],
                            headers,
                        )
                        mixed_actions.append({**action, "action": "update" if exists else "create"})
                    resp = await client.post(
                        self._project_url(project, "repository/commits"),
                        headers=headers,
                        json={
                            "branch": branch,
                            "commit_message": commit_message,
                            "actions": mixed_actions,
                        },
                    )
            resp.raise_for_status()
            commit_sha = str(resp.json()["id"])

        logger.info("Pushed %d files to GitLab %s@%s (%s)", len(files), project, branch, commit_sha[:8])
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
        """Open a merge request (returned as ``PullRequestResult``).

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Target branch for the MR.
            head_branch: Source branch with changes.
            title: MR title.
            body: MR description (Markdown).
            token: GitLab access token.

        Returns:
            PullRequestResult with the MR web URL.
        """
        project = _parse_project_path(repo_url)
        async with self._client(timeout=30) as client:
            headers = self._headers(token)
            resp = await client.post(
                self._project_url(project, "merge_requests"),
                headers=headers,
                json={
                    "source_branch": head_branch,
                    "target_branch": base_branch,
                    "title": title,
                    "description": body,
                },
            )
            if resp.status_code == 409:
                existing = await client.get(
                    self._project_url(project, "merge_requests"),
                    headers=headers,
                    params={
                        "source_branch": head_branch,
                        "target_branch": base_branch,
                        "state": "opened",
                    },
                )
                existing.raise_for_status()
                mrs = existing.json()
                if mrs:
                    mr_url = str(mrs[0]["web_url"])
                    logger.info("Reusing existing GitLab MR %s on %s", mr_url, project)
                    return PullRequestResult(pr_url=mr_url, branch_name=head_branch, provider="gitlab")
            resp.raise_for_status()
            data = resp.json()

        mr_url = str(data["web_url"])
        logger.info("Created GitLab MR %s on %s", mr_url, project)
        return PullRequestResult(pr_url=mr_url, branch_name=head_branch, provider="gitlab")


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
