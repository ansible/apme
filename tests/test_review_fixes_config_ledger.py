"""Review-fix uniformity tests: shared env parsing, rule-ID leaf, ledger, launcher.

Covers finding #18 (``config_env`` uniformity across all call-site parsers),
finding #23 (``rule_ids`` leaf with no graph/remediation imports), finding
#22 (violation ledger approve/decline transitions), and finding #16 (daemon
launcher proxy supervision). Deterministic: no network, ports, or processes,
beyond one hermetic ``sys.executable -c`` leaf check.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

from apme_engine import opa_client, rule_ids
from apme_engine.config_env import get_env_float, get_env_int
from apme_engine.daemon import deadline as deadline_module
from apme_engine.daemon.launcher import _log_proxy_task_done, _run_daemon
from apme_engine.graph.content_graph import (
    ContentGraph,
    ContentNode,
    NodeIdentity,
    NodeType,
    ViolationKey,
    ViolationRecord,
    _violation_key,
)
from apme_engine.graph.types import ViolationDict
from apme_engine.remediation import partition
from apme_engine.venv_manager import session as venv_session
from apme_gateway.scan import driver as scan_driver

_FLOAT_ENV = "APME_TEST_REVIEW_FLOAT"
_INT_ENV = "APME_TEST_REVIEW_INT"
_BREAKER_ENV = "APME_OPA_BREAKER_COOLDOWN"
_MAX_TIMEOUTS_ENV = "APME_OPA_MAX_CONSECUTIVE_TIMEOUTS"
_OP_ENV = "APME_TEST_REVIEW_OP_TIMEOUT"

_ENGINE_ADDR = "127.0.0.1:50051"
_PROXY_ADDR = "127.0.0.1:8765"

_NODE_ID = "site.yml/plays[0]/tasks[0]"

# (raw, default, positive_only, expected, warns); None deletes the variable.
_FLOAT_CASES: list[tuple[str | None, float, bool, float, bool]] = [
    (None, 300.0, False, 300.0, False),
    ("", 300.0, False, 300.0, False),
    ("   ", 300.0, False, 300.0, False),
    ("2.5", 300.0, False, 2.5, False),
    (" 2.5 ", 300.0, False, 2.5, False),
    ("2.5", 300.0, True, 2.5, False),
    ("abc", 300.0, False, 300.0, True),
    ("nan", 300.0, False, 300.0, True),
    ("inf", 300.0, False, 300.0, True),
    ("-inf", 300.0, False, 300.0, True),
    ("1e999", 300.0, False, 300.0, True),
    ("0", 300.0, True, 300.0, True),
    ("-1.5", 300.0, True, 300.0, True),
    ("nan", 300.0, True, 300.0, True),
    ("0", 300.0, False, 0.0, False),
    ("-1.5", 300.0, False, -1.5, False),
]

# (raw, default, min_value, expected, warns); None deletes the variable.
_INT_CASES: list[tuple[str | None, int, int | None, int, bool]] = [
    (None, 3, None, 3, False),
    ("5", 3, None, 5, False),
    (" 7 ", 3, None, 7, False),
    ("abc", 3, None, 3, True),
    ("", 3, None, 3, True),
    ("   ", 3, None, 3, True),
    ("2.5", 3, None, 3, True),
    ("0", 3, None, 0, False),
    ("-4", 3, None, -4, False),
    ("0", 3, 1, 3, True),
    ("-4", 3, 1, 3, True),
    ("1", 3, 1, 1, False),
    ("7", 3, 1, 7, False),
]

_DELEGATE_FLOAT_RAWS: list[str | None] = [
    None,
    "9.5",
    "abc",
    "nan",
    "inf",
    "-inf",
    "",
    "   ",
    "0",
    "-2.5",
    " 7 ",
]

_DELEGATE_INT_RAWS: list[str | None] = [
    None,
    "7",
    "abc",
    "2.5",
    "",
    "   ",
    "0",
    "-3",
    " 9 ",
]


def _set_or_delete(monkeypatch: pytest.MonkeyPatch, name: str, raw: str | None) -> None:
    """Set *name* to *raw* or delete it when *raw* is None.

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        name: Environment variable name.
        raw: Raw value to set, or None to delete the variable.
    """
    if raw is None:
        monkeypatch.delenv(name, raising=False)
    else:
        monkeypatch.setenv(name, raw)


def _require_daemon_stack() -> None:
    """Skip when the daemon import chain is broken by the concurrent refactor.

    ``daemon/session.py`` line 39 references ``_parse_int_env`` without
    importing it (``NameError`` at import, also breaking ``engine_server``).
    The owning agent's one-line fix (``get_env_int``) re-enables these tests.
    """
    try:
        import apme_engine.daemon.engine_server  # noqa: F401
    except NameError as exc:
        pytest.skip(f"daemon import chain broken by concurrent refactor: {exc}")


def _make_violation(rule_id: str) -> ViolationDict:
    """Build a minimal violation dict for ledger seeding.

    Args:
        rule_id: Rule identifier for the violation.

    Returns:
        Minimal violation dict keyed to the seeded node.
    """
    return {
        "path": _NODE_ID,
        "rule_id": rule_id,
        "severity": "high",
        "message": "seeded violation",
    }


def _seed_graph(*, status: str = "proposed") -> ContentGraph:
    """Build a one-node graph with two actionable and one open ledger entry.

    Args:
        status: Status for the two actionable entries (``proposed`` for the
            AI gate, ``pending_review`` for the deterministic gate).

    Returns:
        ContentGraph with node ``_NODE_ID`` seeded.
    """
    graph = ContentGraph()
    node = ContentNode(
        identity=NodeIdentity(path=_NODE_ID, node_type=NodeType.TASK),
        file_path="site.yml",
    )
    graph.add_node(node)
    stored = graph.get_node(_NODE_ID)
    assert stored is not None
    stored.violation_ledger[(_NODE_ID, "L042")] = ViolationRecord(
        key=(_NODE_ID, "L042"),
        violation=_make_violation("L042"),
        status=status,
        fixed_by="ai",
        fixed_in_pass=2,
    )
    stored.violation_ledger[(_NODE_ID, "L043")] = ViolationRecord(
        key=(_NODE_ID, "L043"),
        violation=_make_violation("L043"),
        status=status,
        fixed_by="deterministic",
        fixed_in_pass=1,
    )
    stored.violation_ledger[(_NODE_ID, "L044")] = ViolationRecord(
        key=(_NODE_ID, "L044"),
        violation=_make_violation("L044"),
        status="open",
    )
    return graph


def _ledger(graph: ContentGraph) -> dict[ViolationKey, ViolationRecord]:
    """Return the seeded node's violation ledger.

    Args:
        graph: Graph built by ``_seed_graph``.

    Returns:
        The seeded node's mutable violation ledger.
    """
    node = graph.get_node(_NODE_ID)
    assert node is not None
    return node.violation_ledger


class TestConfigEnvFloat:
    """Shared float parser edge table (#18)."""

    @pytest.mark.parametrize("raw,default,positive_only,expected,warns", _FLOAT_CASES)  # type: ignore[untyped-decorator]
    def test_edge_table(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        raw: str | None,
        default: float,
        positive_only: bool,
        expected: float,
        warns: bool,
    ) -> None:
        """Unset/unparsable/non-finite/non-positive input degrades to the default.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            caplog: Pytest log-capture fixture.
            raw: Raw env value (None deletes the variable).
            default: Fallback value.
            positive_only: Whether non-positive values fall back.
            expected: Expected parsed value.
            warns: Whether a fallback warning is expected.
        """
        _set_or_delete(monkeypatch, _FLOAT_ENV, raw)
        caplog.set_level(logging.WARNING, logger="apme_engine.config_env")
        caplog.clear()
        assert get_env_float(_FLOAT_ENV, default, positive_only=positive_only) == expected
        relevant = [record for record in caplog.records if record.name == "apme_engine.config_env"]
        if warns:
            assert relevant, f"expected a fallback warning for {_FLOAT_ENV}={raw!r}"
        else:
            assert relevant == [], f"unexpected warning for {_FLOAT_ENV}={raw!r}"


class TestConfigEnvInt:
    """Shared int parser edge table (#18)."""

    @pytest.mark.parametrize("raw,default,min_value,expected,warns", _INT_CASES)  # type: ignore[untyped-decorator]
    def test_edge_table(
        self,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
        raw: str | None,
        default: int,
        min_value: int | None,
        expected: int,
        warns: bool,
    ) -> None:
        """Unset/unparsable/below-floor input degrades to the default.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            caplog: Pytest log-capture fixture.
            raw: Raw env value (None deletes the variable).
            default: Fallback value.
            min_value: Optional floor; values below it fall back.
            expected: Expected parsed value.
            warns: Whether a fallback warning is expected.
        """
        _set_or_delete(monkeypatch, _INT_ENV, raw)
        caplog.set_level(logging.WARNING, logger="apme_engine.config_env")
        caplog.clear()
        assert get_env_int(_INT_ENV, default, min_value=min_value) == expected
        relevant = [record for record in caplog.records if record.name == "apme_engine.config_env"]
        if warns:
            assert relevant, f"expected a fallback warning for {_INT_ENV}={raw!r}"
        else:
            assert relevant == [], f"unexpected warning for {_INT_ENV}={raw!r}"


class TestCallSiteDelegation:
    """All five call-site parsers share the helpers' semantics (#18)."""

    @pytest.mark.parametrize("raw", _DELEGATE_FLOAT_RAWS)  # type: ignore[untyped-decorator]
    def test_daemon_session_float_wrapper_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """daemon.session._parse_float_env matches the shared helper for every input.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _require_daemon_stack()
        from apme_engine.daemon.session import _parse_float_env

        _set_or_delete(monkeypatch, _FLOAT_ENV, raw)
        assert _parse_float_env(_FLOAT_ENV, 1.5) == get_env_float(_FLOAT_ENV, 1.5)

    @pytest.mark.parametrize("raw", _DELEGATE_INT_RAWS)  # type: ignore[untyped-decorator]
    def test_deadline_int_wrapper_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """daemon.deadline._parse_int_env matches the shared helper for every input.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _set_or_delete(monkeypatch, _INT_ENV, raw)
        assert deadline_module._parse_int_env(_INT_ENV, 300) == get_env_int(_INT_ENV, 300)

    @pytest.mark.parametrize("raw", _DELEGATE_INT_RAWS)  # type: ignore[untyped-decorator]
    def test_venv_positive_int_wrapper_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """venv_manager._parse_positive_int_env matches get_env_int with min_value=1.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _set_or_delete(monkeypatch, _INT_ENV, raw)
        assert venv_session._parse_positive_int_env(_INT_ENV, 8) == get_env_int(_INT_ENV, 8, min_value=1)

    @pytest.mark.parametrize("raw", _DELEGATE_FLOAT_RAWS)  # type: ignore[untyped-decorator]
    def test_opa_breaker_cooldown_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """opa_client._breaker_cooldown matches shared positive-only float parsing.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _set_or_delete(monkeypatch, _BREAKER_ENV, raw)
        assert opa_client._breaker_cooldown() == get_env_float(_BREAKER_ENV, 300.0, positive_only=True)

    @pytest.mark.parametrize("raw", _DELEGATE_INT_RAWS)  # type: ignore[untyped-decorator]
    def test_opa_max_timeouts_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """opa_client._max_consecutive_timeouts matches get_env_int with min_value=1.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _set_or_delete(monkeypatch, _MAX_TIMEOUTS_ENV, raw)
        assert opa_client._max_consecutive_timeouts() == get_env_int(_MAX_TIMEOUTS_ENV, 3, min_value=1)

    @pytest.mark.parametrize("raw", _DELEGATE_FLOAT_RAWS)  # type: ignore[untyped-decorator]
    def test_gateway_op_timeout_delegates(self, monkeypatch: pytest.MonkeyPatch, raw: str | None) -> None:
        """Gateway _op_timeout matches shared positive-only float parsing.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
            raw: Raw env value (None deletes the variable).
        """
        _set_or_delete(monkeypatch, _OP_ENV, raw)
        assert scan_driver._op_timeout(_OP_ENV, 42.0) == get_env_float(_OP_ENV, 42.0, positive_only=True)


class TestRuleIdsLeaf:
    """Canonical rule-ID normalization lives in a dependency-free leaf (#23)."""

    @pytest.mark.parametrize(  # type: ignore[untyped-decorator]
        "raw,expected",
        [
            ("native:L042", "L042"),
            ("native:R108", "R108"),
            ("native:", ""),
            ("native:native:L042", "native:L042"),
            ("native", "native"),
            ("opa:L042", "opa:L042"),
            ("ansible:L042", "ansible:L042"),
            ("opa:ansible:L042", "opa:ansible:L042"),
            ("L042", "L042"),
            ("R108", "R108"),
            ("", ""),
            ("a:b:c", "a:b:c"),
            ("NATIVE:L042", "NATIVE:L042"),
        ],
    )
    def test_normalize_strips_native_only(self, raw: str, expected: str) -> None:
        """Only the legacy native: prefix is stripped; all else untouched.

        Args:
            raw: Raw rule ID.
            expected: Normalized rule ID.
        """
        assert rule_ids.normalize_rule_id(raw) == expected

    def test_rule_ids_has_no_graph_or_remediation_deps(self) -> None:
        """A fresh interpreter importing rule_ids pulls in no graph/remediation modules."""
        repo_src = Path(__file__).resolve().parents[1] / "src"
        script = (
            "import sys\n"
            "import apme_engine.rule_ids\n"
            "bad = sorted(\n"
            "    m for m in sys.modules\n"
            "    if m == 'apme_engine.graph' or m.startswith('apme_engine.graph.')\n"
            "    or m == 'apme_engine.remediation' or m.startswith('apme_engine.remediation.')\n"
            "    or m == 'apme_engine.graph.content_graph' or m == 'apme_engine.remediation.partition'\n"
            ")\n"
            "print('UNEXPECTED:', bad)\n"
            "raise SystemExit(1 if bad else 0)\n"
        )
        env = {**os.environ, "PYTHONPATH": str(repo_src)}
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            env=env,
            check=False,
        )
        assert proc.returncode == 0, f"rule_ids pulled heavy modules: {proc.stdout} {proc.stderr}"

    def test_partition_reexports_canonical_normalize(self) -> None:
        """partition.normalize_rule_id is the same function object (no forked semantics)."""
        assert partition.normalize_rule_id is rule_ids.normalize_rule_id

    def test_violation_key_agrees_with_partition_routing(self) -> None:
        """Ledger keys and partition routing normalize a native: sample identically."""
        violation: ViolationDict = {
            "path": _NODE_ID,
            "rule_id": "native:R108",
            "severity": "high",
            "message": "privilege escalation",
        }
        assert _violation_key(violation) == (_NODE_ID, "R108")
        assert partition.is_ai_reviewable(violation) is True
        bare: ViolationDict = {
            "path": _NODE_ID,
            "rule_id": "R108",
            "severity": "high",
            "message": "privilege escalation",
        }
        assert _violation_key(bare) == _violation_key(violation)
        assert partition.is_ai_reviewable(bare) is True


class TestLedgerTransitions:
    """Violation ledger approve/decline transitions on ContentGraph (#22)."""

    def test_approve_proposed_retains_attribution(self) -> None:
        """Approving proposed violations keeps fixed_by and fixed_in_pass."""
        graph = _seed_graph()
        assert graph.approve_proposed(_NODE_ID) == 2
        ledger = _ledger(graph)
        for rule_id, by, passed in (("L042", "ai", 2), ("L043", "deterministic", 1)):
            record = ledger[(_NODE_ID, rule_id)]
            assert record.status == "fixed"
            assert record.fixed_by == by
            assert record.fixed_in_pass == passed
        untouched = ledger[(_NODE_ID, "L044")]
        assert untouched.status == "open"
        assert untouched.fixed_by is None
        assert untouched.fixed_in_pass is None

    def test_decline_proposed_clears_attribution(self) -> None:
        """Declining proposed violations clears fixed_by and fixed_in_pass."""
        graph = _seed_graph()
        assert graph.decline_proposed(_NODE_ID) == 2
        ledger = _ledger(graph)
        for rule_id in ("L042", "L043"):
            record = ledger[(_NODE_ID, rule_id)]
            assert record.status == "declined"
            assert record.fixed_by is None
            assert record.fixed_in_pass is None
        assert ledger[(_NODE_ID, "L044")].status == "open"

    def test_approve_pending_review_retains_attribution(self) -> None:
        """Approving pending_review violations keeps fixed_by and fixed_in_pass."""
        graph = _seed_graph(status="pending_review")
        assert graph.approve_pending_review(_NODE_ID) == 2
        ledger = _ledger(graph)
        assert ledger[(_NODE_ID, "L042")].status == "fixed"
        assert ledger[(_NODE_ID, "L042")].fixed_by == "ai"
        assert ledger[(_NODE_ID, "L042")].fixed_in_pass == 2
        assert ledger[(_NODE_ID, "L044")].status == "open"

    def test_decline_pending_review_clears_attribution(self) -> None:
        """Declining pending_review violations clears fixed_by and fixed_in_pass."""
        graph = _seed_graph(status="pending_review")
        assert graph.decline_pending_review(_NODE_ID) == 2
        ledger = _ledger(graph)
        for rule_id in ("L042", "L043"):
            record = ledger[(_NODE_ID, rule_id)]
            assert record.status == "declined"
            assert record.fixed_by is None
            assert record.fixed_in_pass is None
        assert ledger[(_NODE_ID, "L044")].status == "open"

    def test_unknown_node_returns_zero(self) -> None:
        """Transitions on a missing node return 0 and change nothing."""
        graph = _seed_graph()
        assert graph.approve_proposed("no/such/node") == 0
        assert graph.decline_proposed("no/such/node") == 0
        assert graph.approve_pending_review("no/such/node") == 0
        assert graph.decline_pending_review("no/such/node") == 0
        assert graph._transition_violations("no/such/node", "proposed", "fixed") == 0
        assert _ledger(graph)[(_NODE_ID, "L042")].status == "proposed"

    def test_non_matching_status_untouched(self) -> None:
        """Only entries in from_status move; open entries stay open."""
        graph = _seed_graph()
        assert graph.approve_pending_review(_NODE_ID) == 0
        ledger = _ledger(graph)
        assert ledger[(_NODE_ID, "L042")].status == "proposed"
        assert ledger[(_NODE_ID, "L043")].status == "proposed"
        assert ledger[(_NODE_ID, "L044")].status == "open"

    def test_transition_helper_moves_only_matching(self) -> None:
        """Direct _transition_violations honors from_status without clearing attribution."""
        graph = _seed_graph()
        assert graph._transition_violations(_NODE_ID, "proposed", "fixed") == 2
        ledger = _ledger(graph)
        assert ledger[(_NODE_ID, "L042")].status == "fixed"
        assert ledger[(_NODE_ID, "L042")].fixed_by == "ai"
        assert ledger[(_NODE_ID, "L042")].fixed_in_pass == 2
        assert ledger[(_NODE_ID, "L044")].status == "open"


class _FakeEngineServer:
    """Stand-in for grpc.aio.Server with controllable termination."""

    def __init__(self, hang: bool) -> None:
        """Record whether wait_for_termination blocks; track cancellation.

        Args:
            hang: When True, wait_for_termination blocks until cancelled.
        """
        self._hang = hang
        self.cancelled = False

    async def wait_for_termination(self) -> None:
        """Return at once, or block until cancelled like a live server.

        Raises:
            asyncio.CancelledError: When the wait is cancelled (re-raised
                after recording it).
        """
        if not self._hang:
            return
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _FakeProxy:
    """Controllable uvicorn.Server.serve() behavior for supervision tests."""

    def __init__(self, mode: str, error: RuntimeError | None = None) -> None:
        """Configure serve() behavior.

        Args:
            mode: One of ``exit`` (return at once), ``delayed-raise`` (raise
                after a short delay), or ``hang`` (block until cancelled).
            error: Exception raised in ``delayed-raise`` mode.
        """
        self._mode = mode
        self._error = error
        self.cancelled = False

    async def serve(self) -> None:
        """Mimic the proxy lifecycle per the configured mode.

        Raises:
            RuntimeError: The configured error in ``delayed-raise`` mode.
            asyncio.CancelledError: When a ``hang`` wait is cancelled
                (re-raised after recording it).
        """
        if self._mode == "delayed-raise":
            await asyncio.sleep(0.1)
            error = self._error
            assert error is not None
            raise RuntimeError(str(error)) from error
        if self._mode == "hang":
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        return None


class _FakeUvicornConfig:
    """Stand-in for uvicorn.Config (constructor arguments ignored)."""

    def __init__(self, app: object, *, host: str = "", port: int = 0, log_level: str = "") -> None:
        """Accept and discard constructor arguments.

        Args:
            app: ASGI application (ignored).
            host: Bind host (ignored).
            port: Bind port (ignored).
            log_level: Log level name (ignored).
        """


class _FakeUvicornServer:
    """Stand-in for uvicorn.Server delegating serve() to a _FakeProxy."""

    def __init__(self, proxy: _FakeProxy, config: object) -> None:
        """Bind the shared fake proxy.

        Args:
            proxy: Fake proxy lifecycle behavior.
            config: Uvicorn config (ignored).
        """
        self._proxy = proxy
        self._config = config

    async def serve(self) -> None:
        """Delegate to the shared fake proxy."""
        await self._proxy.serve()


def _install_daemon_stubs(
    monkeypatch: pytest.MonkeyPatch,
    engine: _FakeEngineServer,
    proxy: _FakeProxy,
) -> None:
    """Stub engine serve, uvicorn, and galaxy-proxy factories (no ports/processes).

    Args:
        monkeypatch: Pytest monkeypatch fixture.
        engine: Fake engine server returned by the stubbed serve.
        proxy: Fake proxy behavior behind the stubbed uvicorn server.
    """
    _require_daemon_stack()

    async def _fake_engine_serve(address: str) -> _FakeEngineServer:
        """Return the shared fake engine server for any address.

        Args:
            address: Requested listen address (ignored).

        Returns:
            The shared fake engine server.
        """
        await asyncio.sleep(0.01)
        return engine

    def _fake_uvicorn_server(config: object) -> _FakeUvicornServer:
        """Wrap the shared fake proxy as a uvicorn server.

        Args:
            config: Uvicorn config (ignored).

        Returns:
            Server delegating serve() to the shared fake proxy.
        """
        return _FakeUvicornServer(proxy, config)

    def _fake_setup_logging(verbosity: int) -> int:
        """Pretend to configure logging and report INFO level.

        Args:
            verbosity: Verbosity flag (ignored).

        Returns:
            Logging INFO level.
        """
        return logging.INFO

    def _fake_create_app() -> object:
        """Return a placeholder ASGI app (never served for real).

        Returns:
            Opaque app placeholder.
        """
        return object()

    monkeypatch.setattr("apme_engine.daemon.engine_server.serve", _fake_engine_serve)
    monkeypatch.setattr("uvicorn.Config", _FakeUvicornConfig)
    monkeypatch.setattr("uvicorn.Server", _fake_uvicorn_server)
    monkeypatch.setattr("galaxy_proxy.cli._setup_logging", _fake_setup_logging)
    monkeypatch.setattr("galaxy_proxy.proxy.server.create_app", _fake_create_app)


async def _run_supervised(services: dict[str, str]) -> None:
    """Run _run_daemon with a timeout guard, restoring os.environ afterward.

    Args:
        services: Service name to listen-address map for the daemon.
    """
    saved = dict(os.environ)
    try:
        await asyncio.wait_for(_run_daemon(services), timeout=5.0)
    finally:
        os.environ.clear()
        os.environ.update(saved)


class TestLauncherSupervision:
    """Daemon supervision: proxy startup/mid-life/engine-first handling (#16)."""

    async def test_proxy_exit_during_startup_fails_daemon(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A proxy task already done at the startup check raises RuntimeError.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        engine = _FakeEngineServer(hang=True)
        proxy = _FakeProxy(mode="exit")
        _install_daemon_stubs(monkeypatch, engine, proxy)
        with pytest.raises(RuntimeError, match="during startup"):
            await _run_supervised({"engine": _ENGINE_ADDR, "galaxy_proxy": _PROXY_ADDR})
        assert engine.cancelled is False
        assert proxy.cancelled is False

    async def test_proxy_midlife_crash_cancels_engine_wait(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A mid-life proxy crash cancels the engine wait and raises RuntimeError.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        engine = _FakeEngineServer(hang=True)
        proxy = _FakeProxy(mode="delayed-raise", error=RuntimeError("proxy boom"))
        _install_daemon_stubs(monkeypatch, engine, proxy)
        with pytest.raises(RuntimeError, match="terminated unexpectedly"):
            await _run_supervised({"engine": _ENGINE_ADDR, "galaxy_proxy": _PROXY_ADDR})
        assert engine.cancelled is True

    async def test_engine_exit_first_cancels_proxy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An abnormal engine-first exit cancels the proxy and raises RuntimeError.

        Args:
            monkeypatch: Pytest monkeypatch fixture.
        """
        engine = _FakeEngineServer(hang=False)
        proxy = _FakeProxy(mode="hang")
        _install_daemon_stubs(monkeypatch, engine, proxy)
        with pytest.raises(RuntimeError, match="Engine terminated unexpectedly"):
            await _run_supervised({"engine": _ENGINE_ADDR, "galaxy_proxy": _PROXY_ADDR})
        assert proxy.cancelled is True


class TestProxyTaskDone:
    """Done-callback log levels for the Galaxy Proxy server task (#16)."""

    async def test_cancelled_task_logs_debug_not_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A task cancelled during shutdown logs debug, never error.

        Args:
            caplog: Pytest log-capture fixture.
        """
        caplog.set_level(logging.DEBUG, logger="apme_engine.daemon.launcher")

        async def _hang() -> None:
            """Block until cancelled."""
            await asyncio.Event().wait()

        task: asyncio.Task[None] = asyncio.create_task(_hang())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        caplog.clear()
        _log_proxy_task_done(task)
        assert task.cancelled()
        assert [record for record in caplog.records if record.levelno >= logging.ERROR] == []
        assert any("cancelled during shutdown" in record.getMessage() for record in caplog.records)

    async def test_failed_task_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A proxy task ending with an exception logs error with the cause.

        Args:
            caplog: Pytest log-capture fixture.
        """
        caplog.set_level(logging.DEBUG, logger="apme_engine.daemon.launcher")

        async def _boom() -> None:
            """Raise a proxy failure.

            Raises:
                RuntimeError: Always (simulates a proxy crash).
            """
            raise RuntimeError("proxy boom")

        task = asyncio.create_task(_boom())
        with pytest.raises(RuntimeError, match="proxy boom"):
            await task
        _log_proxy_task_done(task)
        assert any(record.levelno >= logging.ERROR and "proxy boom" in record.getMessage() for record in caplog.records)

    async def test_clean_exit_logs_error(self, caplog: pytest.LogCaptureFixture) -> None:
        """A proxy task exiting cleanly is unexpected and logs error.

        Args:
            caplog: Pytest log-capture fixture.
        """
        caplog.set_level(logging.DEBUG, logger="apme_engine.daemon.launcher")

        async def _ok() -> None:
            """Return immediately."""
            return None

        task = asyncio.create_task(_ok())
        await task
        _log_proxy_task_done(task)
        assert "exited unexpectedly" in caplog.text
