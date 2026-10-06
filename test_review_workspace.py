"""Batch edits and exact request previews must retain evidence and be transactional."""
import copy
import io
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

from invoice_lab import run
from invoice_ui import Handler, apply_review_batch, review_summary, execute_model_job
from invoice_core.llm import build_requests, generate, resolve, rate_field_error, apply_extraction
from invoice_lab import evidence


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.run_id = 'b' * 32
        self.output = self.root/self.run_id/'output'
        self.report = run(Path('demo_searchable.pdf'), self.output)

    def tearDown(self):
        self.tmp.cleanup()

    def test_batch_saves_multiple_values_without_mutating_original(self):
        baseline = copy.deepcopy(self.report)
        updated = apply_review_batch(self.report, [
            {'kind': 'invoice', 'field': 'supplier_name', 'value': 'Reviewed supplier'},
            {'kind': 'invoice', 'field': 'grand_total', 'value': '1200.00'}])
        self.assertEqual(self.report, baseline)
        self.assertEqual(updated['fields']['supplier_name']['original_value'], 'Acme Supplies Pvt Ltd')
        self.assertEqual(updated['fields']['grand_total']['triage_score'], baseline['fields']['grand_total']['triage_score'])
        self.assertEqual(updated['totals_validation']['status'], 'MISMATCH')
        self.assertEqual(len(updated['review_history']), 2)

    def test_invalid_second_change_rejects_entire_batch(self):
        baseline = copy.deepcopy(self.report)
        with self.assertRaises(ValueError):
            apply_review_batch(self.report, [
                {'kind': 'invoice', 'field': 'supplier_name', 'value': 'Changed'},
                {'kind': 'invoice', 'field': 'nonexistent', 'value': 'Invalid'}])
        self.assertEqual(self.report, baseline)
        with self.assertRaises(ValueError):
            apply_review_batch(self.report, [{'kind': 'invoice', 'field': 'invoice_number', 'value': 'A'}]*2)

    def test_model_results_do_not_count_as_human_review(self):
        self.report['llm_extraction'] = [{'model': 'test'}]
        self.assertEqual(review_summary(self.report)['review_status'], 'unreviewed')
        changes = [{'kind': 'invoice', 'field': key, 'value': field['value']}
                   for key, field in self.report['fields'].items() if field['value'] is not None]
        reviewed = apply_review_batch(self.report, changes)
        self.assertEqual(review_summary(reviewed)['review_status'], 'reviewed')

    def test_preview_body_matches_actual_generate_request(self):
        for stage in ('extract', 'review'):
            request = build_requests(self.report, 'test-model', stage)[0]
            with patch('invoice_core.llm.urllib.request.urlopen', return_value=io.BytesIO(json.dumps({'response': '{}'}).encode())) as connection:
                generate(request['body']['model'], request['body']['prompt'], request['body']['format'])
            actual = json.loads(connection.call_args.args[0].data)
            self.assertEqual(actual, request['body'])
            self.assertIn(request['evidence_text'], actual['prompt'])
            self.assertEqual(len(request['input']['lines']), len(self.report['lines']))

    def test_extraction_contract_only_allows_saved_source_ids(self):
        request = build_requests(self.report, 'test-model', 'extract')[0]
        entry = request['body']['format']['$defs']['field']
        permitted = entry['properties']['source_line']['enum']
        expected = [f'L{index+1}' for index, line in enumerate(self.report['lines'])] + [None]
        self.assertEqual(permitted, expected)
        self.assertIn('exact shape', request['body']['prompt'])

    def test_source_selection_normalizes_order_and_label_without_inventing_evidence(self):
        words = {w['text']: w['id'] for line in self.report['lines'] for w in line['words']}
        field, error = resolve({'value': 'Beta Traders', 'word_ids': [words['Traders'], words['Beta']]}, self.report['lines'], evidence)
        self.assertIsNone(error)
        self.assertEqual(field['word_ids'], [words['Beta'], words['Traders']])
        field, error = resolve({'value': 'INV-2026-041', 'word_ids': [words['No:'], words['INV-2026-041']]}, self.report['lines'], evidence)
        self.assertIsNone(error)
        self.assertEqual(field['word_ids'], [words['INV-2026-041']])
        field, error = resolve({'value': '90.00', 'word_ids': ['p1-w34', 'p1-w36']}, self.report['lines'], evidence)
        self.assertIsNone(field)
        self.assertIn('does not match', error)

    def test_model_quote_must_exist_in_selected_line(self):
        line = next(line for line in self.report['lines'] if 'Invoice No:' in line['text'])
        field, error = resolve({'value': 'INV-2026-041', 'source_line': line['id'], 'reason': 'Invoice number label'}, self.report['lines'], evidence)
        self.assertIsNone(error)
        self.assertEqual(field['value'], 'INV-2026-041')
        self.assertEqual(field['word_ids'], [line['words'][-1]['id']])
        self.assertIsNone(resolve({'value': 'INVENTED', 'source_line': line['id']}, self.report['lines'], evidence)[0])
        self.assertIsNone(resolve({'value': 'INV-2026-041', 'source_line': 'fake'}, self.report['lines'], evidence)[0])

    def test_rate_field_role_rejects_source_text_with_wrong_meaning(self):
        line = {'id': 'tracking', 'text': 'MACROPOINT tracking is required throughout transit.'}
        self.assertIn('Invoice references', rate_field_error('invoice_references', {'value': line['text'], 'line_id': line['id']}, [line]))
        line = {'id': 'carrier', 'text': 'CARRIER: Example Carrier LLC CONTACT:RC EMAIL 0123456'}
        self.assertIn('labelled order', rate_field_error('order_number', {'value': '0123456', 'line_id': line['id']}, [line]))
        line = {'id': 'order', 'text': 'ORDER Order: 0123456 Pieces: 0'}
        self.assertIsNone(rate_field_error('order_number', {'value': '0123456', 'line_id': line['id']}, [line]))

    def test_partial_rejected_reference_list_keeps_complete_baseline(self):
        baseline = [{'value': '2030011001'}, {'value': '2030011002'}]
        record = {'page': 2, 'carrier': {'value': None}, 'order_number': {'value': None}, 'total_carrier_pay': {'value': None}, 'invoice_references': copy.deepcopy(baseline)}
        report = {'document_type': 'rate_confirmation_packet', 'fields': {}, 'rate_confirmations': [record]}
        extraction = [{'page': 2, 'model': 'test', 'fields': {'invoice_references': [baseline[0]]}, 'rejected_proposals': {'invoice_references': [{'reason': 'bad quote'}]}}]
        apply_extraction(report, extraction)
        self.assertEqual(report['rate_confirmations'][0]['invoice_references'], baseline)

    def test_model_worker_preserves_edits_made_during_inference(self):
        import invoice_ui
        from invoice_core.storage import save_json
        job = {'status': 'running', 'pages': [1], 'completed_pages': [], 'warnings': []}
        proposal = copy.deepcopy(self.report['fields']['invoice_number']); proposal['value'] = 'MODEL-1'
        def infer(*args):
            self.assertTrue(invoice_ui.WRITE_LOCK.acquire(blocking=False))
            invoice_ui.WRITE_LOCK.release()
            changed = apply_review_batch(self.report, [{'kind': 'invoice', 'field': 'invoice_number', 'value': 'HUMAN-1'}])
            save_json(self.output/'reviewed_result.json', changed)
            return [{'page': 1, 'model': 'test', 'fields': {'invoice_number': proposal}, 'raw_response': {}, 'rejected_proposals': {}}]
        with patch('invoice_ui.RUNS', self.root), patch('invoice_ui.JOBS', {self.run_id: job}), patch('invoice_ui.ollama_extract', side_effect=infer):
            execute_model_job(self.run_id, 'test', 'extract', [1])
        saved = json.loads((self.output/'reviewed_result.json').read_text())
        self.assertEqual(saved['fields']['invoice_number']['value'], 'HUMAN-1')
        self.assertEqual(job['status'], 'complete')
        self.assertEqual(job['completed_pages'], [1])

    def test_model_review_discards_output_when_values_change_during_inference(self):
        from invoice_core.storage import save_json
        job = {'status': 'running', 'pages': [1], 'completed_pages': [], 'warnings': []}
        def infer(*args):
            changed = apply_review_batch(self.report, [{'kind': 'invoice', 'field': 'invoice_number', 'value': 'HUMAN-2'}])
            save_json(self.output/'reviewed_result.json', changed)
            return [{'page': 1, 'fields': {'invoice_number': {'reviewed_value': 'INV-2026-041'}}}]
        with patch('invoice_ui.RUNS', self.root), patch('invoice_ui.JOBS', {self.run_id: job}), patch('invoice_ui.ollama_review', side_effect=infer):
            execute_model_job(self.run_id, 'test', 'review', [1])
        saved = json.loads((self.output/'reviewed_result.json').read_text())
        self.assertFalse(saved.get('llm_review'))
        self.assertTrue(job['warnings'])
        self.assertEqual(job['status'], 'complete')

    def test_runtime_error_preserves_ollama_detail(self):
        error = urllib.error.HTTPError('http://localhost', 500, 'Server error', {},
                                      io.BytesIO(b'{"error":"failed to initialize model backend"}'))
        with patch('invoice_core.llm.urllib.request.urlopen', side_effect=error):
            with self.assertRaisesRegex(RuntimeError, 'failed to initialize model backend'):
                generate('test-model', 'prompt', {})

    def test_partial_model_output_is_rejected(self):
        reply = {'done_reason': 'length', 'response': '{"invoice_number":"partial"}'}
        with patch('invoice_core.llm.urllib.request.urlopen', return_value=io.BytesIO(json.dumps(reply).encode())):
            with self.assertRaisesRegex(ValueError, 'No partial fields were applied'):
                generate('test-model', 'prompt', {})

    def test_batch_http_revision_conflict_and_preview_does_not_call_model(self):
        with patch('invoice_ui.RUNS', self.root), patch.object(Handler, 'log_message', lambda *args: None):
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f'http://127.0.0.1:{server.server_port}'
            try:
                with urllib.request.urlopen(f'{base}/api/report/{self.run_id}') as response:
                    saved = json.load(response)
                options = {'changes': [{'kind': 'invoice', 'field': 'invoice_number', 'value': 'EDIT-1'}], 'revision': saved['revision']}
                def post(path, data):
                    return urllib.request.urlopen(urllib.request.Request(base+path, json.dumps(data).encode(), {'Content-Type': 'application/json'}))
                with post(f'/api/review-batch/{self.run_id}', options) as response:
                    updated = json.load(response)
                self.assertEqual(updated['report']['fields']['invoice_number']['value'], 'EDIT-1')
                self.assertNotEqual(updated['revision'], saved['revision'])
                with self.assertRaises(urllib.error.HTTPError) as error:
                    post(f'/api/review-batch/{self.run_id}', options)
                self.assertEqual(error.exception.code, 409)
                with patch('invoice_core.llm.generate') as model:
                    with post(f'/api/llm-preview/{self.run_id}', {'stage': 'review', 'model': 'test'}) as response:
                        preview = json.load(response)
                    model.assert_not_called()
                self.assertIn('EDIT-1', preview['requests'][0]['body']['prompt'])
                self.assertEqual(json.loads((self.output/'result.json').read_text())['fields']['invoice_number']['value'], 'INV-2026-041')
            finally:
                server.shutdown(); server.server_close(); thread.join()


if __name__ == '__main__':
    unittest.main()
