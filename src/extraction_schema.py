"""Field registry and grounding validation for the Sovereign case record.

This module is the executable version of the case-centric data model
described in ``ARCHITECTURE.md`` section 7. It has three jobs:

1. Hold the field registry: every field the system may write, with its
   type, whether it is required or optional, where its value comes from,
   and which writer owns it.
2. Hold the writer-to-field mapping: which of the extraction passes is
   permitted to write which fields.
3. Provide a pure validator for a proposed field update.

The validator is what makes the hallucinated-field rate in ``EVALUATION.md``
(M3) computable at all. A field written to the case record is *supported* only
if the source span it cites actually contains it. A field that cites no span is
not supported either: in an air-gapped deployment there is no external
monitoring pass that could notice an unsupported field later, so the check has
to happen at write time.

Everything here is pure and deterministic. No I/O, no network, no clock, no
model. Given the same inputs it returns the same result, which is what makes
the validator usable as the gate on a measurement.

Field types
-----------

``str``
    Free text. Dates and identifiers are carried as strings because the
    source formats vary and the record preserves what the document said.
``int`` / ``float``
    Numeric. ``billed_amount`` and friends.
``bool``
    Flag.
``enum``
    One of a fixed set of values, drawn from the schema constraints in
    ``ARCHITECTURE.md`` section 7. Panel types and status values are the
    two places the database itself constrains the value space.

Field origins
-------------

The origin of a field decides whether the grounding check applies to it,
and it is the distinction that keeps the M3 denominator honest.

``model``
    Asserted by an extraction pass from a document. These fields must be
    supported by the span they cite, or the update is rejected.
``human``
    Entered by a person, for example an attorney correcting a figure. A
    human attestation is not a model claim and is not counted as a
    hallucination. If a person cites a span, the span is still checked.
``workflow``
    Computed by the application from other fields, for example the
    aggregate value summed from the ledger rows. A computed value is not
    a model claim.

Getting this wrong in either direction is a mistake worth naming. Counting
human and workflow fields in the denominator would understate the
hallucination rate by including fields that were never model-asserted.
Excluding model fields would overstate it. The rate uses model-asserted
fields only.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "FieldSpec",
    "ORIGINS",
    "EXTRACTION_PASSES",
    "WRITERS",
    "PASS_FIELDS",
    "REGISTRY",
    "SUPPORT",
    "Verdict",
    "ValidationResult",
    "RecordValidation",
    "normalize_text",
    "parse_iso_date",
    "parse_decimal",
    "validate_update",
    "validate_record",
    "is_supported",
    "field_spec",
    "fields_for_writer",
]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FieldSpec:
    """One field in the case record.

    Attributes
    ----------
    name:
        Column name in the case record.
    type:
        One of ``str``, ``int``, ``float``, ``bool``, ``enum``.
    required:
        Whether the field must be present for a record to be considered
        complete. Required does not mean the extraction must guess it: a
        required field with no support in the source is left empty, and
        ``validate_update`` returns ``MISSING`` rather than inventing a
        value.
    origin:
        ``model``, ``human``, or ``workflow``. Decides whether the
        grounding check applies.
    writer:
        Which writer owns the field: one of the three extraction passes,
        ``workflow``, or ``attorney_review``.
    description:
        Short human-readable meaning, used in error messages.
    values:
        Allowed values, for ``enum`` fields only.
    """

    name: str
    type: str
    required: bool
    origin: str
    writer: str
    description: str
    values: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.type == "enum" and not self.values:
            raise ValueError(f"enum field {self.name!r} has no allowed values")
        if self.origin not in ORIGINS:
            raise ValueError(
                f"field {self.name!r} has unknown origin {self.origin!r}"
            )
        if self.writer not in WRITERS:
            raise ValueError(
                f"field {self.name!r} has unknown writer {self.writer!r}"
            )


#: Where a field's value comes from.
ORIGINS: tuple[str, ...] = ("model", "human", "workflow")

#: The three focused extraction passes, in order.
EXTRACTION_PASSES: tuple[str, ...] = (
    "matter_identity",
    "financial_ledger",
    "deadlines",
)

#: Writers that may write to the record: the extraction passes, plus the
#: application and the attorney review step.
WRITERS: tuple[str, ...] = EXTRACTION_PASSES + ("workflow", "attorney_review")


def _spec(
    name: str,
    type_: str,
    required: bool,
    origin: str,
    writer: str,
    description: str,
    values: Sequence[str] = (),
) -> FieldSpec:
    return FieldSpec(
        name=name,
        type=type_,
        required=required,
        origin=origin,
        writer=writer,
        description=description,
        values=tuple(values),
    )


#: Every field that may be written to the case record, keyed by column name.
#:
#: The grouping follows ``ARCHITECTURE.md`` section 7: matter identity,
#: matter state, ledger entry, deadline.
REGISTRY: Mapping[str, FieldSpec] = {
    spec.name: spec
    for spec in (
        # ---- matter identity, asserted by the model ------------------------
        _spec("case_name", "str", True, "model", "matter_identity",
              "Matter caption as stated in the source."),
        _spec("case_number", "str", False, "model", "matter_identity",
              "Docket number as stated in the source."),
        _spec("venue", "str", False, "model", "matter_identity",
              "Court or venue named in the source."),
        _spec("incident_date", "str", False, "model", "matter_identity",
              "Date of the incident as stated in the source."),
        _spec("cause_of_action", "str", False, "model", "matter_identity",
              "Claim as characterized in the source."),
        _spec("injury_details", "str", False, "model", "matter_identity",
              "Description of the injury as stated in the source."),
        # ---- matter state, computed by the application ---------------------
        _spec("status", "enum", True, "workflow", "workflow",
              "Lifecycle stage of the matter.",
              ("intake", "discovery", "mediation", "trial", "settled")),
        _spec("total_value", "float", False, "workflow", "workflow",
              "Aggregate value computed from the ledger rows."),
        _spec("next_deadline", "str", False, "workflow", "workflow",
              "Nearest upcoming deadline, computed from the deadline rows."),
        _spec("discovery_counts", "str", False, "workflow", "workflow",
              "JSON counters for interrogatories, document requests, RFAs."),
        _spec("document_count", "int", False, "workflow", "workflow",
              "Number of documents in the matter."),
        _spec("total_pages", "int", False, "workflow", "workflow",
              "Total pages across the matter's documents."),
        # ---- ledger entry --------------------------------------------------
        _spec("provider_name", "str", True, "model", "financial_ledger",
              "Treating provider named in the source."),
        _spec("service_date", "str", False, "model", "financial_ledger",
              "Date of service as stated in the source."),
        _spec("billed_amount", "float", False, "model", "financial_ledger",
              "Amount billed as stated in the source."),
        _spec("insurance_paid", "float", False, "model", "financial_ledger",
              "Amount paid by the insurer as stated in the source."),
        _spec("patient_balance", "float", False, "model", "financial_ledger",
              "Balance attributed to the patient as stated in the source."),
        _spec("source_document", "str", True, "model", "financial_ledger",
              "Document the row was extracted from."),
        _spec("attorney_override_amount", "float", False, "human",
              "attorney_review",
              "Amount set by a person, not by the extraction pass."),
        _spec("attorney_notes", "str", False, "human", "attorney_review",
              "Note written by a person, not by the extraction pass."),
        # ---- deadline ------------------------------------------------------
        _spec("deadline_type", "enum", True, "model", "deadlines",
              "Kind of deadline.",
              ("discovery", "motion", "hearing", "trial", "statute")),
        _spec("deadline_date", "str", True, "model", "deadlines",
              "Date of the deadline as stated in the source."),
        _spec("description", "str", False, "model", "deadlines",
              "Description of the deadline as stated in the source."),
        _spec("rule_basis", "str", False, "model", "deadlines",
              "The provision the date derives from."),
    )
}

#: Which fields each writer owns.
PASS_FIELDS: Mapping[str, tuple[str, ...]] = {
    writer: tuple(
        name for name, spec in REGISTRY.items() if spec.writer == writer
    )
    for writer in WRITERS
}


def field_spec(name: str) -> FieldSpec:
    """Return the spec for ``name``.

    Raises
    ------
    KeyError
        If the field is not in the registry. Callers that want a softer
        failure should use :func:`validate_update` instead.
    """
    return REGISTRY[name]


def fields_for_writer(writer: str) -> tuple[str, ...]:
    """Return the field names owned by ``writer``."""
    return PASS_FIELDS[writer]


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

_WHITESPACE = re.compile(r"\s+")
_NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def normalize_text(value: Any) -> str:
    """Normalize a string for comparison.

    Lowercases, strips accents, collapses whitespace, and removes
    punctuation that carries no identifying content. Two strings that
    differ only in that punctuation are the same string for grounding
    purposes: ``"Total: $1,200."`` and ``"total $1200"`` should both match
    a span that contains the amount.

    This normalization is deliberately aggressive. It increases the number
    of spans that will match, which raises the risk of a false match. The
    trade is stated in ``EVALUATION.md`` M3: the metric measures unsupported
    fields, and aggressive normalization trades a small amount of
    sensitivity for a large amount of specificity.
    """
    if value is None:
        return ""
    text = str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower()
    text = re.sub(r"[^\w\s\.\-/]", " ", text)
    text = _WHITESPACE.sub(" ", text)
    return text.strip()


def parse_iso_date(value: Any) -> tuple[int, int, int] | None:
    """Parse a date into a ``(year, month, day)`` tuple, or return ``None``.

    Handles the forms that actually appear in the source documents this
    pipeline handles: ISO, US ordering with slashes or dashes, and
    day-first ordering. Returns ``None`` when the string cannot be read
    as a date, which the caller reports as a value error rather than
    guessing.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()

    m = re.fullmatch(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", text)
    if m:
        return int(m.group(1)), int(m.group(2)), int(m.group(3))

    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})", text)
    if m:
        a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
        if year < 100:
            year += 2000
        # A value with a first component above 12 is unambiguous: it is
        # the day. Otherwise treat it as month-first, which is what the
        # intake population uses.
        if a > 12:
            return year, b, a
        return year, a, b

    return None


def parse_decimal(value: Any) -> float | None:
    """Parse a numeric value, or return ``None``.

    Accepts a number or a string containing a number, with or without
    thousands separators and with an optional currency prefix.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    m = _NUMBER.search(value.replace("$", ""))
    if not m:
        return None
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Grounding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Support:
    """The result of checking a value against a source span.

    Attributes
    ----------
    supported:
        Whether the value was found in the span.
    reason:
        A short machine-readable explanation, one of the keys in
        :data:`SUPPORT`.
    """

    supported: bool
    reason: str

    def __bool__(self) -> bool:  # pragma: no cover - convenience
        return self.supported


#: The machine-readable support reasons.
SUPPORT = {
    "substring": "value appears as a contiguous string in the cited span",
    "tokens": "every content word in the value appears in the cited span",
    "numeric": "numeric value matches a number in the cited span",
    "date": "date matches a date in the cited span",
    "absent": "the field carries no value, which is absence rather than a claim",
    "no_span": "the update cites no source span",
    "not_found": "the value does not appear in the cited span",
    "human_attested": "value was entered by a person, not asserted by the model",
    "workflow_computed": "value was computed by the application, not asserted",
    "wrong_field": "the field is unknown, or owned by a different writer",
    "wrong_type": "the value does not match the field's declared type",
}


def _numbers_in(text: str) -> list[float]:
    out: list[float] = []
    for m in _NUMBER.finditer(text.replace("$", "")):
        try:
            out.append(float(m.group(0).replace(",", "")))
        except ValueError:
            continue
    return out


def _dates_in(text: str) -> list[tuple[int, int, int]]:
    out: list[tuple[int, int, int]] = []
    for pattern in (
        r"\b(\d{4})[-/](\d{1,2})[-/](\d{1,2})\b",
        r"\b(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})\b",
    ):
        for m in re.finditer(pattern, text):
            if len(m.group(1)) == 4:
                out.append((int(m.group(1)), int(m.group(2)), int(m.group(3))))
            else:
                a, b, year = int(m.group(1)), int(m.group(2)), int(m.group(3))
                if year < 100:
                    year += 2000
                if a > 12:
                    out.append((year, b, a))
                else:
                    out.append((year, a, b))
    return out


def _content_tokens(text: str) -> list[str]:
    """Split normalized text into comparable content tokens.

    Strips punctuation that ``normalize_text`` deliberately keeps inside a
    token, such as the trailing period in ``"collision."``. Without this,
    a value that matches the end of a sentence would fail the token
    comparison for a purely mechanical reason.
    """
    tokens: list[str] = []
    for raw in text.split():
        token = raw.strip(".,;:!?")
        if len(token) > 2:
            tokens.append(token)
    return tokens


def _string_supported(value: str, span: str) -> Support:
    """Check a string value against a span."""
    normalized_value = normalize_text(value)
    normalized_span = normalize_text(span)

    if not normalized_value:
        # An empty value is not a claim. It is neither supported nor
        # unsupported; callers treat it as absent.
        return Support(True, "absent")

    if normalized_value in normalized_span:
        return Support(True, "substring")

    # Token-level containment. The value's content words must all appear
    # in the span. This catches the common case where the extraction
    # reorders a phrase or drops a connector word.
    value_tokens = _content_tokens(normalized_value)
    span_tokens = set(_content_tokens(normalized_span))
    if value_tokens and all(tok in span_tokens for tok in value_tokens):
        return Support(True, "tokens")

    return Support(False, "not_found")


def is_supported(value: Any, span: str | None, spec: FieldSpec) -> Support:
    """Check whether ``value`` is supported by ``span``.

    For a ``model`` field, the value must be found in the cited span. For a
    ``human`` or ``workflow`` field, a missing span is acceptable because
    the value was not asserted by the model; if a span is cited anyway it is
    checked the same way.

    Parameters
    ----------
    value:
        The proposed field value.
    span:
        The cited source span, or ``None`` if the update cites none.
    spec:
        The field's spec, which determines the comparison rule.

    Returns
    -------
    Support
        Whether the value is supported, and why.
    """
    if value is None:
        # Absence is not a claim. An empty field is not a hallucination.
        return Support(True, "absent")

    if spec.origin != "model":
        if span is None or not str(span).strip():
            reason = (
                "human_attested"
                if spec.origin == "human"
                else "workflow_computed"
            )
            return Support(True, reason)
        # A span was cited, so check it. A person who cites a document
        # should mean it.
        return _grounded(value, span, spec)

    if span is None or not str(span).strip():
        return Support(False, "no_span")

    return _grounded(value, span, spec)


def _grounded(value: Any, span: str, spec: FieldSpec) -> Support:
    """Compare a value to a span according to the field's type."""
    if spec.type in ("int", "float"):
        number = parse_decimal(value)
        if number is None:
            return Support(False, "not_found")
        for candidate in _numbers_in(span):
            if abs(candidate - number) < 0.005:
                return Support(True, "numeric")
        return Support(False, "not_found")

    if spec.name in ("incident_date", "service_date", "deadline_date"):
        parsed = parse_iso_date(str(value))
        if parsed is None:
            return Support(False, "not_found")
        for candidate in _dates_in(span):
            if candidate == parsed:
                return Support(True, "date")
        return Support(False, "not_found")

    return _string_supported(str(value), span)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


class Verdict:
    """Outcome codes.

    ``OK``
        The update is well-formed and supported.
    ``UNKNOWN_FIELD``
        The field is not in the registry.
    ``WRITER_MISMATCH``
        The field is owned by a different writer than the one that
        produced the update.
    ``TYPE_MISMATCH``
        The value does not match the field's declared type.
    ``MISSING``
        A required field was given a null or empty value.
    ``UNSUPPORTED``
        The value is not found in the cited span.
    ``NO_SPAN``
        The update cites no source span, so support cannot be established.
    """

    OK = "OK"
    UNKNOWN_FIELD = "UNKNOWN_FIELD"
    WRITER_MISMATCH = "WRITER_MISMATCH"
    TYPE_MISMATCH = "TYPE_MISMATCH"
    MISSING = "MISSING"
    UNSUPPORTED = "UNSUPPORTED"
    NO_SPAN = "NO_SPAN"


@dataclass(frozen=True)
class ValidationResult:
    """The result of validating one proposed field update.

    Attributes
    ----------
    field:
        The column that was written.
    value:
        The proposed value.
    verdict:
        One of the :class:`Verdict` codes.
    reason:
        The machine-readable support reason, one of the keys in
        :data:`SUPPORT`. This is the field to compare against when
        checking behavior, because it is stable and does not change when
        the wording of an error message changes.
    message:
        A human-readable explanation. Not part of the contract.
    supported:
        Whether the value was established as supported.
    source_span:
        The span the update cited.
    """

    field: str
    value: Any
    verdict: str
    reason: str = ""
    message: str = ""
    supported: bool = False
    source_span: str | None = None
    origin: str = ""

    @property
    def ok(self) -> bool:
        """True when the update is well-formed and supported."""
        return self.verdict == Verdict.OK

    @property
    def asserted(self) -> bool:
        """True when the update makes a claim the model produced.

        Only model-asserted fields count toward the hallucinated-field
        rate. A field a person entered, or one the application computed,
        is not a model claim.
        """
        if self.origin != "model":
            return False
        if self.value is None:
            return False
        if isinstance(self.value, str) and not self.value.strip():
            return False
        return True

    def __str__(self) -> str:  # pragma: no cover - convenience
        return f"{self.field}: {self.verdict}"


def _type_ok(value: Any, spec: FieldSpec) -> bool:
    """Type check a value against its spec.

    Numeric fields accept a numeric string, because the extraction passes
    emit amounts as text often enough that rejecting them would reject
    correct extractions. A numeric string that does not parse as a number
    is a type mismatch.
    """
    if spec.type == "str":
        return isinstance(value, str)
    if spec.type == "int":
        if isinstance(value, bool):
            return False
        if isinstance(value, int):
            return True
        if isinstance(value, float):
            return value.is_integer()
        if isinstance(value, str):
            parsed = parse_decimal(value)
            return parsed is not None and parsed.is_integer()
        return False
    if spec.type == "float":
        if isinstance(value, bool):
            return False
        if isinstance(value, (int, float)):
            return True
        if isinstance(value, str):
            return parse_decimal(value) is not None
        return False
    if spec.type == "bool":
        return isinstance(value, bool)
    if spec.type == "enum":
        return isinstance(value, str) and value in spec.values
    return False


def validate_update(
    field_name: str,
    value: Any,
    *,
    source_span: str | None = None,
    writer: str | None = None,
    spec: FieldSpec | None = None,
) -> ValidationResult:
    """Validate one proposed field update.

    This is the whole grounding check. It answers three questions in
    order:

    1. Is this a field we recognize?
    2. Is the value the right shape for that field, and did the writer
       that produced it own the field?
    3. Is the value supported by the span it cites?

    The third question is the one that matters for legal risk. A field
    whose value is not in its cited span is a claim the document does not
    make, and in an enclave with no external monitoring it will not be
    caught later.

    Parameters
    ----------
    field_name:
        The column being written.
    value:
        The proposed value.
    source_span:
        The text the extraction cited as its basis. ``None`` means the
        update cited nothing, which is reported as ``NO_SPAN`` for a
        model-asserted field rather than silently accepted.
    writer:
        The writer that produced the update: one of the extraction pass
        names, ``workflow``, or ``attorney_review``. When given, a field
        owned by a different writer is rejected.
    spec:
        A spec to use instead of the registry lookup. Useful for tests.

    Returns
    -------
    ValidationResult
    """
    resolved = spec if spec is not None else REGISTRY.get(field_name)
    if resolved is None:
        return ValidationResult(
            field=field_name,
            value=value,
            verdict=Verdict.UNKNOWN_FIELD,
            reason="wrong_field",
            message=f"{field_name!r} is not a field in the case record",
            source_span=source_span,
        )

    if writer is not None and resolved.writer != writer:
        return ValidationResult(
            field=field_name,
            value=value,
            verdict=Verdict.WRITER_MISMATCH,
            reason="wrong_field",
            message=(
                f"{field_name!r} belongs to the {resolved.writer!r} writer, "
                f"not {writer!r}"
            ),
            source_span=source_span,
            origin=resolved.origin,
        )

    is_empty = value is None or (isinstance(value, str) and not value.strip())

    if is_empty:
        if resolved.required:
            return ValidationResult(
                field=field_name,
                value=value,
                verdict=Verdict.MISSING,
                reason="absent",
                message=(
                    f"{field_name!r} is required but the update carries no "
                    f"value; leave it unset rather than guessing"
                ),
                source_span=source_span,
                origin=resolved.origin,
            )
        # An optional field left empty is not a claim and not a
        # hallucination.
        return ValidationResult(
            field=field_name,
            value=value,
            verdict=Verdict.OK,
            reason="absent",
            message="optional field left empty",
            supported=True,
            source_span=source_span,
            origin=resolved.origin,
        )

    if not _type_ok(value, resolved):
        return ValidationResult(
            field=field_name,
            value=value,
            verdict=Verdict.TYPE_MISMATCH,
            reason="wrong_type",
            message=(
                f"{field_name!r} expects {resolved.type}, got "
                f"{type(value).__name__}"
            ),
            source_span=source_span,
            origin=resolved.origin,
        )

    support = is_supported(value, source_span, resolved)
    if not support.supported:
        verdict = (
            Verdict.NO_SPAN if support.reason == "no_span" else Verdict.UNSUPPORTED
        )
        if verdict == Verdict.NO_SPAN:
            message = (
                f"{field_name!r} cites no source span, so its support "
                f"cannot be established"
            )
        else:
            message = f"{field_name!r} value is not present in the cited span"
        return ValidationResult(
            field=field_name,
            value=value,
            verdict=verdict,
            reason=support.reason,
            message=message,
            supported=False,
            source_span=source_span,
            origin=resolved.origin,
        )

    return ValidationResult(
        field=field_name,
        value=value,
        verdict=Verdict.OK,
        reason=support.reason,
        message=support.reason,
        supported=True,
        source_span=source_span,
        origin=resolved.origin,
    )


@dataclass
class RecordValidation:
    """The result of validating a whole record update."""

    results: list[ValidationResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(r.ok for r in self.results)

    @property
    def unsupported(self) -> list[ValidationResult]:
        """Model-asserted updates that failed the grounding check."""
        return [
            r for r in self.results
            if r.verdict in (Verdict.UNSUPPORTED, Verdict.NO_SPAN)
        ]

    @property
    def invalid(self) -> list[ValidationResult]:
        """Updates that failed for a reason other than grounding."""
        return [
            r for r in self.results
            if r.verdict
            in (
                Verdict.UNKNOWN_FIELD,
                Verdict.WRITER_MISMATCH,
                Verdict.TYPE_MISMATCH,
                Verdict.MISSING,
            )
        ]

    @property
    def asserted(self) -> list[ValidationResult]:
        """The model-asserted updates in this record."""
        return [r for r in self.results if r.asserted]

    @property
    def hallucinated_field_rate(self) -> float:
        """The fraction of model-asserted fields that are unsupported.

        This is M3 from ``EVALUATION.md``. The denominator is
        model-asserted fields: fields a person entered and fields the
        application computed are not model claims, and including them
        would understate the rate. An empty or null value is not an
        assertion and is excluded from both numerator and denominator.
        """
        asserted = self.asserted
        if not asserted:
            return 0.0
        unsupported_ids = {id(r) for r in self.unsupported}
        hits = sum(1 for r in asserted if id(r) in unsupported_ids)
        return hits / len(asserted)

    def by_field(self) -> dict[str, list[ValidationResult]]:
        out: dict[str, list[ValidationResult]] = {}
        for r in self.results:
            out.setdefault(r.field, []).append(r)
        return out


def validate_record(
    updates: Iterable[Mapping[str, Any]],
    *,
    writer: str | None = None,
) -> RecordValidation:
    """Validate a set of proposed field updates.

    Parameters
    ----------
    updates:
        An iterable of mappings, each with a ``field`` key, a ``value``
        key, and optionally a ``source_span`` key.
    writer:
        The writer that produced these updates, applied to all of them.

    Returns
    -------
    RecordValidation
        With per-update results and the computed hallucinated-field
        rate.
    """
    record = RecordValidation()
    for update in updates:
        name = update.get("field")
        if name is None:
            record.results.append(
                ValidationResult(
                    field="<unnamed>",
                    value=update.get("value"),
                    verdict=Verdict.UNKNOWN_FIELD,
                    reason="wrong_field",
                    message="update has no 'field' key",
                )
            )
            continue
        record.results.append(
            validate_update(
                str(name),
                update.get("value"),
                source_span=update.get("source_span"),
                writer=writer,
            )
        )
    return record
