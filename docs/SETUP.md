# Setup and operation

## Requirements

- Python 3.10 or newer. Python 3.13 is used by the existing local environment.
- Python packages in `requirements.txt`: PyMuPDF and Pillow.
- Tesseract with English language data for image/scanned-document processing.
- Ollama and an installed model for optional local AI extraction and review.

The application has no Node.js build step. The browser assets are plain HTML,
CSS and JavaScript served by Python.

## Create the Python environment

Run these commands from the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
```

On Windows, use `py -m venv .venv`, then `.venv\Scripts\Activate.ps1` in
PowerShell. If script activation is restricted, invoke
`.venv\Scripts\python.exe` directly. This release was verified locally on
macOS; Windows installation is not part of the tested platform coverage.

## Install the OCR engine

For a supported Homebrew macOS installation:

```bash
brew install tesseract
tesseract --version
tesseract --list-langs
```

For Ubuntu/Debian:

```bash
sudo apt-get update
sudo apt-get install tesseract-ocr tesseract-ocr-eng
tesseract --version
```

The code discovers Tesseract using `TESSERACT_CMD`, then PATH, then the
macOS fallback `/opt/homebrew/bin/tesseract`. A nonstandard installation can
be configured before starting the application:

```bash
export TESSERACT_CMD=/absolute/path/to/tesseract
```

The original development machine has an isolated conda-forge OCR installation
behind `/opt/homebrew/bin/tesseract`. That machine-specific runtime is not
bundled with this repository.

## Install and run a local model

Install Ollama from its official distribution. In a terminal:

```bash
ollama pull qwen2.5:1.5b
ollama serve
```

Skip `ollama serve` if a running Ollama desktop installation already serves
`127.0.0.1:11434`. `qwen2.5:1.5b` is the small model used during local
verification, not a guarantee of sufficient quality for every document.
Other installed compatible models can be selected in the UI.

The application itself does not download models. A model download requires
network access; local OCR and inference do not require a cloud inference API.

## Start Invoice Lab

In a second terminal, from the repository root:

```bash
source .venv/bin/activate
python invoice_ui.py --port 8766
```

Open `http://127.0.0.1:8766/`. The script attempts to open a browser by default.
Use `--no-browser` when running it in an environment without a desktop.

The code's default port is 8765; the documentation explicitly chooses 8766.
The server binds to localhost. Do not expose this development server to a
network as an authenticated production service.

## Fictional demo

The bundled `demo_searchable.pdf`, `demo_scanned.pdf` and `ground_truth.json`
are generated examples, not customer documents. To regenerate them:

```bash
python make_demo.py
```

Upload either PDF in **New analysis**. Check the local AI option to run
extraction and review after the document-reading stage. Supplying
`ground_truth.json` through the optional evaluation control compares the
specified expected fields with extracted values.

## CLI

```bash
python invoice_lab.py extract demo_searchable.pdf --out output/searchable
python invoice_lab.py extract demo_scanned.pdf --out output/scanned
python invoice_lab.py extract demo_searchable.pdf --force-ocr --out output/forced
python invoice_lab.py extract demo_searchable.pdf --llm-model qwen2.5:1.5b --out output/ai
python invoice_lab.py evaluate output/searchable/result.json ground_truth.json
```

The CLI reads the document and saves structure before optional model inference.
`--llama-server` is a legacy compatibility path, not the primary UI workflow.

## Stop and restart

Press **Ctrl+C** in the Invoice Lab terminal to stop the web server. If you
started Ollama in another terminal, stop that process separately. Saved files
remain on disk. Restart with the same commands and reopen **Saved results**.
After changing Python code, restart the server and refresh the browser.

## Runtime configuration

| Setting | Purpose |
|---|---|
| `--port` | Web server port |
| `--no-browser` | Prevent automatic browser opening |
| `--force-ocr` / UI Force OCR | Read PDF pages using Tesseract |
| `TESSERACT_CMD` | Explicit OCR executable path |
| `INVOICE_LLM_GPU_LAYERS` | Ollama `num_gpu` request option; defaults to `0` |

The default model requests use CPU layers. To permit Ollama's automatic GPU
selection, start Invoice Lab with `INVOICE_LLM_GPU_LAYERS=-1`. Hardware support
depends on the installed runtime. The original machine also used
`LLAMA_ARG_DEVICE=none ollama serve` when its runtime attempted to initialize
an unusable Metal device. This is a troubleshooting option, not a portable
installation requirement.
