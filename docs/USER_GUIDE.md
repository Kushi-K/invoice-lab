# User guide

## Workspace sections

| Section | Purpose |
|---|---|
| New analysis | Upload a PDF/image and choose OCR/AI options |
| Saved results | Search, filter and reopen saved analyses |
| Fields | Inspect and edit invoice or rate-confirmation values |
| Line items | Inspect and edit detected invoice rows, when applicable |
| How extraction works | Understand field roles and see evidence sent to the model |
| AI explanation & results | Read generated interpretations, proposals, rejections and model review |
| OCR transcript | Read source text before interpretation |
| Source preview | Inspect the original page and highlighted evidence |
| Export | Download the original or current report as JSON |

## Analyze a document

1. Open **New analysis** and choose a supported PDF or image, up to 25 MB.
2. Enable **Force OCR** if the PDF text layer is incomplete or unreliable.
3. Keep **Run local AI analysis after OCR** checked to start AI automatically
   when an installed local model is connected. Disable it for rules-only analysis.
4. Optionally supply expected values as JSON for evaluation.
5. Select **Analyze document**. The reading stage completes before model analysis.
6. Follow per-page AI progress and inspect **AI explanation & results**.

An existing report can be analyzed using **Run AI analysis**. The AI results
screen also offers separate extraction and review actions. Choose an installed
model in the model selector. A disconnected runtime is reported explicitly.

## Correct and confirm values

Click inside a value or select its pencil control. Changed values are highlighted.
Edit several fields and use **Save changes** once. A blank field value clears
that field. Notes are optional. **Discard** abandons unsaved corrections.
**Confirm all populated fields** records human confirmation; it should be used
only after actually checking the values.

Inputs remain editable while model analysis runs, but the UI waits until that job
finishes before permitting a save. Unsaved drafts remain visible during polling.
If a stale-save message appears, reload the latest report before retrying.
Human-confirmed/corrected values are protected from later model overwrites.

For a field or accepted model proposal, use its source control to inspect the
original page. The OCR transcript stays unchanged when fields are corrected.

## Understand the AI results

- **Interpretation:** the model's generated account of document meaning.
- **Proposed value:** what the model selected for the requested field.
- **Source validated:** the proposal passed implemented source checks.
- **Rejected:** a proposal failed a quote or role check and was not promoted.
- **Model review:** a separate request assesses the current values.
- **Raw response:** optional audit detail, not the main reading interface.

Generated explanations are not access to private model reasoning. A validated
quote does not guarantee correct interpretation. Review consequential values
against the original document.

## Interpret scores

OCR confidence describes recognition. Evidence scores are weighted review
heuristics. Model scores are model opinions. None is a measured accuracy
percentage. Native PDF text has no invented OCR score. Missing evidence is
shown as unavailable rather than replaced with a fabricated confidence.

`PASS` indicates the implemented arithmetic check succeeded. `MISMATCH`
indicates a discrepancy. `not_checked` means the inputs or applicable validation
were unavailable. Rate-confirmation payments do not use the tax-invoice formula.

## Saved reports and evaluation

Saved results reopen the latest reviewed report without reprocessing the source.
Exports distinguish initial extraction from the current report.

Expected-values JSON may use a `fields` mapping or the supported field keys at
the top level. For example:

```json
{
  "fields": {
    "invoice_number": "INV-EXAMPLE-001",
    "grand_total": "1180.00"
  }
}
```

Only independently checked expected values provide meaningful evaluation.
Editing an output and treating the same output as ground truth does not
establish an independent accuracy benchmark.
