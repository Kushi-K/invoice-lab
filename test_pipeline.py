"""Stage ordering, source evidence, model boundaries, and correction invariants."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from invoice_lab import run, extract_items, totals_check
from invoice_core.document import native_text_quality
from invoice_core.confidence import evidence, score_fields
from invoice_core.llm import resolve, ollama_extract, ollama_review, apply_extraction
from invoice_core.validation import validate_fields
from invoice_ui import apply_human_review
from test_invoice_lab import line, positioned_line


class PipelineTests(unittest.TestCase):
    def report(self):
        with tempfile.TemporaryDirectory() as tmp:
            return run(Path('demo_searchable.pdf'), Path(tmp))

    def test_stages_preserve_words_and_pdf_confidence_is_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = run(Path('demo_searchable.pdf'), Path(tmp))
            extracted = json.loads((Path(tmp)/'extracted.json').read_text())
            structured = json.loads((Path(tmp)/'structured.json').read_text())
            self.assertEqual(extracted['lines'], structured['lines'])
            self.assertTrue(report['lines'][0]['words'])
            self.assertIsNone(report['lines'][0]['words'][0]['ocr_confidence'])
            self.assertTrue(structured['blocks'])
            self.assertEqual(report['stages'][3]['status'], 'pending')

    def test_long_garbled_text_layer_is_unusable(self):
        self.assertFalse(native_text_quality([{'text': '\ufffd'*100}])['usable'])
        self.assertTrue(native_text_quality([{'text': 'Invoice number INV-041 Acme Supplies Total 1000.00'}])['usable'])

    def test_mixed_pdf_routes_independently_and_force_ocr_applies_per_page(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'mixed.pdf'
            doc = pymupdf.open()
            doc.insert_pdf(pymupdf.open('demo_searchable.pdf'))
            doc.new_page()
            doc.save(path)
            with patch('invoice_core.document.tesseract_words', return_value=[] ) as ocr:
                report = run(path, Path(tmp)/'out')
                self.assertEqual([p['method'] for p in report['pages']], ['pdf_text', 'tesseract'])
                self.assertEqual(ocr.call_count, 1)
                self.assertTrue(report['pages'][0]['embedded_words'])
            with patch('invoice_core.document.tesseract_words', return_value=[]) as ocr:
                report = run(path, Path(tmp)/'forced', force_ocr=True)
                self.assertEqual([p['method'] for p in report['pages']], ['tesseract', 'tesseract'])
                self.assertEqual(ocr.call_count, 2)

    def test_field_confidence_uses_value_words(self):
        source = line('Grand Total: 1180.00', 100, 1)
        source['words'][0]['ocr_confidence'] = .1
        source['words'][-1]['ocr_confidence'] = .98
        field = evidence(source, '1180.00', 'total label')
        self.assertEqual(field['ocr_confidence'], .98)
        self.assertEqual(field['word_ids'], [source['words'][-1]['id']])

    def test_model_cannot_invent_source_value_or_word_ids(self):
        source = line('INV-041', 100, 1)
        valid = {'value': 'INV-041', 'word_ids': source['word_ids'], 'reason': 'printed number'}
        self.assertIsNotNone(resolve(valid, [source], evidence)[0])
        self.assertIsNone(resolve({**valid, 'value': 'INV-999'}, [source], evidence)[0])
        self.assertIsNone(resolve({**valid, 'word_ids': ['fake']}, [source], evidence)[0])

    def test_extraction_receives_structure_and_preserves_rule_baseline(self):
        report = self.report()
        source = next(l for l in report['lines'] if 'INV-2026-041' in l['text'])
        word = next(w for w in source['words'] if w['text'] == 'INV-2026-041')
        reply = {'invoice_number': {'value': word['text'], 'word_ids': [word['id']], 'reason': 'number label'}}
        original = copy.deepcopy(report['fields'])
        with patch('invoice_core.llm.generate', return_value=reply) as generate:
            proposals = ollama_extract(report, 'test-model', evidence)
            self.assertIn('Description Qty Rate Amount', generate.call_args.args[1])
        apply_extraction(report, proposals)
        self.assertEqual(report['rule_fields'], original)
        self.assertEqual(report['fields']['invoice_number']['extraction_provider'], 'ollama')

    def test_model_extraction_and_validation_keep_human_original_scores(self):
        report = self.report()
        original = copy.deepcopy(report['fields']['grand_total'])
        apply_human_review(report, {'kind': 'invoice', 'field': 'grand_total', 'value': '999.00'})
        apply_extraction(report, [{'page': 1, 'model': 'test', 'fields': {'grand_total': original}}])
        validate_fields(report['fields'])
        score_fields(report['fields'], totals_check(report['fields']))
        self.assertEqual(report['fields']['grand_total']['value'], '999.00')
        self.assertEqual(report['fields']['grand_total']['triage_score'], original['triage_score'])
        self.assertEqual(len(report['review_history']), 1)

    def test_clear_field_keeps_original_and_invalidates_model_review(self):
        report = self.report()
        report['llm_review'] = [{'old': True}]
        apply_human_review(report, {'kind': 'invoice', 'field': 'grand_total', 'value': ''})
        self.assertIsNone(report['fields']['grand_total']['value'])
        self.assertEqual(report['fields']['grand_total']['original_value'], '1180.00')
        self.assertEqual(report['llm_review'], [])

    def test_repeated_model_value_on_multiple_pages_is_not_a_conflict(self):
        report = self.report()
        field = copy.deepcopy(report['fields']['invoice_number'])
        repeated = {**field, 'page': 2}
        apply_extraction(report, [{'page': 1, 'model': 'test', 'fields': {'invoice_number': field}},
                                  {'page': 2, 'model': 'test', 'fields': {'invoice_number': repeated}}])
        self.assertNotIn('extraction_conflict', report['fields']['invoice_number'])
        apply_extraction(report, [{'page': 1, 'model': 'test', 'fields': {'invoice_number': field}},
                                  {'page': 2, 'model': 'test', 'fields': {'invoice_number': {**repeated, 'value': 'OTHER'}}}])
        self.assertIn('extraction_conflict', report['fields']['invoice_number'])

    def test_review_scores_are_separate_and_invalid_scores_rejected(self):
        report = self.report()
        before = copy.deepcopy(report['fields'])
        reply = {key: {'score': .5, 'reason': 'uncertain layout'} for key in report['fields']}
        with patch('invoice_core.llm.generate', return_value=reply):
            review = ollama_review(report, 'test-model')
        self.assertEqual(report['fields'], before)
        self.assertEqual(review[0]['fields']['grand_total']['score_type'], 'model_self_assessment')
        reply['grand_total']['score'] = 5
        with patch('invoice_core.llm.generate', return_value=reply), self.assertRaises(ValueError):
            ollama_review(report, 'test-model')

    def test_mismatched_table_row_is_retained(self):
        lines = [positioned_line(['Description', 'Qty', 'Rate', 'Amount'], [40,180,260,350],100,0),
                 positioned_line(['Supplies', '2', '500.00', '900.00'], [40,180,260,350],130,1)]
        items = extract_items(lines)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['arithmetic_check'], 'MISMATCH')


if __name__ == '__main__':
    unittest.main()
