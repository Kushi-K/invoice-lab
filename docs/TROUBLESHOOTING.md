# Troubleshooting

| Symptom | Check and action |
|---|---|
| Tesseract executable not found | Run `tesseract --version`; check PATH or set `TESSERACT_CMD` to the executable |
| English language data missing | Run `tesseract --list-langs` and install the `eng` trained data through your package manager |
| Ollama offline | Start `ollama serve`, or the installed desktop service, on localhost port 11434 |
| No installed models | Run `ollama pull qwen2.5:1.5b`, then check the connection in the UI |
| Port already in use | Stop the existing Invoice Lab process or choose another `--port` |
| Browser does not open | Open the printed localhost URL manually; use `--no-browser` on a headless machine |
| PDF text is incomplete | Enable Force OCR and create a new analysis; inspect the transcript against the source |
| An encrypted PDF fails | Supply an unlocked document; password entry is not implemented |
| A value is missing or ambiguous | Inspect candidates, source text and source page; correct the value manually if verified |
| A model proposal is rejected | Read its validation reason. Unknown citations and unsupported/ambiguous quotes are intentionally rejected |
| Response reaches the output/context limit | Use an input/model combination that completes the request; partial truncated fields are not applied |
| Save reports a changed analysis | Reload the latest report and reapply the correction |
| Save disabled while AI runs | Prepare corrections and wait for completion; the interface explains the state |
| Interrupted model job | Restart the application and run analysis again; there is no automatic job resume |
| OCR tests skip | Tesseract or the optional private test fixture is absent; see `TESTING.md` |

## Inspect artifacts

Browser runs are stored under `ui-runs/<id>/`. Inspect `extracted.json` for
the reading output, `structured.json` for layout candidates, and the latest
report for proposals and rejections. `model_job.json` records progress/errors.
Do not attach these artifacts to public issues if they contain customer data.

## Hardware-specific Ollama issues

Model requests default to `num_gpu=0`. If a runtime still attempts to initialize
an unusable Metal device, the original development environment used:

```bash
LLAMA_ARG_DEVICE=none ollama serve
```

Conversely, `INVOICE_LLM_GPU_LAYERS=-1` when starting Invoice Lab requests
automatic GPU selection. Verify behavior with the installed Ollama version and
hardware; these settings do not imply a universal performance guarantee.
