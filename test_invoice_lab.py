import unittest
import io
import json
import shutil
import tempfile
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
from invoice_lab import evaluate, extract, extract_items, extract_rate_confirmations, llama_server_review, load_document, tesseract_words, totals_check


def line(text, y, i):
    words = []
    x = 40
    for j, token in enumerate(text.split()):
        words.append({"id": f"p1-w{i}-{j}", "line_key": str(i), "text": token,
                      "box": [x,y,x+len(token)*7,y+14], "ocr_confidence": .91})
        x += len(token)*7+5
    return {"id": f"p1-l{i}", "page": 1, "text": text,
            "box": [40,y,x,y+14], "page_size": [600,800], "method": "tesseract",
            "word_ids": [w["id"] for w in words], "words": words}


def positioned_line(tokens, xs, y, i):
    out = line(" ".join(tokens), y, i)
    for word, x in zip(out["words"], xs):
        word["box"] = [x, y, x+len(word["text"])*6, y+14]
    out["box"] = [min(xs), y, max(w["box"][2] for w in out["words"]), y+14]
    return out


class InvoiceTests(unittest.TestCase):
    def test_unmatched_quote_in_tesseract_tsv_keeps_later_rows(self):
        header = "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        rows = ["5\t1\t1\t1\t1\t1\t10\t20\t30\t12\t90\t\"Best",
                "5\t1\t2\t1\t1\t1\t10\t40\t50\t12\t95\tTotal",
                "5\t1\t2\t1\t1\t2\t70\t40\t60\t12\t95\t38026.00"]
        with patch("invoice_lab.subprocess.run",
                   return_value=SimpleNamespace(stdout=(header+"\n".join(rows)+"\n").encode())):
            words = tesseract_words(b"png", 1)
        self.assertEqual([w["text"] for w in words], ['"Best', 'Total', '38026.00'])

    def test_gstin_is_assigned_by_heading_even_when_buyer_first(self):
        lines = [line("Bill To", 60, 0), line("GSTIN: 27PQRST6789L1Z2", 83, 1),
                 line("Supplier", 150, 2), line("GSTIN: 29ABCDE1234F1Z5", 173, 3)]
        fields, _ = extract(lines)
        self.assertEqual(fields["buyer_gstin"]["value"], "27PQRST6789L1Z2")
        self.assertEqual(fields["supplier_gstin"]["value"], "29ABCDE1234F1Z5")

    def test_ambiguous_supplier_is_not_guessed(self):
        lines = [line("Supplier", 60, 0), line("GSTIN: 29ABCDE1234F1Z5", 80, 1),
                 line("GSTIN: 29AAAAA1234A1Z1", 100, 2)]
        fields, candidates = extract(lines)
        self.assertIsNone(fields["supplier_gstin"]["value"])
        self.assertEqual(len(candidates["gstin_candidates"]["supplier"]), 2)

    def test_arithmetic_mismatch_is_reported(self):
        fields, _ = extract([line("Taxable Amount: 1000.00", 100, 0),
                             line("CGST: 90.00", 120, 1), line("SGST: 90.00", 140, 2),
                             line("Grand Total: 1190.00", 160, 3)])
        self.assertEqual(totals_check(fields)["status"], "MISMATCH")

    def test_empty_igst_does_not_take_a_bank_account_suffix(self):
        fields, _ = extract([line("Account No: 20412XXXX05 IGST Amt:", 100, 0)])
        self.assertIsNone(fields["igst"]["value"])

    @unittest.skipUnless(shutil.which("tesseract") and Path(__file__).with_name("sample_invoice.png").is_file() and Path(__file__).with_name("sample_invoice_truth.json").is_file(), "Tesseract or private image fixture unavailable")
    def test_grid_image_extraction_and_masked_gstin_review(self):
        pages, lines = load_document(Path(__file__).with_name("sample_invoice.png"))
        fields, _ = extract(lines)
        self.assertEqual(pages[0]["method"], "tesseract")
        truth = json.loads(Path(__file__).with_name("sample_invoice_truth.json").read_text())["fields"]
        for key, expected in truth.items():
            self.assertEqual(fields[key]["value"], expected, key)
        self.assertIsNone(fields["igst"]["value"])
        for key in ("supplier_gstin", "buyer_gstin"):
            self.assertEqual(fields[key]["review_status"], "needs_review")
            self.assertEqual(fields[key]["triage_components"]["format"], 0.0)
        self.assertEqual(totals_check(fields, lines)["status"], "PASS")

    def test_item_row_split_into_column_lines(self):
        lines = [positioned_line(["Description", "Qty"], [40, 180], 100, 0),
                 positioned_line(["Rate"], [260], 101, 1),
                 positioned_line(["Amount"], [350], 99, 2),
                 positioned_line(["Office", "Supplies", "2"], [40, 80, 185], 130, 3),
                 positioned_line(["500.00"], [260], 131, 4),
                 positioned_line(["1000.00"], [350], 129, 5),
                 positioned_line(["Taxable", "Amount:", "1000.00"], [40, 100, 200], 180, 6)]
        items = extract_items(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["description"], "Office Supplies")
        self.assertEqual(items[0]["arithmetic_check"], "PASS")

    @unittest.skipUnless(shutil.which("tesseract") and Path(__file__).with_name("variant_scanned_invoice.pdf").is_file(), "Tesseract or private PDF fixture unavailable")
    def test_variant_scanned_pdf_has_two_item_rows(self):
        pages, lines = load_document(Path(__file__).with_name("variant_scanned_invoice.pdf"))
        self.assertEqual(pages[0]["method"], "tesseract")
        self.assertEqual([(x["description"], x["amount"]) for x in extract_items(lines)],
                         [("Notebooks", "500.00"), ("Markers", "600.00")])

    def test_rate_confirmation_has_multiple_invoice_references(self):
        lines = [line("CARRIER: Example Carrier LLC CONTACT: Dispatch", 70, 0),
                 line("Order: 0123456", 100, 1),
                 line("INVOICE NO.", 200, 2),
                 line("2030011001", 220, 3),
                 line("2030011002", 240, 4),
                 line("RATE DETAILS Units Rate", 500, 5),
                 line("Total Carrier Pay $4,000.00", 600, 6)]
        records = extract_rate_confirmations(lines)
        self.assertEqual(len(records), 1)
        self.assertEqual([x["value"] for x in records[0]["invoice_references"]],
                         ["2030011001", "2030011002"])
        self.assertEqual(records[0]["total_carrier_pay"]["value"], "4000.00")

    def test_llm_rejects_unrelated_text_as_invoice_references(self):
        lines = [line("INVOICE NO.", 200, 0), line("2030011001", 220, 1),
                 line("2030011002", 240, 2), line("Reference: EXAMPLETRACK001", 260, 3),
                 line("Line Haul 1 4000", 280, 4), line("RATE DETAILS", 500, 5),
                 line("Total Carrier Pay $4,000.00", 600, 6)]
        record = extract_rate_confirmations(lines)
        fields, _ = extract(lines)
        raw = {"invoice_references": ["2030011001", "2030011002",
                                      "EXAMPLETRACK001", "Line Haul 1"]}
        reply = {"choices": [{"message": {"content": json.dumps(raw)}}]}
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): self.close()
        with patch("invoice_lab.urllib.request.urlopen",
                   return_value=Response(json.dumps(reply).encode())):
            review = llama_server_review(lines, fields, record)
        refs = review[0]["fields"]["invoice_references"]
        self.assertEqual(len(refs["proposed_line_ids"]), 2)
        self.assertEqual(len(refs["rejected_proposals"]), 2)
        self.assertTrue(refs["verified_subset_matches_rules"])
        self.assertFalse(refs["agrees_with_rules"])

    def test_rate_evaluation_counts_extra_reference_as_error(self):
        with tempfile.TemporaryDirectory() as folder:
            prediction = Path(folder) / "prediction.json"
            truth = Path(folder) / "truth.json"
            prediction.write_text(json.dumps({"fields": {}, "rate_confirmations": [
                {"page": 2, "invoice_references": [{"value": "12345678"},
                                                    {"value": "87654321"}]}]}))
            truth.write_text(json.dumps({"rate_confirmations": [
                {"page": 2, "invoice_references": ["12345678"]}]}))
            result = evaluate(prediction, truth)
        self.assertEqual(result["reference_values_checked"], 2)
        self.assertEqual(result["reference_exact_match_accuracy"], .5)


if __name__ == "__main__":
    unittest.main()
