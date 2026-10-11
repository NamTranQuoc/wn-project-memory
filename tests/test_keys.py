import pytest

from src.services.namespace_service import (
    KeyRuleError,
    build_key,
    key_matches,
    parse_template,
    slugify,
    validate_namespace_name,
)

NS = "decision.api"
TEMPLATE = "decision.api.{service}.{topic}"


class TestSlugify:
    def test_words(self) -> None:
        assert slugify("Idempotency Key") == "idempotency-key"

    def test_vietnamese_marks_fold(self) -> None:
        assert slugify("Đăng nhập bằng SSO") == "dang-nhap-bang-sso"

    def test_symbols_collapse(self) -> None:
        assert slugify("  POST /charge -- v2 ") == "post-charge-v2"


class TestTemplate:
    def test_valid(self) -> None:
        assert parse_template(NS, TEMPLATE) == [None, None]

    def test_literal_segment(self) -> None:
        assert parse_template("rule", "rule.code.{area}") == ["code", None]

    def test_must_start_with_namespace(self) -> None:
        with pytest.raises(KeyRuleError):
            parse_template(NS, "decision.{topic}")

    def test_needs_a_slot(self) -> None:
        with pytest.raises(KeyRuleError):
            parse_template(NS, "decision.api.fixed")

    def test_bad_namespace(self) -> None:
        with pytest.raises(KeyRuleError):
            validate_namespace_name("Decision API")


class TestBuildAndMatch:
    def test_same_parts_same_key(self) -> None:
        parts = {"service": "Payment", "topic": "Idempotency Key"}
        assert build_key(NS, TEMPLATE, parts) == "decision.api.payment.idempotency-key"
        assert build_key(NS, TEMPLATE, dict(parts)) == build_key(NS, TEMPLATE, parts)

    def test_missing_part(self) -> None:
        with pytest.raises(KeyRuleError, match="missing parts"):
            build_key(NS, TEMPLATE, {"service": "payment"})

    def test_match(self) -> None:
        assert key_matches(NS, TEMPLATE, "decision.api.payment.idempotency-key")

    def test_wrong_segment_count(self) -> None:
        assert not key_matches(NS, TEMPLATE, "decision.api.payment")
        assert not key_matches(NS, TEMPLATE, "decision.api.payment.a.b")

    def test_wrong_literal(self) -> None:
        assert not key_matches("rule", "rule.code.{area}", "rule.docs.naming")

    def test_unslugged_part_rejected(self) -> None:
        assert not key_matches(NS, TEMPLATE, "decision.api.Payment.topic")
