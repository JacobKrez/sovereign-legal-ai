# Evaluating Sovereign

This document defines how you would evaluate a system like this, what each
metric means, how it would be measured, what threshold we would hold it to,
and whether the number is MEASURED or a TARGET.

The honesty rule for this file: a threshold that has never been measured is
labeled TARGET. We do not report a number we have not computed, and we do not
report a target as if it were a result. Of the metrics below, exactly one has
a measured value, and it is measured on synthetic fixtures rather than on
matter data.

## Why these metrics and not others

The metrics split into three groups, and the split matters more than any
individual number.

* **Output correctness.** Did the extraction produce what the document says?
  These are measured against human-reviewed samples and they are the metrics a
  law firm would ask about first.
* **Legal risk.** Did the extraction produce something the document does not
  say? A field with no support in the source is worse than a missing field,
  because a missing field is visibly missing and an unsupported field looks
  like knowledge.
* **Operational and evaluability.** Can the system be trusted to run, and can
  anyone outside the enclave find out what it did? These are the metrics that
  an air gap makes hard, and they are the reason this repository exists.

The legal-risk group is the one we would weight most heavily, and the
hallucinated-field rate is the metric that carries it.

## The measurement protocol

Every output-correctness metric below is measured the same way.

1. **Sample.** Draw a stratified sample of documents from the intake
   population. Stratify by document type, by page count, and by how much of
   the page is machine-printed rather than scanned. A sample that is only
   clean born-digital PDFs measures a different system than the one that runs
   in production.
2. **Reference annotation.** Two annotators independently produce the expected
   field values from the source document, without seeing the model's output.
   Disagreements are adjudicated by a third reviewer and the adjudication is
   recorded. This is the ground truth for the fidelity metrics.
3. **Run.** The extraction passes run over the sample with the same
   configuration as production: same model weights, same prompts, same
   parameters.
4. **Compare.** Field by field, against the adjudicated reference.
5. **Record.** The per-field results are stored with the sample identifiers,
   the model checksum, the prompt version, and the validator version. Without
   those four, a measurement cannot be compared to a later one, which is the
   property an air-gapped deployment most needs and most often loses.

The reference annotation is the expensive part. It is also the part that
cannot be skipped if the metrics are to mean anything, and it is the part
that most needs to live inside the enclave, because the sample is privileged
material.

## Metric definitions

### M1. Extraction fidelity per field

- **Definition.** For each field in the registry, the fraction of sampled
  documents where the extracted value exactly matches the adjudicated
  reference value, after normalization (whitespace collapsed, dates
  normalized to ISO, numeric strings parsed to numbers, empty and null
  treated as equivalent).
- **Measurement.** Protocol above, per field, reported per field rather than
  averaged. The per-field report is the point: an aggregate over fields with
  different difficulty is not actionable.
- **Target.** 0.95 or better for identity fields (`case_name`, `case_number`,
  `venue`, `incident_date`). 0.90 or better for ledger fields
  (`provider_name`, `service_date`, `billed_amount`). 0.90 or better for
  deadline fields (`deadline_type`, `deadline_date`).
- **Status.** TARGET. Not measured.
- **Why 0.95 and not 1.0.** A perfect score on a sampled set means either
  that the sample is trivial or that the reference annotation is contaminated
  by the model's output. Real documents contain genuinely ambiguous fields,
  and a threshold of 1.0 would either be unreachable or be met by a
  degenerate sample. 0.95 on identity fields means roughly one error per
  twenty documents, which for a field an attorney checks on open is
  tolerable. It is not tolerable for every field, which is why the ledger and
  deadline thresholds sit lower.

### M2. Document-to-case-record accuracy

- **Definition.** The fraction of sampled documents for which the resulting
  case record is correct as a whole, meaning every field the document
  supports is present and correct, and every field it does not support is
  absent. This is stricter than M1 because it is per-document rather than
  per-field: a document with four correct fields out of five fails.
- **Measurement.** Protocol above, judged against the adjudicated reference
  record.
- **Target.** 0.85 or better.
- **Status.** TARGET. Not measured.
- **Why 0.85.** This is a whole-record measure and it compounds field error.
  With per-field fidelity near 0.95 across five fields, roughly 0.77 of
  documents would be expected to be fully correct, so a 0.85 target on the
  record assumes either better per-field performance than M1's floor or
  fewer fields per document than the sample average. It is a target that
  requires measurement to justify, and the first measurement will either
  confirm it or move it. We would rather publish a target that turns out to
  be wrong than a measurement we cannot reproduce.

### M3. Hallucinated-field rate

- **Definition.** The fraction of fields written to the case record that
  carry no support in the cited source span. A field is unsupported if the
  validator in `src/extraction_schema.py` cannot find the value in the span
  it cites, or if it cites no span at all. The denominator is model-asserted
  fields: fields a person entered and fields the application computed are
  not model claims, and counting them would understate the rate. An absent
  field cannot be a hallucination either, so a field that was never written
  is excluded from both numerator and denominator.
- **Measurement.** Every field written during the protocol run is checked
  against its cited span by the validator. The rate is computed per field
  group and per pass, because a high rate concentrated in the ledger pass is
  a different problem from a high rate spread evenly.
- **Target.** 0.02 or better overall, and 0.05 or better for any single
  field.
- **Status.** TARGET. Not measured on matter data.
- **Why 0.02.** This is the metric that matters most, and the threshold is
  set low because the failure is asymmetric. An unsupported date in a
  deadline table is a malpractice exposure; an unsupported amount in a
  damages summary is a damages calculation built on a number that was never
  in the record. Two percent of fields means one unsupported field per
  fifty. We have no basis for saying that two percent is acceptable to a
  firm. It is a floor for a system that refuses to guess, and the number
  that should be reported to a reviewing attorney is the per-field figure,
  not the aggregate.
- **The honest caveat.** The validator is lexical. It checks that the value
  appears in the cited span. A value that is present but attached to the
  wrong entity, or a correct value with a misleading label, passes. The
  metric measures unsupported fields, not incorrect fields. Those are
  different failure modes and M3 does not cover the second one. This is
  stated in the roadmap as the grounding-paraphrase problem: relaxing the
  lexical check to accept paraphrase is where a hallucination rate starts
  climbing, and doing it well is a research problem, not a threshold change.
- **The query path is not covered by M3.** M3 measures the record. The
  conversational answers are guarded by a separate rule: when retrieval
  returns nothing for a factual question, the system declines rather than
  answering from general knowledge. That rule catches absent evidence. It
  does not check whether a returned passage supports the claim built from
  it, so there is no equivalent metric here for the conversational path.
  Adding one would require the same adjudicated sample as M1, and it is not
  on the roadmap yet.

### M4. Ingestion success rate

- **Definition.** The fraction of inbound jobs that reach a completed
  extraction, meaning the document parsed, at least one pass produced output,
  and the record was written. A job that fails is counted against this rate
  whether it failed at parse, at OCR, or at merge.
- **Measurement.** Counted from the processing log, over a stated interval.
  Reported with the failure breakdown by stage, since a single rate with four
  failure causes is not actionable.
- **Target.** 0.98 or better for text-layer documents. 0.90 or better for
  scanned documents.
- **Status.** TARGET. Not measured.
- **Why 0.98 and why lower for scans.** Born-digital documents fail for
  reasons that are usually fixable, and two percent is a reasonable
  allowance for encrypted files and formats the parser does not handle.
  Scanned documents fail for reasons that are not fixable in the parser:
  handwriting, degraded scans, skewed pages, and multi-column layouts. The
  OCR fallback is a heuristic threshold, not a document classifier. We expect
  the scanned rate to be materially lower and we would rather say so in
  advance than discover it in a report.

### M5. Cluster availability

- **Definition.** The fraction of scheduled hours in which the inference tier
  was able to serve a request, measured from the local metrics collector.
  Two components, reported separately: the inference container's uptime, and
  the fraction of jobs completed without a manual restart.
- **Measurement.** Local metrics, retained inside the enclave. Because the
  metrics never leave, the measurement exists only for the firm that runs
  the enclave. It is not comparable across deployments and we do not attempt
  to make it so.
- **Target.** 0.99 for the inference container. 0.98 for jobs completed
  without a manual restart.
- **Status.** TARGET. Not measured.
- **Why 0.99.** Hardware failure in a cluster with a hot-swap backup node
  should be transparent, and the container restarts itself after a node
  reboot. Ninety-nine percent is roughly eight hours a year of unavailability,
  which for a single-firm internal system with an alternative of manual
  re-processing is a reasonable floor. The second figure is lower because a
  job can fail without the container being down, and the difference between
  the two numbers is the honest measure of how much of the system's
  reliability comes from hardware versus from software.

### M6. Model off-distribution detection rate

- **Definition.** The fraction of deliberately out-of-distribution inputs
  that the system detects as anomalous, where detection means the grounding
  validator flags a field, or the record fails to converge across repeated
  runs of the same input. Measured against a held-out set of perturbed
  documents: redactions, unusual formatting, unexpected document genres,
  and inputs whose fields are near-duplicates of each other.
- **Measurement.** Run the perturbed set through the pipeline and count
  detections. Report both the detection rate and the false-positive rate on
  the in-distribution sample.
- **Target.** 0.90 or better detection, and 0.05 or better false-positive
  rate on in-distribution inputs.
- **Status.** TARGET. Not measured.
- **Why this metric exists.** This is the operational face of research
  question Q3 in the README. A model whose behavior has shifted will not
  announce itself in an enclave with no telemetry, so detection has to be
  derived from artifacts the enclave already produces. The false-positive
  figure is stated alongside it because a detector that fires on ordinary
  documents is worse than no detector: it trains the attorney to ignore it.
- **The honest caveat.** Detection here is a property of the extraction
  pipeline's outputs, not of the model. A model that is confidently and
  consistently wrong produces well-supported-looking fields, and this metric
  would score it as in-distribution. We are measuring pipeline-level
  instability, and we are calling it that.

### M7. Third-party audit artifact completeness

- **Definition.** The fraction of artifacts in the audit set that a third
  party can obtain and that contains what it is supposed to contain, without
  access to the firm, the enclave, or the model weights.
- **Measurement.** An enumeration with a presence check per artifact, run
  against a request from an auditor who has not been given help. Presence is
  not sufficient: the artifact must also contain the fields listed in
  `ARCHITECTURE.md` section 7, or the score counts the artifact as present
  but incomplete.
- **Target.** 0.95 or better.
- **Status.** TARGET. Not measured.
- **Why it is here.** This is the metric that operationalizes research
  question Q2. The score is deliberately low-bar by design: the artifacts
  are records of what the pipeline did, not evidence about the model, and a
  high score on this metric must not be read as a claim about the model's
  behavior. We think that confusion is the main risk in publishing a number
  like this, which is why the definition says "completeness" and the target
  says nothing about model quality.

## The audit artifact set

The artifacts that make up M7. Each is a file the enclave can produce on
demand.

| Artifact | Contents | Produced without enclave access |
|---|---|---|
| Field registry export | The versioned field list, types, required flags, and pass mapping | Yes |
| Prompt templates | The three extraction prompts, versioned | Yes |
| Model and runtime manifest | Weight checksum, runtime version, container image digest | Yes |
| Extraction log | Per-job record of passes run, inputs, and outputs written | Yes |
| Grounding report | Per-field validator results over a stated interval | Yes |
| Document inventory | Filenames, hashes, and timestamps, with no content | Yes |
| Schema export | The database schema, with no rows | Yes |
| Configuration manifest | Isolation settings, mount flags, capability drops, network mode | Yes |

The set deliberately excludes model weights, document content, and the case
database. Those stay inside. A third party can verify that the pipeline did
what the manifest says, and can check the grounding report for unsupported
fields. They cannot verify the model's behavior on inputs the pipeline never
saw. We consider that boundary a property of the deployment rather than a
gap in the artifact set, and we would rather state it than paper over it.

## Threshold summary

| ID | Metric | Threshold | Status |
|---|---|---|---|
| M1 | Extraction fidelity per field | 0.95 identity, 0.90 ledger and deadline | TARGET |
| M2 | Document-to-case-record accuracy | 0.85 | TARGET |
| M3 | Hallucinated-field rate | 0.02 overall, 0.05 per field | TARGET |
| M4 | Ingestion success rate | 0.98 text-layer, 0.90 scanned | TARGET |
| M5 | Cluster availability | 0.99 inference, 0.98 job completion | TARGET |
| M6 | Off-distribution detection rate | 0.90 detection, 0.05 false positive | TARGET |
| M7 | Audit artifact completeness | 0.95 | TARGET |

## What is actually measured here

One thing: the grounding validator's behavior on the committed synthetic
fixtures in `tests/fixtures/grounding_cases.json`. The test suite runs those
fixtures through `src/extraction_schema.py` and asserts the expected
verdict for each case. The suite passing is evidence that the validator
implements the specification in this document on that fixture set. It is not
evidence about any real matter, and it says nothing about M1 through M7, all
of which require a matter corpus that does not exist in this repository.

The fixture set exists for a specific reason. When the first real measurement
is run, the validator will need to be trusted, and the trust should be
justified by something more than "the code looks right." A committed
fixture set with expected verdicts is the smallest thing that can carry that
weight. It is also the artifact a third party can read to understand exactly
what the grounding check does and does not accept.

## What we would need to measure any of this

1. An adjudicated reference sample inside the enclave. Nothing in this
   repository approximates it.
2. A validator version that can be cited with each measurement, so two
   measurements are comparable.
3. A decision about how many fields the lexical grounding check should
   accept. This is the threshold that decides whether M3 measures
   hallucination or measures summarization.
4. Enough measurement interval for M4 and M5 to mean anything. Both are
   rates over time, and a week of production is not a sample.
