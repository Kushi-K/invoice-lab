"""Explainable evidence heuristics; never calibrated accuracy probabilities."""

def evidence(line, value, reason, checks=None, format_ok=True):
    # Prefer the words containing the field value rather than an entire label line.
    tokens = str(value).casefold().split()
    source_words = line.get("words", [])
    selected = []
    for start in range(len(source_words)):
        part = source_words[start:start+len(tokens)]
        if [w["text"].casefold().strip(":") for w in part] == tokens:
            selected = part
            break
    if not selected and len(tokens) == 1:
        normalized = tokens[0].replace(",", "").replace("₹", "").replace("$", "")
        selected = [w for w in source_words if w["text"].casefold().strip(":").replace(",", "").replace("₹", "").replace("$", "") == normalized]
    selected = selected or source_words
    conf = [w["ocr_confidence"] for w in selected if w["ocr_confidence"] is not None]
    ocr = round(sum(conf)/len(conf), 3) if conf else None
    # This is a transparent triage heuristic, never an accuracy probability.
    parts = {"ocr": ocr, "label_or_section": 1.0 if not reason.startswith("Ollama") and any(k in reason for k in ("label", "section", "heading")) else 0.5,
             "format": 1.0 if format_ok else 0.0,
             "consistency": 1.0 if checks is True else (0.0 if checks is False else None)}
    weighted = [(parts[k], weight) for k, weight in
                (("ocr", .35), ("label_or_section", .35), ("format", .2), ("consistency", .1))
                if parts[k] is not None]
    score = round(sum(v * w for v, w in weighted)/sum(w for _, w in weighted), 3)
    return {"value": value, "page": line["page"], "box": line["box"], "line_id": line["id"],
            "word_ids": [w["id"] for w in selected], "confidence_basis": "value words" if selected != source_words else "supporting line", "source_text": line["text"],
            "method": line["method"], "reason": reason, "ocr_confidence": ocr,
            "triage_score": score, "triage_components": parts,
            "review_status": "pending" if format_ok else "needs_review"}




def score_fields(fields, validation):
    """Update consistency after extraction; retain components for inspection."""
    for key, field in fields.items():
        if field.get("review_status", "").startswith("human_"):
            continue
        parts = field.get("triage_components")
        if not parts:
            field["review_priority"] = "high"
            continue
        if key in ("taxable_amount", "cgst", "sgst", "igst", "grand_total"):
            parts["consistency"] = {"PASS": 1.0, "MISMATCH": 0.0}.get(validation["status"])
        weighted = [(parts[k], weight) for k, weight in
                    (("ocr", .35), ("label_or_section", .35), ("format", .2), ("consistency", .1))
                    if parts[k] is not None]
        field["triage_score"] = round(sum(v*w for v,w in weighted)/sum(w for _,w in weighted), 3)
        field["review_priority"] = ("high" if field.get("extraction_conflict") or parts["format"] == 0 or parts["consistency"] == 0
                                    or field["triage_score"] < .7 else
                                    "medium" if field["triage_score"] < .9 else "low")
    return fields


def assess_rate_fields(records):
    for record in records:
        fields = {k: record[k] for k in ("carrier", "order_number", "total_carrier_pay")}
        fields.update({f"invoice_reference_{i}": field for i, field in enumerate(record["invoice_references"])})
        score_fields(fields, {"status": "not_checked"})
