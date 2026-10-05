"""Third-party Plugin discovery and gRPC client helpers (ADR-042).

Plugins are optional sidecars discovered via ``APME_PLUGIN_<NAME>_ADDRESS``.
They are never required for Engine Health. A configured plugin whose
Validate RPC fails emits ``EXT-<name>-unavailable`` so ``apme check``
is not a silent pass.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final

import grpc
import grpc.aio
import yaml

from apme.v1 import plugin_pb2_grpc
from apme.v1.common_pb2 import File, HealthRequest, HealthResponse, ServiceHealth
from apme.v1.plugin_pb2 import (
    DescribeRequest,
    DescribeResponse,
    TransformRequest,
    TransformResponse,
)
from apme.v1.validate_pb2 import ValidateRequest
from apme_engine.daemon.violation_convert import (
    violation_dict_to_proto,
    violation_proto_to_dict,
)
from apme_engine.engine.models import ViolationDict

logger = logging.getLogger("apme.engine.plugins")

PLUGIN_ENV_PATTERN: Final[re.Pattern[str]] = re.compile(r"^APME_PLUGIN_([A-Z0-9]+)_ADDRESS$")
EXT_PREFIX: Final[str] = "EXT-"
_GRPC_MAX_MSG: Final[int] = 50 * 1024 * 1024
_DESCRIBE_TIMEOUT: Final[float] = 5.0
_HEALTH_TIMEOUT: Final[float] = 5.0
_VALIDATE_TIMEOUT: Final[float] = 300.0
_TRANSFORM_TIMEOUT: Final[float] = 60.0
PLUGIN_TRANSFORM_TRANSPORT: Final[str] = "__apme_plugin_transport__"


@dataclass(frozen=True)
class DiscoveredPlugin:
    """One plugin sidecar discovered from the environment.

    Attributes:
        name: Lowercase plugin name from the env var token.
        address: ``host:port`` gRPC address.
        rule_id_prefix: Prefix violations must use (e.g. ``EXT-orgpolicy-``).
        transform_rule_ids: Rule IDs this plugin can Transform.
        version: Plugin-reported version (may be empty).
    """

    name: str
    address: str
    rule_id_prefix: str
    transform_rule_ids: frozenset[str] = field(default_factory=frozenset)
    version: str = ""


def discover_plugin_addresses(environ: Mapping[str, str] | None = None) -> list[tuple[str, str]]:
    """Return ``(name, address)`` pairs from ``APME_PLUGIN_*_ADDRESS`` env vars.

    Args:
        environ: Mapping to scan; defaults to ``os.environ``.

    Returns:
        Sorted list of ``(lowercase_name, address)`` for non-empty addresses.
    """
    env = os.environ if environ is None else environ
    found: list[tuple[str, str]] = []
    for key, raw in env.items():
        match = PLUGIN_ENV_PATTERN.match(key)
        if match is None:
            continue
        address = raw.strip()
        if not address:
            continue
        found.append((match.group(1).lower(), address))
    found.sort()
    return found


def inferred_rule_id_prefix(name: str) -> str:
    """Build the default ``EXT-<name>-`` prefix.

    Args:
        name: Plugin name (already lowercased).

    Returns:
        Prefix string ending with ``-``.
    """
    return f"{EXT_PREFIX}{name}-"


def normalize_rule_id_prefix(name: str, reported: str) -> str:
    """Normalize a Describe ``rule_id_prefix`` to ``EXT-<name>-``.

    Args:
        name: Plugin name used when the reported prefix is empty.
        reported: Value from ``DescribeResponse.rule_id_prefix``.

    Returns:
        Prefix starting with ``EXT-`` and ending with ``-``.
    """
    prefix = (reported or "").strip() or inferred_rule_id_prefix(name)
    if not prefix.startswith(EXT_PREFIX):
        prefix = f"{EXT_PREFIX}{prefix.lstrip('-')}"
    if not prefix.endswith("-"):
        prefix = f"{prefix}-"
    return prefix


def filter_plugin_violations(
    plugin: DiscoveredPlugin,
    violations: Sequence[ViolationDict],
) -> list[ViolationDict]:
    """Keep violations whose rule IDs match this plugin's EXT- prefix.

    Non-matching IDs are logged and dropped (ADR-042 prefix enforcement).

    Args:
        plugin: Source plugin (for prefix and ``source`` attribution).
        violations: Raw violations from ``Validate``.

    Returns:
        Filtered copies with ``source`` set to ``plugin:<name>``.
    """
    kept: list[ViolationDict] = []
    prefix = plugin.rule_id_prefix
    for raw in violations:
        rule_id = str(raw.get("rule_id") or "")
        if not rule_id.startswith(EXT_PREFIX) or (prefix and not rule_id.startswith(prefix)):
            logger.warning(
                "Dropping plugin %s violation with invalid rule_id %r (prefix %s)",
                plugin.name,
                rule_id,
                prefix,
            )
            continue
        if rule_id.rsplit("-", 1)[-1] == "unavailable":
            logger.warning(
                "Dropping plugin %s reserved rule_id %r",
                plugin.name,
                rule_id,
            )
            continue
        item = dict(raw)
        item["source"] = f"plugin:{plugin.name}"
        kept.append(item)
    return kept


def plugin_for_rule(plugins: Sequence[DiscoveredPlugin], rule_id: str) -> DiscoveredPlugin | None:
    """Return the plugin that owns ``rule_id``, longest prefix first.

    Args:
        plugins: Discovered plugins.
        rule_id: Violation rule ID.

    Returns:
        Matching plugin, or ``None``.
    """
    matches = [p for p in plugins if rule_id.startswith(p.rule_id_prefix)]
    if not matches:
        return None
    matches.sort(key=lambda p: len(p.rule_id_prefix), reverse=True)
    return matches[0]


def transform_rule_ids(plugins: Sequence[DiscoveredPlugin]) -> frozenset[str]:
    """Union of all plugin-declared Transform rule IDs.

    Args:
        plugins: Discovered plugins.

    Returns:
        Frozen set of rule IDs that should be Tier 1 via plugin Transform.
    """
    ids: set[str] = set()
    for plugin in plugins:
        ids.update(plugin.transform_rule_ids)
    return frozenset(ids)


def yaml_is_well_formed(text: str) -> bool:
    """Return True when ``text`` is a single non-null YAML document.

    Args:
        text: YAML document or node fragment.

    Returns:
        False when empty, multi-document, null, not a mapping, or parse fails.
    """
    if not text.strip():
        return False
    try:
        docs = list(yaml.safe_load_all(text))
    except yaml.YAMLError:
        return False
    if len(docs) != 1 or docs[0] is None:
        return False
    data = docs[0]
    if isinstance(data, dict):
        return True
    return isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict)


def _channel(address: str) -> grpc.aio.Channel:
    """Open an insecure aio channel with large message limits.

    Args:
        address: ``host:port``.

    Returns:
        Channel (caller must close).
    """
    return grpc.aio.insecure_channel(
        address,
        options=[
            ("grpc.max_send_message_length", _GRPC_MAX_MSG),
            ("grpc.max_receive_message_length", _GRPC_MAX_MSG),
        ],
    )


async def describe_plugin(name: str, address: str) -> DiscoveredPlugin:
    """Call ``Describe``; fall back to inferred prefix on UNIMPLEMENTED.

    Args:
        name: Plugin name from the env var.
        address: gRPC address.

    Returns:
        Populated ``DiscoveredPlugin``.

    Raises:
        grpc.RpcError: On transport errors other than UNIMPLEMENTED.
    """
    default_prefix = inferred_rule_id_prefix(name)
    channel = _channel(address)
    stub = plugin_pb2_grpc.PluginStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp: DescribeResponse = await stub.Describe(DescribeRequest(), timeout=_DESCRIBE_TIMEOUT)
    except grpc.RpcError as exc:
        code = exc.code() if callable(getattr(exc, "code", None)) else None
        if code == grpc.StatusCode.UNIMPLEMENTED:
            logger.info("Plugin %s Describe UNIMPLEMENTED; using prefix %s", name, default_prefix)
            return DiscoveredPlugin(name=name, address=address, rule_id_prefix=default_prefix)
        raise
    finally:
        await channel.close(grace=None)

    # Identity is the env-var token, never Describe.name (a plugin must not
    # impersonate a built-in validator name such as ``opa`` / ``native``).
    prefix = inferred_rule_id_prefix(name)
    reported_prefix = normalize_rule_id_prefix(name, resp.rule_id_prefix)
    if not reported_prefix.startswith(prefix):
        logger.warning(
            "Plugin %s reported prefix %s; using env prefix %s",
            name,
            reported_prefix,
            prefix,
        )
        rule_prefix = prefix
    else:
        rule_prefix = reported_prefix
    allowed: set[str] = set()
    for rid in resp.transform_rule_ids:
        token = (rid or "").strip()
        if not token:
            continue
        if token.rsplit("-", 1)[-1] == "unavailable":
            logger.warning(
                "Plugin %s declared reserved transform_rule_id %s",
                name,
                token,
            )
            continue
        if token.startswith(rule_prefix):
            allowed.add(token)
            continue
        if token.startswith(EXT_PREFIX):
            logger.warning(
                "Plugin %s declared transform_rule_id %s outside prefix %s",
                name,
                token,
                rule_prefix,
            )
            continue
        allowed.add(f"{rule_prefix}{token.lstrip('-')}")
    return DiscoveredPlugin(
        name=name,
        address=address,
        rule_id_prefix=rule_prefix,
        transform_rule_ids=frozenset(allowed),
        version=resp.version or "",
    )


async def load_plugins(environ: Mapping[str, str] | None = None) -> list[DiscoveredPlugin]:
    """Discover addresses and Describe each plugin; skip failures.

    Args:
        environ: Optional env mapping (tests).

    Returns:
        Successfully described plugins.
    """
    loaded: list[DiscoveredPlugin] = []
    for name, address in discover_plugin_addresses(environ):
        try:
            loaded.append(await describe_plugin(name, address))
        except Exception:  # noqa: BLE001 - plugins are optional
            logger.warning("Plugin %s at %s Describe failed; skipping", name, address, exc_info=True)
    return loaded


async def call_plugin_validate(
    address: str,
    request: ValidateRequest,
    timeout: float = _VALIDATE_TIMEOUT,
) -> tuple[list[ViolationDict], str | None]:
    """Call Plugin.Validate.

    Args:
        address: Plugin gRPC address.
        request: Validate request (Engine must leave ``scandata`` empty).
        timeout: RPC timeout in seconds.

    Returns:
        Tuple of (violations, error_string_or_None).
    """
    channel = _channel(address)
    stub = plugin_pb2_grpc.PluginStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp = await stub.Validate(request, timeout=timeout)
        return [violation_proto_to_dict(v) for v in resp.violations], None
    except grpc.RpcError:
        logger.error("Plugin Validate at %s failed (req=%s)", address, request.request_id)
        return [], "rpc error"
    except Exception:  # noqa: BLE001 - plugins are optional
        logger.error(
            "Plugin Validate at %s failed (req=%s)",
            address,
            request.request_id,
            exc_info=True,
        )
        return [], "validate error"
    finally:
        await channel.close(grace=None)


async def call_plugin_transform(
    address: str,
    *,
    request_id: str,
    file_path: str,
    yaml_content: str,
    violation: ViolationDict,
    timeout: float = _TRANSFORM_TIMEOUT,
    hierarchy_payload: bytes = b"",
) -> tuple[bool, str | None, str]:
    """Call Plugin.Transform for one node YAML fragment.

    Args:
        address: Plugin gRPC address.
        request_id: Correlation ID.
        file_path: Playbook path (metadata only).
        yaml_content: Current node YAML.
        violation: Finding to fix.
        timeout: RPC timeout in seconds.
        hierarchy_payload: Current hierarchy JSON (ADR-042 Transform context).

    Returns:
        Tuple of ``(applied, new_yaml_or_None, error)``.
    """
    req = TransformRequest(
        request_id=request_id,
        file=File(path=file_path, content=yaml_content.encode("utf-8")),
        violation=violation_dict_to_proto(violation),
        hierarchy_payload=hierarchy_payload,
    )
    channel = _channel(address)
    stub = plugin_pb2_grpc.PluginStub(channel)  # type: ignore[no-untyped-call]
    resp: TransformResponse | None = None
    try:
        resp = await stub.Transform(req, timeout=timeout)
    except grpc.RpcError:
        logger.error("Plugin Transform at %s failed (req=%s)", address, request_id)
        return False, None, PLUGIN_TRANSFORM_TRANSPORT
    except Exception:  # noqa: BLE001 - plugins are optional
        logger.error("Plugin Transform at %s failed (req=%s)", address, request_id, exc_info=True)
        return False, None, PLUGIN_TRANSFORM_TRANSPORT
    finally:
        await channel.close(grace=None)

    if resp is None:
        return False, None, PLUGIN_TRANSFORM_TRANSPORT
    if resp.error:
        err = resp.error
        if err == PLUGIN_TRANSFORM_TRANSPORT:
            err = "plugin error"
        return False, None, err
    if not resp.applied:
        return False, None, ""
    new_text = resp.file.content.decode("utf-8", errors="replace")
    return True, new_text, ""


async def probe_plugin_health(address: str) -> str:
    """Return plugin Health status string (``ok`` or error text).

    Args:
        address: Plugin gRPC address.

    Returns:
        Status suitable for ``ServiceHealth.status``.
    """
    channel = _channel(address)
    stub = plugin_pb2_grpc.PluginStub(channel)  # type: ignore[no-untyped-call]
    try:
        resp: HealthResponse = await stub.Health(HealthRequest(), timeout=_HEALTH_TIMEOUT)
        status = (resp.status or "").strip()
        if status == "ok":
            return "ok"
        return status or "error: empty health status"
    except grpc.RpcError as exc:
        return f"error: {exc}"
    except Exception as exc:  # noqa: BLE001 - plugins are optional
        return f"error: {exc}"
    finally:
        await channel.close(grace=None)


async def probe_plugin_health_outcome(name: str, address: str) -> tuple[bool, ServiceHealth | None]:
    """Probe one plugin for Engine aggregate Health (never required).

    Args:
        name: Env-var plugin token (lowercase).
        address: Plugin gRPC address.

    Returns:
        ``(False, ServiceHealth)`` so a down plugin cannot fail Engine.
    """
    status = await probe_plugin_health(address)
    return (
        False,
        ServiceHealth(name=f"plugin:{name}", status=status, address=address),
    )
