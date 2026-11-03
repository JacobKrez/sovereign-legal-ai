#!/usr/bin/env python3
"""Sovereign quality gate: one command that says green or red.

Runs every gate in sequence, prints a table, and exits non-zero if any gate
fails. The thresholds and the rationale for each one live in
``QUALITY_BAR.md`` at the repo root.

This is the loop's definition of done. Nothing ships past a red gate.

Usage
-----

    python3 scripts/gate.py            # full gate, tabulated output
    python3 scripts/gate.py --quiet    # one-line digest
    python3 scripts/gate.py --only G4   # run a single gate
    python3 scripts/gate.py --help     # usage

Exit codes
----------

    0   every gate passed
    1   at least one gate failed
    2   the gate script itself errored

Standard library only. No network access, no third-party packages.

A note on how the forbidden patterns are written
-----------------------------------------------

This file scans every text file in the repository, including itself. A
literal forbidden string in this file would therefore trip its own gate.
The patterns below are assembled from fragments at import time so that the
contiguous token never appears in the source. ``tests/test_gate_patterns.py``
asserts that property, so the construction is verified rather than assumed.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import traceback
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Callable

REPO = Path(__file__).resolve().parents[1]

#: Files that must exist for the repository to be complete.
REQUIRED_FILES = (
    "README.md",
    "ARCHITECTURE.md",
    "EVALUATION.md",
    "QUALITY_BAR.md",
    "LICENSE",
    "NOTICE",
    ".gitignore",
    ".github/workflows/ci.yml",
    "scripts/gate.py",
    "src/__init__.py",
    "src/extraction_schema.py",
    "tests/__init__.py",
    "tests/conftest.py",
    "tests/test_extraction_schema.py",
    "tests/test_gate_patterns.py",
    "tests/fixtures/grounding_cases.json",
)

#: Sections the README must contain, in this order. The list is the
#: contract for a reviewer skimming the file.
REQUIRED_README_SECTIONS = (
    "What this is",
    "What this is not",
    "What we cut, and why",
    "The air-gap design",
    "Document processing pipeline",
    "The case-centric data model",
    "Why air-gapped, and what it costs",
    "Open research questions this raises",
    "Known limitations",
    "Production roadmap",
    "Repository layout",
    "Safety",
)

#: The non-claims the repository must state explicitly, checked as
#: case-insensitive substrings. A repository that documents an
#: air-gapped AI deployment without these is misrepresenting itself.
REQUIRED_NON_CLAIMS = (
    "no model training",
    "no interpretability technique",
    "no novel alignment method",
    "no evaluated safety claim",
)

#: Absolute paths from the author's workstation must never appear.
AUTHOR_PATH_PREFIXES = (
    "/Users/" + "jacob",
    "/home/" + "jacob",
    "C:\\Users\\" + "jacob",
)

#: Secret patterns, as ``(label, pattern)`` pairs. Each is assembled from
#: fragments so that the token this file forbids does not appear in the
#: file that forbids it.
SECRET_PATTERNS: tuple[tuple[str, str], ...] = (
    ("priv" + "ate_key", r"priv" + r"ate[_ -]?key\s*[:=]"),
    ("pem" + " header", r"-----BEGIN\s+(RSA|OPENSSH|EC|DSA|PGP)?\s*PRIVATE"),
    ("api" + " key assignment", r"\b(api" + r"[_ -]?key)\s*[:=]\s*['\"][A-Za-z0-9_\-]{16,}"),
    ("aws" + " access key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("aws" + " secret key", r"\baws_" + r"secret_access_key\s*[:=]"),
    ("pass" + "word assignment", r"\bpass" + r"word\s*[:=]\s*['\"][^'\"]{6,}"),
    ("bearer" + " token", r"\bBear" + r"er\s+[A-Za-z0-9_\-\.=]{20,}"),
    ("github" + " token", r"\bgh[pousr]_" + r"[A-Za-z0-9]{20,}"),
    ("slack" + " token", r"\bxox[baprs]-" + r"[A-Za-z0-9\-]{10,}"),
)

#: Markers of real client or matter data. A case study about a legal
#: document pipeline must not contain any of these, because the moment it
#: does, the repository is no longer publishable.
#:
#: The party-role markers are matched case-sensitively as standalone words.
#: Lowercase identifiers such as ``plaintiff_name`` are schema column names
#: that appear legitimately in ``ARCHITECTURE.md``; a real caption writes
#: the role capitalized.
CLIENT_DATA_PATTERNS: tuple[tuple[str, str], ...] = (
    ("party role: plain" + "tiff", r"\bPlain" + r"tiff\b"),
    ("party role: defen" + "dant", r"\bDefen" + r"dant\b"),
    ("medical " + "bill", r"\bmedical " + r"bills?\b"),
    ("clinical " + "diagno" + "sis", r"\bdiagno" + r"s(is|es|ed)\b"),
    ("medical record " + "number", r"\bMR" + r"N\b"),
    ("patient " + "identifier", r"\b(patient|client) " + r"(id|number|no\.?)\b"),
    ("social " + "security " + "number", r"\b(social " + r"security|s" + r"sn)\b"),
)

#: The shape of a matter caption: capitalized words on each side of a
#: versal. Checked separately from the patterns above because it needs the
#: placeholder exemption below.
MATTER_CAPTION_PATTERN = (
    r"\b((?:[A-Z][a-z]+[\s,]+)+)(?:v\.?|vs\.)\s+((?:[A-Z][a-z]+[\s,]+)+)"
)

#: The character the em-dash rule looks for, written as a code point so
#: that this file does not contain the character it forbids.
EM_DASH = chr(0x2014)

#: Words that mark a caption as a placeholder rather than a real matter.
#: The committed test fixtures need a realistic caption value to exercise
#: the validator, and the exemption is a closed list so it cannot be
#: widened by accident.
PLACEHOLDER_TOKENS = frozenset({
    "sample",
    "example",
    "test",
    "demo",
    "placeholder",
    "fake",
    "dummy",
})

#: Filename shapes that would indicate a real case file had been committed.
CASE_FILENAME_PATTERNS: tuple[str, ...] = (
    r"(?i)^(plain" + r"tiff|defen" + r"dant)",
    r"(?i)medical",
    r"(?i)\bbill\b",
    r"(?i)injury",
    r"(?i)discover(y|ies)",
    r"(?i)\bcmo\b",
    r"(?i)medical[-_]?record",
    r"(?i)\bpatient\b",
    r"(?i)deposition",
    r"(?i)\baffidavit\b",
)

#: Files excluded from the vocabulary check. The gate itself is excluded
#: because it necessarily names the words it forbids. It is scanned by
#: every other content gate.
VOCABULARY_EXEMPT = ("scripts/gate.py",)

#: Extensions treated as text by the content gates.
TEXT_SUFFIXES = {
    ".md", ".py", ".json", ".yml", ".yaml", ".txt", ".toml", ".cfg",
    ".ini", ".example", ".sh",
}

#: Paths never scanned for content.
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".venv", "venv"}

#: Commercial vocabulary. A research case study must not read as a sales
#: document, and a reviewer will notice.
BANNED_VOCABULARY: tuple[str, ...] = (
    "revolu" + "tionary",
    "game-" + "changing",
    "game " + "changing",
    "cutting-" + "edge",
    "cutting " + "edge",
    "seam" + "less",
    "world-" + "class",
    "world " + "class",
    "syn" + "ergy",
    "emp" + "ower",
    "best-in-" + "class",
    "enterprise-" + "grade",
    "military-" + "grade",
    "bank-" + "grade",
)

#: Phrases that indicate pricing or commercial framing. Each is assembled
#: from fragments so that this file does not contain the phrase it forbids.
COMMERCIAL_PHRASES: tuple[str, ...] = (
    "per " + "month",
    "per " + "seat",
    "per " + "attorney",
    "per " + "user per " + "month",
    "$/" + "mo",
    "pricing " + "tier",
    "price " + "tier",
    "free " + "trial",
    "money-" + "back",
    "monthly " + "subscription",
    "enterprise " + "plan",
    "request a " + "quote",
    "contact us for " + "pricing",
)


# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


class _C:
    def __init__(self) -> None:
        if _supports_color():
            self.R = "\033[31m"
            self.G = "\033[32m"
            self.Y = "\033[33m"
            self.B = "\033[1m"
            self.D = "\033[2m"
            self.X = "\033[0m"
        else:
            self.R = self.G = self.Y = self.B = self.D = self.X = ""


C = _C()


# ---------------------------------------------------------------------------
# Gate plumbing
# ---------------------------------------------------------------------------


class GateResult:
    def __init__(
        self,
        name: str,
        status: str,
        detail: str,
        traceback: str = "",
    ) -> None:
        self.name = name
        self.status = status  # PASS | FAIL | SKIP
        self.detail = detail
        self.traceback = traceback

    @property
    def ok(self) -> bool:
        return self.status in ("PASS", "SKIP")

    def short(self) -> str:
        if self.status == "PASS":
            return f"{C.G}PASS{C.X}"
        if self.status == "SKIP":
            return f"{C.Y}SKIP{C.X}"
        return f"{C.R}FAIL{C.X}"


class Gate:
    def __init__(self, name: str, fn: Callable[[], GateResult]) -> None:
        self.name = name
        self.fn = fn

    def run(self) -> GateResult:
        try:
            return self.fn()
        except Exception as exc:  # pragma: no cover - the gate itself blew up
            return GateResult(
                name=self.name,
                status="FAIL",
                detail=f"gate raised: {exc}",
                traceback=traceback.format_exc(limit=4),
            )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _scannable_files() -> list[Path]:
    """Every text file in the repository, excluding VCS and cache dirs."""
    out: list[Path] = []
    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        out.append(path)
    return out


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------------------
# G1 required files
# ---------------------------------------------------------------------------


def gate_required_files() -> GateResult:
    """G1 - every required file is present."""
    missing = [f for f in REQUIRED_FILES if not (REPO / f).exists()]
    if missing:
        return GateResult(
            "G1 required_files",
            "FAIL",
            f"missing: {', '.join(missing)}",
        )
    return GateResult(
        "G1 required_files",
        "PASS",
        f"{len(REQUIRED_FILES)} required files present",
    )


# ---------------------------------------------------------------------------
# G2 README sections
# ---------------------------------------------------------------------------


def gate_readme_sections() -> GateResult:
    """G2 - the README carries every required section, in order."""
    readme = REPO / "README.md"
    if not readme.exists():
        return GateResult("G2 readme_sections", "FAIL", "README.md missing")

    text = _read(readme)
    positions: list[int] = []
    missing: list[str] = []
    for section in REQUIRED_README_SECTIONS:
        heading = f"## {section}"
        index = text.find(heading)
        if index == -1:
            missing.append(section)
        else:
            positions.append(index)

    if missing:
        return GateResult(
            "G2 readme_sections",
            "FAIL",
            f"missing sections: {', '.join(missing)}",
        )
    if positions != sorted(positions):
        return GateResult(
            "G2 readme_sections",
            "FAIL",
            "sections are present but out of order",
        )
    return GateResult(
        "G2 readme_sections",
        "PASS",
        f"{len(REQUIRED_README_SECTIONS)} sections present and ordered",
    )


# ---------------------------------------------------------------------------
# G3 mermaid blocks
# ---------------------------------------------------------------------------

_MERMAID_HEADER = re.compile(
    r"^\s*(flowchart|graph|sequenceDiagram|classDiagram|stateDiagram|"
    r"stateDiagram-v2|erDiagram|journey|gantt|pie)\b",
    re.IGNORECASE,
)


def gate_mermaid() -> GateResult:
    """G3 - mermaid diagrams are well-formed enough to render."""
    problems: list[str] = []
    total = 0

    for path in _scannable_files():
        if path.suffix.lower() != ".md":
            continue
        text = _read(path)
        lines = text.splitlines()
        index = 0
        while index < len(lines):
            if lines[index].strip().lower() != "```mermaid":
                index += 1
                continue
            total += 1
            start_line = index + 1
            body: list[str] = []
            index += 1
            closed = False
            while index < len(lines):
                if lines[index].strip().startswith("```"):
                    closed = True
                    break
                body.append(lines[index])
                index += 1
            if not closed:
                problems.append(f"{_rel(path)}:{start_line} unclosed mermaid block")
                break
            if not body:
                problems.append(f"{_rel(path)}:{start_line} empty mermaid block")
            elif not _MERMAID_HEADER.match(body[0]):
                problems.append(
                    f"{_rel(path)}:{start_line} first line is not a diagram header"
                )
            else:
                for offset, line in enumerate(body):
                    opens = line.count("[") + line.count("(") + line.count("{")
                    closes = line.count("]") + line.count(")") + line.count("}")
                    if opens != closes:
                        problems.append(
                            f"{_rel(path)}:{start_line + offset} unbalanced brackets"
                        )
                        break
            index += 1

    if problems:
        detail = "; ".join(problems[:4])
        if len(problems) > 4:
            detail += f" (+{len(problems) - 4} more)"
        return GateResult("G3 mermaid", "FAIL", detail)
    if total == 0:
        return GateResult(
            "G3 mermaid",
            "FAIL",
            "no mermaid diagram found in any markdown file",
        )
    return GateResult("G3 mermaid", "PASS", f"{total} mermaid blocks well-formed")


# ---------------------------------------------------------------------------
# G4 secrets
# ---------------------------------------------------------------------------


def gate_secrets() -> GateResult:
    """G4 - no credential-shaped string anywhere in the repository."""
    hits: list[str] = []
    for path in _scannable_files():
        text = _read(path)
        for label, pattern in SECRET_PATTERNS:
            for match in re.finditer(pattern, text, re.IGNORECASE):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{_rel(path)}:{line} {label}")
    if hits:
        detail = "; ".join(hits[:5])
        if len(hits) > 5:
            detail += f" (+{len(hits) - 5} more)"
        return GateResult("G4 secrets", "FAIL", detail)
    return GateResult(
        "G4 secrets",
        "PASS",
        f"no secret patterns in {len(_scannable_files())} files",
    )


# ---------------------------------------------------------------------------
# G5 client data
# ---------------------------------------------------------------------------


def _caption_findings(text: str) -> list[tuple[int, str]]:
    """Find matter-caption shapes that are not placeholder captions.

    A caption is capitalized words on each side of a versal. The
    placeholder exemption is applied per side: a caption where either
    party name uses a word from the placeholder vocabulary is exempt, and
    one where neither side does is a finding.
    """
    findings: list[tuple[int, str]] = []
    for match in re.finditer(MATTER_CAPTION_PATTERN, text):
        left_tokens = {
            t.lower().strip(",") for t in match.group(1).split()
        }
        right_tokens = {
            t.lower().strip(",") for t in match.group(2).split()
        }
        if left_tokens & PLACEHOLDER_TOKENS or right_tokens & PLACEHOLDER_TOKENS:
            continue
        findings.append((match.start(), "matter caption shape"))
    return findings


def gate_client_data() -> GateResult:
    """G5 - no client, matter, or medical-record content.

    Checks file contents for matter-shaped markers and file names for
    case-file shapes. Either finding blocks the build.
    """
    hits: list[str] = []

    for path in _scannable_files():
        text = _read(path)
        for label, pattern in CLIENT_DATA_PATTERNS:
            for match in re.finditer(pattern, text):
                line = text.count("\n", 0, match.start()) + 1
                hits.append(f"{_rel(path)}:{line} {label}")
        for offset, label in _caption_findings(text):
            line = text.count("\n", 0, offset) + 1
            hits.append(f"{_rel(path)}:{line} {label}")

    for path in sorted(REPO.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        name = path.name
        for pattern in CASE_FILENAME_PATTERNS:
            if re.search(pattern, name):
                hits.append(f"{_rel(path)} case-file-shaped filename")
                break

    if hits:
        detail = "; ".join(hits[:5])
        if len(hits) > 5:
            detail += f" (+{len(hits) - 5} more)"
        return GateResult("G5 client_data", "FAIL", detail)
    return GateResult("G5 client_data", "PASS", "no client or matter markers")


# ---------------------------------------------------------------------------
# G6 author paths
# ---------------------------------------------------------------------------


def gate_author_paths() -> GateResult:
    """G6 - no absolute path from the author's workstation."""
    hits: list[str] = []
    for path in _scannable_files():
        text = _read(path)
        for prefix in AUTHOR_PATH_PREFIXES:
            index = text.find(prefix)
            while index != -1:
                line = text.count("\n", 0, index) + 1
                hits.append(f"{_rel(path)}:{line}")
                index = text.find(prefix, index + 1)
    if hits:
        return GateResult(
            "G6 author_paths",
            "FAIL",
            f"absolute author path in: {', '.join(sorted(set(hits))[:5])}",
        )
    return GateResult("G6 author_paths", "PASS", "no absolute author paths")


# ---------------------------------------------------------------------------
# G7 commercial content
# ---------------------------------------------------------------------------


def gate_commercial() -> GateResult:
    """G7 - no dollar figures and no commercial framing.

    Dollar amounts are forbidden in documentation and in data files, where
    they would read as pricing. The Python sources are exempt from the
    currency-symbol rule only, because the ledger validator has to parse
    currency symbols to do its job. Commercial vocabulary is checked
    everywhere, including in the Python sources.
    """
    hits: list[str] = []

    for path in _scannable_files():
        if path.suffix.lower() == ".py":
            continue
        text = _read(path)
        for match in re.finditer(r"[$€£]\s?\d", text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{_rel(path)}:{line} dollar figure")

    for path in _scannable_files():
        text = _read(path).lower()
        for phrase in COMMERCIAL_PHRASES:
            if phrase in text:
                line = text.count("\n", 0, text.find(phrase)) + 1
                hits.append(f"{_rel(path)}:{line} commercial phrase '{phrase}'")

    if hits:
        detail = "; ".join(hits[:5])
        if len(hits) > 5:
            detail += f" (+{len(hits) - 5} more)"
        return GateResult("G7 commercial", "FAIL", detail)
    return GateResult("G7 commercial", "PASS", "no dollar figures or commercial framing")


# ---------------------------------------------------------------------------
# G8 stated non-claims
# ---------------------------------------------------------------------------


def gate_non_claims() -> GateResult:
    """G8 - the repository states its non-claims explicitly."""
    readme = REPO / "README.md"
    if not readme.exists():
        return GateResult("G8 non_claims", "FAIL", "README.md missing")
    text = _read(readme).lower()
    missing = [claim for claim in REQUIRED_NON_CLAIMS if claim not in text]
    if missing:
        return GateResult(
            "G8 non_claims",
            "FAIL",
            f"non-claims missing: {', '.join(missing)}",
        )
    return GateResult(
        "G8 non_claims",
        "PASS",
        f"{len(REQUIRED_NON_CLAIMS)} non-claims stated explicitly",
    )


# ---------------------------------------------------------------------------
# G9 vocabulary
# ---------------------------------------------------------------------------


def gate_vocabulary() -> GateResult:
    """G9 - the prose does not use marketing vocabulary or em dashes."""
    hits: list[str] = []
    for path in _scannable_files():
        rel = _rel(path)
        if rel in VOCABULARY_EXEMPT:
            continue
        text = _read(path)
        lowered = text.lower()
        for word in BANNED_VOCABULARY:
            if word in lowered:
                line = lowered.count("\n", 0, lowered.find(word)) + 1
                hits.append(f"{rel}:{line} '{word}'")
        for match in re.finditer(EM_DASH, text):
            line = text.count("\n", 0, match.start()) + 1
            hits.append(f"{rel}:{line} em dash")
    if hits:
        detail = "; ".join(hits[:5])
        if len(hits) > 5:
            detail += f" (+{len(hits) - 5} more)"
        return GateResult("G9 vocabulary", "FAIL", detail)
    return GateResult("G9 vocabulary", "PASS", "no marketing vocabulary or em dashes")


# ---------------------------------------------------------------------------
# G10 pytest
# ---------------------------------------------------------------------------


def gate_pytest() -> GateResult:
    """G10 - the pytest suite is green."""
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--tb=line"],
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=600,
    )
    combined = proc.stdout + "\n" + proc.stderr
    if proc.returncode != 0:
        tail = "\n".join(combined.splitlines()[-6:])
        return GateResult(
            "G10 pytest",
            "FAIL",
            f"pytest exit={proc.returncode}; tail: {tail.strip()}",
        )
    if "Traceback (most recent call last)" in combined:
        return GateResult(
            "G10 pytest",
            "FAIL",
            "pytest exit=0 but a traceback was printed",
        )
    match = re.search(r"=+\s*(\d+)\s+passed[^\n]*", combined)
    summary = match.group(0).strip() if match else "passed"
    return GateResult("G10 pytest", "PASS", summary)


# ---------------------------------------------------------------------------
# G11 quality bar consistency
# ---------------------------------------------------------------------------


def gate_quality_bar() -> GateResult:
    """G11 - QUALITY_BAR.md documents every gate this script runs."""
    bar = REPO / "QUALITY_BAR.md"
    if not bar.exists():
        return GateResult("G11 quality_bar", "FAIL", "QUALITY_BAR.md missing")
    text = _read(bar)
    missing = [g.name.split()[0] for g in GATES if g.name.split()[0] not in text]
    if missing:
        return GateResult(
            "G11 quality_bar",
            "FAIL",
            f"gates not documented in QUALITY_BAR.md: {', '.join(missing)}",
        )
    return GateResult(
        "G11 quality_bar",
        "PASS",
        f"all {len(GATES)} gates documented",
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


GATES: list[Gate] = [
    Gate("G1 required_files", gate_required_files),
    Gate("G2 readme_sections", gate_readme_sections),
    Gate("G3 mermaid", gate_mermaid),
    Gate("G4 secrets", gate_secrets),
    Gate("G5 client_data", gate_client_data),
    Gate("G6 author_paths", gate_author_paths),
    Gate("G7 commercial", gate_commercial),
    Gate("G8 non_claims", gate_non_claims),
    Gate("G9 vocabulary", gate_vocabulary),
    Gate("G10 pytest", gate_pytest),
    Gate("G11 quality_bar", gate_quality_bar),
]


def _print_table(results: list[GateResult], quiet: bool) -> None:
    if quiet:
        return
    name_w = max(len(r.name) for r in results)
    print(f"{C.B}Sovereign quality gate - {datetime.now().isoformat(timespec='seconds')}{C.X}")
    print()
    for r in results:
        print(f"  {r.name.ljust(name_w)}  {r.short().ljust(13)}  {C.D}{r.detail}{C.X}")
    print()


def _summary_line(results: list[GateResult]) -> str:
    counts = Counter(r.status for r in results)
    overall = "PASS" if all(r.ok for r in results) else "FAIL"
    return (
        f"gate overall={overall} "
        f"pass={counts.get('PASS', 0)} "
        f"skip={counts.get('SKIP', 0)} "
        f"fail={counts.get('FAIL', 0)} "
        f"@{datetime.now().isoformat(timespec='seconds')}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sovereign quality gate - see QUALITY_BAR.md for thresholds.",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="print only the one-line summary.",
    )
    parser.add_argument(
        "--only",
        help="run only the gate whose name starts with this prefix.",
    )
    args = parser.parse_args(argv)

    selected = GATES
    if args.only:
        selected = [g for g in GATES if g.name.startswith(args.only)]
        if not selected:
            print(f"no gate matches {args.only!r}", file=sys.stderr)
            return 2

    results: list[GateResult] = []
    for gate in selected:
        if not args.quiet:
            print(f"{C.D}... running {gate.name}{C.X}", flush=True)
        result = gate.run()
        results.append(result)
        if not args.quiet and result.status == "FAIL" and result.traceback:
            print(f"{C.R}traceback:{C.X}")
            print(result.traceback)

    _print_table(results, args.quiet)
    print(_summary_line(results))
    return 0 if all(r.ok for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
