"""Conservative rule baseline and field/item candidates."""
import re
from decimal import Decimal, InvalidOperation
from .document import union
from .confidence import evidence

GSTIN = re.compile(r"\b\d{2}[A-Z]{5}\d{4}[A-Z][A-Z0-9]Z[A-Z0-9]\b", re.I)
DATE = re.compile(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b")
MONTH_DATE = re.compile(r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},?\s+\d{4}\b", re.I)
MONEY = re.compile(r"(?:₹|Rs\.?|INR)?\s*(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d+(?:\.\d{2})?)\b", re.I)
INVOICE = re.compile(r"\b(?:invoice\s*(?:no\.?|number|#)|inv\.?\s*no\.?)\s*[:#-]?\s*([A-Z0-9][A-Z0-9/-]*\d[A-Z0-9/-]*)", re.I)
INLINE_INVOICE = re.compile(r"^\s*INVOICE\s+([A-Z0-9/-]*\d[A-Z0-9/-]*)\s*$", re.I)
FIELDS = ("supplier_name", "supplier_gstin", "buyer_name", "buyer_gstin",
          "invoice_number", "invoice_date", "taxable_amount", "cgst", "sgst", "igst", "grand_total")


def blank(reason):
    return {"value": None, "reason": reason, "review_status": "pending", "triage_score": None}


def choose(candidates):
    if len(candidates) == 1:
        return candidates[0]
    if candidates and len({c["value"] for c in candidates}) == 1:
        return candidates[0]  # Repeated matching value, e.g. total amount and amount due.
    return blank("not_found" if not candidates else "ambiguous: review candidates")


def adjacent_amount(line, lines):
    """Read a value in a separate OCR line to the right of a label."""
    cy = (line["box"][1]+line["box"][3])/2
    near = []
    for candidate in lines:
        if candidate is line or candidate["page"] != line["page"]:
            continue
        center = (candidate["box"][1]+candidate["box"][3])/2
        if abs(center-cy) > line["page_size"][1]*.012:
            continue
        if candidate["box"][0] < line["box"][2]-line["page_size"][0]*.02:
            continue
        matches = list(MONEY.finditer(candidate["text"]))
        if len(matches) == 1 and not re.search(r"[A-Za-z]", candidate["text"]):
            near.append((candidate, matches[0].group(1).replace(",", "")))
    near.sort(key=lambda pair: pair[0]["box"][0])
    return near[0] if len(near) == 1 else None


def section_role(line, page_lines):
    """Use the nearest preceding section heading, not the GSTIN's occurrence order."""
    on_page = [l for l in page_lines if l["page"] == line["page"]]
    cy = (line["box"][1]+line["box"][3])/2
    headings = []
    for l in on_page:
        if l["box"][1] > cy:
            continue
        s = l["text"].lower()
        if re.search(r"\b(bill\s*to|billed\s*to|buyer|customer)\b", s):
            headings.append((l, "buyer"))
        if re.search(r"\b(from|seller|supplier|vendor)\b", s):
            headings.append((l, "supplier"))
    if headings:
        # Favor same column, then vertical proximity. Side-by-side sections may share a y.
        cx = (line["box"][0]+line["box"][2])/2
        ranked = sorted(((abs(cy-(h["box"][1]+h["box"][3])/2) / line["page_size"][1]
                          + abs(cx-(h["box"][0]+h["box"][2])/2)/line["page_size"][0], role)
                         for h, role in headings))
        if ranked[0][0] < .30 and (len(ranked) == 1 or ranked[1][0]-ranked[0][0] > .025):
            return ranked[0][1], "nearest section heading"
    return None, "section unclear"


def extract(lines):
    result = {name: blank("not_found") for name in FIELDS}
    gst = {"supplier": [], "buyer": [], "unassigned": []}
    numbers, dates, totals = [], [], []
    names = {"supplier": [], "buyer": []}
    raw_gstin = []
    masked_gst = {"supplier": [], "buyer": []}
    tax = {key: [] for key in ("taxable_amount", "cgst", "sgst", "igst")}
    bill_headings = [l for l in lines if re.search(r"\bbill\s*to\b", l["text"], re.I)]
    bill_y = min((l["box"][1] for l in bill_headings), default=None)
    ship_y = min((l["box"][1] for l in lines
                  if re.search(r"\bship+p?\s*to\b", l["text"], re.I)), default=None)
    for index, line in enumerate(lines):
        s = line["text"]
        for raw in re.finditer(r"\bGSTIN\s*:\s*[|_—-]*\s*([A-Z0-9]{8,15})\b", s, re.I):
            value = raw.group(1).upper()
            if not GSTIN.fullmatch(value):
                suggested = list(value)
                for position in (0, 1, 7, 8, 9, 10):
                    if position < len(suggested) and suggested[position] == "O":
                        suggested[position] = "0"
                candidate = evidence(line, value, "GSTIN label; masked or invalid format; image review required",
                                     format_ok=False)
                proposal = "".join(suggested)
                candidate["possible_ocr_correction"] = proposal if GSTIN.fullmatch(proposal) else None
                raw_gstin.append(candidate)
                y = line["box"][1]
                if bill_y is not None and y < bill_y:
                    masked_gst["supplier"].append(candidate)
                elif bill_y is not None and bill_y < y and (ship_y is None or y < ship_y):
                    masked_gst["buyer"].append(candidate)
        # A name directly below a standalone party heading is less ambiguous
        # than selecting the first large text on the page.
        header = re.fullmatch(r"\s*(Supplier|Seller|Vendor|From|Bill\s*To|Buyer|Customer)\s*:?\s*", s, re.I)
        if header and index+1 < len(lines):
            following = lines[index+1]
            role = "buyer" if re.search(r"bill|buyer|customer", header.group(1), re.I) else "supplier"
            if following["page"] == line["page"] and 0 < following["box"][1]-line["box"][1] < line["page_size"][1]*.06:
                if not GSTIN.search(following["text"]) and not re.search(r"\b(?:gstin|invoice|date)\b", following["text"], re.I):
                    names[role].append(evidence(following, following["text"], "party section heading"))
        for match in GSTIN.finditer(s):
            role, why = section_role(line, lines)
            if role is None and bill_y is not None and line["box"][1] < bill_y:
                role, why = "supplier", "business header before Bill To section"
            gst[role or "unassigned"].append(evidence(line, match.group().upper(), why))
        if m := INVOICE.search(s) or INLINE_INVOICE.search(s):
            numbers.append(evidence(line, m.group(1), "invoice number label"))
        if date_label := re.search(r"\b(?:invoice|inv\.?)\s*date\b|^\s*date\b", s, re.I):
            if m := DATE.search(s[date_label.end():]) or MONTH_DATE.search(s[date_label.end():]):
                dates.append(evidence(line, m.group(), "date label"))
        # A labelled name in the Bill To section may share an OCR line with
        # invoice metadata in a separate column.
        if (bill_y is not None and bill_y < line["box"][1]
                and (ship_y is None or line["box"][1] < ship_y)):
            if m := re.search(r"\bName\s*:\s*[|_—-]*\s*([A-Za-z][A-Za-z .&'-]+?)"
                              r"(?=\s+(?:Inv\.?\s*Date|Invoice\s*Date|Vehicle\s*Number|Address|GSTIN)\b|$)", s, re.I):
                names["buyer"].append(evidence(line, m.group(1).strip(), "Bill To section name label"))
        if re.search(r"\b(?:grand\s*total|total\s*amount|amount\s*payable|net\s*payable)\b", s, re.I):
            amount = list(MONEY.finditer(re.sub(r"(?i)grand\s*total|total\s*amount|amount\s*payable|net\s*payable", "", s)))
            if len(amount) == 1:
                value = amount[0].group(1).replace(",", "")
                totals.append(evidence(line, value, "grand total label"))
            elif not amount and (near := adjacent_amount(line, lines)):
                value_line, value = near
                totals.append(evidence(value_line, value, "grand total label on neighboring line"))
        for key, pattern in (("taxable_amount", r"taxable\s*(?:amount|value)"),
                             ("cgst", r"\bcgst\b"), ("sgst", r"\bsgst\b"), ("igst", r"\bigst\b")):
            if label := re.search(pattern, s, re.I):
                # Only inspect text after the tax label. A bank-account number
                # to its left must never become an empty IGST value.
                suffix = re.sub(r"^\s*(?:Amt|Amount)?\s*[:|—-]?\s*", "", s[label.end():], flags=re.I)
                matches = list(MONEY.finditer(suffix))
                if len(matches) == 1:
                    tax[key].append(evidence(line, matches[0].group(1).replace(",", ""), f"{key} label"))
                elif not matches and (near := adjacent_amount(line, lines)):
                    value_line, value = near
                    tax[key].append(evidence(value_line, value, f"{key} label on neighboring line"))
    # The taxable subtotal in a grid is aligned below the Taxable column.
    # Read its position, not the first arbitrary number on the subtotal row.
    if not tax["taxable_amount"]:
        for subtotal in (l for l in lines if re.search(r"\bsub[- ]?total\b", l["text"], re.I)):
            headers = [w for l in lines if l["page"] == subtotal["page"]
                       and l["box"][1] < subtotal["box"][1]
                       for w in l["words"] if w["text"].lower().strip(":") == "taxable"]
            if not headers:
                continue
            header = max(headers, key=lambda w: w["box"][1])
            x = (header["box"][0]+header["box"][2])/2
            aligned = [w for w in subtotal["words"]
                       if abs((w["box"][0]+w["box"][2])/2-x) < subtotal["page_size"][0]*.04
                       and re.fullmatch(r"[\d,]+(?:\.\d{2})?", w["text"])]
            if len(aligned) == 1:
                tax["taxable_amount"].append(evidence(subtotal, aligned[0]["text"].replace(",", ""),
                                                      "subtotal aligned with Taxable column"))
    # Conservative supplier name fallback for forms with a business block
    # above the Bill To grid but no explicit Supplier heading.
    if not names["supplier"] and bill_y is not None:
        for gst_line in (l for l in lines if l["box"][1] < bill_y
                         and re.search(r"\bGSTIN\s*:", l["text"], re.I)):
            following = [l for l in lines if l["page"] == gst_line["page"]
                         and 0 < l["box"][1]-gst_line["box"][3] < l["page_size"][1]*.025
                         and re.fullmatch(r"[A-Z][A-Z\s.&-]{3,}", l["text"].strip())]
            if following:
                nearest = min(following, key=lambda l: l["box"][1])
                names["supplier"].append(evidence(nearest, nearest["text"],
                                                   "business name below header GSTIN label"))
    result["supplier_name"] = choose(names["supplier"])
    result["buyer_name"] = choose(names["buyer"])
    result["supplier_gstin"] = choose(gst["supplier"] or masked_gst["supplier"])
    result["buyer_gstin"] = choose(gst["buyer"] or masked_gst["buyer"])
    result["invoice_number"] = choose(numbers)
    result["invoice_date"] = choose(dates)
    result["grand_total"] = choose(totals)
    for key in tax:
        result[key] = choose(tax[key])
    # No fallback based on order. Preserve ambiguity and unassigned candidates.
    return result, {"gstin_candidates": gst, "invalid_gstin_ocr_candidates": raw_gstin,
                    "invoice_numbers": numbers,
                    "invoice_dates": dates, "grand_totals": totals, "tax": tax}


def extract_items(lines):
    """Conservative four-column table, grouping nearby words across OCR lines.

    Tesseract may split one visual row into several text lines by column. We
    regroup words by their vertical positions before using header x positions.
    """
    items = []
    pages = sorted({line["page"] for line in lines})
    for number in pages:
        page_lines = [line for line in lines if line["page"] == number]
        if not page_lines:
            continue
        height = page_lines[0]["page_size"][1]
        words = [word for line in page_lines for word in line["words"]]
        words.sort(key=lambda w: (w["box"][1]+w["box"][3])/2)
        visual_rows = []
        for word in words:
            cy = (word["box"][1]+word["box"][3])/2
            if not visual_rows or cy-visual_rows[-1][0] > height*.008:
                visual_rows.append((cy, [word]))
            else:
                visual_rows[-1][1].append(word)
        for header_index, (_, header_words) in enumerate(visual_rows):
            labels = {}
            for word in header_words:
                key = word["text"].lower().strip(":")
                if key in ("description", "qty", "quantity", "rate", "amount"):
                    labels[{"qty": "quantity"}.get(key, key)] = word
            columns = ("description", "quantity", "rate", "amount")
            if not all(k in labels for k in columns):
                continue
            centers = [(labels[k]["box"][0]+labels[k]["box"][2])/2 for k in columns]
            if centers != sorted(centers) or len(set(centers)) != 4:
                continue
            cutoffs = [(a+b)/2 for a,b in zip(centers, centers[1:])]
            header_y = min(w["box"][1] for w in header_words)
            for row_index, (_, row_words) in enumerate(visual_rows[header_index+1:], header_index+1):
                row_words = sorted(row_words, key=lambda w: w["box"][0])
                row_text = " ".join(w["text"] for w in row_words)
                if min(w["box"][1] for w in row_words)-header_y > height*.35:
                    break
                if re.search(r"\b(?:taxable|subtotal|total|cgst|sgst|igst)\b", row_text, re.I):
                    break
                cells = [[], [], [], []]
                for word in row_words:
                    cx = (word["box"][0]+word["box"][2])/2
                    cells[sum(cx > boundary for boundary in cutoffs)].append(word["text"])
                description = " ".join(cells[0]).strip()
                try:
                    qty, rate, amount = (Decimal("".join(c).replace(",", "")) for c in cells[1:])
                except (InvalidOperation, ValueError):
                    continue
                if not description:
                    continue
                row = {"id": f"p{number}-table-row-{row_index}", "page": number,
                       "box": union([w["box"] for w in row_words]), "text": row_text,
                       "word_ids": [w["id"] for w in row_words], "words": row_words,
                       "method": page_lines[0]["method"], "page_size": page_lines[0]["page_size"]}
                items.append({"description": description, "quantity": str(qty), "rate": str(rate),
                              "amount": str(amount), "arithmetic_check": "PASS" if abs(qty*rate-amount) <= Decimal("0.01") else "MISMATCH",
                              "evidence": evidence(row, row_text, "four-column row; arithmetic checked", abs(qty*rate-amount) <= Decimal("0.01"))})
            break  # One table layout per page in this reference implementation.
    return items


def extract_rate_confirmations(lines):
    """Extract page-specific references from a carrier rate-confirmation form.

    Invoice numbers here are a *list of references*, not a singular tax-invoice
    identifier. Repeated pages stay separate because carrier details may vary.
    """
    records = []
    for page in sorted({line["page"] for line in lines}):
        page_lines = [line for line in lines if line["page"] == page]
        if not (any("rate details" in l["text"].casefold() for l in page_lines)
                and any("total carrier pay" in l["text"].casefold() for l in page_lines)):
            continue
        record = {"page": page, "carrier": blank("not_found"),
                  "order_number": blank("not_found"), "invoice_references": [],
                  "total_carrier_pay": blank("not_found")}
        for i, line in enumerate(page_lines):
            s = line["text"]
            if m := re.search(r"\bCARRIER\s*:\s*(.*?)\s+CONTACT\s*:", s, re.I):
                value = m.group(1).strip()
                if value:
                    record["carrier"] = evidence(line, value, "carrier label")
            if m := re.search(r"\bOrder\s*:\s*(\d{5,})\b", s, re.I):
                record["order_number"] = evidence(line, m.group(1), "order label")
            if re.fullmatch(r"\s*INVOICE\s+NO\.?\s*", s, re.I):
                heading_y = line["box"][3]
                for below in page_lines[i+1:]:
                    if below["box"][1]-heading_y > line["page_size"][1]*.09:
                        break
                    value = below["text"].strip()
                    if re.fullmatch(r"\d{8,12}", value) and abs(below["box"][0]-line["box"][0]) < line["page_size"][0]*.05:
                        record["invoice_references"].append(evidence(below, value, "invoice reference under heading"))
                    elif record["invoice_references"]:
                        break
            if m := re.search(r"\bTotal\s+Carrier\s+Pay\b\s*[$₹]?\s*([\d,]+(?:\.\d{2})?)", s, re.I):
                record["total_carrier_pay"] = evidence(line, m.group(1).replace(",", ""), "total carrier pay label")
        records.append(record)
    return records


