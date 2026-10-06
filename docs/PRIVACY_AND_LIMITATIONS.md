# Privacy, scope and limitations

## Current privacy boundary

The application serves on `127.0.0.1` and its inference calls target local
Ollama at `127.0.0.1:11434`. The implemented extraction workflow does not send
document text to a cloud inference provider. Dependencies and model downloads
may require internet access. Local processing does not itself establish
encryption, regulatory compliance or restricted access to files on the machine.

Uploaded sources, previews, OCR text, reviews and expected values persist on
disk under `ui-runs/`. The project does not automatically expire them. Filesystem
access, retention and backups are the operator's responsibility.

The repository excludes real input documents, optional private fixtures,
saved analyses, generated reports, environment files, logs and the Python
virtual environment. Only generated fictional demo files are shared. Ignore
rules are a safeguard, not a substitute for inspecting staged files before
every push; already tracked files are not removed by `.gitignore`.

## Supported scope

- PDFs and common image formats, with a 25 MB UI upload limit.
- English OCR; usable PDF text can bypass OCR.
- The implemented invoice header/tax/total fields and page-specific
  rate-confirmation fields.
- Rule/table-based invoice line items.
- Human corrections, source inspection and original/current JSON exports.
- Optional local model extraction and a separate model self-review.

## Known limits

- Embedded-text quality and document classification are heuristics.
- Handwriting, all invoice layouts and multilingual processing are not
  established capabilities.
- Tesseract can omit/misread content; table grouping remains provisional.
- The small installed model can hallucinate explanations, misassign roles or
  provide generic justifications. Schema conformity is not correctness.
- Exact quote checks reduce unsupported values but do not prove business truth.
- Strong role checks are targeted to the implemented rate-confirmation fields.
- Model self-review is not independent verification. Scores are not accuracy
  probabilities; high scores can coexist with mistakes.
- Invoice arithmetic does not cover every discount, fee, adjustment or tax law.
  GSTIN format validation does not establish registry validity.
- Corrections do not fine-tune the model or automatically teach future runs.
- Jobs use in-process threads and persisted status, not a durable distributed
  queue. Interrupted jobs do not automatically resume.
- The standard-library HTTP server and JSON storage are for a local workbench.
  Authentication, tenant isolation, enterprise monitoring, database transactions
  and production deployment hardening are not implemented.

## Before client-wide deployment

Establish an independently labeled benchmark for the client's documents and
agree on review/acceptance criteria. Then add authenticated access, resource
limits, input hardening, durable jobs, appropriate storage, monitoring, backups
and a data-retention policy. Benchmark compatible models rather than assuming
a larger model or longer prompt automatically improves every extraction.

No accuracy percentage or compliance guarantee is claimed by this release.
