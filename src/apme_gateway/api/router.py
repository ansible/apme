"""REST and WebSocket API endpoints for the gateway (ADR-029, ADR-037).

Read endpoints serve persisted check/remediate activity.  Write operations happen via the
gRPC Reporting servicer (engine push model, ADR-020).  The ``WS /ws/session``
endpoint bridges the browser to Primary's FixSession gRPC stream for the
playground check + remediate lifecycle (ADR-029).  Project operations use the new
``WS /projects/{id}/ws/operate`` endpoint (ADR-037).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from typing import cast

from fastapi import APIRouter, HTTPException, Query, WebSocket
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.websockets import WebSocketDisconnect  # type: ignore[import-not-found]

from apme_engine.severity_defaults import severity_from_proto, severity_to_label
from apme_gateway.api.schemas import (
    ActivityDetail,
    ActivitySummary,
    AiAcceptanceEntry,
    AiModelInfo,
    CollectionDetail,
    CollectionProjectRef,
    CollectionRefSchema,
    CollectionSummary,
    ComponentHealth,
    CreateGalaxyServerRequest,
    CreateProjectRequest,
    DashboardSummary,
    GalaxyServerSchema,
    HealthStatus,
    LogEntry,
    PaginatedResponse,
    PatchDetail,
    ProjectDependencies,
    ProjectDetail,
    ProjectRanking,
    ProjectSummary,
    ProposalDetail,
    PythonPackageDetail,
    PythonPackageProjectRef,
    PythonPackageRefSchema,
    PythonPackageSummary,
    RemediationRateEntry,
    SessionDetail,
    SessionSummary,
    TopViolation,
    TrendPoint,
    UpdateGalaxyServerRequest,
    UpdateProjectRequest,
    ViolationDetail,
)
from apme_gateway.db import get_session
from apme_gateway.db import queries as q
from apme_gateway.db.models import GalaxyServer, Rule, RuleOverride, Scan, ScanManifest

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1")

try:
    from importlib.metadata import version as _pkg_version  # noqa: PLC0415

    _TOOLS_VERSION = _pkg_version("apme")
except Exception:  # noqa: BLE001
    _TOOLS_VERSION = "0.1.0"


_UPSTREAM_SERVICES: list[tuple[str, str, str]] = [
    ("Primary Orchestrator", "APME_PRIMARY_ADDRESS", "127.0.0.1:50051"),
    ("Native Validator", "NATIVE_GRPC_ADDRESS", "127.0.0.1:50055"),
    ("OPA Validator", "OPA_GRPC_ADDRESS", "127.0.0.1:50054"),
    ("Ansible Validator", "ANSIBLE_GRPC_ADDRESS", "127.0.0.1:50053"),
    ("Gitleaks Validator", "GITLEAKS_GRPC_ADDRESS", "127.0.0.1:50056"),
    ("Galaxy Proxy", "APME_GALAXY_PROXY_URL", "http://127.0.0.1:8765"),
    ("Abbenay AI", "APME_ABBENAY_ADDR", "127.0.0.1:50057"),
]


async def _probe_grpc(address: str) -> bool:
    """Probe a gRPC service via the standard health check service.

    Returns ``True`` if the health check succeeds **or** the service
    responds with ``UNIMPLEMENTED`` (reachable but no health service).

    Args:
        address: ``host:port`` of the gRPC service.

    Returns:
        True if the service is reachable.
    """
    import grpc.aio

    channel = grpc.aio.insecure_channel(address)
    try:
        try:
            from grpc_health.v1 import health_pb2, health_pb2_grpc

            stub = health_pb2_grpc.HealthStub(channel)
            await stub.Check(health_pb2.HealthCheckRequest(), timeout=2)
            return True
        except grpc.aio.AioRpcError as e:
            return e.code() == grpc.StatusCode.UNIMPLEMENTED
        except Exception:
            return False
    finally:
        await channel.close(grace=None)


async def _probe_http(url: str) -> bool:
    """Probe an HTTP service via a simple GET.

    Args:
        url: Base URL (e.g. ``http://127.0.0.1:8765``).

    Returns:
        True if the service responds with a non-server-error status (< 500).
    """
    import httpx

    try:
        async with httpx.AsyncClient(timeout=2) as client:
            resp = await client.get(url.rstrip("/") + "/simple/")
            return bool(resp.status_code < 500)
    except Exception:
        return False


async def _check_component(name: str, env_var: str, default: str) -> ComponentHealth:
    """Check a single upstream component.

    Args:
        name: Display name for the component.
        env_var: Environment variable holding the address.
        default: Default address if env var is not set.

    Returns:
        ComponentHealth with probed status.
    """
    address = os.environ.get(env_var, "").strip() or default
    if address.startswith("http"):
        ok = await _probe_http(address)
    else:
        ok = await _probe_grpc(address)
    return ComponentHealth(
        name=name,
        status="ok" if ok else "unavailable",
        address=address,
    )


@router.get("/health")  # type: ignore[untyped-decorator]
async def health() -> HealthStatus:
    """Check gateway health including database and upstream services.

    Returns:
        HealthStatus with overall, database, and per-component statuses.
    """
    db_ok = True
    try:
        async with get_session() as db:
            await q.session_count(db)
    except Exception:
        db_ok = False

    components = await asyncio.gather(
        *(_check_component(name, env_var, default) for name, env_var, default in _UPSTREAM_SERVICES)
    )
    component_list = list(components)

    all_ok = db_ok and all(c.status == "ok" for c in component_list)

    return HealthStatus(
        status="ok" if all_ok else "degraded",
        database="ok" if db_ok else "unavailable",
        components=component_list,
    )


@router.get("/ai/models")  # type: ignore[untyped-decorator]
async def list_ai_models() -> list[AiModelInfo]:
    """Return AI models available from the Abbenay daemon via Primary.

    Calls the Primary's ``ListAIModels`` gRPC method and translates the
    response to JSON.  Returns an empty list when Primary or Abbenay is
    unreachable (graceful degradation).

    Returns:
        List of available AI models.
    """
    import grpc.aio  # noqa: PLC0415

    from apme.v1 import primary_pb2, primary_pb2_grpc  # noqa: PLC0415

    primary_addr = os.environ.get("APME_PRIMARY_ADDRESS", "").strip() or "127.0.0.1:50051"
    channel = grpc.aio.insecure_channel(primary_addr)
    try:
        stub = primary_pb2_grpc.PrimaryStub(channel)  # type: ignore[no-untyped-call]
        resp = await stub.ListAIModels(primary_pb2.ListAIModelsRequest(), timeout=5)
        models = [AiModelInfo(id=m.id, provider=m.provider, name=m.name) for m in resp.models]
        if not models:
            logger.warning("ListAIModels returned 0 models from Primary at %s", primary_addr)
        return models
    except Exception:
        logger.warning("Failed to fetch AI models from Primary at %s", primary_addr, exc_info=True)
        return []
    finally:
        await channel.close(grace=None)


# ── Galaxy server settings (ADR-045) ─────────────────────────────────


def _to_galaxy_schema(gs: GalaxyServer) -> GalaxyServerSchema:
    """Convert a GalaxyServer ORM row to the API response schema.

    The token value is never exposed; only ``has_token`` is reported.

    Args:
        gs: GalaxyServer ORM instance.

    Returns:
        Pydantic GalaxyServerSchema.
    """
    return GalaxyServerSchema(
        id=gs.id,
        name=gs.name,
        url=gs.url,
        auth_url=gs.auth_url,
        has_token=bool(gs.token),
        created_at=gs.created_at,
        updated_at=gs.updated_at,
    )


@router.get("/settings/galaxy-servers")  # type: ignore[untyped-decorator]
async def list_galaxy_servers() -> list[GalaxyServerSchema]:
    """Return all globally configured Galaxy servers.

    Returns:
        List of Galaxy server definitions (tokens masked).
    """
    async with get_session() as db:
        servers = await q.list_galaxy_servers(db)
    return [_to_galaxy_schema(s) for s in servers]


@router.post("/settings/galaxy-servers", status_code=201)  # type: ignore[untyped-decorator]
async def create_galaxy_server(body: CreateGalaxyServerRequest) -> GalaxyServerSchema:
    """Create a new global Galaxy server definition.

    Args:
        body: Galaxy server creation payload.

    Returns:
        Newly created Galaxy server (token masked).

    Raises:
        HTTPException: 409 if a server with the same name already exists.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    try:
        async with get_session() as db:
            server = await q.create_galaxy_server(
                db,
                name=body.name,
                url=body.url,
                token=body.token,
                auth_url=body.auth_url,
            )
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Galaxy server named '{body.name}' already exists",
        ) from None

    from apme_gateway._galaxy_proxy_sync import schedule_push  # noqa: PLC0415

    schedule_push()
    return _to_galaxy_schema(server)


@router.get("/settings/galaxy-servers/{server_id}")  # type: ignore[untyped-decorator]
async def get_galaxy_server(server_id: int) -> GalaxyServerSchema:
    """Fetch a single Galaxy server by ID.

    Args:
        server_id: Primary key.

    Returns:
        Galaxy server definition (token masked).

    Raises:
        HTTPException: 404 if not found.
    """
    async with get_session() as db:
        server = await q.get_galaxy_server(db, server_id)
    if server is None:
        raise HTTPException(status_code=404, detail="Galaxy server not found")
    return _to_galaxy_schema(server)


@router.patch("/settings/galaxy-servers/{server_id}")  # type: ignore[untyped-decorator]
async def update_galaxy_server(
    server_id: int,
    body: UpdateGalaxyServerRequest,
) -> GalaxyServerSchema:
    """Update a Galaxy server definition.

    Args:
        server_id: Primary key.
        body: Fields to update.

    Returns:
        Updated Galaxy server (token masked).

    Raises:
        HTTPException: 400 if no fields provided, 404 if not found,
            409 if the new name conflicts with an existing server.
    """
    from sqlalchemy.exc import IntegrityError  # noqa: PLC0415

    updates: dict[str, str] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.url is not None:
        updates["url"] = body.url
    if body.token is not None:
        updates["token"] = body.token
    if body.auth_url is not None:
        updates["auth_url"] = body.auth_url
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    try:
        async with get_session() as db:
            server = await q.update_galaxy_server(db, server_id, **updates)
    except IntegrityError:
        raise HTTPException(
            status_code=409,
            detail=f"Galaxy server named '{updates.get('name', '')}' already exists",
        ) from None
    if server is None:
        raise HTTPException(status_code=404, detail="Galaxy server not found")

    from apme_gateway._galaxy_proxy_sync import schedule_push  # noqa: PLC0415

    schedule_push()
    return _to_galaxy_schema(server)


@router.delete("/settings/galaxy-servers/{server_id}", status_code=204)  # type: ignore[untyped-decorator]
async def delete_galaxy_server(server_id: int) -> None:
    """Delete a Galaxy server definition.

    Args:
        server_id: Primary key.

    Raises:
        HTTPException: 404 if not found.
    """
    async with get_session() as db:
        ok = await q.delete_galaxy_server(db, server_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Galaxy server not found")

    from apme_gateway._galaxy_proxy_sync import schedule_push  # noqa: PLC0415

    schedule_push()


# ── Project CRUD (ADR-037) ───────────────────────────────────────────


@router.post("/projects", status_code=201)  # type: ignore[untyped-decorator]
async def create_project(body: CreateProjectRequest) -> ProjectSummary:
    """Create a new project.

    Args:
        body: Project creation payload.

    Returns:
        Newly created project summary.

    Raises:
        HTTPException: 409 if a project with the same name already exists.
    """
    from sqlalchemy.exc import IntegrityError

    project_id = uuid.uuid4().hex
    try:
        async with get_session() as db:
            proj = await q.create_project(
                db,
                project_id=project_id,
                name=body.name,
                repo_url=body.repo_url,
                branch=body.branch,
            )
    except IntegrityError:
        raise HTTPException(status_code=409, detail=f"Project named '{body.name}' already exists") from None
    return ProjectSummary(
        id=proj.id,
        name=proj.name,
        repo_url=proj.repo_url,
        branch=proj.branch,
        created_at=proj.created_at,
        health_score=proj.health_score,
    )


@router.get("/projects")  # type: ignore[untyped-decorator]
async def list_projects(
    sort_by: str = Query(default="created_at"),
    order: str = Query(default="desc"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List all projects with summary data.

    Args:
        sort_by: Column to sort by.
        order: Sort direction (asc or desc).
        limit: Page size.
        offset: Page offset.

    Returns:
        Paginated list of project summaries.
    """
    async with get_session() as db:
        projects = await q.list_projects(
            db,
            limit=limit,
            offset=offset,
            sort_by=sort_by,
            order=order,
        )
        total = await q.project_count(db)
        items: list[ProjectSummary] = []
        for proj in projects:
            scan_cnt = await q.project_scan_count(db, proj.id)
            trend_scans = await q.project_trend(db, proj.id, limit=5)
            last_scan_at = trend_scans[-1].created_at if trend_scans else None
            vt = _compute_violation_trend(trend_scans)
            violations = await q.project_violations(db, proj.id)
            items.append(
                ProjectSummary(
                    id=proj.id,
                    name=proj.name,
                    repo_url=proj.repo_url,
                    branch=proj.branch,
                    created_at=proj.created_at,
                    health_score=proj.health_score,
                    total_violations=len(violations),
                    violation_trend=vt,
                    scan_count=scan_cnt,
                    last_scanned_at=last_scan_at,
                )
            )
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


def _compute_violation_trend(scans: list[Scan]) -> str:
    """Derive a trend direction from recent run violation counts.

    Args:
        scans: Recent Scan ORM rows ordered oldest-first (asc).

    Returns:
        One of ``"improving"``, ``"declining"``, or ``"stable"``.
    """
    if len(scans) < 2:
        return "stable"
    counts = [s.total_violations for s in scans]
    if counts[-1] < counts[0]:
        return "improving"
    if counts[-1] > counts[0]:
        return "declining"
    return "stable"


@router.get("/projects/{project_id}")  # type: ignore[untyped-decorator]
async def get_project_detail(project_id: str) -> ProjectDetail:
    """Fetch a project with latest activity info.

    Args:
        project_id: Project UUID.

    Returns:
        Full project detail.

    Raises:
        HTTPException: 404 if project not found.
    """
    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        severity = await q.project_severity_breakdown(db, proj.id)
        trend = await q.project_trend(db, proj.id, limit=5)
        scan_cnt = await q.project_scan_count(db, proj.id)
        scans = await q.project_scans(db, proj.id, limit=1)
        latest = scans[0] if scans else None
        total_v = latest.total_violations if latest else 0
        latest_summary = _to_activity_summary(latest) if latest else None
        vt = _compute_violation_trend(trend)
        last_scan_at = trend[-1].created_at if trend else None
    return ProjectDetail(
        id=proj.id,
        name=proj.name,
        repo_url=proj.repo_url,
        branch=proj.branch,
        created_at=proj.created_at,
        health_score=proj.health_score,
        total_violations=total_v,
        violation_trend=vt,
        scan_count=scan_cnt,
        last_scanned_at=last_scan_at,
        latest_scan=latest_summary,
        severity_breakdown=severity,
    )


@router.patch("/projects/{project_id}")  # type: ignore[untyped-decorator]
async def update_project(
    project_id: str,
    body: UpdateProjectRequest,
) -> ProjectSummary:
    """Update project metadata.

    Args:
        project_id: Project UUID.
        body: Fields to update.

    Returns:
        Updated project summary.

    Raises:
        HTTPException: 400 if no fields provided, 404 if not found.
    """
    updates: dict[str, str] = {}
    if body.name is not None:
        updates["name"] = body.name
    if body.repo_url is not None:
        updates["repo_url"] = body.repo_url
    if body.branch is not None:
        updates["branch"] = body.branch
    if not updates:
        raise HTTPException(status_code=400, detail="No fields to update")
    async with get_session() as db:
        proj = await q.update_project(db, project_id, **updates)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
    return ProjectSummary(
        id=proj.id,
        name=proj.name,
        repo_url=proj.repo_url,
        branch=proj.branch,
        created_at=proj.created_at,
        health_score=proj.health_score,
    )


@router.delete("/projects/{project_id}", status_code=204)  # type: ignore[untyped-decorator]
async def delete_project_endpoint(project_id: str) -> None:
    """Delete a project and cascade to its scan rows.

    Args:
        project_id: Project UUID.

    Raises:
        HTTPException: 404 if not found.
    """
    async with get_session() as db:
        ok = await q.delete_project(db, project_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Project not found")


# ── Project-scoped endpoints (ADR-037) ───────────────────────────────


@router.get("/projects/{project_id}/activity")  # type: ignore[untyped-decorator]
async def list_project_activity(
    project_id: str,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """Return activity (check/remediate runs) for a project.

    Args:
        project_id: Project UUID.
        limit: Page size.
        offset: Page offset.

    Returns:
        Paginated activity summaries.

    Raises:
        HTTPException: 404 if project not found.
    """
    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        scans = await q.project_scans(db, proj.id, limit=limit, offset=offset)
        total = await q.project_scan_count(db, proj.id)
    items = [_to_activity_summary(s) for s in scans]
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/projects/{project_id}/violations")  # type: ignore[untyped-decorator]
async def list_project_violations(
    project_id: str,
    severity: str | None = Query(default=None),
    rule_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ViolationDetail]:
    """Return violations from the latest check run of a project.

    Args:
        project_id: Project UUID.
        severity: Optional severity filter.
        rule_id: Optional rule_id filter.
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of violation details.

    Raises:
        HTTPException: 404 if project not found.
    """
    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        violations = await q.project_violations(
            db,
            proj.id,
            severity=severity,
            rule_id=rule_id,
            limit=limit,
            offset=offset,
        )
    return [
        ViolationDetail(
            id=v.id,
            rule_id=v.rule_id,
            level=v.level,
            message=v.message,
            file=v.file,
            line=v.line,
            path=v.path,
            remediation_class=v.remediation_class,
            scope=v.scope,
            validator_source=v.validator_source,
            snippet=v.snippet,
        )
        for v in violations
    ]


@router.get("/projects/{project_id}/trend")  # type: ignore[untyped-decorator]
async def project_trend_endpoint(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TrendPoint]:
    """Return violation trend for a project over time.

    Args:
        project_id: Project UUID.
        limit: Max number of data points.

    Returns:
        List of trend points ordered newest-first.

    Raises:
        HTTPException: 404 if project not found.
    """
    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        trend = await q.project_trend(db, proj.id, limit=limit)
    return [
        TrendPoint(
            scan_id=t.scan_id,
            created_at=t.created_at,
            total_violations=t.total_violations,
            fixable=t.auto_fixable,
            scan_type=t.scan_type,
        )
        for t in trend
    ]


# ── Project dependencies (ADR-040) ───────────────────────────────────


@router.get("/projects/{project_id}/dependencies")  # type: ignore[untyped-decorator]
async def project_dependencies_endpoint(project_id: str) -> ProjectDependencies:
    """Return the dependency manifest for a project's latest scan.

    Args:
        project_id: Project UUID.

    Returns:
        Collections, Python packages, and ansible-core version.

    Raises:
        HTTPException: 404 if project not found.
    """
    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        manifest, collections, packages = await q.project_dependencies(db, proj.id)

    return ProjectDependencies(
        ansible_core_version=manifest.ansible_core_version if manifest else "",
        collections=[
            CollectionRefSchema(fqcn=c.fqcn, version=c.version, source=c.source, license=c.license, supplier=c.supplier)
            for c in collections
        ],
        python_packages=[
            PythonPackageRefSchema(name=p.name, version=p.version, license=p.license, supplier=p.supplier)
            for p in packages
        ],
        requirements_files=_parse_requirements_files(manifest),
        dependency_tree=manifest.dependency_tree if manifest else "",
    )


def _parse_requirements_files(manifest: ScanManifest | None) -> list[str]:
    """Extract requirements_files from a ScanManifest.

    Args:
        manifest: ScanManifest ORM instance or None.

    Returns:
        List of requirement file paths.
    """
    if manifest is None:
        return []
    import json as _json  # noqa: PLC0415

    try:
        return list(_json.loads(manifest.requirements_files_json))
    except (TypeError, ValueError):
        return []


# ── SBOM endpoint ────────────────────────────────────────────────────


@router.get("/projects/{project_id}/sbom")  # type: ignore[untyped-decorator]
async def project_sbom_endpoint(
    project_id: str,
    format: str = Query(default="cyclonedx"),
) -> JSONResponse:
    """Return an SBOM for a project's latest scan.

    Args:
        project_id: Project UUID or name.
        format: SBOM output format (currently only ``cyclonedx``).

    Returns:
        CycloneDX 1.5 JSON response.

    Raises:
        HTTPException: 400 for unsupported format, 404 if project or scan data not found.
    """
    if format not in ("cyclonedx",):
        raise HTTPException(status_code=400, detail=f"Unsupported SBOM format: {format}")

    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        manifest, collections, packages = await q.project_dependencies(db, proj.id)

    if manifest is None:
        raise HTTPException(status_code=404, detail="No scan data available")

    from apme_gateway.api.sbom import manifest_to_cyclonedx  # noqa: PLC0415

    bom = manifest_to_cyclonedx(manifest, collections, packages, tools_version=_TOOLS_VERSION)
    return JSONResponse(content=bom, media_type="application/vnd.cyclonedx+json")


# ── ContentGraph visualization ───────────────────────────────────────


@router.get("/projects/{project_id}/graph")  # type: ignore[untyped-decorator]
async def project_graph_endpoint(project_id: str) -> JSONResponse:
    """Return the ContentGraph JSON for a project's latest scan.

    The response is the raw ``ContentGraph.to_dict()`` output — nodes,
    edges, and execution_edges — ready for client-side D3/dagre rendering.

    Args:
        project_id: Project UUID or name.

    Returns:
        JSON response with graph data.

    Raises:
        HTTPException: 404 if project not found or no graph data available.
    """
    import json as _json  # noqa: PLC0415

    async with get_session() as db:
        proj = await q.resolve_project(db, project_id)
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        graph = await q.project_graph(db, proj.id)

    if graph is None:
        raise HTTPException(status_code=404, detail="No graph data available")

    try:
        graph_content = _json.loads(graph.graph_json)
    except (_json.JSONDecodeError, TypeError) as exc:
        raise HTTPException(
            status_code=500,
            detail="Stored graph data is invalid JSON",
        ) from exc

    if not isinstance(graph_content, dict):
        raise HTTPException(
            status_code=500,
            detail="Stored graph data is not a JSON object",
        )

    return JSONResponse(content=graph_content)


# ── Collections and packages (ADR-040) ──────────────────────────────


@router.get("/collections")  # type: ignore[untyped-decorator]
async def list_collections(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[CollectionSummary]:
    """Return all collections seen across projects with usage counts.

    Args:
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of collection summaries.
    """
    async with get_session() as db:
        rows = await q.all_collections(db, limit=limit, offset=offset)
    return [
        CollectionSummary(
            fqcn=str(r["fqcn"]),
            version=str(r["version"]),
            source=str(r["source"]),
            project_count=cast(int, r["project_count"]),
        )
        for r in rows
    ]


@router.get("/collections/{fqcn}")  # type: ignore[untyped-decorator]
async def get_collection_detail(fqcn: str) -> CollectionDetail:
    """Return detail for a specific collection.

    Args:
        fqcn: Fully-qualified collection name (e.g. ``community.general``).

    Returns:
        Collection detail with version list and dependent projects.

    Raises:
        HTTPException: 404 if collection not found.
    """
    async with get_session() as db:
        detail = await q.collection_detail(db, fqcn)
    if detail is None:
        raise HTTPException(status_code=404, detail="Collection not found")
    projects_list = cast(list[dict[str, object]], detail.get("projects", []))
    return CollectionDetail(
        fqcn=str(detail["fqcn"]),
        versions=cast(list[str], detail.get("versions", [])),
        source=str(detail.get("source", "unknown")),
        project_count=cast(int, detail.get("project_count", 0)),
        projects=[
            CollectionProjectRef(
                id=str(p["id"]),
                name=str(p["name"]),
                health_score=cast(int, p.get("health_score", 0)),
                collection_version=str(p.get("version", "")),
            )
            for p in projects_list
        ],
    )


@router.get("/collections/{fqcn}/projects")  # type: ignore[untyped-decorator]
async def list_collection_projects(fqcn: str) -> list[CollectionProjectRef]:
    """Return projects that depend on a specific collection.

    Args:
        fqcn: Fully-qualified collection name.

    Returns:
        List of project references with the collection version.
    """
    async with get_session() as db:
        rows = await q.collection_projects(db, fqcn)
    return [
        CollectionProjectRef(
            id=str(r["id"]),
            name=str(r["name"]),
            health_score=cast(int, r.get("health_score", 0)),
            collection_version=str(r.get("collection_version", "")),
        )
        for r in rows
    ]


@router.get("/python-packages")  # type: ignore[untyped-decorator]
async def list_python_packages(
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[PythonPackageSummary]:
    """Return all Python packages seen across projects with usage counts.

    Args:
        limit: Maximum rows.
        offset: Rows to skip.

    Returns:
        List of package summaries.
    """
    async with get_session() as db:
        rows = await q.all_python_packages(db, limit=limit, offset=offset)
    return [
        PythonPackageSummary(
            name=str(r["name"]),
            version=str(r["version"]),
            project_count=cast(int, r["project_count"]),
        )
        for r in rows
    ]


@router.get("/python-packages/{name}")  # type: ignore[untyped-decorator]
async def get_python_package_detail(name: str) -> PythonPackageDetail:
    """Return detail for a specific Python package.

    Args:
        name: PyPI package name.

    Returns:
        Package detail with version list and dependent projects.

    Raises:
        HTTPException: 404 if package not found.
    """
    async with get_session() as db:
        detail = await q.python_package_detail(db, name)
    if detail is None:
        raise HTTPException(status_code=404, detail="Python package not found")
    pkg_projects = cast(list[dict[str, object]], detail.get("projects", []))
    return PythonPackageDetail(
        name=str(detail["name"]),
        versions=cast(list[str], detail.get("versions", [])),
        project_count=cast(int, detail.get("project_count", 0)),
        projects=[
            PythonPackageProjectRef(
                id=str(p["id"]),
                name=str(p["name"]),
                health_score=cast(int, p.get("health_score", 0)),
                package_version=str(p.get("package_version", "")),
            )
            for p in pkg_projects
        ],
    )


# ── Dashboard (ADR-037) ─────────────────────────────────────────────


@router.get("/dashboard/summary")  # type: ignore[untyped-decorator]
async def dashboard_summary_endpoint() -> DashboardSummary:
    """Return cross-project aggregate statistics.

    Returns:
        Dashboard summary with totals and averages.
    """
    async with get_session() as db:
        summary = await q.dashboard_summary(db)
    return DashboardSummary(**summary)


@router.get("/dashboard/rankings")  # type: ignore[untyped-decorator]
async def dashboard_rankings(
    sort_by: str = Query(default="health_score"),
    order: str = Query(default="desc"),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[ProjectRanking]:
    """Return ranked projects for dashboard tables.

    Args:
        sort_by: Ranking criterion.
        order: Sort direction.
        limit: Max results.

    Returns:
        Ranked list of projects.
    """
    async with get_session() as db:
        rankings = await q.project_rankings(db, sort_by=sort_by, order=order, limit=limit)
    return [ProjectRanking(**r) for r in rankings]


@router.get("/sessions")  # type: ignore[untyped-decorator]
async def list_sessions(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List sessions ordered by most recently seen.

    Args:
        limit: Page size.
        offset: Row offset.

    Returns:
        Paginated list of sessions.
    """
    async with get_session() as db:
        total = await q.session_count(db)
        rows = await q.list_sessions(db, limit=limit, offset=offset)
    items = [
        SessionSummary(
            session_id=s.session_id,
            project_path=s.project_path,
            first_seen=s.first_seen,
            last_seen=s.last_seen,
        )
        for s in rows
    ]
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/sessions/{session_id}")  # type: ignore[untyped-decorator]
async def get_session_detail(session_id: str) -> SessionDetail:
    """Fetch a session and its activity rows.

    Args:
        session_id: Deterministic session hash.

    Returns:
        Session with embedded activity list.

    Raises:
        HTTPException: 404 if session not found.
    """
    async with get_session() as db:
        sess = await q.get_session(db, session_id)
    if sess is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionDetail(
        session_id=sess.session_id,
        project_path=sess.project_path,
        first_seen=sess.first_seen,
        last_seen=sess.last_seen,
        scans=[_to_activity_summary(s) for s in sess.scans],
    )


@router.get("/activity")  # type: ignore[untyped-decorator]
async def list_activity(
    session_id: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> PaginatedResponse:
    """List activity, optionally filtered by session.

    Args:
        session_id: Optional session filter.
        limit: Page size.
        offset: Row offset.

    Returns:
        Paginated list of activity summaries.
    """
    async with get_session() as db:
        total = await q.scan_count(db, session_id=session_id)
        rows = await q.list_scans(db, session_id=session_id, limit=limit, offset=offset)
    items = [_to_activity_summary(s) for s in rows]
    return PaginatedResponse(total=total, limit=limit, offset=offset, items=items)


@router.get("/activity/{activity_id}")  # type: ignore[untyped-decorator]
async def get_activity_detail(activity_id: str) -> ActivityDetail:
    """Fetch one activity run with violations, proposals, and logs.

    Args:
        activity_id: UUID of the run (``scans.scan_id``).

    Returns:
        Full activity detail.

    Raises:
        HTTPException: 404 if not found.
    """
    async with get_session() as db:
        scan = await q.get_scan(db, activity_id)
    if scan is None:
        raise HTTPException(status_code=404, detail="Activity not found")
    display_path = scan.project.name if scan.project is not None else scan.project_path
    return ActivityDetail(
        scan_id=scan.scan_id,
        session_id=scan.session_id,
        project_path=display_path,
        source=scan.source,
        created_at=scan.created_at,
        scan_type=scan.scan_type,
        total_violations=scan.total_violations,
        fixable=scan.auto_fixable,
        ai_candidate=scan.ai_candidate,
        ai_proposed=scan.ai_proposed,
        ai_declined=scan.ai_declined,
        ai_accepted=scan.ai_accepted,
        manual_review=scan.manual_review,
        remediated_count=scan.fixed_count if scan.scan_type == "remediate" else 0,
        diagnostics_json=scan.diagnostics_json,
        violations=[
            ViolationDetail(
                id=v.id,
                rule_id=v.rule_id,
                level=v.level,
                message=v.message,
                file=v.file,
                line=v.line,
                path=v.path,
                remediation_class=v.remediation_class,
                scope=v.scope,
                validator_source=v.validator_source,
                snippet=v.snippet,
            )
            for v in scan.violations
        ],
        proposals=[
            ProposalDetail(
                id=p.id,
                proposal_id=p.proposal_id,
                rule_id=p.rule_id,
                file=p.file,
                tier=p.tier,
                confidence=p.confidence,
                status=p.status,
            )
            for p in scan.proposals
        ],
        logs=[
            LogEntry(
                id=lg.id,
                message=lg.message,
                phase=lg.phase,
                progress=lg.progress,
                level=lg.level,
            )
            for lg in scan.logs
        ],
        patches=[PatchDetail(id=pt.id, file=pt.file, diff=pt.diff) for pt in scan.patches],
    )


@router.delete("/activity/{activity_id}", status_code=204)  # type: ignore[untyped-decorator]
async def delete_activity(activity_id: str) -> None:
    """Delete an activity row and its related data.

    Args:
        activity_id: UUID of the run to delete (``scans.scan_id``).

    Raises:
        HTTPException: 404 if not found.
    """
    async with get_session() as db:
        deleted = await q.delete_scan(db, activity_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Activity not found")


@router.get("/violations/top")  # type: ignore[untyped-decorator]
async def top_violations(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[TopViolation]:
    """Return the most frequently violated rules across all activity.

    Args:
        limit: Maximum number of rules to return.

    Returns:
        List of rules sorted by violation count descending.
    """
    async with get_session() as db:
        rows = await q.top_violations(db, limit=limit)
    return [TopViolation(rule_id=rule_id, count=count) for rule_id, count in rows]


@router.get("/sessions/{session_id}/trend")  # type: ignore[untyped-decorator]
async def session_trend_endpoint(session_id: str) -> list[TrendPoint]:
    """Return violation trend for a session over time.

    Args:
        session_id: Deterministic session hash.

    Returns:
        List of trend data points.

    Raises:
        HTTPException: 404 if session not found.
    """
    async with get_session() as db:
        sess = await q.get_session(db, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail="Session not found")
        scans = await q.session_trend(db, session_id)
    return [
        TrendPoint(
            scan_id=s.scan_id,
            created_at=s.created_at,
            total_violations=s.total_violations,
            fixable=s.auto_fixable,
            scan_type=s.scan_type,
        )
        for s in scans
    ]


@router.get("/stats/remediation-rates")  # type: ignore[untyped-decorator]
async def remediation_rates_endpoint(
    limit: int = Query(default=20, ge=1, le=100),
) -> list[RemediationRateEntry]:
    """Return most frequently violated rules in remediate runs.

    Args:
        limit: Maximum number of rules to return.

    Returns:
        List of rules sorted by remediation count descending.
    """
    async with get_session() as db:
        rows = await q.remediation_rates(db, limit=limit)
    return [RemediationRateEntry(rule_id=rule_id, fix_count=count) for rule_id, count in rows]


@router.get("/stats/ai-acceptance")  # type: ignore[untyped-decorator]
async def ai_acceptance_endpoint() -> list[AiAcceptanceEntry]:
    """Return per-rule AI proposal acceptance statistics.

    Returns:
        List of rules with approval/rejection counts and confidence.
    """
    async with get_session() as db:
        rows = await q.ai_acceptance(db)
    return [
        AiAcceptanceEntry(
            rule_id=rule_id,
            approved=approved,
            rejected=rejected,
            pending=pending,
            avg_confidence=round(avg_conf, 3),
        )
        for rule_id, approved, rejected, pending, avg_conf in rows
    ]


def _to_activity_summary(scan: Scan) -> ActivitySummary:
    """Convert an ORM Scan row to an ActivitySummary response model.

    Args:
        scan: ORM Scan instance.

    Returns:
        Pydantic ActivitySummary.
    """
    display_path = scan.project_path
    if scan.project is not None:
        display_path = scan.project.name
    return ActivitySummary(
        scan_id=scan.scan_id,
        session_id=scan.session_id,
        project_path=display_path,
        source=scan.source,
        created_at=scan.created_at,
        scan_type=scan.scan_type,
        total_violations=scan.total_violations,
        fixable=scan.auto_fixable,
        ai_candidate=scan.ai_candidate,
        ai_proposed=scan.ai_proposed,
        ai_declined=scan.ai_declined,
        ai_accepted=scan.ai_accepted,
        manual_review=scan.manual_review,
        remediated_count=scan.fixed_count if scan.scan_type == "remediate" else 0,
    )


# ── Rule catalog (ADR-041) ───────────────────────────────────────────


class RuleOverrideOut(BaseModel):  # type: ignore[misc]
    """Serialized rule override for REST responses.

    Attributes:
        severity_override: Overridden severity enum int, or None if not overridden.
        enabled_override: Overridden enabled flag, or None if not overridden.
        enforced: When True, inline apme:ignore is bypassed.
        updated_at: ISO 8601 timestamp of the last override change.
    """

    severity_override: int | None
    enabled_override: bool | None
    enforced: bool
    updated_at: str


class RuleListItem(BaseModel):  # type: ignore[misc]
    """One rule with default and resolved severity / enabled state.

    Attributes:
        rule_id: Rule identifier.
        category: Rule category (lint, modernize, risk, policy, secrets).
        source: Validator source name.
        description: Human-readable description.
        scope: RuleScope enum value from registration.
        default_severity: Catalog default severity (proto enum int).
        default_severity_label: Label for default_severity.
        resolved_severity: Effective severity after override.
        resolved_severity_label: Label for resolved_severity.
        enabled: Catalog default enabled flag.
        resolved_enabled: Effective enabled state after override.
        registered_at: ISO 8601 registration timestamp.
        override: Active override row, if any.
    """

    rule_id: str
    category: str
    source: str
    description: str
    scope: int
    default_severity: int
    default_severity_label: str
    resolved_severity: int
    resolved_severity_label: str
    enabled: bool
    resolved_enabled: bool
    registered_at: str
    override: RuleOverrideOut | None


class RuleDetailOut(BaseModel):  # type: ignore[misc]
    """Single rule with full override information.

    Attributes:
        rule_id: Rule identifier.
        category: Rule category (lint, modernize, risk, policy, secrets).
        source: Validator source name.
        description: Human-readable description.
        scope: RuleScope enum value from registration.
        default_severity: Catalog default severity (proto enum int).
        default_severity_label: Label for default_severity.
        resolved_severity: Effective severity after override.
        resolved_severity_label: Label for resolved_severity.
        enabled: Catalog default enabled flag.
        resolved_enabled: Effective enabled state after override.
        registered_at: ISO 8601 registration timestamp.
        override: Active override row, if any.
    """

    rule_id: str
    category: str
    source: str
    description: str
    scope: int
    default_severity: int
    default_severity_label: str
    resolved_severity: int
    resolved_severity_label: str
    enabled: bool
    resolved_enabled: bool
    registered_at: str
    override: RuleOverrideOut | None


class RuleStatsOut(BaseModel):  # type: ignore[misc]
    """Aggregate statistics for the registered rule catalog.

    Attributes:
        total: Number of registered rules.
        by_category: Counts keyed by category.
        by_source: Counts keyed by validator source.
        override_count: Number of rules that have at least one override row.
    """

    total: int
    by_category: dict[str, int]
    by_source: dict[str, int]
    override_count: int


class RuleConfigPutBody(BaseModel):  # type: ignore[misc]
    """Payload for creating or partially updating a rule override.

    Omitted fields are left unchanged on an existing override.

    Attributes:
        severity_override: Severity enum int (0-6), or None to clear severity override.
        enabled_override: New enabled flag, or None to clear enabled override.
        enforced: Whether to enforce despite inline ignores; omit to leave unchanged.
    """

    severity_override: int | None = Field(default=None, ge=0, le=6)
    enabled_override: bool | None = None
    enforced: bool | None = None


def _primary_override(rule: Rule) -> RuleOverride | None:
    """Return the single override row for a rule, if any.

    Args:
        rule: Rule ORM instance with overrides eagerly loaded.

    Returns:
        First override or None.
    """
    return rule.overrides[0] if rule.overrides else None


def _severity_label_from_int(value: int) -> str:
    """Map stored proto severity int to API label.

    Args:
        value: Proto Severity enum as int.

    Returns:
        Lowercase severity label string.
    """
    return severity_to_label(severity_from_proto(value))


def _resolved_severity(rule: Rule) -> int:
    """Effective severity (override or catalog default).

    Args:
        rule: Rule ORM instance.

    Returns:
        Resolved severity as proto enum int.
    """
    ov = _primary_override(rule)
    if ov is not None and ov.severity_override is not None:
        return cast(int, ov.severity_override)
    return cast(int, rule.default_severity)


def _resolved_enabled(rule: Rule) -> bool:
    """Effective enabled flag (override or catalog default).

    Args:
        rule: Rule ORM instance.

    Returns:
        True if the rule is enabled after overrides.
    """
    ov = _primary_override(rule)
    if ov is not None and ov.enabled_override is not None:
        return cast(bool, ov.enabled_override)
    return cast(bool, rule.enabled)


def _override_out(ov: RuleOverride | None) -> RuleOverrideOut | None:
    """Build API override payload from ORM row.

    Args:
        ov: Override row or None.

    Returns:
        Serialized override or None.
    """
    if ov is None:
        return None
    return RuleOverrideOut(
        severity_override=ov.severity_override,
        enabled_override=ov.enabled_override,
        enforced=ov.enforced,
        updated_at=ov.updated_at,
    )


def _to_rule_list_item(rule: Rule) -> RuleListItem:
    rs = _resolved_severity(rule)
    return RuleListItem(
        rule_id=rule.rule_id,
        category=rule.category,
        source=rule.source,
        description=rule.description,
        scope=rule.scope,
        default_severity=rule.default_severity,
        default_severity_label=_severity_label_from_int(rule.default_severity),
        resolved_severity=rs,
        resolved_severity_label=_severity_label_from_int(rs),
        enabled=rule.enabled,
        resolved_enabled=_resolved_enabled(rule),
        registered_at=rule.registered_at,
        override=_override_out(_primary_override(rule)),
    )


def _to_rule_detail(rule: Rule) -> RuleDetailOut:
    rs = _resolved_severity(rule)
    return RuleDetailOut(
        rule_id=rule.rule_id,
        category=rule.category,
        source=rule.source,
        description=rule.description,
        scope=rule.scope,
        default_severity=rule.default_severity,
        default_severity_label=_severity_label_from_int(rule.default_severity),
        resolved_severity=rs,
        resolved_severity_label=_severity_label_from_int(rs),
        enabled=rule.enabled,
        resolved_enabled=_resolved_enabled(rule),
        registered_at=rule.registered_at,
        override=_override_out(_primary_override(rule)),
    )


@router.get("/rules")  # type: ignore[untyped-decorator]
async def list_rules_endpoint(
    category: str | None = Query(default=None),
    source: str | None = Query(default=None),
    enabled_only: bool = Query(default=False),
) -> list[RuleListItem]:
    """List registered rules with optional filters and resolved config.

    Args:
        category: Filter by rule category.
        source: Filter by validator source.
        enabled_only: If true, only rules enabled by catalog default.

    Returns:
        Rules with default and effective severity labels and override metadata.
    """
    async with get_session() as db:
        rules = await q.list_rules(db, category=category, source=source, enabled_only=enabled_only)
    return [_to_rule_list_item(r) for r in rules]


@router.get("/rules/stats")  # type: ignore[untyped-decorator]
async def rule_stats_endpoint() -> RuleStatsOut:
    """Return summary statistics for the rule catalog.

    Returns:
        Totals grouped by category and source, plus override count.
    """
    async with get_session() as db:
        stats = await q.get_rule_stats(db)
    return RuleStatsOut(
        total=cast(int, stats["total"]),
        by_category=cast(dict[str, int], stats["by_category"]),
        by_source=cast(dict[str, int], stats["by_source"]),
        override_count=cast(int, stats["override_count"]),
    )


@router.get("/rules/{rule_id}")  # type: ignore[untyped-decorator]
async def get_rule_endpoint(rule_id: str) -> RuleDetailOut:
    """Return one rule with override details.

    Args:
        rule_id: Rule identifier (e.g. ``L026``).

    Returns:
        Full rule detail.

    Raises:
        HTTPException: 404 if the rule is not registered.
    """
    async with get_session() as db:
        rule = await q.get_rule(db, rule_id)
    if rule is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _to_rule_detail(rule)


@router.put("/rules/{rule_id}/config")  # type: ignore[untyped-decorator]
async def put_rule_config(rule_id: str, body: RuleConfigPutBody) -> RuleDetailOut:
    """Create or update overrides for a rule (partial merge per field).

    Args:
        rule_id: Rule identifier.
        body: Fields to set; omitted keys leave existing override values unchanged.

    Returns:
        Updated rule detail.

    Raises:
        HTTPException: 400 if the body is empty, 404 if the rule does not exist.
    """
    if not body.model_fields_set:
        raise HTTPException(status_code=400, detail="No fields to update")
    async with get_session() as db:
        rule = await q.get_rule(db, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        existing_ov = await q.get_rule_override(db, rule_id)
        sev = (
            body.severity_override
            if "severity_override" in body.model_fields_set
            else (existing_ov.severity_override if existing_ov is not None else None)
        )
        en = (
            body.enabled_override
            if "enabled_override" in body.model_fields_set
            else (existing_ov.enabled_override if existing_ov is not None else None)
        )
        if "enforced" in body.model_fields_set:
            enf = False if body.enforced is None else body.enforced
        else:
            enf = existing_ov.enforced if existing_ov is not None else False
        await q.upsert_rule_override(
            db,
            rule_id,
            severity_override=sev,
            enabled_override=en,
            enforced=enf,
        )
        updated = await q.get_rule(db, rule_id)
        if updated is not None:
            await db.refresh(updated, attribute_names=["overrides"])
    if updated is None:
        raise HTTPException(status_code=404, detail="Rule not found")
    return _to_rule_detail(updated)


@router.delete("/rules/{rule_id}/config", status_code=204)  # type: ignore[untyped-decorator]
async def delete_rule_config(rule_id: str) -> None:
    """Remove overrides for a rule (revert to catalog defaults).

    Args:
        rule_id: Rule identifier.

    Raises:
        HTTPException: 404 if the rule does not exist or has no override.
    """
    async with get_session() as db:
        rule = await q.get_rule(db, rule_id)
        if rule is None:
            raise HTTPException(status_code=404, detail="Rule not found")
        ok = await q.delete_rule_override(db, rule_id)
    if not ok:
        raise HTTPException(status_code=404, detail="No override configured for this rule")


# ── Project WebSocket (ADR-037) ──────────────────────────────────────


@router.websocket("/projects/{project_id}/ws/operate")  # type: ignore[untyped-decorator]
async def project_operate_ws(
    websocket: WebSocket,
    project_id: str,
) -> None:
    """Bidirectional WebSocket for project check/remediate operations (ADR-037, ADR-039).

    The client sends ``{"action": "check"|"remediate", "options": {...}}`` (or
    ``{"remediate": true}``) to start an operation.  The gateway clones the repo,
    drives Primary ``FixSession`` via gRPC, and streams progress back over the WebSocket.

    Args:
        websocket: Incoming WebSocket connection.
        project_id: Target project UUID.
    """
    from apme_gateway._galaxy_inject import load_galaxy_server_defs
    from apme_gateway.config import load_config
    from apme_gateway.scan.driver import run_project_operation

    await websocket.accept()

    try:
        msg = await websocket.receive_json()
        is_remediate = bool(msg.get("remediate", False)) or msg.get("action") == "remediate"
        options: dict[str, object] = msg.get("options", {})

        async with get_session() as db:
            proj = await q.resolve_project(db, project_id)
        if not proj:
            await websocket.send_json({"type": "error", "message": "Project not found"})
            return

        cfg = load_config()
        galaxy_servers = await load_galaxy_server_defs()

        op_scan_id = uuid.uuid4().hex
        started_sent = False
        completed_scan_id: str | None = None
        captured_patches: list[dict[str, str]] = []
        ai_proposed_count = 0
        ai_declined_count = 0
        ai_accepted_count = 0

        async def _progress_cb(event: object) -> None:
            """Translate FixSession ``SessionEvent`` protobufs into WebSocket messages.

            Args:
                event: gRPC SessionEvent protobuf.
            """
            nonlocal started_sent, ai_proposed_count, ai_declined_count, ai_accepted_count

            kind = None
            with contextlib.suppress(Exception):
                kind = event.WhichOneof("event")  # type: ignore[attr-defined]

            async def _ensure_started() -> None:
                nonlocal started_sent
                if not started_sent:
                    started_sent = True
                    await websocket.send_json({"type": "started", "scan_id": op_scan_id})

            if kind == "progress":
                await _ensure_started()
                prog = event.progress  # type: ignore[attr-defined]
                await websocket.send_json(
                    {
                        "type": "progress",
                        "phase": prog.phase or "processing",
                        "message": prog.message or "",
                        "progress": prog.progress,
                        "level": prog.level,
                    }
                )
            elif kind == "proposals":
                await _ensure_started()
                props = event.proposals  # type: ignore[attr-defined]
                items = [
                    {
                        "id": p.id,
                        "rule_id": p.rule_id,
                        "file": p.file,
                        "tier": p.tier,
                        "confidence": p.confidence,
                        "explanation": p.explanation,
                        "diff_hunk": p.diff_hunk,
                        "status": p.status or "proposed",
                        "suggestion": p.suggestion,
                        "line_start": p.line_start,
                    }
                    for p in props.proposals
                ]
                ai_proposed_count = sum(1 for i in items if i.get("status") != "declined")
                ai_declined_count = sum(1 for i in items if i.get("status") == "declined")
                await websocket.send_json({"type": "proposals", "proposals": items})
            elif kind == "approval_ack":
                ack = event.approval_ack  # type: ignore[attr-defined]
                ai_accepted_count = getattr(ack, "applied_count", 0)
                await websocket.send_json(
                    {
                        "type": "approval_ack",
                        "applied_count": ai_accepted_count,
                    }
                )
            elif kind == "result":
                await _ensure_started()
                res = event.result  # type: ignore[attr-defined]
                report = getattr(res, "report", None)
                remaining = getattr(res, "remaining_violations", [])
                fixed_viols = getattr(res, "fixed_violations", [])
                fixed = report.fixed if report else 0
                total = len(remaining) + fixed

                def _extract_line(v: object) -> int | None:
                    if v.HasField("line"):  # type: ignore[attr-defined]
                        return v.line  # type: ignore[attr-defined, no-any-return]
                    if v.HasField("line_range"):  # type: ignore[attr-defined]
                        return v.line_range.start  # type: ignore[attr-defined, no-any-return]
                    return None

                fixed_violations_json = [
                    {
                        "rule_id": v.rule_id,
                        "severity": severity_to_label(severity_from_proto(v.severity)),
                        "message": v.message,
                        "file": v.file,
                        "line": _extract_line(v),
                        "path": v.path,
                    }
                    for v in fixed_viols
                ]

                result_patches = getattr(res, "patches", [])
                patches_json = [{"file": p.path, "diff": p.diff} for p in result_patches if p.diff]
                captured_patches.extend(patches_json)

                remediated = (fixed + ai_accepted_count) if is_remediate else 0
                await websocket.send_json(
                    {
                        "type": "result",
                        "total_violations": total,
                        "fixable": fixed,
                        "ai_proposed": ai_proposed_count,
                        "ai_declined": ai_declined_count,
                        "ai_accepted": ai_accepted_count,
                        "manual_review": report.remaining_manual if report else 0,
                        "remediated_count": remediated,
                        "fixed_violations": fixed_violations_json,
                        "patches": patches_json,
                    }
                )

        raw_specs = options.get("collection_specs", [])
        specs = [str(s) for s in raw_specs] if isinstance(raw_specs, list) else []

        await websocket.send_json({"type": "cloning"})

        if is_remediate:
            approval_queue: asyncio.Queue[list[str]] = asyncio.Queue()
            op_result: tuple[str, object] | None = None

            async def _run_op() -> tuple[str, object]:
                return await run_project_operation(
                    project_id=proj.id,
                    repo_url=proj.repo_url,
                    branch=proj.branch,
                    primary_address=cfg.primary_address,
                    remediate=True,
                    ansible_version=str(options.get("ansible_version", "")),
                    collection_specs=specs,
                    enable_ai=bool(options.get("enable_ai", True)),
                    ai_model=str(options.get("ai_model", "")),
                    progress_callback=_progress_cb,
                    approval_queue=approval_queue,
                    scan_id=op_scan_id,
                    galaxy_servers=galaxy_servers or None,
                )

            op_task = asyncio.create_task(_run_op())
            try:
                while not op_task.done():
                    try:
                        client_msg = await asyncio.wait_for(
                            websocket.receive_json(),
                            timeout=1.0,
                        )
                    except TimeoutError:
                        continue

                    msg_type = client_msg.get("type", "")
                    if msg_type == "approve":
                        ids = client_msg.get("approved_ids", [])
                        approved = [str(i) for i in ids] if isinstance(ids, list) else []
                        await approval_queue.put(approved)
                    elif msg_type == "cancel":
                        op_task.cancel()
                        break
            finally:
                if not op_task.done():
                    op_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    op_result = await op_task

            if op_result is not None:
                completed_scan_id = op_result[0]
        else:
            scan_id, _result = await run_project_operation(
                project_id=proj.id,
                repo_url=proj.repo_url,
                branch=proj.branch,
                primary_address=cfg.primary_address,
                remediate=False,
                ansible_version=str(options.get("ansible_version", "")),
                collection_specs=specs,
                progress_callback=_progress_cb,
                scan_id=op_scan_id,
                galaxy_servers=galaxy_servers or None,
            )
            completed_scan_id = scan_id

        if completed_scan_id:
            op_scan_type = "remediate" if is_remediate else "check"
            async with get_session() as db:
                await q.link_scan_to_project(
                    db,
                    completed_scan_id,
                    proj.id,
                    trigger="ui",
                    scan_type=op_scan_type,
                )
                await q.update_ai_counts(
                    db,
                    completed_scan_id,
                    ai_proposed=ai_proposed_count,
                    ai_declined=ai_declined_count,
                    ai_accepted=ai_accepted_count,
                )
                if captured_patches:
                    await q.store_patches(db, completed_scan_id, captured_patches)
                await q.update_project_health(db, proj.id)

        await websocket.send_json({"type": "closed"})
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected for project %s", project_id)
    except Exception as exc:
        logger.exception("Error during project operation for %s", project_id)
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "error", "message": f"Operation failed ({type(exc).__name__})"})
    finally:
        with contextlib.suppress(Exception):
            await websocket.close()


# ── Playground WebSocket (ADR-028 / ADR-029) ─────────────────────────


@router.websocket("/ws/session")  # type: ignore[untyped-decorator]
async def session_ws(
    websocket: WebSocket,
    resume: str | None = None,
    scan_id: str | None = None,
) -> None:
    """Bidirectional WebSocket bridge to Primary's FixSession gRPC stream.

    Handles the full check + remediate lifecycle: file upload, real-time progress,
    Tier 1 auto-fix results, AI proposal delivery, interactive approval,
    and final session results — all over a single connection.

    Pass ``?resume=<session_id>&scan_id=<scan_id>`` to reconnect to an
    existing session (e.g. after a dropped WebSocket during proposal review).

    Args:
        websocket: Incoming WebSocket connection.
        resume: Optional session ID to resume instead of starting fresh.
        scan_id: Original scan_id to preserve on reconnect.
    """
    from apme_gateway.config import load_config
    from apme_gateway.session_client import handle_session

    await websocket.accept()

    cfg = load_config()
    await handle_session(
        websocket,
        cfg.primary_address,
        resume_session_id=resume,
        resume_scan_id=scan_id if resume else None,
    )
