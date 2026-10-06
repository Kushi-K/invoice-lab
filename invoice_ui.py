"""Local browser UI for Invoice Evidence Lab (Python standard library only).

Run: python invoice_ui.py
Open: http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import copy
import hashlib
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json
import re
import threading
import uuid
import webbrowser
from email.parser import BytesParser
from email.policy import default
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from invoice_lab import evaluate, run, totals_check
from invoice_core.confidence import evidence, score_fields, assess_rate_fields
from invoice_core.validation import validate_fields
from invoice_core.llm import available_models, ollama_extract, ollama_review, apply_extraction, build_requests
from invoice_core.storage import save_json

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "ui"
RUNS = ROOT / "ui-runs"
MAX_REQUEST = 26 * 1024 * 1024
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"}
RUN_ID = re.compile(r"^[0-9a-f]{32}$")
WRITE_LOCK = threading.Lock()


def parse_form(content_type: str, body: bytes):
    if not content_type.lower().startswith("multipart/form-data;"):
        raise ValueError("Choose a PDF or image file first.")
    envelope = (f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
                + body)
    message = BytesParser(policy=default).parsebytes(envelope)
    if not message.is_multipart():
        raise ValueError("Could not read the upload. Please select the file again.")
    fields = {}
    for part in message.iter_parts():
        key = part.get_param("name", header="content-disposition")
        if key:
            fields[key] = {"filename": part.get_filename(),
                           "data": part.get_payload(decode=True) or b""}
    return fields


def saved_result(run_id):
    """Open the latest saved report without reprocessing the source file."""
    folder = RUNS / run_id
    original = folder / "output" / "result.json"
    if not original.is_file():
        return None
    reviewed = folder / "output" / "reviewed_result.json"
    report = json.loads((reviewed if reviewed.is_file() else original).read_text(encoding="utf8"))
    assessment, assessment_error = None, None
    truth = folder / "reviewed_truth.json"
    if truth.is_file():
        try:
            assessment = evaluate(reviewed if reviewed.is_file() else original, truth)
        except (ValueError, KeyError, TypeError) as exc:
            assessment_error = f"Ground truth could not be evaluated: {exc}"
    return {"run_id": run_id, "report": report, "evaluation": assessment,
            "evaluation_error": assessment_error, "revision": report_revision(reviewed if reviewed.is_file() else original)}


def report_revision(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def review_summary(report):
    if report.get("document_type") == "rate_confirmation_packet":
        fields = [entry for record in report.get("rate_confirmations", [])
                  for key in ("carrier", "order_number", "total_carrier_pay", "invoice_references")
                  for entry in (record.get(key, []) if key == "invoice_references" else [record.get(key, {})])]
    else:
        fields = list(report.get("fields", {}).values())
    populated = [field for field in fields if field.get("value") is not None]
    reviewed = sum(field.get("review_status", "").startswith("human_") for field in populated)
    status = "reviewed" if populated and reviewed == len(populated) else "in_progress" if reviewed else "unreviewed"
    return {"review_status": status, "human_reviewed": reviewed, "populated": len(populated),
            "model_status": "reviewed" if report.get("llm_review") else "extracted" if report.get("llm_extraction") else "not_run"}


def model_status():
    try:
        models = available_models()
        return {"models": models, "status": "ready" if models else "no_models",
                "message": "Ollama connected" if models else "Ollama is running, but no models are installed.",
                "action": None if models else "Install a model with ollama pull <model-name>, then check the connection."}
    except Exception as exc:
        return {"models": [], "status": "offline", "message": "Ollama is not reachable on this computer.",
                "action": "Start Ollama or run ollama serve, then check the connection.", "detail": str(exc)}


def recent_results(limit=1000):
    """A small local index; files and previews remain in their original runs."""
    if not RUNS.is_dir():
        return []
    folders = []
    for folder in RUNS.iterdir():
        if not folder.is_dir() or not RUN_ID.fullmatch(folder.name):
            continue
        original = folder / "output" / "result.json"
        reviewed = folder / "output" / "reviewed_result.json"
        if original.is_file():
            folders.append((max(original.stat().st_mtime,
                                reviewed.stat().st_mtime if reviewed.is_file() else 0),
                            folder, reviewed if reviewed.is_file() else original))
    folders.sort(reverse=True, key=lambda entry: entry[0])
    results = []
    for changed, folder, report_path in folders[:limit]:
        try:
            report = json.loads(report_path.read_text(encoding="utf8"))
            rate = report.get("document_type") == "rate_confirmation_packet"
            found = (sum(len(r.get("invoice_references", [])) for r in report.get("rate_confirmations", []))
                     if rate else sum(f.get("value") is not None for f in report.get("fields", {}).values()))
            results.append({"run_id": folder.name, "source": report.get("source", "Document"),
                            "document_type": report.get("document_type", "invoice_candidate"),
                            "pages": len(report.get("pages", [])), "values": found,
                            "reviewed": report_path.name == "reviewed_result.json",
                            "updated_at": datetime.fromtimestamp(changed, timezone.utc).isoformat(), **review_summary(report)})
        except (OSError, ValueError, TypeError):
            continue
    return results


JOBS = {}
JOB_LOCK = threading.Lock()


def job_status(run_id):
    with JOB_LOCK:
        if run_id in JOBS:
            return copy.deepcopy(JOBS[run_id])
    path = RUNS/run_id/'output/model_job.json'
    if not path.is_file():
        return {'status': 'not_run'}
    job = json.loads(path.read_text())
    if job['status'] == 'running':
        job.update(status='interrupted', message='The app restarted before analysis completed. Run AI analysis to resume.')
    return job


def update_job(run_id, **changes):
    with JOB_LOCK:
        JOBS[run_id].update(changes)
        save_json(RUNS/run_id/'output/model_job.json', JOBS[run_id])


def start_model_job(run_id, model, stage='analyze'):
    if stage not in ('analyze', 'extract', 'review'):
        raise ValueError('Unknown analysis action.')
    saved = saved_result(run_id)
    if saved is None:
        raise ValueError('Analysis not found.')
    if model not in available_models():
        raise ValueError('Choose an installed model before running AI analysis.')
    pages = [request['page'] for request in build_requests(saved['report'], model, 'review' if stage == 'review' else 'extract')]
    if not pages:
        raise ValueError('No extractable document fields were found. Inspect the OCR transcript first.')
    with JOB_LOCK:
        if JOBS.get(run_id, {}).get('status') == 'running':
            return copy.deepcopy(JOBS[run_id])
        job = {'status': 'running', 'model': model, 'stage': stage, 'pages': pages,
               'completed_pages': [], 'page': pages[0], 'phase': 'extract' if stage != 'review' else 'review',
               'message': 'Preparing AI analysis', 'warnings': [], 'started_at': datetime.now(timezone.utc).isoformat()}
        JOBS[run_id] = job
        save_json(RUNS/run_id/'output/model_job.json', job)
    threading.Thread(target=execute_model_job, args=(run_id, model, stage, pages), daemon=True).start()
    return job


def execute_model_job(run_id, model, stage, pages):
    path = RUNS/run_id/'output/reviewed_result.json'
    try:
        for number in pages:
            if stage != 'review':
                update_job(run_id, page=number, phase='extract', message=f'Reading OCR passages and extracting fields on page {number}')
                snapshot = saved_result(run_id)['report']
                page_report = {**snapshot, 'pages': [p for p in snapshot['pages'] if p['number'] == number]}
                extraction = ollama_extract(page_report, model, evidence)
                with WRITE_LOCK:
                    latest = saved_result(run_id)['report']
                    retained = [entry for entry in latest.get('llm_extraction', []) if entry['page'] != number]
                    apply_extraction(latest, sorted(retained + extraction, key=lambda entry: entry['page']))
                    latest['totals_validation'] = totals_check(latest['fields'], latest['lines'])
                    validate_fields(latest['fields']); score_fields(latest['fields'], latest['totals_validation'])
                    assess_rate_fields(latest['rate_confirmations'])
                    latest['llm_review'] = [entry for entry in latest.get('llm_review', []) if entry['page'] != number]
                    latest['llm_error'] = None
                    save_json(path, latest)
            if stage != 'extract':
                update_job(run_id, page=number, phase='review', message=f'Checking current values against OCR passages on page {number}')
                saved = saved_result(run_id)
                snapshot = saved['report']
                page_report = {**snapshot, 'pages': [p for p in snapshot['pages'] if p['number'] == number]}
                reviews = ollama_review(page_report, model)
                with WRITE_LOCK:
                    current = saved_result(run_id)
                    if current['revision'] == saved['revision']:
                        latest = current['report']
                        latest['llm_review'] = sorted([entry for entry in latest.get('llm_review', []) if entry['page'] != number] + reviews, key=lambda entry: entry['page'])
                        latest['llm_model'] = model; latest['llm_error'] = None
                        save_json(path, latest)
                    else:
                        update_job(run_id, warnings=job_status(run_id)['warnings'] + [f'Page {number} changed during review. Its stale review was discarded; rerun verification for the latest values.'])
            completed = job_status(run_id)['completed_pages'] + [number]
            update_job(run_id, completed_pages=completed)
        update_job(run_id, status='complete', phase='complete', message='AI analysis completed', finished_at=datetime.now(timezone.utc).isoformat())
    except Exception as exc:
        update_job(run_id, status='failed', message=str(exc), finished_at=datetime.now(timezone.utc).isoformat())


class Handler(BaseHTTPRequestHandler):
    server_version = "InvoiceEvidenceLab/1.0"

    def log_message(self, format, *args):
        # Keep filenames and OCR text out of the terminal log.
        print("UI request:", self.command, urlparse(self.path).path.split("?")[0])

    def send_bytes(self, data: bytes, mime: str, status=200):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; connect-src 'self'; object-src 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, data, status=200):
        self.send_bytes(json.dumps(data, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8", status)

    def do_GET(self):
        path = urlparse(self.path).path
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/ui/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/ui/app.js": ("app.js", "text/javascript; charset=utf-8")}
        if path in assets:
            filename, mime = assets[path]
            self.send_bytes((ASSETS / filename).read_bytes(), mime)
            return
        if path == "/api/health":
            self.send_json({"status": "ok", "service": "Invoice Evidence Lab"})
            return
        job = re.fullmatch(r"/api/model-job/([0-9a-f]{32})", path)
        if job:
            self.send_json(job_status(job.group(1)))
            return
        if path == "/api/models":
            self.send_json(model_status())
            return
        if path == "/api/history":
            self.send_json({"results": recent_results()})
            return
        saved = re.fullmatch(r"/api/report/([0-9a-f]{32})", path)
        if saved:
            try:
                result = saved_result(saved.group(1))
                self.send_json(result if result is not None else {"error": "Result not found."},
                               200 if result is not None else 404)
            except (OSError, ValueError, TypeError) as exc:
                self.send_json({"error": f"Could not open the saved result: {exc}"}, 500)
            return
        original = re.fullmatch(r"/api/original/([0-9a-f]{32})", path)
        if original:
            source = RUNS / original.group(1) / "output" / "result.json"
            if source.is_file():
                self.send_bytes(source.read_bytes(), "application/json; charset=utf-8")
                return
        match = re.fullmatch(r"/api/preview/([0-9a-f]{32})/(\d{1,3})", path)
        if match:
            run_id, page = match.groups()
            image = RUNS / run_id / "output" / f"page-{int(page)}.png"
            if image.is_file():
                self.send_bytes(image.read_bytes(), "image/png")
                return
        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        path = urlparse(self.path).path
        model_job = re.fullmatch(r"/api/model-job/([0-9a-f]{32})", path)
        if model_job:
            try:
                options = self.read_json_body()
                self.send_json(start_model_job(model_job.group(1), options.get('model'), options.get('stage', 'analyze')), 202)
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({'error': str(exc)}, 400)
            except Exception as exc:
                self.send_json({'error': f'Could not start local AI analysis: {exc}'}, 503)
            return
        preview = re.fullmatch(r"/api/llm-preview/([0-9a-f]{32})", path)
        if preview:
            try:
                saved = saved_result(preview.group(1))
                if saved is None:
                    self.send_json({"error": "Analysis not found."}, 404)
                    return
                options = self.read_json_body()
                requests = build_requests(saved["report"], options.get("model") or "<select-installed-model>", options.get("stage", "extract"))
                self.send_json({"requests": requests, "revision": saved["revision"],
                                "stage": options.get("stage", "extract"),
                                "note": "This exact builder also constructs actual Ollama requests. Preview uses saved values; no model call is made."})
            except (ValueError, KeyError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        batch = re.fullmatch(r"/api/review-batch/([0-9a-f]{32})", path)
        if batch:
            try:
                options = self.read_json_body(max_size=256*1024)
                folder = RUNS / batch.group(1)
                original = folder / "output" / "result.json"
                reviewed = folder / "output" / "reviewed_result.json"
                if not original.is_file():
                    self.send_json({"error": "Analysis not found."}, 404)
                    return
                with WRITE_LOCK:
                    current = reviewed if reviewed.is_file() else original
                    if options.get("revision") and options["revision"] != report_revision(current):
                        self.send_json({"error": "This analysis changed in another window. Reopen it before saving."}, 409)
                        return
                    report = apply_review_batch(json.loads(current.read_text()), options.get("changes"))
                    save_json(reviewed, report)
                self.send_json(saved_result(batch.group(1)))
            except (ValueError, KeyError, TypeError, IndexError) as exc:
                self.send_json({"error": str(exc)}, 400)
            except OSError as exc:
                self.send_json({"error": f"Could not save changes: {exc}"}, 500)
            return
        review = re.fullmatch(r"/api/review/([0-9a-f]{32})", path)
        llm = re.fullmatch(r"/api/llm-review/([0-9a-f]{32})", path)
        model_extract = re.fullmatch(r"/api/llm-extract/([0-9a-f]{32})", path)
        if review or llm or model_extract:
            try:
                run_id = (review or llm or model_extract).group(1)
                folder = RUNS / run_id
                original = folder / "output" / "result.json"
                if not original.is_file():
                    self.send_json({"error": "Analysis run not found."}, 404)
                    return
                with WRITE_LOCK:
                    reviewed = folder / "output" / "reviewed_result.json"
                    report = json.loads((reviewed if reviewed.exists() else original).read_text())
                    if review:
                        change = self.read_json_body()
                        apply_human_review(report, change)
                        if change.get("kind") == "invoice":
                            report["totals_validation"] = totals_check(report["fields"], report.get("lines", []))
                    else:
                        options = self.read_json_body()
                        model = options.get("model") or report.get("llm_model")
                        if not model:
                            raise ValueError("Choose an installed Ollama model first.")
                        if model_extract:
                            proposals = ollama_extract(report, model, evidence)
                            apply_extraction(report, proposals)
                            report["totals_validation"] = totals_check(report["fields"], report["lines"])
                            validate_fields(report["fields"])
                            score_fields(report["fields"], report["totals_validation"])
                            assess_rate_fields(report["rate_confirmations"])
                            # Old model reviews refer to values before this new extraction.
                            report["llm_review"] = []
                            for stage in report.get("stages", []):
                                if stage["name"] == "ollama_extract": stage["status"] = "complete"
                                if stage["name"] == "ollama_review": stage["status"] = "pending"
                        else:
                            report["llm_review"] = ollama_review(report, model)
                            for stage in report.get("stages", []):
                                if stage["name"] == "ollama_review": stage["status"] = "complete"
                        report["llm_model"] = model
                        report["llm_error"] = None
                    save_json(reviewed, report)
                    truth = folder / "reviewed_truth.json"
                    assessment = evaluate(reviewed, truth) if truth.is_file() else None
                self.send_json({"report": report, "evaluation": assessment, "revision": report_revision(reviewed)})
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                self.send_json({"error": str(exc)}, 400)
            except Exception as exc:
                self.send_json({"error": f"Ollama unavailable: {exc}" if (llm or model_extract) else f"Could not save review: {exc}"}, 503 if (llm or model_extract) else 500)
            return
        if path != "/api/extract":
            self.send_json({"error": "Not found"}, 404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST:
                raise ValueError("Choose a file smaller than 25 MB.")
            fields = parse_form(self.headers.get("Content-Type", ""), self.rfile.read(length))
            uploaded = fields.get("document")
            if not uploaded or not uploaded["filename"] or not uploaded["data"]:
                raise ValueError("Choose a PDF or image file first.")
            name = Path(uploaded["filename"].replace("\\", "/")).name
            suffix = Path(name).suffix.lower()
            if suffix not in EXTENSIONS:
                raise ValueError("Supported files: PDF, PNG, JPG, WebP, TIFF.")
            if len(uploaded["data"]) > 25 * 1024 * 1024:
                raise ValueError("Choose a file smaller than 25 MB.")
            run_id = uuid.uuid4().hex
            folder = RUNS / run_id
            folder.mkdir(parents=True)
            safe_name = re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:100]
            source = folder / safe_name
            source.write_bytes(uploaded["data"])
            force = fields.get("force_ocr", {}).get("data") == b"true"
            report = run(source, folder / "output", force_ocr=force)
            assessment, assessment_error = None, None
            truth = fields.get("truth")
            if truth and truth["data"]:
                try:
                    truth_path = folder / "reviewed_truth.json"
                    truth_path.write_bytes(truth["data"])
                    assessment = evaluate(folder / "output" / "result.json", truth_path)
                except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
                    assessment_error = f"Ground truth could not be evaluated: {exc}"
            self.send_json({"run_id": run_id, "report": report,
                            "evaluation": assessment, "evaluation_error": assessment_error,
                            "revision": report_revision(folder / "output" / "result.json")})
        except (ValueError, OSError, RuntimeError) as exc:
            self.send_json({"error": str(exc)}, 400)
        except Exception as exc:
            self.send_json({"error": f"Extraction failed: {exc}"}, 500)

    def read_json_body(self, max_size=4096):
        size = int(self.headers.get("Content-Length", "0"))
        if size < 1 or size > max_size:
            raise ValueError("Review request is empty or too large.")
        content = json.loads(self.rfile.read(size))
        if not isinstance(content, dict):
            raise ValueError("Invalid review request.")
        return content


def apply_human_review(report, change):
    """Preserve original OCR evidence; change only a human-reviewed value."""
    kind, field = change.get("kind"), change.get("field")
    value, note = change.get("value"), change.get("note", "")
    if not isinstance(value, str) or len(value) > 200:
        raise ValueError("Enter a value up to 200 characters; leave it blank to clear the field.")
    if not isinstance(note, str) or len(note) > 500:
        raise ValueError("Review note is too long.")
    value, note = value.strip(), note.strip()
    target, review_info = None, None
    if kind == "invoice" and report["document_type"] != "rate_confirmation_packet":
        if field not in report["fields"]:
            raise ValueError("Unknown invoice field.")
        target = report["fields"][field]
    elif kind == "rate" and report["document_type"] == "rate_confirmation_packet":
        page = change.get("page")
        record = next((r for r in report["rate_confirmations"] if r["page"] == page), None)
        if record is None or field not in ("carrier", "order_number", "invoice_references", "total_carrier_pay"):
            raise ValueError("Unknown rate-confirmation field.")
        if field == "invoice_references":
            index = change.get("index")
            if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(record[field]):
                raise ValueError("Unknown invoice reference.")
            target = record[field][index]
        else:
            target = record[field]
    elif kind == "item" and report["document_type"] != "rate_confirmation_packet":
        index = change.get("index")
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(report["items"]):
            raise ValueError("Unknown item row.")
        if field not in ("description", "quantity", "rate", "amount"):
            raise ValueError("Unknown item field.")
        item = report["items"][index]
        old = item[field]
        reviews = item.setdefault("human_reviews", {})
        prior = reviews.get(field, {})
        item_original = prior.get("original_value", old)
        reviews[field] = {"original_value": item_original, "value": value,
                          "status": "human_verified" if value == item_original else "human_corrected",
                          "note": note, "updated_at": datetime.now(timezone.utc).isoformat()}
        report.setdefault("review_history", []).append({**change, "previous_value": old,
            "updated_at": datetime.now(timezone.utc).isoformat()})
        report["llm_review"] = []
        item[field] = value
        try:
            result = Decimal(item["quantity"]) * Decimal(item["rate"])
            item["arithmetic_check"] = "PASS" if abs(result - Decimal(item["amount"])) <= Decimal("0.01") else "MISMATCH"
        except (InvalidOperation, ValueError):
            item["arithmetic_check"] = "not_checked"
        return
    else:
        raise ValueError("This document does not contain that field type.")
    original = target.get("original_value", target.get("value"))
    report.setdefault("review_history", []).append({**change, "previous_value": target.get("value"),
        "updated_at": datetime.now(timezone.utc).isoformat()})
    report["llm_review"] = []
    for stage in report.get("stages", []):
        if stage["name"] == "ollama_review": stage["status"] = "pending"
    target["original_value"] = original
    target["value"] = value or None
    target["review_status"] = "human_verified" if value == original else "human_corrected"
    target["human_review"] = {"note": note, "updated_at": datetime.now(timezone.utc).isoformat()}


def apply_review_batch(report, changes):
    """Validate the entire batch on a copy before any report is written."""
    if not isinstance(changes, list) or not 1 <= len(changes) <= 500:
        raise ValueError("Save between 1 and 500 field changes at a time.")
    updated = copy.deepcopy(report)
    identities = set()
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("Invalid field change.")
        identity = (change.get("kind"), change.get("page"), change.get("field"), change.get("index"))
        if identity in identities:
            raise ValueError("A field appears twice in this save.")
        identities.add(identity)
        apply_human_review(updated, change)
    if updated.get("fields"):
        updated["totals_validation"] = totals_check(updated["fields"], updated.get("lines", []))
    for stage in updated.get("stages", []):
        if stage["name"] == "ollama_review": stage["status"] = "pending"
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    RUNS.mkdir(exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    url = f"http://127.0.0.1:{args.port}"
    print(f"Invoice Evidence Lab UI: {url}")
    print("Press Ctrl+C to stop. Files stay in the local ui-runs folder.")
    if not args.no_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
