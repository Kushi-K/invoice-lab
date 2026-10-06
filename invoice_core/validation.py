"""Format and arithmetic checks kept separate from model opinions."""
from decimal import Decimal, InvalidOperation
import re
from .extraction import MONEY, GSTIN

def totals_check(fields, lines=()):
    def amount(name):
        try:
            return Decimal(fields[name]["value"])
        except (InvalidOperation, TypeError):
            return None
    base, total = amount("taxable_amount"), amount("grand_total")
    taxes = [amount(k) for k in ("cgst", "sgst", "igst")]
    if base is None or total is None or all(v is None for v in taxes):
        return {"status": "not_checked", "reason": "Missing total or tax values"}
    expected = base + sum((v or Decimal(0) for v in taxes), Decimal(0))
    adjustment = None
    for line in lines:
        if label := re.search(r"\bround\s*off\b", line["text"], re.I):
            values = list(MONEY.finditer(line["text"][label.end():]))
            if len(values) == 1:
                adjustment = Decimal(values[0].group(1).replace(",", ""))
                break
    if adjustment is not None and abs(expected + adjustment - total) <= Decimal("0.01"):
        expected += adjustment
    return {"status": "PASS" if abs(expected-total) <= Decimal("0.01") else "MISMATCH",
            "computed": str(expected), "printed": str(total),
            "round_off_adjustment": str(adjustment) if adjustment is not None else None,
            "note": "Arithmetic consistency is not proof that OCR read the image correctly."}


def validate_fields(fields):
    for key, field in fields.items():
        if field.get("review_status", "").startswith("human_"):
            continue
        value = field.get("value")
        if value is None or not field.get("triage_components"):
            continue
        ok = True
        if key.endswith("gstin"):
            ok = bool(GSTIN.fullmatch(str(value)))
        elif key in ("taxable_amount", "cgst", "sgst", "igst", "grand_total"):
            try:
                ok = Decimal(str(value)).is_finite()
            except InvalidOperation:
                ok = False
        elif key == "invoice_date":
            from datetime import datetime
            ok = False
            for pattern in ("%d-%m-%y", "%d-%m-%Y", "%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d", "%B %d, %Y", "%B %d %Y"):
                try:
                    datetime.strptime(str(value), pattern)
                    ok = True
                    break
                except ValueError:
                    pass
        field["triage_components"]["format"] = float(ok)
        if not ok:
            field["review_status"] = "needs_review"
    return fields
