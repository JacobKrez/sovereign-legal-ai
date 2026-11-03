"""Tests for the field registry and the grounding validator.

The grounding tests are the substance of this repository. Everything else in
``src/extraction_schema.py`` is bookkeeping in service of the question "is
this field supported by the document it claims to come from".

The fixture-driven tests read ``tests/fixtures/grounding_cases.json`` so that
the expected verdicts live in data rather than in assertions scattered across
the file. That matters for the audit story in ``EVALUATION.md``: a third party
should be able to read one file and see exactly what the validator accepts and
rejects.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.extraction_schema import (
    EXTRACTION_PASSES,
    ORIGINS,
    PASS_FIELDS,
    REGISTRY,
    SUPPORT,
    WRITERS,
    Verdict,
    field_spec,
    fields_for_writer,
    is_supported,
    normalize_text,
    parse_decimal,
    parse_iso_date,
    validate_record,
    validate_update,
)

FIXTURES = Path(__file__).parent / "fixtures" / "grounding_cases.json"


def _load_cases() -> list[dict]:
    payload = json.loads(FIXTURES.read_text(encoding="utf-8"))
    return payload["cases"]


CASES = _load_cases()


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


def test_registry_is_not_empty():
    assert len(REGISTRY) > 0


def test_every_field_belongs_to_a_known_writer():
    for name, spec in REGISTRY.items():
        assert spec.writer in WRITERS, f"{name} has unknown writer {spec.writer!r}"


def test_every_field_has_a_known_origin():
    for name, spec in REGISTRY.items():
        assert spec.origin in ORIGINS, f"{name} has unknown origin {spec.origin!r}"


def test_writer_fields_partition_the_registry():
    """Every field is owned by exactly one writer, and the map agrees."""
    owned: list[str] = []
    for writer in WRITERS:
        owned.extend(fields_for_writer(writer))
    assert sorted(owned) == sorted(REGISTRY)
    assert len(owned) == len(set(owned)), "a field is owned by two writers"


def test_extraction_passes_each_own_at_least_one_field():
    for pass_name in EXTRACTION_PASSES:
        assert PASS_FIELDS[pass_name], f"{pass_name} owns no fields"


def test_required_fields_are_a_strict_subset_of_the_registry():
    required = {n for n, s in REGISTRY.items() if s.required}
    assert required
    assert required <= set(REGISTRY)


def test_enum_fields_all_declare_values():
    for name, spec in REGISTRY.items():
        if spec.type == "enum":
            assert spec.values, f"{name} is an enum with no values"
            assert len(set(spec.values)) == len(spec.values)


def test_field_spec_raises_on_unknown_field():
    with pytest.raises(KeyError):
        field_spec("no_such_field")


def test_registry_fields_have_descriptions():
    for name, spec in REGISTRY.items():
        assert spec.description.strip(), f"{name} has no description"


def test_model_fields_are_owned_by_an_extraction_pass():
    """A model-asserted field must be owned by a pass, not by a person.

    If a model field were owned by the workflow or the attorney, the
    grounding check would never be applied to it, because those writers
    are not required to cite a span.
    """
    for name, spec in REGISTRY.items():
        if spec.origin == "model":
            assert spec.writer in EXTRACTION_PASSES, (
                f"{name} is model-asserted but owned by {spec.writer!r}"
            )


def test_human_and_workflow_fields_are_not_owned_by_extraction_passes():
    for name, spec in REGISTRY.items():
        if spec.origin in ("human", "workflow"):
            assert spec.writer not in EXTRACTION_PASSES, (
                f"{name} is {spec.origin} but owned by an extraction pass"
            )


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------


def test_normalize_text_lowercases_and_collapses_whitespace():
    assert normalize_text("  Total   Charges\n") == "total charges"


def test_normalize_text_strips_accents():
    assert normalize_text("Payer Uberweisung") == "payer uberweisung"


def test_normalize_text_handles_none():
    assert normalize_text(None) == ""


def test_parse_iso_date_reads_iso():
    assert parse_iso_date("2026-01-14") == (2026, 1, 14)


def test_parse_iso_date_reads_us_order():
    assert parse_iso_date("01/14/2026") == (2026, 1, 14)


def test_parse_iso_date_reads_day_first_when_unambiguous():
    # 25 cannot be a month, so the ordering is decided.
    assert parse_iso_date("25/03/2026") == (2026, 3, 25)


def test_parse_iso_date_expands_two_digit_year():
    assert parse_iso_date("01/14/26") == (2026, 1, 14)


def test_parse_iso_date_returns_none_for_non_dates():
    assert parse_iso_date("not a date") is None
    assert parse_iso_date(None) is None


def test_parse_decimal_handles_separators_and_currency():
    assert parse_decimal("$4,820.50") == 4820.50
    assert parse_decimal(1200) == 1200.0
    assert parse_decimal("1200") == 1200.0


def test_parse_decimal_rejects_booleans():
    # bool is an int subclass, and a flag is not an amount.
    assert parse_decimal(True) is None


def test_parse_decimal_returns_none_for_garbage():
    assert parse_decimal("none") is None


# ---------------------------------------------------------------------------
# Grounding, fixture driven
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture_verdict(case):
    result = validate_update(
        case["field"],
        case["value"],
        source_span=case["source_span"],
        writer=case["writer"],
    )
    assert result.verdict == case["expected_verdict"], (
        f"{case['id']}: expected {case['expected_verdict']}, "
        f"got {result.verdict} ({result.message})"
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_fixture_reason(case):
    """The machine-readable reason must match the fixture exactly.

    The reason is a stable contract. The human-readable message is not,
    and the fixture does not pin it.
    """
    result = validate_update(
        case["field"],
        case["value"],
        source_span=case["source_span"],
        writer=case["writer"],
    )
    assert result.reason == case["expected_reason"], (
        f"{case['id']}: expected reason {case['expected_reason']!r}, "
        f"got {result.reason!r}"
    )


def test_fixture_ids_are_unique():
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))


def test_every_fixture_case_names_a_known_field_or_expects_unknown():
    for case in CASES:
        assert (
            case["field"] in REGISTRY
            or case["expected_verdict"] == Verdict.UNKNOWN_FIELD
        )


def test_every_fixture_reason_is_a_known_support_reason():
    for case in CASES:
        assert case["expected_reason"] in SUPPORT


def test_every_fixture_writer_is_known():
    for case in CASES:
        assert case["writer"] in WRITERS


# ---------------------------------------------------------------------------
# Grounding, explicit behavior
# ---------------------------------------------------------------------------


def test_value_in_span_is_supported():
    spec = field_spec("case_name")
    support = is_supported("Example Corp", "Example Corp, Case No. 1", spec)
    assert support.supported
    assert support.reason == "substring"


def test_value_not_in_span_is_not_supported():
    spec = field_spec("case_name")
    support = is_supported("Different Corp", "Example Corp, Case No. 1", spec)
    assert not support.supported
    assert support.reason == "not_found"


def test_reordered_words_are_supported_by_token_containment():
    spec = field_spec("cause_of_action")
    support = is_supported(
        "collision rear-end", "The incident was a rear-end collision.", spec
    )
    assert support.supported
    assert support.reason == "tokens"


def test_missing_span_is_not_supported():
    spec = field_spec("case_name")
    support = is_supported("Example Corp", None, spec)
    assert not support.supported
    assert support.reason == "no_span"


def test_blank_span_is_treated_as_missing_span():
    spec = field_spec("case_name")
    support = is_supported("Example Corp", "   ", spec)
    assert not support.supported
    assert support.reason == "no_span"


def test_null_value_is_not_a_claim():
    """A null value is absence, not hallucination."""
    spec = field_spec("case_name")
    support = is_supported(None, "anything at all", spec)
    assert support.supported
    assert support.reason == "absent"


def test_numeric_value_matches_number_in_span():
    spec = field_spec("billed_amount")
    support = is_supported(4820.50, "Total charges: $4,820.50", spec)
    assert support.supported
    assert support.reason == "numeric"


def test_numeric_value_not_in_span_is_unsupported():
    spec = field_spec("billed_amount")
    support = is_supported(9310.25, "Total charges: $4,820.50", spec)
    assert not support.supported


def test_numeric_value_ignores_formatting_difference():
    spec = field_spec("billed_amount")
    assert is_supported(4820.5, "Total: 4820.50", spec).supported
    assert is_supported(4820.5, "Total: 4,820.5", spec).supported


def test_date_value_matches_span_in_another_format():
    spec = field_spec("deadline_date")
    support = is_supported("2026-03-01", "Discovery shall close on 03/01/2026.", spec)
    assert support.supported
    assert support.reason == "date"


def test_date_value_not_in_span_is_unsupported():
    spec = field_spec("deadline_date")
    support = is_supported("2026-06-15", "Discovery shall close on 03/01/2026.", spec)
    assert not support.supported


def test_unparseable_date_is_unsupported_rather_than_guessed():
    spec = field_spec("deadline_date")
    support = is_supported("sometime soon", "Discovery shall close on 03/01/2026.", spec)
    assert not support.supported


def test_human_field_needs_no_span():
    spec = field_spec("attorney_notes")
    support = is_supported("Corrected by hand.", None, spec)
    assert support.supported
    assert support.reason == "human_attested"


def test_workflow_field_needs_no_span():
    spec = field_spec("status")
    support = is_supported("discovery", None, spec)
    assert support.supported
    assert support.reason == "workflow_computed"


def test_a_cited_span_is_still_checked_for_a_human_field():
    """If a person cites a document, the document has to say what they claim."""
    spec = field_spec("attorney_notes")
    support = is_supported("the total was understated", "Total charges: $1,200.00", spec)
    assert not support.supported


# ---------------------------------------------------------------------------
# Validation semantics
# ---------------------------------------------------------------------------


def test_unknown_field_is_rejected():
    result = validate_update("no_such_field", "x", source_span="some text")
    assert result.verdict == Verdict.UNKNOWN_FIELD
    assert not result.ok


def test_type_mismatch_is_rejected():
    result = validate_update(
        "billed_amount",
        "not a number",
        source_span="Total: 100",
        writer="financial_ledger",
    )
    assert result.verdict == Verdict.TYPE_MISMATCH
    assert result.reason == "wrong_type"


def test_numeric_string_is_accepted_for_a_numeric_field():
    result = validate_update(
        "billed_amount",
        "1200",
        source_span="Total: $1,200.00",
        writer="financial_ledger",
    )
    assert result.verdict == Verdict.OK


def test_boolean_is_rejected_for_a_numeric_field():
    result = validate_update(
        "billed_amount",
        True,
        source_span="Total: 1200",
        writer="financial_ledger",
    )
    assert result.verdict == Verdict.TYPE_MISMATCH


def test_writer_mismatch_is_rejected():
    result = validate_update(
        "billed_amount",
        100.0,
        source_span="Total: 100",
        writer="matter_identity",
    )
    assert result.verdict == Verdict.WRITER_MISMATCH


def test_required_field_with_empty_value_is_missing_not_unsupported():
    """A required field with no value is reported as missing, not hallucinated.

    The distinction matters: the extractor is not allowed to guess, and the
    validator must not report a refusal to guess as a false claim.
    """
    result = validate_update("case_name", None, source_span="anything")
    assert result.verdict == Verdict.MISSING


def test_optional_field_with_empty_value_is_ok():
    result = validate_update("case_number", "", source_span="anything")
    assert result.verdict == Verdict.OK
    assert result.supported
    assert result.reason == "absent"


def test_update_without_a_span_is_no_span_not_unsupported():
    result = validate_update("case_name", "Example Corp")
    assert result.verdict == Verdict.NO_SPAN
    assert not result.supported


def test_ok_result_carries_the_support_reason():
    result = validate_update("case_number", "24-CV-0001", source_span="Case No. 24-CV-0001")
    assert result.verdict == Verdict.OK
    assert result.reason == "substring"
    assert result.ok


# ---------------------------------------------------------------------------
# Record-level validation and the hallucinated-field rate
# ---------------------------------------------------------------------------


def test_validate_record_reports_per_field_results():
    record = validate_record(
        [
            {
                "field": "case_number",
                "value": "24-CV-0001",
                "source_span": "Case No. 24-CV-0001",
            },
            {
                "field": "venue",
                "value": "Nowhere Court",
                "source_span": "Case No. 24-CV-0001",
            },
        ]
    )
    assert len(record.results) == 2
    assert not record.ok
    assert len(record.unsupported) == 1
    assert record.unsupported[0].field == "venue"


def test_hallucinated_field_rate_counts_only_model_asserted_fields():
    record = validate_record(
        [
            {
                "field": "case_number",
                "value": "24-CV-0001",
                "source_span": "Case No. 24-CV-0001",
            },
            {
                "field": "case_number",
                "value": "99-CV-0002",
                "source_span": "Case No. 24-CV-0001",
            },
            {"field": "injury_details", "value": None, "source_span": None},
        ]
    )
    # Two model-asserted fields, one of them unsupported.
    assert record.hallucinated_field_rate == pytest.approx(0.5)


def test_human_and_workflow_fields_are_excluded_from_the_rate():
    """A person typing a note is not a model hallucination.

    Counting these in the denominator would understate the rate, which is
    the metric a law firm would actually act on.
    """
    record = validate_record(
        [
            {
                "field": "case_number",
                "value": "24-CV-0001",
                "source_span": "Case No. 24-CV-0001",
            },
            {
                "field": "attorney_notes",
                "value": "Hand-corrected.",
                "source_span": None,
                "writer": "attorney_review",
            },
            {"field": "status", "value": "discovery", "source_span": None},
        ]
    )
    assert len(record.asserted) == 1
    assert record.hallucinated_field_rate == 0.0


def test_hallucinated_field_rate_is_zero_for_a_clean_record():
    record = validate_record(
        [
            {
                "field": "case_number",
                "value": "24-CV-0001",
                "source_span": "Case No. 24-CV-0001",
            },
            {
                "field": "venue",
                "value": "Superior Court of Ex State",
                "source_span": "IN THE SUPERIOR COURT OF EX STATE",
            },
        ]
    )
    assert record.hallucinated_field_rate == 0.0


def test_hallucinated_field_rate_is_zero_for_an_empty_record():
    """No asserted fields means no hallucinations, not a division by zero."""
    assert validate_record([]).hallucinated_field_rate == 0.0


def test_update_with_no_field_key_is_reported():
    record = validate_record([{"value": "x", "source_span": "y"}])
    assert record.results[0].verdict == Verdict.UNKNOWN_FIELD


def test_record_invalid_separates_structural_failures_from_grounding_failures():
    record = validate_record(
        [
            {"field": "no_such_field", "value": "x", "source_span": "y"},
            {
                "field": "case_number",
                "value": "99-CV-0002",
                "source_span": "Case No. 24-CV-0001",
            },
        ]
    )
    assert len(record.invalid) == 1
    assert len(record.unsupported) == 1


def test_by_field_groups_results():
    record = validate_record(
        [
            {"field": "case_number", "value": "a", "source_span": "a"},
            {"field": "case_number", "value": "b", "source_span": "b"},
        ]
    )
    assert list(record.by_field()) == ["case_number"]
    assert len(record.by_field()["case_number"]) == 2


def test_record_rate_matches_a_hand_computed_count():
    updates = [
        {
            "field": "case_number",
            "value": "24-CV-0001",
            "source_span": "Case No. 24-CV-0001",
        },
        {
            "field": "venue",
            "value": "Nowhere",
            "source_span": "IN THE SUPERIOR COURT OF EX STATE",
        },
        {"field": "case_name", "value": "A v. B", "source_span": None},
        {
            "field": "cause_of_action",
            "value": "rear-end collision",
            "source_span": "rear-end collision",
        },
    ]
    record = validate_record(updates)
    assert len(record.unsupported) == 2
    assert record.hallucinated_field_rate == pytest.approx(2 / 4)


def test_fixture_corpus_has_both_supported_and_unsupported_cases():
    """The corpus must exercise both branches or it proves nothing."""
    verdicts = {c["expected_verdict"] for c in CASES}
    assert "OK" in verdicts
    assert "UNSUPPORTED" in verdicts
    assert "NO_SPAN" in verdicts
    assert "TYPE_MISMATCH" in verdicts
