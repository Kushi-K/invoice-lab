"""Deterministic layout candidates, saved before any model sees the document."""
import re
from .document import union


def structure_document(lines, pages):
    blocks, tables = [], []
    for page in pages:
        page_lines = [line for line in lines if line['page'] == page['number']]
        grouped = {}
        for line in page_lines:
            # Engine block IDs retain source segmentation instead of guessing semantics.
            key = (line["method"], "header" if "header-" in line["id"] else "main",
                   str(line["words"][0].get("block_id", line["id"].split("-l", 1)[-1].split("-")[0])) if line.get("words") else line["id"])
            grouped.setdefault(key, []).append(line)
        for index, members in enumerate(grouped.values()):
            blocks.append({'id': f"p{page['number']}-b{index}", 'page': page['number'],
                           'box': union([line['box'] for line in members]),
                           'line_ids': [line['id'] for line in members],
                           'text': '\n'.join(line['text'] for line in members),
                           'basis': 'source engine block grouping'})
        # Table headers can be split into separate source lines at the same y.
        rows = []
        words = {w['id']: w for line in page_lines for w in line.get('words', [])}
        for word in sorted(words.values(), key=lambda w: (w['box'][1]+w['box'][3])/2):
            cy = (word['box'][1]+word['box'][3])/2
            if not rows or abs(cy-rows[-1][0]) > page['size'][1]*.008:
                rows.append((cy, [word]))
            else:
                rows[-1][1].append(word)
        for i, (_, row) in enumerate(rows):
            headers = [w['text'] for w in row if re.fullmatch(
                r'description|item|qty|quantity|rate|price|amount', w['text'].strip(':'), re.I)]
            if len(headers) < 3:
                continue
            candidates = []
            for _, following in rows[i+1:]:
                text = ' '.join(w['text'] for w in sorted(following, key=lambda w: w['box'][0]))
                if re.search(r'\b(total|subtotal|taxable|cgst|sgst|igst)\b', text, re.I):
                    break
                if min(w['box'][1] for w in following)-min(w['box'][1] for w in row) > page['size'][1]*.35:
                    break
                candidates.append({'text': text, 'box': union([w['box'] for w in following]),
                                   'word_ids': [w['id'] for w in following]})
            tables.append({'id': f"p{page['number']}-t{len(tables)}", 'page': page['number'],
                           'box': union([w['box'] for w in row] + [r['box'] for r in candidates]),
                           'headers': headers, 'header_word_ids': [w['id'] for w in row],
                           'rows': candidates, 'status': 'candidate',
                           'reason': 'aligned header words; rows have not been certified'})
    return {'version': 1, 'reading_order': [line['id'] for line in lines],
            'lines': lines, 'blocks': blocks, 'table_candidates': tables,
            'note': 'Layout candidates precede field extraction and model review.'}
