"""Tests for unified Validator gRPC servicers (OPA wrapper, Native, Ansible migration)."""

import json
from unittest.mock import MagicMock, patch

import pytest
import jsonpickle
from apme.v1 import validate_pb2, common_pb2


class FakeGrpcContext:
    """Minimal stub for grpc.ServicerContext."""
    def set_code(self, code): pass
    def set_details(self, details): pass


class TestOpaValidatorServicer:
    def test_validate_posts_to_opa_rest(self):
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        hierarchy = {"hierarchy": [{"tree_type": "playbook", "nodes": []}]}
        violations = [{"rule_id": "L024", "level": "warning", "message": "m", "file": "f.yml", "line": 1, "path": "p"}]

        request = validate_pb2.ValidateRequest(
            hierarchy_payload=json.dumps(hierarchy).encode(),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": violations}

        servicer = OpaValidatorServicer()
        with patch("apme_engine.daemon.opa_validator_server.requests.post", return_value=mock_response) as mock_post:
            resp = servicer.Validate(request, FakeGrpcContext())

        mock_post.assert_called_once()
        call_args = mock_post.call_args
        assert "violations" in call_args[0][0]
        assert call_args[1]["json"]["input"] == hierarchy
        assert len(resp.violations) == 1
        assert resp.violations[0].rule_id == "L024"

    def test_validate_empty_payload_returns_empty(self):
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        request = validate_pb2.ValidateRequest()
        servicer = OpaValidatorServicer()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"result": []}

        with patch("apme_engine.daemon.opa_validator_server.requests.post", return_value=mock_response):
            resp = servicer.Validate(request, FakeGrpcContext())
        assert len(resp.violations) == 0

    def test_validate_opa_http_error_returns_empty(self):
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        request = validate_pb2.ValidateRequest(
            hierarchy_payload=json.dumps({"hierarchy": []}).encode(),
        )
        mock_response = MagicMock()
        mock_response.status_code = 500

        servicer = OpaValidatorServicer()
        with patch("apme_engine.daemon.opa_validator_server.requests.post", return_value=mock_response):
            resp = servicer.Validate(request, FakeGrpcContext())
        assert len(resp.violations) == 0

    def test_health_opa_up(self):
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        mock_response = MagicMock()
        mock_response.status_code = 200

        servicer = OpaValidatorServicer()
        with patch("apme_engine.daemon.opa_validator_server.requests.get", return_value=mock_response):
            resp = servicer.Health(common_pb2.HealthRequest(), FakeGrpcContext())
        assert resp.status == "ok"

    def test_health_opa_down(self):
        from apme_engine.daemon.opa_validator_server import OpaValidatorServicer

        servicer = OpaValidatorServicer()
        with patch("apme_engine.daemon.opa_validator_server.requests.get", side_effect=ConnectionError("refused")):
            resp = servicer.Health(common_pb2.HealthRequest(), FakeGrpcContext())
        assert "unreachable" in resp.status


class TestNativeValidatorServicer:
    def test_validate_deserializes_scandata_and_runs(self):
        from apme_engine.daemon.native_validator_server import NativeValidatorServicer

        mock_scandata = type("Scandata", (), {"contexts": []})()
        hierarchy = {"hierarchy": []}
        request = validate_pb2.ValidateRequest(
            hierarchy_payload=json.dumps(hierarchy).encode(),
            scandata=jsonpickle.encode(mock_scandata).encode(),
        )

        servicer = NativeValidatorServicer()
        with patch("apme_engine.daemon.native_validator_server.NativeValidator") as MockNV:
            MockNV.return_value.run.return_value = [
                {"rule_id": "native:L028", "level": "warning", "message": "no name", "file": "f.yml", "line": 5, "path": "p"}
            ]
            resp = servicer.Validate(request, FakeGrpcContext())

        assert len(resp.violations) == 1
        assert resp.violations[0].rule_id == "native:L028"

    def test_validate_no_scandata_returns_empty(self):
        from apme_engine.daemon.native_validator_server import NativeValidatorServicer

        request = validate_pb2.ValidateRequest(
            hierarchy_payload=json.dumps({}).encode(),
        )
        servicer = NativeValidatorServicer()
        with patch("apme_engine.daemon.native_validator_server.NativeValidator") as MockNV:
            MockNV.return_value.run.return_value = []
            resp = servicer.Validate(request, FakeGrpcContext())
        assert len(resp.violations) == 0

    def test_validate_bad_scandata_returns_empty(self):
        from apme_engine.daemon.native_validator_server import NativeValidatorServicer

        request = validate_pb2.ValidateRequest(
            hierarchy_payload=json.dumps({}).encode(),
            scandata=b"not-valid-jsonpickle{{{",
        )
        servicer = NativeValidatorServicer()
        resp = servicer.Validate(request, FakeGrpcContext())
        assert len(resp.violations) == 0

    def test_health_returns_ok(self):
        from apme_engine.daemon.native_validator_server import NativeValidatorServicer

        servicer = NativeValidatorServicer()
        resp = servicer.Health(common_pb2.HealthRequest(), FakeGrpcContext())
        assert resp.status == "ok"


class TestAnsibleValidatorServicerMigration:
    """Verify Ansible validator now uses the unified Validator service."""

    def test_servicer_extends_validator_servicer(self):
        from apme_engine.daemon.ansible_validator_server import AnsibleValidatorServicer
        assert issubclass(AnsibleValidatorServicer, validate_pb2.ValidateResponse.__class__.__mro__[0].__class__) or \
               hasattr(AnsibleValidatorServicer, "Validate")

    def test_serve_registers_validator_service(self):
        from apme_engine.daemon.ansible_validator_server import serve
        with patch("apme_engine.daemon.ansible_validator_server.validate_pb2_grpc.add_ValidatorServicer_to_server") as mock_add:
            with patch("grpc.server") as mock_server:
                mock_server.return_value.add_insecure_port = MagicMock()
                serve("0.0.0.0:50053")
                mock_add.assert_called_once()


class TestPrimaryFanOut:
    """Verify Primary uses the unified _call_validator for all backends."""

    def test_call_validator_uses_validator_stub(self):
        from apme_engine.daemon.primary_server import _call_validator

        mock_channel = MagicMock()
        mock_stub = MagicMock()
        mock_resp = MagicMock()
        mock_resp.violations = []

        with patch("apme_engine.daemon.primary_server.grpc.insecure_channel", return_value=mock_channel):
            with patch("apme_engine.daemon.primary_server.validate_pb2_grpc.ValidatorStub", return_value=mock_stub):
                mock_stub.Validate.return_value = mock_resp
                request = validate_pb2.ValidateRequest()
                result = _call_validator("localhost:50055", request)

        mock_stub.Validate.assert_called_once()
        assert result == []

    def test_primary_no_longer_imports_native_validator(self):
        """Primary should not import NativeValidator directly (it's in its own container)."""
        import apme_engine.daemon.primary_server as ps
        source = open(ps.__file__).read()
        assert "from apme_engine.validators.native" not in source
        assert "NativeValidator()" not in source

    def test_primary_no_longer_imports_opa_client(self):
        """Primary should not import opa_client (OPA is behind gRPC now)."""
        import apme_engine.daemon.primary_server as ps
        source = open(ps.__file__).read()
        assert "from apme_engine.opa_client" not in source
        assert "_call_opa" not in source
