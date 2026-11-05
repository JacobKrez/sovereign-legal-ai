# Sovereign quality bar

This file is the source of truth for what "publishable" means for this
repository. It is the companion to `scripts/gate.py`. The gate enforces the
thresholds; this file explains them.

A reviewer will ask "why this threshold and not the other one?" more than
once. This document is the answer, written down. When a threshold moves, edit
this file and the gate follows by reading the same constants.

## How to use

```
python3 scripts/gate.py             # full gate, tabulated output
python3 scripts/gate.py --quiet     # one-line digest
python3 scripts/gate.py --only G4   # run a single gate
```

Exit code 0 means green. Exit code 1 means at least one gate failed. The gate
runs as the final step in CI on every push and pull request.

## Gates

The gate runs 11 gates in sequence. Each is a runnable check. None of them
move at runtime: the thresholds are fixed below.

### G1 - required files present

- **Threshold:** every file in `REQUIRED_FILES` exists.
- **Source of truth:** the repository tree.
- **Why this threshold:** the set is the minimum for a reviewer to read the
  repository as a case study rather than as a code dump. `ARCHITECTURE.md`
  carries the topology, `EVALUATION.md` carries the metrics,
  `QUALITY_BAR.md` carries this file, and `src/extraction_schema.py` plus
  its tests are the one executable artifact. A missing file means the
  reviewer hits a hole mid-read, and a hole mid-read is where trust goes.
- **Why not just the README.** A README alone is a landing page. The
  difference between a landing page and a case study is whether the claims
  have somewhere to be checked.

### G2 - README sections present and ordered

- **Threshold:** every section in `REQUIRED_README_SECTIONS` is present as a
  level-two heading, in the listed order.
- **Source of truth:** `README.md`.
- **Why this threshold:** the section list is the argument structure of the
  repository, and the order is part of it. "What this is" and "What this is
  not" have to come before the architecture, because the architecture is
  only interesting once the reader knows what is and is not being claimed.
  "Open research questions" comes after the tradeoffs, because the questions
  are only honest once the costs have been stated.
- **Why order is checked and not just presence.** A reader who finds "Known
  limitations" after "Production roadmap" has already formed the wrong
  impression, and no amount of section content fixes that.

### G3 - mermaid diagrams are well-formed

- **Threshold:** at least one mermaid block exists; every block is closed;
  every block's first line is a recognized diagram header; brackets balance
  on every line.
- **Source of truth:** the markdown files in the repository.
- **Why this threshold:** a broken diagram renders as a red error box in the
  README on the first visit. That is the worst possible first impression for
  a document whose entire purpose is to be read carefully by a stranger.
- **Why the header check.** Mermaid accepts arbitrary content in a block and
  fails at render time. Checking the first line against the known diagram
  keywords catches the common case of a truncated or mistyped block.
- **Why bracket balance.** An unbalanced bracket in a node label is the
  difference between a diagram and a syntax error, and it is checkable
  without a mermaid parser.

### G4 - no secrets

- **Threshold:** zero matches for any pattern in `SECRET_PATTERNS`, across
  every text file in the repository.
- **Source of truth:** a full scan of the tracked files. Tracked means
  `git ls-files`, so a reader's virtual environment or scratch directory
  is never scanned, whatever it is named (in a source archive with no
  `.git`, any directory holding a `pyvenv.cfg` is skipped). The same
  file list feeds G5, G6, G7 and G9. An earlier version walked the
  working tree and went red on any venv not called `.venv` or `venv`;
  `tests/test_gate_scope.py` is the regression test.
- **Why this threshold:** a repository that documents a secure deployment
  cannot contain a key. The failure mode is not theoretical: a credential
  committed once is compromised once, and a rotation is a bad afternoon.
  The patterns cover private-key headers, high-entropy API key assignments,
  AWS access key identifiers, password assignments, bearer tokens, and the
  common provider token prefixes.
- **Why the gate scans itself.** The patterns are assembled from string
  fragments so that the gate file does not match its own patterns. This
  means the gate has no blind spot for the file that defines the check.
- **What is not covered.** This is a pattern scan, not a secret scanner. It
  will not find a base64-encoded blob or a credential split across two
  variables. It is a floor, and we say so rather than implying it is a
  guarantee.

### G5 - no client or matter data

- **Threshold:** zero matches for any pattern in `CLIENT_DATA_PATTERNS` in
  file contents, and zero filenames matching `CASE_FILENAME_PATTERNS`.
- **Source of truth:** a full scan of the tracked files.
- **Why this threshold:** this is the gate that makes the repository
  publishable rather than merely embarrassing. The subject matter is legal
  documents about real people. A repository about an air-gapped legal AI
  system that contains one real matter name has destroyed the exact property
  it describes.
- **Why party roles are matched case-sensitively.** Lowercase identifiers
  such as `plaintiff_name` are schema column names that belong in
  `ARCHITECTURE.md`. A real caption writes the role capitalized. Matching
  capitalized standalone words catches the data and leaves the schema
  intact, which means the gate does not force us to describe the system
  inaccurately to pass it.
- **Why the caption shape is checked, and how placeholders are handled.**
  The pattern for a matter caption is two capitalized names separated by a
  versal, which is the shape a real matter takes in a case record. The
  check exempts captions built from an explicit placeholder vocabulary
  (sample, example, test, demo, placeholder, fake, dummy), because the
  committed test fixtures need a realistic caption value to exercise the
  validator. Every other caption shape is a finding. The exemption is a
  closed list rather than a heuristic, so it cannot be widened by accident.
- **Why filenames are checked separately.** A committed file called
  `medical_bill_001.pdf` is client data even if its contents were
  redacted, and it is also a strong signal that the rest of the tree was
  not sanitized.
- **Why clinical vocabulary is included.** The pipeline's document population
  includes treatment and billing records, so a repository about it can leak
  clinical content without any party name appearing at all. The check
  therefore covers billing and clinical terms, record identifiers, and
  government identifiers as well as matter names.

### G6 - no absolute author paths

- **Threshold:** zero occurrences of an absolute path rooted at the
  author's home directory.
- **Source of truth:** a full scan of the tracked files.
- **Why this threshold:** an absolute path leaks the author's username and
  workstation layout, and it breaks for anyone else who clones the
  repository. It is also a reliable signal that a file was pasted in from a
  working session rather than written for the repository.
- **Why the pattern is assembled from fragments.** So the gate file itself
  does not contain the string it forbids.

### G7 - no dollar figures, no commercial framing

- **Threshold:** zero currency amounts in markdown and data files, and zero
  matches for any phrase in `COMMERCIAL_PHRASES` anywhere.
- **Source of truth:** a full scan of the tracked files.
- **Why this threshold:** a research case study that contains pricing is not
  a research case study. The reviewer is trying to judge the deployment and
  the questions it raises, and a price table turns the document into a
  product page and invites the reader to evaluate the product instead.
- **Why the Python sources are exempt from the currency-symbol rule.** The
  ledger validator has to parse currency symbols to do its job: `4,820.50`
  and the same amount with a currency prefix are one number, and the parser
  is tested against both. Exempting the symbol in code while forbidding the
  amount in prose keeps the rule honest. Commercial vocabulary is checked in
  the Python sources too, so the exemption is narrow.
- **What "commercial framing" means here.** Per-seat pricing language,
  recurring-payment language, free-trial language, and refund-guarantee
  language. The point is that the document should read as engineering
  research, which it is. The exact phrase list lives in
  `COMMERCIAL_PHRASES` in `scripts/gate.py`; it is not restated here so
  that this file passes the same check the prose does.
- **Why the phrase list is short.** A long list of commercial phrases turns
  into a linting project and starts producing false positives on ordinary
  technical writing. The list covers the framings that would actually
  change how a reviewer reads the document.

### G8 - non-claims stated explicitly

- **Threshold:** `README.md` contains each phrase in `REQUIRED_NON_CLAIMS`,
  case-insensitively.
- **Source of truth:** `README.md`.
- **Why this threshold:** this is the most important gate in the file, and
  the one a reviewer will check first. A repository that documents a
  deployment where a model runs without any external oversight has an
  obligation to say plainly what it does not contain: no training, no
  interpretability, no novel alignment method, and no evaluated safety
  claims. If those phrases are removed in a later edit, the repository
  starts implying safety properties it never established. The gate makes
  that regression unshippable.
- **Why it is a gate and not a paragraph.** Intentions do not survive
  edits. A stated non-claim that cannot be deleted without breaking the
  build is a claim.

### G9 - no marketing vocabulary, no em dashes

- **Threshold:** zero occurrences of any term in `BANNED_VOCABULARY`, and
  zero em dashes, in every scanned file except `scripts/gate.py`.
- **Source of truth:** a full scan of the tracked files.
- **Why this threshold:** the register of the document is part of the
  argument. A repository that describes its own system with the vocabulary
  of a product announcement is asking to be trusted in a register it has not
  earned. The banned list is short on purpose: it catches the terms that
  carry a marketing connotation regardless of context. The exact list lives
  in `BANNED_VOCABULARY` in `scripts/gate.py` and is not restated here, so
  that this file is held to the same check the prose is.
- **Why em dashes are banned.** They are a stylistic tic, and this
  repository's voice is meant to read like technical writing: plain
  declarative sentences, concrete specifics, no typographic flourish. The
  rule is trivially checkable, so it is checked.
- **Why the gate file is exempt.** It necessarily names the words it
  forbids. It is scanned by every other content gate, so the exemption is
  one rule wide rather than a general carve-out.

### G10 - pytest suite green

- **Threshold:** zero failures, zero errors, no `Traceback` line in the
  output.
- **Source of truth:** `python3 -m pytest -q` from the repository root.
- **Why this threshold:** every other gate assumes the deterministic suite
  passes. A single failing test is a regression we ship, with no partial
  credit. The traceback check is a belt-and-braces against a suite that
  swallows an exception and exits zero.
- **Why the suite is the substance and not decoration.** The grounding
  validator is the only executable artifact, and it is what makes the
  hallucinated-field rate in `EVALUATION.md` computable. The fixture file
  is written so a third party can read one JSON file and see exactly what
  the validator accepts and rejects.

### G11 - this file documents every gate

- **Threshold:** every gate identifier in this script appears in
  `QUALITY_BAR.md`.
- **Source of truth:** `QUALITY_BAR.md` compared against the gate list.
- **Why this threshold:** a gate without a stated threshold and a stated
  reason is a rule nobody can argue with. It also means adding a gate
  forces a written justification, which is the point: the reason is the
  artifact, not the check.
- **Why it is cheap.** It is a string comparison, so it cannot be the gate
  that fails when everything else passes.

## Thresholds at a glance

| Gate | Threshold | Why |
|------|-----------|-----|
| G1 required_files | every required file present | a hole mid-read is where trust goes |
| G2 readme_sections | all sections present, in order | the order is the argument |
| G3 mermaid | closed, headed, balanced | a red error box on first visit |
| G4 secrets | zero matches, whole tree | a committed key is compromised once |
| G5 client_data | zero markers, zero case-shaped filenames | one real caption ends the claim |
| G6 author_paths | zero absolute author paths | leaks identity, breaks clones |
| G7 commercial | zero dollar figures, zero commercial phrases | research, not a product page |
| G8 non_claims | all four non-claims stated explicitly | a deletable non-claim is not a claim |
| G9 vocabulary | zero banned terms, zero em dashes | the register is part of the argument |
| G10 pytest | 0 failures, 0 errors, no traceback | regressions ship with no partial credit |
| G11 quality_bar | every gate documented here | a gate without a reason is arbitrary |

## CI

`.github/workflows/ci.yml` runs `python3 -m pytest -q`, then
`python3 scripts/gate.py --quiet` as the final step. A red final step blocks
the build.

The gate re-runs the test suite as its own G10, which means the suite runs
twice in CI. That is deliberate. The duplicate run means the gate is
self-contained: a developer can clone the repository, run one command, and
get the same answer the CI got, without reconstructing which subset of
checks the CI happens to apply.

## Editing this file

When a threshold needs to move:

1. Update the threshold in this file, with the new rationale.
2. Update the matching constant in `scripts/gate.py` if it lives there.
3. Run `python3 scripts/gate.py` and confirm the new threshold is the one
   being enforced.
4. Commit both changes together.
5. Add a gate identifier to `REQUIRED_*` lists if the change introduced a
   new check.

Do not relax a threshold to make a build pass. The thresholds are the
definition of done. Relaxing one to make CI green is the failure mode this
file exists to prevent.
