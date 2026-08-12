"""Bitbucket SCM providers — Phase 2 of ADR-050.

Implements Bitbucket Cloud REST API 2.0 and Bitbucket Server/Data Center
REST API 1.0 behind the shared ``bitbucket`` registry key.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote, urlparse

import httpx

from apme_gateway.scm._http import async_client
from apme_gateway.scm.base import PullRequestResult
from apme_gateway.scm.urls import (
    DEFAULT_BITBUCKET_CLOUD_API_URL,
    is_bitbucket_cloud_api,
    split_user_pass_token,
)

logger = logging.getLogger(__name__)


def _parse_workspace_repo(repo_url: str) -> tuple[str, str]:
    """Extract ``(workspace, repo_slug)`` from a Bitbucket Cloud clone URL.

    Args:
        repo_url: HTTPS URL like ``https://bitbucket.org/workspace/repo.git``.

    Returns:
        Tuple of workspace and repository slug.

    Raises:
        ValueError: If the URL cannot be parsed.
    """
    parsed = urlparse(repo_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        msg = f"Cannot extract workspace/repo from Bitbucket URL: {repo_url}"
        raise ValueError(msg)
    return parts[0], parts[1].removesuffix(".git")


def parse_server_project_repo(repo_url: str) -> tuple[str, str]:
    """Extract ``(project_key, repo_slug)`` from a Bitbucket Server clone URL.

    Supports ``/scm/{PROJECT}/{repo}.git`` and
    ``/projects/{PROJECT}/repos/{repo}`` path layouts.

    Args:
        repo_url: HTTPS clone URL for Bitbucket Server/DC.

    Returns:
        Tuple of project key and repository slug.

    Raises:
        ValueError: If the URL cannot be parsed.
    """
    parsed = urlparse(repo_url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if not parts:
        msg = f"Cannot extract project/repo from Bitbucket Server URL: {repo_url}"
        raise ValueError(msg)

    if parts[0] == "scm" and len(parts) >= 3:
        return parts[1], parts[2].removesuffix(".git")

    if "projects" in parts and "repos" in parts:
        try:
            proj_idx = parts.index("projects")
            repos_idx = parts.index("repos")
            if repos_idx == proj_idx + 2 and len(parts) > repos_idx:
                return parts[proj_idx + 1], parts[repos_idx + 1].removesuffix(".git")
        except (ValueError, IndexError):
            pass

    # Fail closed — do not guess from trailing path segments.
    msg = (
        f"Cannot extract project/repo from Bitbucket Server URL: {repo_url}. "
        "Expected /scm/{PROJECT}/{repo}.git or /projects/{PROJECT}/repos/{repo}."
    )
    raise ValueError(msg)


def _auth_headers(token: str) -> dict[str, str]:
    """Build Authorization headers for Bitbucket tokens.

    Raw tokens use Bearer auth. ``username:app_password`` uses Basic auth.

    Args:
        token: Bitbucket access token or ``username:app_password``.

    Returns:
        Request headers including Authorization.
    """
    user_pass = split_user_pass_token(token)
    if user_pass is not None:
        user, password = user_pass
        encoded = base64.b64encode(f"{user}:{password}".encode()).decode()
        return {"Authorization": f"Basic {encoded}"}
    return {"Authorization": f"Bearer {token}"}


class BitbucketCloudProvider:
    """Bitbucket Cloud REST API 2.0 implementation of :class:`ScmProvider`."""

    def __init__(self, api_base_url: str = DEFAULT_BITBUCKET_CLOUD_API_URL) -> None:
        """Store the API base URL for subsequent requests.

        Args:
            api_base_url: Cloud API base (default ``https://api.bitbucket.org/2.0``).
        """
        self._api = api_base_url.rstrip("/")

    @staticmethod
    def _client(*, timeout: float) -> httpx.AsyncClient:
        """Build an HTTP client with the configured CA bundle.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            Configured ``httpx.AsyncClient`` instance.
        """
        return async_client(timeout=timeout)

    def _repo_url(self, workspace: str, repo: str, *parts: str) -> str:
        """Build a repository-scoped Cloud API URL.

        Args:
            workspace: Bitbucket Cloud workspace slug.
            repo: Repository slug.
            *parts: Additional path segments.

        Returns:
            Fully qualified API URL.
        """
        suffix = "/".join(parts)
        return f"{self._api}/repositories/{workspace}/{repo}/{suffix}"

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
            token: Bitbucket access token or app password.

        Returns:
            Commit SHA when the branch exists, else ``None``.
        """
        workspace, repo = _parse_workspace_repo(repo_url)
        async with self._client(timeout=30) as client:
            resp = await client.get(
                self._repo_url(workspace, repo, "refs/branches", quote(branch, safe="")),
                headers=_auth_headers(token),
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            target = resp.json().get("target") or {}
            sha = target.get("hash")
            return str(sha) if sha else None

    async def create_branch(
        self,
        repo_url: str,
        base_branch: str,
        new_branch: str,
        token: str,
    ) -> str:
        """Create *new_branch* from the tip of *base_branch*.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Source branch.
            new_branch: New branch name.
            token: Bitbucket access token or app password.

        Returns:
            Commit SHA at the tip of *new_branch*.

        Raises:
            ValueError: If *base_branch* does not exist.
        """
        workspace, repo = _parse_workspace_repo(repo_url)
        existing = await self.branch_head_sha(repo_url, new_branch, token)
        if existing:
            logger.info("Branch %s already exists on %s/%s", new_branch, workspace, repo)
            return existing

        base_sha = await self.branch_head_sha(repo_url, base_branch, token)
        if not base_sha:
            msg = f"Base branch '{base_branch}' not found on {workspace}/{repo}"
            raise ValueError(msg)

        async with self._client(timeout=30) as client:
            resp = await client.post(
                self._repo_url(workspace, repo, "refs/branches"),
                headers={**_auth_headers(token), "Content-Type": "application/json"},
                json={"name": new_branch, "target": {"hash": base_sha}},
            )
            if resp.status_code in {400, 409}:
                existing_after = await self.branch_head_sha(repo_url, new_branch, token)
                if existing_after:
                    logger.info("Branch %s already exists on %s/%s (create raced)", new_branch, workspace, repo)
                    return existing_after
            resp.raise_for_status()

        tip = await self.branch_head_sha(repo_url, new_branch, token) or base_sha
        logger.info("Created Bitbucket Cloud branch %s from %s@%s", new_branch, base_branch, tip[:8])
        return tip

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
        """Push files via the Bitbucket Cloud source (multipart) API.

        Args:
            repo_url: HTTPS clone URL.
            branch: Target branch (must already exist).
            files: Mapping of relative path → file content.
            commit_message: Commit message for the push.
            token: Bitbucket access token or app password.
            parent_commit_sha: Optional parent commit SHA; defaults to branch tip.

        Returns:
            The SHA of the new commit (branch tip after push).

        Raises:
            RuntimeError: If the commit succeeds but the branch tip cannot be resolved.
        """
        workspace, repo = _parse_workspace_repo(repo_url)
        parent = parent_commit_sha or await self.branch_head_sha(repo_url, branch, token)

        # Metadata parts must use (None, value) so httpx does not emit a
        # filename= parameter — Bitbucket treats named parts as file uploads.
        multipart: list[tuple[str, tuple[None, str] | tuple[str, bytes]]] = [
            ("message", (None, commit_message)),
            ("branch", (None, branch)),
        ]
        if parent:
            multipart.append(("parents", (None, parent)))
        for path, content in files.items():
            multipart.append((path, (path.split("/")[-1], content)))

        async with self._client(timeout=60) as client:
            resp = await client.post(
                self._repo_url(workspace, repo, "src"),
                headers=_auth_headers(token),
                files=multipart,
            )
            # Cloud returns 201 with empty body on success; resolve new tip.
            if resp.status_code not in {200, 201}:
                resp.raise_for_status()

        new_sha = await self.branch_head_sha(repo_url, branch, token)
        if not new_sha:
            msg = f"Commit succeeded but branch tip missing on {workspace}/{repo}@{branch}"
            raise RuntimeError(msg)
        logger.info(
            "Pushed %d files to Bitbucket Cloud %s/%s@%s (%s)", len(files), workspace, repo, branch, new_sha[:8]
        )
        return new_sha

    async def create_pull_request(
        self,
        repo_url: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequestResult:
        """Open a pull request on Bitbucket Cloud.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Target branch for the PR.
            head_branch: Source branch with changes.
            title: PR title.
            body: PR description (Markdown).
            token: Bitbucket access token or app password.

        Returns:
            PullRequestResult with the PR web URL.
        """
        workspace, repo = _parse_workspace_repo(repo_url)
        async with self._client(timeout=30) as client:
            headers = {**_auth_headers(token), "Content-Type": "application/json"}
            resp = await client.post(
                self._repo_url(workspace, repo, "pullrequests"),
                headers=headers,
                json={
                    "title": title,
                    "description": body,
                    "source": {"branch": {"name": head_branch}},
                    "destination": {"branch": {"name": base_branch}},
                },
            )
            if resp.status_code in {400, 409}:
                existing = await client.get(
                    self._repo_url(workspace, repo, "pullrequests"),
                    headers=_auth_headers(token),
                    params={"state": "OPEN"},
                )
                if existing.status_code == 200:
                    values = existing.json().get("values") or []
                    for pr in values:
                        source = (pr.get("source") or {}).get("branch") or {}
                        if source.get("name") == head_branch:
                            pr_url = str(pr["links"]["html"]["href"])
                            logger.info("Reusing existing Bitbucket Cloud PR %s", pr_url)
                            return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="bitbucket")
            resp.raise_for_status()
            data = resp.json()

        pr_url = str(data["links"]["html"]["href"])
        logger.info("Created Bitbucket Cloud PR %s on %s/%s", pr_url, workspace, repo)
        return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="bitbucket")


class BitbucketServerProvider:
    """Bitbucket Server/Data Center REST API 1.0 implementation."""

    def __init__(self, api_base_url: str) -> None:
        """Store the Server/DC REST API base URL.

        Args:
            api_base_url: Base such as ``https://bitbucket.example.com/rest/api/1.0``.
        """
        self._api = api_base_url.rstrip("/")

    @staticmethod
    def _client(*, timeout: float) -> httpx.AsyncClient:
        """Build an HTTP client with the configured CA bundle.

        Args:
            timeout: Request timeout in seconds.

        Returns:
            Configured ``httpx.AsyncClient`` instance.
        """
        return async_client(timeout=timeout)

    def _repo_url(self, project: str, repo: str, *parts: str) -> str:
        """Build a repository-scoped Server/DC API URL.

        Args:
            project: Bitbucket project key.
            repo: Repository slug.
            *parts: Additional path segments.

        Returns:
            Fully qualified API URL.
        """
        suffix = "/".join(parts)
        return f"{self._api}/projects/{project}/repos/{repo}/{suffix}"

    async def branch_head_sha(
        self,
        repo_url: str,
        branch: str,
        token: str,
    ) -> str | None:
        """Return the latest commit SHA for *branch*.

        Args:
            repo_url: HTTPS clone URL.
            branch: Branch name.
            token: Bitbucket access token or app password.

        Returns:
            Commit SHA when the branch exists, else ``None``.
        """
        project, repo = parse_server_project_repo(repo_url)
        async with self._client(timeout=30) as client:
            resp = await client.get(
                self._repo_url(project, repo, "commits"),
                headers=_auth_headers(token),
                params={"until": f"refs/heads/{branch}", "limit": 1},
            )
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            values = resp.json().get("values") or []
            if not values:
                return None
            return str(values[0]["id"])

    async def create_branch(
        self,
        repo_url: str,
        base_branch: str,
        new_branch: str,
        token: str,
    ) -> str:
        """Create *new_branch* from *base_branch* on Server/DC.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Source branch.
            new_branch: New branch name.
            token: Bitbucket access token or app password.

        Returns:
            Commit SHA at the tip of *new_branch*.
        """
        project, repo = parse_server_project_repo(repo_url)
        existing = await self.branch_head_sha(repo_url, new_branch, token)
        if existing:
            logger.info("Branch %s already exists on %s/%s", new_branch, project, repo)
            return existing

        async with self._client(timeout=30) as client:
            resp = await client.post(
                self._repo_url(project, repo, "branches"),
                headers={**_auth_headers(token), "Content-Type": "application/json"},
                json={
                    "name": new_branch,
                    "startPoint": f"refs/heads/{base_branch}",
                },
            )
            if resp.status_code in {400, 409}:
                existing_after = await self.branch_head_sha(repo_url, new_branch, token)
                if existing_after:
                    logger.info("Branch %s already exists on %s/%s (create raced)", new_branch, project, repo)
                    return existing_after
            resp.raise_for_status()
            data = resp.json()
            sha = str(data.get("latestCommit") or data.get("id") or "")
            if not sha:
                sha = await self.branch_head_sha(repo_url, new_branch, token) or ""
        logger.info("Created Bitbucket Server branch %s from %s on %s/%s", new_branch, base_branch, project, repo)
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
        """Push files via sequential Server browse (edit) commits.

        Bitbucket Server/DC has no public atomic multi-file commit API; files
        are committed one-by-one while chaining ``sourceCommitId``. Each PUT
        uses multipart form data as required by the Server REST API.

        Args:
            repo_url: HTTPS clone URL.
            branch: Target branch (must already exist).
            files: Mapping of relative path → file content.
            commit_message: Commit message for each file commit.
            token: Bitbucket access token or app password.
            parent_commit_sha: Optional parent commit SHA; defaults to branch tip.

        Returns:
            The SHA of the latest commit after all files are pushed.

        Raises:
            ValueError: If the tip of *branch* cannot be resolved, or if a file
                cannot be committed (including binary content Server cannot store
                via this API path when decoding fails in unexpected ways).
        """
        project, repo = parse_server_project_repo(repo_url)
        commit_sha = parent_commit_sha or await self.branch_head_sha(repo_url, branch, token)
        if not commit_sha:
            msg = f"Cannot resolve tip of branch '{branch}' on {project}/{repo}"
            raise ValueError(msg)

        async with self._client(timeout=60) as client:
            headers = _auth_headers(token)
            for path, content in files.items():
                # Probe existence: sourceCommitId is required for edits and must
                # be omitted for new files.
                probe = await client.get(
                    self._repo_url(project, repo, "browse", quote(path, safe="/")),
                    headers=headers,
                    params={"at": f"refs/heads/{branch}"},
                )
                exists = probe.status_code == 200

                # Multipart form — Server rejects urlencoded bodies for browse PUT.
                # Send raw bytes; text is UTF-8, binary is raw file bytes.
                form_parts: list[tuple[str, tuple[None, str | bytes]]] = [
                    ("content", (None, content)),
                    ("message", (None, commit_message)),
                    ("branch", (None, branch)),
                ]
                if exists:
                    form_parts.append(("sourceCommitId", (None, commit_sha)))

                resp = await client.put(
                    self._repo_url(project, repo, "browse", quote(path, safe="/")),
                    headers=headers,
                    files=form_parts,
                )
                resp.raise_for_status()
                # Response includes the new commit id; fall back to tip lookup.
                try:
                    body = resp.json()
                    commit_sha = str(body.get("id") or body.get("commitId") or commit_sha)
                except Exception:
                    tip = await self.branch_head_sha(repo_url, branch, token)
                    if tip:
                        commit_sha = tip
                else:
                    # Always refresh tip so the next file's sourceCommitId is current.
                    tip = await self.branch_head_sha(repo_url, branch, token)
                    if tip:
                        commit_sha = tip

        tip = await self.branch_head_sha(repo_url, branch, token) or commit_sha
        logger.info("Pushed %d files to Bitbucket Server %s/%s@%s (%s)", len(files), project, repo, branch, tip[:8])
        return tip

    async def create_pull_request(
        self,
        repo_url: str,
        base_branch: str,
        head_branch: str,
        title: str,
        body: str,
        token: str,
    ) -> PullRequestResult:
        """Open a pull request on Bitbucket Server/DC.

        Args:
            repo_url: HTTPS clone URL.
            base_branch: Target branch for the PR.
            head_branch: Source branch with changes.
            title: PR title.
            body: PR description (Markdown).
            token: Bitbucket access token or app password.

        Returns:
            PullRequestResult with the PR web URL.
        """
        project, repo = parse_server_project_repo(repo_url)
        async with self._client(timeout=30) as client:
            headers = {**_auth_headers(token), "Content-Type": "application/json"}
            resp = await client.post(
                self._repo_url(project, repo, "pull-requests"),
                headers=headers,
                json={
                    "title": title,
                    "description": body,
                    "fromRef": {
                        "id": f"refs/heads/{head_branch}",
                        "repository": {
                            "slug": repo,
                            "project": {"key": project},
                        },
                    },
                    "toRef": {
                        "id": f"refs/heads/{base_branch}",
                        "repository": {
                            "slug": repo,
                            "project": {"key": project},
                        },
                    },
                },
            )
            if resp.status_code in {409, 400}:
                existing = await client.get(
                    self._repo_url(project, repo, "pull-requests"),
                    headers=_auth_headers(token),
                    params={"direction": "OUTGOING", "at": f"refs/heads/{head_branch}", "state": "OPEN"},
                )
                if existing.status_code == 200:
                    values = existing.json().get("values") or []
                    if values:
                        pr_url = _server_pr_url(values[0], repo_url)
                        logger.info("Reusing existing Bitbucket Server PR %s", pr_url)
                        return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="bitbucket")
            resp.raise_for_status()
            data = resp.json()

        pr_url = _server_pr_url(data, repo_url)
        logger.info("Created Bitbucket Server PR %s on %s/%s", pr_url, project, repo)
        return PullRequestResult(pr_url=pr_url, branch_name=head_branch, provider="bitbucket")


def _server_pr_url(payload: dict[str, object], repo_url: str) -> str:
    """Extract a web URL from a Server PR payload, with a fallback builder.

    Args:
        payload: JSON body from a Bitbucket Server pull-request response.
        repo_url: HTTPS clone URL used to derive host/project/repo for fallbacks.

    Returns:
        Web URL for the pull request.
    """
    links = payload.get("links")
    if isinstance(links, dict):
        self_link = links.get("self")
        if isinstance(self_link, list) and self_link:
            href = self_link[0].get("href") if isinstance(self_link[0], dict) else None
            if href:
                return str(href)
    pr_id = payload.get("id")
    parsed = urlparse(repo_url)
    project, repo = parse_server_project_repo(repo_url)
    return f"{parsed.scheme}://{parsed.netloc}/projects/{project}/repos/{repo}/pull-requests/{pr_id}"


def create_bitbucket_provider(api_base_url: str) -> BitbucketCloudProvider | BitbucketServerProvider:
    """Return the Cloud or Server provider for *api_base_url*.

    Args:
        api_base_url: Bitbucket API base URL (Cloud 2.0 or Server/DC 1.0).

    Returns:
        :class:`BitbucketCloudProvider` for Cloud API bases, else
        :class:`BitbucketServerProvider`.
    """
    if is_bitbucket_cloud_api(api_base_url):
        return BitbucketCloudProvider(api_base_url=api_base_url)
    return BitbucketServerProvider(api_base_url=api_base_url)
