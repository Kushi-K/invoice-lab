"""Evidence-first invoice extraction from PDFs and images.

Requires Tesseract executable on PATH. Optional local LLM passes use Ollama or
llama.cpp. No LLM proposal silently replaces evidence from the document.
"""
from __future__ import annotations

import argparse
import io
import json
import re
import subprocess
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

from invoice_core.document import union, load_document, tesseract_words
from invoice_core.confidence import evidence, score_fields, assess_rate_fields
from invoice_core.structure import structure_document
from invoice_core.llm import ollama_extract, ollama_review

from invoice_core.extraction import (FIELDS, GSTIN, DATE, INVOICE, MONEY, blank, extract, extract_items, extract_rate_confirmations)
from invoice_core.validation import totals_check, validate_fields
from invoice_core.llm import apply_extraction

from invoice_core.legacy_llm import llm_suggestions, llama_server_review


def annotated_pages(pages, lines, output):
    output.mkdir(parents=True, exist_ok=True)
    for page in pages:
        img = Image.open(io.BytesIO(page["png"])).convert("RGB")
        draw = ImageDraw.Draw(img)
        ratio_x, ratio_y = img.width/page["size"][0], img.height/page["size"][1]
        for line in lines:
            if line["page"] != page["number"] or "-lheader-" in line["id"]:
                continue
            x0,y0,x1,y1 = line["box"]
            draw.rectangle((x0*ratio_x,y0*ratio_y,x1*ratio_x,y1*ratio_y), outline="red", width=2)
            draw.text((x0*ratio_x,max(0,y0*ratio_y-12)), line["id"], fill="blue")
        img.save(output / f"page-{page['number']}.png")


def run(source, output, force_ocr=False, llm_model=None, llama_server=False):
    """Save text and layout stages before optional field extraction/review."""
    from invoice_core.storage import save_json
    output.mkdir(parents=True, exist_ok=True)
    pages, lines = load_document(source, force_ocr)
    page_metadata = [{k: v for k, v in p.items() if k != "png"} for p in pages]
    save_json(output / "extracted.json", {"pages": page_metadata, "lines": lines})
    structure = structure_document(lines, pages)
    save_json(output / "structured.json", structure)
    fields, candidates = extract(lines)
    items = extract_items(lines)
    validation = totals_check(fields, lines)
    validate_fields(fields)
    score_fields(fields, validation)
    rate_confirmations = extract_rate_confirmations(lines)
    assess_rate_fields(rate_confirmations)
    report = {"schema_version": 2, "source": source.name, "pages": page_metadata,
              "document_type": "rate_confirmation_packet" if rate_confirmations else "invoice_candidate",
              "rate_confirmations": rate_confirmations, "fields": fields, "items": items,
              "totals_validation": validation, "candidates": candidates,
              "llm_suggestions": {}, "llm_review": [], "llm_extraction": [],
              "llm_model": llm_model, "llm_error": None, "lines": lines, "structure": structure,
              "stages": [{"name": "text", "status": "complete", "artifact": "extracted.json"},
                         {"name": "structure", "status": "complete", "artifact": "structured.json"},
                         {"name": "rule_fields", "status": "complete"},
                         {"name": "ollama_extract", "status": "pending"},
                         {"name": "ollama_review", "status": "pending"}],
              "note": "Evidence scores are heuristics. LLM scores are self-assessed opinions. Neither is measured accuracy."}
    if llm_model:
        try:
            apply_extraction(report, ollama_extract(report, llm_model, evidence))
            report["totals_validation"] = totals_check(report["fields"], lines)
            validate_fields(report["fields"])
            score_fields(report["fields"], report["totals_validation"])
            assess_rate_fields(report["rate_confirmations"])
            report["stages"][3]["status"] = "complete"
            report["llm_review"] = ollama_review(report, llm_model)
            report["stages"][4]["status"] = "complete"
        except Exception as exc:
            report["llm_error"] = f"Ollama unavailable or response invalid: {exc}"
    elif llama_server:
        try:
            report["llm_review"] = llama_server_review(lines, fields, rate_confirmations)
        except Exception as exc:
            report["llm_error"] = f"Legacy llama.cpp review unavailable: {exc}"
    save_json(output / "result.json", report)
    annotated_pages(pages, lines, output)
    return report


def evaluate(result_path, truth_path):
    result = json.loads(result_path.read_text())
    truth = json.loads(truth_path.read_text())
    rows = {}
    field_truth = truth.get("fields", {k: v for k, v in truth.items() if k in FIELDS})
    if set(truth) - set(FIELDS) - {"fields", "rate_confirmations"}:
        raise ValueError("Unknown ground-truth key")
    for name, expected in field_truth.items():
        if name not in FIELDS:
            raise ValueError(f"Unknown ground-truth field: {name}")
        predicted = result["fields"][name]["value"]
        rows[name] = {"expected": expected, "predicted": predicted,
                      "exact_match": str(predicted).strip().casefold() == str(expected).strip().casefold()}
    rate_rows, correct_refs, checked_refs = [], 0, 0
    by_page = {record["page"]: record for record in result.get("rate_confirmations", [])}
    for checked in truth.get("rate_confirmations", []):
        page = checked["page"]
        found = by_page.get(page, {})
        expected = [str(value).strip() for value in checked.get("invoice_references", [])]
        predicted = [str(entry["value"]).strip() for entry in found.get("invoice_references", [])]
        reference_rows = []
        for i in range(max(len(expected), len(predicted))):
            a = expected[i] if i < len(expected) else None
            b = predicted[i] if i < len(predicted) else None
            match = a is not None and a == b
            reference_rows.append({"expected": a, "predicted": b, "exact_match": match})
            checked_refs += 1
            correct_refs += int(match)
        row = {"page": page, "invoice_references": reference_rows}
        if "total_carrier_pay" in checked:
            pay = found.get("total_carrier_pay", {}).get("value")
            row["total_carrier_pay"] = {"expected": checked["total_carrier_pay"],
                                        "predicted": pay,
                                        "exact_match": str(pay) == str(checked["total_carrier_pay"])}
        rate_rows.append(row)
    return {"fields_checked": len(rows), "exact_match_accuracy":
            round(sum(r["exact_match"] for r in rows.values())/len(rows), 3) if rows else None,
            "per_field": rows, "rate_confirmations_checked": len(rate_rows),
            "reference_values_checked": checked_refs,
            "reference_exact_match_accuracy": round(correct_refs/checked_refs, 3) if checked_refs else None,
            "per_rate_confirmation": rate_rows}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("extract"); p.add_argument("file", type=Path)
    p.add_argument("--out", type=Path, default=Path("output"))
    p.add_argument("--force-ocr", action="store_true", help="Run Tesseract even on searchable PDFs")
    p.add_argument("--llm-model", help="Optional installed local Ollama model")
    p.add_argument("--llama-server", action="store_true", help="Review evidence with local llama.cpp at 127.0.0.1:8080")
    e = sub.add_parser("evaluate"); e.add_argument("result", type=Path); e.add_argument("truth", type=Path)
    a = parser.parse_args()
    if a.command == "evaluate":
        print(json.dumps(evaluate(a.result, a.truth), indent=2)); return
    if not a.file.is_file() or a.file.suffix.lower() not in (".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
        parser.error("Input must be an existing PDF or supported image")
    report = run(a.file, a.out, a.force_ocr, a.llm_model, a.llama_server)
    print(json.dumps({"fields": {k:v["value"] for k,v in report["fields"].items()},
                      "document_type": report["document_type"],
                      "rate_confirmation_pages": [r["page"] for r in report["rate_confirmations"]],
                      "page_methods": [p["method"] for p in report["pages"]],
                      "llm_review_pages": [r["page"] for r in report["llm_review"]],
                      "result": str(a.out/"result.json"), "llm_error": report["llm_error"]}, indent=2))


if __name__ == "__main__":
    main()
