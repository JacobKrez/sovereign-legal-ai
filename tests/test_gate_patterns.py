"""Tests for the quality gate's own construction.

The gate in ``scripts/gate.py`` scans every text file in the repository,
including itself. That is only possible because the forbidden strings are
assembled from fragments at import time, so the contiguous token never
appears in the source.

These tests verify that property rather than assuming it. If someone later
"cleans up" a pattern by writing the literal string, the gate would start
matching its own source and fail every run. These tests fail first, with a
message that says what happened.

The same discipline applies to this file. It has to contain sample strings
that the gate would otherwise flag, so those are assembled from fragments
here too, and the tests below assert that the assembled forms are what the
patterns actually catch.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from scripts.gate import (
    AUTHOR_PATH_PREFIXES,
    BANNED_VOCABULARY,
    CASE_FILENAME_PATTERNS,
    CLIENT_DATA_PATTERNS,
    COMMERCIAL_PHRASES,
    EM_DASH,
    MATTER_CAPTION_PATTERN,
    PLACEHOLDER_TOKENS,
    REQUIRED_FILES,
    REQUIRED_NON_CLAIMS,
    REQUIRED_README_SECTIONS,
    SECRET_PATTERNS,
    _caption_findings,
)

REPO = Path(__file__).resolve().parents[1]
GATE_SOURCE = (REPO / "scripts" / "gate.py").read_text(encoding="utf-8")
SELF_SOURCE = Path(__file__).read_text(encoding="utf-8")

# Sample strings, assembled from fragments so that this file does not
# contain the tokens the gate forbids.
FAKE_PEM_HEADER = "-----BEGIN " + "RSA PRIVATE KEY-----"
FAKE_AWS_CREDENTIAL = "AKIA" + "IOSFODNN7EXAMPLE"
FAKE_PARTY_ROLE = "Plain" + "tiff: a name"
REAL_CAPTION = "Harlow v. " + "Ridgeline Freight"
PLACEHOLDER_CAPTION = "Sample Matter v. " + "Example Corp"
ONE_SIDED_CAPTION = "Example Corp v. " + "Harlow"


# ---------------------------------------------------------------------------
# The gate does not match its own source
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "label,pattern", SECRET_PATTERNS, ids=[p[0] for p in SECRET_PATTERNS]
)
def test_secret_patterns_do_not_match_the_gate_source(label, pattern):
    match = re.search(pattern, GATE_SOURCE, re.IGNORECASE)
    assert match is None, (
        f"the {label!r} pattern matches scripts/gate.py at offset "
        f"{match.start() if match else 0}; re-fragment it"
    )


@pytest.mark.parametrize(
    "label,pattern",
    CLIENT_DATA_PATTERNS,
    ids=[p[0] for p in CLIENT_DATA_PATTERNS],
)
def test_client_data_patterns_do_not_match_the_gate_source(label, pattern):
    match = re.search(pattern, GATE_SOURCE)
    assert match is None, (
        f"the {label!r} pattern matches scripts/gate.py at offset "
        f"{match.start() if match else 0}; re-fragment it"
    )


def test_author_path_prefixes_do_not_appear_in_the_gate_source():
    for prefix in AUTHOR_PATH_PREFIXES:
        assert prefix not in GATE_SOURCE, (
            f"the author-path prefix {prefix!r} appears literally in "
            f"scripts/gate.py; re-fragment it"
        )


def test_commercial_phrases_do_not_appear_in_the_gate_source():
    for phrase in COMMERCIAL_PHRASES:
        assert phrase not in GATE_SOURCE, (
            f"the commercial phrase {phrase!r} appears literally in "
            f"scripts/gate.py; re-fragment it"
        )


def test_banned_vocabulary_does_not_appear_in_the_gate_source():
    for word in BANNED_VOCABULARY:
        assert word not in GATE_SOURCE.lower(), (
            f"the banned term {word!r} appears literally in scripts/gate.py"
        )


def test_the_gate_source_has_no_matter_caption_shape():
    findings = _caption_findings(GATE_SOURCE)
    assert findings == [], (
        f"scripts/gate.py contains a caption-shaped string at offset "
        f"{findings[0][0] if findings else 0}"
    )


def test_the_gate_source_has_no_em_dash():
    assert EM_DASH not in GATE_SOURCE


def test_the_gate_source_has_no_dollar_figure():
    assert not re.search(r"[$€£]\s?\d", GATE_SOURCE)


def test_this_test_file_also_avoids_the_forbidden_tokens():
    """The same discipline applies to this file.

    A test file that quotes the forbidden strings would trip the content
    gates it is testing.
    """
    assert not re.search(r"[$€£]\s?\d", SELF_SOURCE)
    assert EM_DASH not in SELF_SOURCE
    assert _caption_findings(SELF_SOURCE) == []
    for label, pattern in CLIENT_DATA_PATTERNS:
        assert re.search(pattern, SELF_SOURCE) is None, (
            f"the {label!r} pattern matches this test file"
        )
    for label, pattern in SECRET_PATTERNS:
        assert re.search(pattern, SELF_SOURCE, re.IGNORECASE) is None, (
            f"the {label!r} pattern matches this test file"
        )


# ---------------------------------------------------------------------------
# The patterns behave as documented
# ---------------------------------------------------------------------------


def test_secret_pattern_catches_a_private_key_header():
    pattern = dict(SECRET_PATTERNS)["pem" + " header"]
    assert re.search(pattern, FAKE_PEM_HEADER) is not None


def test_secret_pattern_catches_an_aws_access_key():
    pattern = dict(SECRET_PATTERNS)["aws" + " access key"]
    assert re.search(pattern, FAKE_AWS_CREDENTIAL) is not None


def test_client_pattern_catches_a_capitalized_party_role():
    pattern = dict(CLIENT_DATA_PATTERNS)["party role: plain" + "tiff"]
    assert re.search(pattern, FAKE_PARTY_ROLE) is not None


def test_client_pattern_does_not_catch_a_schema_column_name():
    """Lowercase column names are schema, not data."""
    pattern = dict(CLIENT_DATA_PATTERNS)["party role: plain" + "tiff"]
    assert re.search(pattern, "plaintiff_name TEXT") is None


def test_placeholder_captions_are_exempt():
    assert _caption_findings(PLACEHOLDER_CAPTION) == []


def test_a_real_caption_is_a_finding():
    findings = _caption_findings(REAL_CAPTION)
    assert len(findings) == 1


def test_a_caption_with_one_placeholder_side_is_exempt():
    assert _caption_findings(ONE_SIDED_CAPTION) == []


def test_placeholder_vocabulary_is_closed_and_lowercase():
    assert all(token.islower() for token in PLACEHOLDER_TOKENS)
    assert "sample" in PLACEHOLDER_TOKENS


def test_commercial_phrase_list_is_lowercase():
    for phrase in COMMERCIAL_PHRASES:
        assert phrase == phrase.lower()


def test_banned_vocabulary_is_lowercase():
    for word in BANNED_VOCABULARY:
        assert word == word.lower()


# ---------------------------------------------------------------------------
# The gate's own configuration is coherent
# ---------------------------------------------------------------------------


def test_every_required_file_exists():
    for name in REQUIRED_FILES:
        assert (REPO / name).exists(), f"required file {name!r} is missing"


def test_required_readme_sections_are_unique():
    assert len(REQUIRED_README_SECTIONS) == len(set(REQUIRED_README_SECTIONS))


def test_required_non_claims_are_unique_and_lowercase():
    assert len(REQUIRED_NON_CLAIMS) == len(set(REQUIRED_NON_CLAIMS))
    for claim in REQUIRED_NON_CLAIMS:
        assert claim == claim.lower()


def test_case_filename_patterns_are_not_empty():
    assert CASE_FILENAME_PATTERNS
    for pattern in CASE_FILENAME_PATTERNS:
        assert re.compile(pattern)


def test_matter_caption_pattern_compiles():
    assert re.compile(MATTER_CAPTION_PATTERN)
