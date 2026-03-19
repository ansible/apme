"""Tests for violation_convert: dict ↔ proto conversion."""
# mypy: disable-error-code="attr-defined"

from apme.v1 import common_pb2
from apme.v1.common_pb2 import LineRange, Violation
from apme_engine.daemon.violation_convert import (
    violation_dict_to_proto,
    violation_proto_to_dict,
)
from apme_engine.engine.models import RemediationClass, RemediationResolution, ViolationDict


class TestViolationDictToProto:
    """Tests for converting dict violations to proto."""

    def test_basic_conversion(self) -> None:
        """Dict fields map to proto fields."""
        v: ViolationDict = {
            "rule_id": "L021",
            "level": "high",
            "message": "Missing mode",
            "file": "playbook.yml",
            "line": 10,
            "path": "tasks",
        }
        proto = violation_dict_to_proto(v)
        assert proto.rule_id == "L021"
        assert proto.level == "high"
        assert proto.message == "Missing mode"
        assert proto.file == "playbook.yml"
        assert proto.line == 10
        assert proto.path == "tasks"

    def test_line_range_conversion(self) -> None:
        """Line range tuple converts to LineRange proto."""
        v: ViolationDict = {"rule_id": "L021", "line": [5, 10]}
        proto = violation_dict_to_proto(v)
        assert proto.HasField("line_range")
        assert proto.line_range.start == 5
        assert proto.line_range.end == 10

    def test_remediation_class_auto_fixable(self) -> None:
        """Auto-fixable class converts to proto enum."""
        v = {"rule_id": "L021", "remediation_class": RemediationClass.AUTO_FIXABLE}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_class == common_pb2.REMEDIATION_CLASS_AUTO_FIXABLE

    def test_remediation_class_ai_candidate(self) -> None:
        """AI-candidate class converts to proto enum."""
        v = {"rule_id": "L021", "remediation_class": RemediationClass.AI_CANDIDATE}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_class == common_pb2.REMEDIATION_CLASS_AI_CANDIDATE

    def test_remediation_class_manual_review(self) -> None:
        """Manual-review class converts to proto enum."""
        v = {"rule_id": "L021", "remediation_class": RemediationClass.MANUAL_REVIEW}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_class == common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW

    def test_missing_remediation_class_defaults_to_ai(self) -> None:
        """Missing remediation_class defaults to AI_CANDIDATE."""
        v = {"rule_id": "L021"}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_class == common_pb2.REMEDIATION_CLASS_AI_CANDIDATE

    def test_resolution_transform_failed(self) -> None:
        """TRANSFORM_FAILED resolution converts to proto enum."""
        v = {"rule_id": "L021", "remediation_resolution": RemediationResolution.TRANSFORM_FAILED}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_resolution == common_pb2.REMEDIATION_RESOLUTION_TRANSFORM_FAILED

    def test_resolution_oscillation(self) -> None:
        """OSCILLATION resolution converts to proto enum."""
        v = {"rule_id": "L021", "remediation_resolution": RemediationResolution.OSCILLATION}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_resolution == common_pb2.REMEDIATION_RESOLUTION_OSCILLATION

    def test_resolution_ai_values(self) -> None:
        """AI resolution values convert to proto enums."""
        for res, expected in [
            (RemediationResolution.AI_PROPOSED, common_pb2.REMEDIATION_RESOLUTION_AI_PROPOSED),
            (RemediationResolution.AI_FAILED, common_pb2.REMEDIATION_RESOLUTION_AI_FAILED),
            (RemediationResolution.AI_LOW_CONFIDENCE, common_pb2.REMEDIATION_RESOLUTION_AI_LOW_CONFIDENCE),
            (RemediationResolution.USER_REJECTED, common_pb2.REMEDIATION_RESOLUTION_USER_REJECTED),
        ]:
            v = {"rule_id": "L021", "remediation_resolution": res}
            proto = violation_dict_to_proto(v)
            assert proto.remediation_resolution == expected

    def test_missing_resolution_defaults_to_unresolved(self) -> None:
        """Missing remediation_resolution defaults to UNRESOLVED."""
        v = {"rule_id": "L021"}
        proto = violation_dict_to_proto(v)
        assert proto.remediation_resolution == common_pb2.REMEDIATION_RESOLUTION_UNRESOLVED


class TestViolationProtoToDict:
    """Tests for converting proto violations to dict."""

    def test_basic_conversion(self) -> None:
        """Proto fields map to dict fields."""
        proto = Violation(
            rule_id="L021",
            level="high",
            message="Missing mode",
            file="playbook.yml",
            line=10,
            path="tasks",
        )
        d = violation_proto_to_dict(proto)
        assert d["rule_id"] == "L021"
        assert d["level"] == "high"
        assert d["message"] == "Missing mode"
        assert d["file"] == "playbook.yml"
        assert d["line"] == 10
        assert d["path"] == "tasks"

    def test_line_range_conversion(self) -> None:
        """LineRange proto converts to list."""
        proto = Violation(rule_id="L021")
        proto.line_range.CopyFrom(LineRange(start=5, end=10))
        d = violation_proto_to_dict(proto)
        assert d["line"] == [5, 10]

    def test_remediation_class_auto_fixable(self) -> None:
        """Proto AUTO_FIXABLE enum converts to string."""
        proto = Violation(
            rule_id="L021",
            remediation_class=common_pb2.REMEDIATION_CLASS_AUTO_FIXABLE,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_class"] == RemediationClass.AUTO_FIXABLE

    def test_remediation_class_ai_candidate(self) -> None:
        """Proto AI_CANDIDATE enum converts to string."""
        proto = Violation(
            rule_id="L021",
            remediation_class=common_pb2.REMEDIATION_CLASS_AI_CANDIDATE,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_class"] == RemediationClass.AI_CANDIDATE

    def test_remediation_class_manual_review(self) -> None:
        """Proto MANUAL_REVIEW enum converts to string."""
        proto = Violation(
            rule_id="L021",
            remediation_class=common_pb2.REMEDIATION_CLASS_MANUAL_REVIEW,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_class"] == RemediationClass.MANUAL_REVIEW

    def test_unspecified_remediation_class_defaults_to_ai(self) -> None:
        """Unspecified remediation_class defaults to AI_CANDIDATE."""
        proto = Violation(
            rule_id="L021",
            remediation_class=common_pb2.REMEDIATION_CLASS_UNSPECIFIED,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_class"] == RemediationClass.AI_CANDIDATE

    def test_resolution_transform_failed(self) -> None:
        """Proto TRANSFORM_FAILED resolution converts to string."""
        proto = Violation(
            rule_id="L021",
            remediation_resolution=common_pb2.REMEDIATION_RESOLUTION_TRANSFORM_FAILED,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_resolution"] == RemediationResolution.TRANSFORM_FAILED

    def test_resolution_oscillation(self) -> None:
        """Proto OSCILLATION resolution converts to string."""
        proto = Violation(
            rule_id="L021",
            remediation_resolution=common_pb2.REMEDIATION_RESOLUTION_OSCILLATION,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_resolution"] == RemediationResolution.OSCILLATION

    def test_unspecified_resolution_defaults_to_unresolved(self) -> None:
        """Unspecified remediation_resolution defaults to UNRESOLVED."""
        proto = Violation(
            rule_id="L021",
            remediation_resolution=common_pb2.REMEDIATION_RESOLUTION_UNSPECIFIED,
        )
        d = violation_proto_to_dict(proto)
        assert d["remediation_resolution"] == RemediationResolution.UNRESOLVED

    def test_resolution_all_ai_values(self) -> None:
        """All AI resolution proto enums convert to strings."""
        for proto_val, expected in [
            (common_pb2.REMEDIATION_RESOLUTION_AI_PROPOSED, RemediationResolution.AI_PROPOSED),
            (common_pb2.REMEDIATION_RESOLUTION_AI_FAILED, RemediationResolution.AI_FAILED),
            (common_pb2.REMEDIATION_RESOLUTION_AI_LOW_CONFIDENCE, RemediationResolution.AI_LOW_CONFIDENCE),
            (common_pb2.REMEDIATION_RESOLUTION_USER_REJECTED, RemediationResolution.USER_REJECTED),
        ]:
            proto = Violation(rule_id="L021", remediation_resolution=proto_val)
            d = violation_proto_to_dict(proto)
            assert d["remediation_resolution"] == expected


class TestRoundTrip:
    """Tests for round-trip conversion dict → proto → dict."""

    def test_round_trip_preserves_values(self) -> None:
        """Converting dict to proto and back preserves all fields."""
        original: ViolationDict = {
            "rule_id": "L021",
            "level": "high",
            "message": "Missing mode",
            "file": "playbook.yml",
            "line": 10,
            "path": "tasks",
            "remediation_class": RemediationClass.AUTO_FIXABLE,
            "remediation_resolution": RemediationResolution.UNRESOLVED,
        }
        proto = violation_dict_to_proto(original)
        result = violation_proto_to_dict(proto)
        assert result["rule_id"] == original["rule_id"]
        assert result["level"] == original["level"]
        assert result["message"] == original["message"]
        assert result["file"] == original["file"]
        assert result["line"] == original["line"]
        assert result["path"] == original["path"]
        assert result["remediation_class"] == original["remediation_class"]
        assert result["remediation_resolution"] == original["remediation_resolution"]

    def test_round_trip_line_range(self) -> None:
        """Line range round-trips correctly."""
        original: ViolationDict = {"rule_id": "L021", "line": [5, 10]}
        proto = violation_dict_to_proto(original)
        result = violation_proto_to_dict(proto)
        assert result["line"] == [5, 10]

    def test_round_trip_resolution(self) -> None:
        """All resolution values round-trip correctly."""
        for res in RemediationResolution:
            original: ViolationDict = {
                "rule_id": "L021",
                "remediation_resolution": res,
            }
            proto = violation_dict_to_proto(original)
            result = violation_proto_to_dict(proto)
            assert result["remediation_resolution"] == res, f"Failed for {res}"


class TestStringLineParsing:
    """Tests for string line format parsing in violation_dict_to_proto."""

    def test_string_line_range(self) -> None:
        """String line 'L15-19' is parsed as a LineRange."""
        v: ViolationDict = {"rule_id": "L007", "line": "L15-19"}
        proto = violation_dict_to_proto(v)
        assert proto.HasField("line_range")
        assert proto.line_range.start == 15
        assert proto.line_range.end == 19

    def test_string_single_line(self) -> None:
        """String line 'L42' is parsed as a single line number."""
        v: ViolationDict = {"rule_id": "L007", "line": "L42"}
        proto = violation_dict_to_proto(v)
        assert proto.line == 42

    def test_string_line_without_prefix(self) -> None:
        """String line '10-20' (no L prefix) is parsed as a range."""
        v: ViolationDict = {"rule_id": "L007", "line": "10-20"}
        proto = violation_dict_to_proto(v)
        assert proto.HasField("line_range")
        assert proto.line_range.start == 10
        assert proto.line_range.end == 20

    def test_invalid_string_line_ignored(self) -> None:
        """Non-numeric string line is silently ignored."""
        v: ViolationDict = {"rule_id": "L007", "line": "unknown"}
        proto = violation_dict_to_proto(v)
        assert proto.line == 0
        assert not proto.HasField("line_range")
