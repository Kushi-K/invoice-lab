"""Review invariants: corrections keep source evidence and document boundaries."""
import unittest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch

from invoice_ui import apply_human_review, recent_results, saved_result


class ReviewTests(unittest.TestCase):
    def test_rate_reference_correction_preserves_original_and_ocr_score(self):
        ref = {"value": "2030011001", "source_text": "2030011001",
               "ocr_confidence": .965, "triage_score": .986}
        report = {"document_type": "rate_confirmation_packet", "rate_confirmations": [
            {"page": 2, "invoice_references": [ref]}]}
        apply_human_review(report, {"kind": "rate", "page": 2,
                                   "field": "invoice_references", "index": 0,
                                   "value": "2030011003", "note": "checked on original"})
        self.assertEqual(ref["value"], "2030011003")
        self.assertEqual(ref["original_value"], "2030011001")
        self.assertEqual(ref["source_text"], "2030011001")
        self.assertEqual(ref["ocr_confidence"], .965)
        self.assertEqual(ref["review_status"], "human_corrected")
        with self.assertRaises(ValueError):
            apply_human_review(report, {"kind": "invoice", "field": "cgst", "value": "90.00"})

    def test_invoice_field_can_be_confirmed_without_inflating_score(self):
        field = {"value": "1180.00", "ocr_confidence": .81}
        report = {"document_type": "invoice_candidate", "fields": {"grand_total": field}}
        apply_human_review(report, {"kind": "invoice", "field": "grand_total",
                                   "value": "1180.00"})
        self.assertEqual(field["review_status"], "human_verified")
        self.assertEqual(field["ocr_confidence"], .81)

    def test_item_correction_flags_new_arithmetic_mismatch(self):
        row = {"description": "Supplies", "quantity": "2", "rate": "500.00",
               "amount": "1000.00", "arithmetic_check": "PASS"}
        report = {"document_type": "invoice_candidate", "items": [row]}
        apply_human_review(report, {"kind": "item", "index": 0,
                                   "field": "amount", "value": "900.00"})
        self.assertEqual(row["arithmetic_check"], "MISMATCH")
        self.assertEqual(row["human_reviews"]["amount"]["original_value"], "1000.00")

    def test_previous_result_reopens_saved_review(self):
        with tempfile.TemporaryDirectory() as tmp, patch("invoice_ui.RUNS", Path(tmp)):
            run_id = "a" * 32
            output = Path(tmp) / run_id / "output"
            output.mkdir(parents=True)
            original = {"source": "invoice.pdf", "document_type": "invoice_candidate",
                        "pages": [{"number": 1}], "fields": {"invoice_number": {"value": "INV-10"}}}
            corrected = json.loads(json.dumps(original))
            corrected["fields"]["invoice_number"]["value"] = "INV-11"
            (output / "result.json").write_text(json.dumps(original))
            (output / "reviewed_result.json").write_text(json.dumps(corrected))
            history = recent_results()
            reopened = saved_result(run_id)
        self.assertEqual(len(history), 1)
        self.assertTrue(history[0]["reviewed"])
        self.assertEqual(reopened["report"]["fields"]["invoice_number"]["value"], "INV-11")


if __name__ == "__main__":
    unittest.main()
