"""Compatibility adapters for prior CLI runs; the UI uses Ollama."""
import json
import re
import urllib.request
from .extraction import GSTIN, DATE, INVOICE, MONEY
from .confidence import evidence

def llm_suggestions(lines, missing, model):
    """Local LLM proposes line IDs only. The program resolves values from OCR text."""
    allowed = [l for l in lines if any(x in l["text"].lower() for x in
               ("gst", "invoice", "date", "total", "supplier", "buyer", "bill", "seller"))]
    prompt = ("Choose supporting line IDs for missing invoice fields. Return a JSON object "
              "mapping each field to a line ID or null. Never invent text. Fields: "
              + ", ".join(missing) + "\nLines:\n" + "\n".join(f"{l['id']}: {l['text']}" for l in allowed[:120]))
    schema = {"type": "object", "properties": {k: {"type": ["string", "null"]} for k in missing},
              "required": missing, "additionalProperties": False}
    req = urllib.request.Request("http://127.0.0.1:11434/api/generate",
          json.dumps({"model": model, "prompt": prompt, "format": schema, "stream": False}).encode(),
          {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as response:
        proposed = json.loads(json.load(response)["response"])
    by_id = {l["id"]: l for l in allowed}
    suggestions = {}
    for name in missing:
        line = by_id.get(proposed.get(name))
        if not line:
            continue
        text = line["text"]
        pattern = GSTIN if name.endswith("gstin") else INVOICE if name == "invoice_number" else DATE if name == "invoice_date" else MONEY
        matches = list(pattern.finditer(text))
        if len(matches) != 1:
            continue
        value = matches[0].group(1) if name in ("invoice_number", "grand_total") else matches[0].group()
        suggestions[name] = evidence(line, value, "LLM line suggestion; verify section and image")
        suggestions[name]["review_status"] = "suggestion_only"
    return suggestions


def llama_server_review(lines, fields, rate_confirmations, url="http://127.0.0.1:8080"):
    """Ask a local model to independently select OCR evidence IDs, then verify IDs.

    This is a second opinion on layout assignment, not a confidence probability.
    The actual values and boxes always come from OCR/PDF evidence, not model text.
    """
    reviews = []
    records = rate_confirmations or [None]
    for record in records:
        page = record["page"] if record else 1
        page_lines = [line for line in lines if line["page"] == page]
        if record:
            targets = {key: record[key] for key in ("carrier", "order_number", "total_carrier_pay")}
            targets["invoice_references"] = record["invoice_references"]
        else:
            targets = fields
        target_ids = set()
        for value in targets.values():
            entries = value if isinstance(value, list) else [value]
            target_ids.update(entry.get("line_id") for entry in entries if entry.get("line_id"))
        # Keep the prompt short enough for a small CPU model while including
        # each extracted value, its heading, and plausible competing labels.
        selected = []
        for i, line in enumerate(page_lines):
            relevant = (line["id"] in target_ids or
                        re.search(r"\b(?:gstin|invoice|supplier|seller|buyer|bill|carrier|order|date|taxable|cgst|sgst|igst|total)\b",
                                  line["text"], re.I))
            if relevant:
                selected.extend(page_lines[max(0, i-1):i+2])
        by_id = {line["id"]: line for line in selected}
        priority = [line for line in by_id.values() if line["id"] in target_ids]
        selected = (priority + [line for line in by_id.values() if line["id"] not in target_ids])[:65]
        by_id = {line["id"]: line for line in selected}
        allowed_ids = {line["id"] for line in selected}
        invoice_headings = [line for line in page_lines
                            if re.fullmatch(r"\s*INVOICE\s+NO\.?\s*", line["text"], re.I)]
        def is_invoice_reference(line_id):
            line = by_id[line_id]
            if not re.fullmatch(r"\d{8,12}", line["text"].strip()):
                return False
            return any(0 <= line["box"][1]-heading["box"][3] <= line["page_size"][1]*.09
                       and abs(line["box"][0]-heading["box"][0]) < line["page_size"][0]*.05
                       for heading in invoice_headings)
        prompt = ("Read the following OCR/PDF lines from ONE page. Map each requested field to "
                  "the line ID containing its value; use null if absent or uncertain. "
                  "For invoice_references, return a list of line IDs. "
                  "Do not guess values or invent IDs. Return only a JSON object.\n"
                  f"Fields: {', '.join(targets)}\n"
                  + "\n".join(f"{line['id']}: {line['text'][:160]}" for line in selected))
        request = urllib.request.Request(url.rstrip("/") + "/v1/chat/completions",
            data=json.dumps({"model": "local", "messages": [
                {"role": "system", "content": "You identify evidence lines in invoices. Reply with JSON only."},
                {"role": "user", "content": prompt}], "temperature": 0,
                "max_tokens": 320, "response_format": {"type": "json_object"}}).encode(),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=240) as response:
            proposed = json.loads(json.load(response)["choices"][0]["message"]["content"])
        if not isinstance(proposed, dict):
            raise ValueError("Model did not return a JSON object")
        def resolve(token):
            if not isinstance(token, str):
                return None, None
            if token in allowed_ids:
                return token, "line_id"
            # Small models sometimes return the printed value despite being
            # asked for an ID. Accept it only when it occurs on exactly one
            # provided OCR line; an amount repeated on two lines is ambiguous.
            if not token.strip() or len(token) > 160:
                return None, None
            pattern = re.compile(r"(?<!\w)" + re.escape(token.strip()) + r"(?!\w)", re.I)
            matching = [line["id"] for line in selected if pattern.search(line["text"])]
            return (matching[0], "unique_source_text") if len(matching) == 1 else (None, None)
        comparisons = {}
        for key, expected in targets.items():
            prediction = proposed.get(key)
            if isinstance(expected, list):
                ids = prediction if isinstance(prediction, list) else []
                resolved = [resolve(entry) for entry in ids]
                valid, modes, rejected = [], [], []
                for entry, (line_id, mode) in zip(ids, resolved):
                    if not line_id:
                        rejected.append({"proposal": entry, "reason": "no unique OCR line"})
                    elif key == "invoice_references" and not is_invoice_reference(line_id):
                        rejected.append({"proposal": entry, "reason": "not a number below INVOICE NO."})
                    elif line_id in valid:
                        rejected.append({"proposal": entry, "reason": "duplicate line"})
                    else:
                        valid.append(line_id)
                        modes.append(mode)
                actual = [entry["line_id"] for entry in expected]
                comparisons[key] = {"proposed_line_ids": valid,
                                    "raw_proposals": ids,
                                    "resolutions": modes, "rejected_proposals": rejected,
                                    "rule_line_ids": actual, "verified_subset_matches_rules": valid == actual,
                                    "agrees_with_rules": valid == actual and not rejected,
                                    "source_texts": [by_id[entry]["text"] for entry in valid]}
            else:
                valid, mode = resolve(prediction)
                actual = expected.get("line_id")
                comparisons[key] = {"proposed_line_id": valid,
                                    "raw_proposal": prediction, "resolution": mode,
                                    "rejected_id": prediction if prediction is not None and valid is None else None,
                                    "rule_line_id": actual, "agrees_with_rules": valid == actual,
                                    "source_text": by_id[valid]["text"] if valid else None}
        reviews.append({"page": page, "fields": comparisons,
                        "note": "Agreement checks line selection only; neither system proves the printed value is correct."})
    return reviews


