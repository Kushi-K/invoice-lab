# Testing and evaluation

Run from the repository root after installing Python dependencies:

```bash
python -m unittest -v test_invoice_lab.py test_invoice_ui.py test_pipeline.py test_review_workspace.py
```

No live Ollama service or model download is required for the automated suite;
model boundaries are exercised with controlled responses. The HTTP tests bind
to localhost, so a restricted execution environment may require permission.

## Coverage

| Module | Examples of covered behavior |
|---|---|
| `test_invoice_lab.py` | TSV parsing, party assignment, ambiguous candidates, arithmetic, rate references and evaluation |
| `test_invoice_ui.py` | Original evidence preservation, confirmation, corrected item arithmetic and reopening |
| `test_pipeline.py` | Stage ordering, PDF routing, source validation, model promotion and score boundaries |
| `test_review_workspace.py` | Batch transactions, exact request preview, revision conflicts, background edits and stale reviews |

The suite contains 40 tests at this release. Two local OCR regression tests
require Tesseract on PATH and private fixtures (`sample_invoice.png` and
`variant_scanned_invoice.pdf`, plus `sample_invoice_truth.json` for the image). Those files are deliberately excluded from
the shared repository. These tests skip when their prerequisites are absent.
The remaining tests must pass in a clean checkout; skipped private-fixture
tests must not be presented as executed coverage.

## Reproducible OCR smoke check

The bundled demo is fictional and can be regenerated:

```bash
python make_demo.py
python invoice_lab.py extract demo_searchable.pdf --out output/searchable
python invoice_lab.py extract demo_scanned.pdf --out output/scanned
python invoice_lab.py extract demo_searchable.pdf --force-ocr --out output/forced
python invoice_lab.py evaluate output/scanned/result.json ground_truth.json
```

Expected demo values include invoice number `INV-2026-041`, taxable amount
`1000.00`, CGST and SGST `90.00` each, and grand total `1180.00`. The invoice
totals check should pass. OCR variations may require inspection of saved lines
and annotated previews; record the engine/version when reporting results.

## Live model check

With Ollama running and a model installed:

```bash
python invoice_lab.py extract demo_searchable.pdf --llm-model qwen2.5:1.5b --out output/ai
```

Inspect `llm_error`, `llm_extraction`, rejected proposals and `llm_review` in
the saved report. Source-backed values and model explanations are separate
verification targets. The small model may produce weak explanations even
when quoted values pass validation.

## Browser acceptance checks

1. Upload the fictional demo with auto-AI enabled and observe progress.
2. Edit a value, save it and reload; confirm persistence and original evidence.
3. Discard another draft and verify the saved value remains unchanged.
4. Open a source control and verify the page/quote correspondence.
5. Compare readable passages and optional exact request details.
6. Confirm explicit connection/failure states when Ollama is unavailable.
7. Check desktop and small-screen layouts and saved-result search/filtering.

## Accuracy claims

Passing tests demonstrates the covered behaviors, not general document
accuracy. `evaluate` reports exact matches on supplied expected values; rate
reference comparison is ordered and penalizes extra or missing entries.
Tax/total agreement and high scores are not substitutes for ground truth.
A client benchmark needs independently labeled representative documents,
including layout, scan-quality, missing-field and ambiguous-value cases.
