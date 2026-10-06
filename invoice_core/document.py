"""Read source documents and retain engine words in their source coordinates."""
import csv
import io
import os
import shutil
import subprocess
import pymupdf as fitz
from PIL import Image

def union(boxes):
    return [round(min(b[0] for b in boxes), 1), round(min(b[1] for b in boxes), 1),
            round(max(b[2] for b in boxes), 1), round(max(b[3] for b in boxes), 1)]


def make_line(words, page, method, page_size):
    words = sorted(words, key=lambda w: w["box"][0])
    return {"id": f"p{page}-l{words[0]['line_key']}", "page": page,
            "text": " ".join(w["text"] for w in words), "box": union([w["box"] for w in words]),
            "word_ids": [w["id"] for w in words], "method": method,
            "page_size": list(page_size), "words": words}


def pdf_page_words(page, number):
    out = []
    for i, (x0, y0, x1, y1, text, block, line, word) in enumerate(page.get_text("words")):
        out.append({"id": f"p{number}-w{i}", "line_key": f"{block}-{line}",
                    "text": text, "box": [x0, y0, x1, y1], "ocr_confidence": None, "block_id": block, "coordinate_system": "pdf_points"})
    return out


def tesseract_words(png, number, psm=3, channel=""):
    try:
        executable = os.environ.get("TESSERACT_CMD") or shutil.which("tesseract") or ("/opt/homebrew/bin/tesseract" if os.path.isfile("/opt/homebrew/bin/tesseract") else "tesseract")
        proc = subprocess.run([executable, "stdin", "stdout", "-l", "eng", "--psm", str(psm), "tsv"],
                              input=png, capture_output=True, check=True, timeout=120)
    except FileNotFoundError as exc:
        raise RuntimeError("Tesseract executable not found. Install it, then check `tesseract --version`.") from exc
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(exc.stderr.decode(errors="replace")) from exc
    out = []
    # Tesseract's TSV text column can contain an unmatched quotation mark.
    # Standard CSV quoting would swallow every following row into that field.
    for i, row in enumerate(csv.DictReader(io.StringIO(proc.stdout.decode("utf-8-sig")),
                                           delimiter="\t", quoting=csv.QUOTE_NONE)):
        if not row["text"].strip() or int(row["level"]) != 5:
            continue
        x, y, w, h = (int(row[k]) for k in ("left", "top", "width", "height"))
        conf = float(row["conf"])
        out.append({"id": f"p{number}-{channel}w{i}", "line_key": channel + "-".join(row[k] for k in
                    ("block_num", "par_num", "line_num")), "text": row["text"],
                    "box": [x, y, x+w, y+h], "block_id": row["block_num"],
                    "coordinate_system": "rendered_pixels", "raw_ocr_confidence": conf, "ocr_confidence": round(conf/100, 3) if conf >= 0 else None})
    return out


def native_text_quality(words):
    """Observable routing signals, not an OCR accuracy estimate."""
    text = " ".join(w["text"] for w in words)
    characters = len(text.replace(" ", ""))
    bad = sum(c == "\ufffd" or (not c.isprintable() and not c.isspace()) for c in text)
    alnum = sum(c.isalnum() for c in text)
    usable = characters >= 30 and alnum / max(1, len(text)) >= .4 and bad / max(1, len(text)) < .02
    return {"usable": usable, "characters": characters, "word_count": len(words),
            "alphanumeric_ratio": round(alnum/max(1, len(text)), 3),
            "invalid_character_ratio": round(bad/max(1, len(text)), 3),
            "reason": "usable embedded text" if usable else "sparse or malformed embedded text",
            "limitation": "A plausible but incomplete text layer can still pass these heuristics; use force OCR."}


def recover_table_words(png, words, number, size):
    """Recover isolated table cells omitted by automatic page segmentation."""
    clean = lambda word: word['text'].lower().strip(':')
    header = next((word for word in words if clean(word) == 'description' and
                   {'rate', 'amount'} <= {clean(other) for other in words
                    if abs(other['box'][1] - word['box'][1]) < size[1] * .008} and
                   any(clean(other) in ('qty', 'quantity') and
                       abs(other['box'][1] - word['box'][1]) < size[1] * .008 for other in words)), None)
    if header is None:
        return words, None
    start = header['box'][3]
    stop = min((word['box'][1] for word in words if word['box'][1] > start and
                clean(word) in ('taxable', 'subtotal', 'total', 'grand', 'cgst', 'sgst', 'igst')),
               default=size[1])
    recovered = []
    for word in tesseract_words(png, number, 6, 'table-recovery-'):
        box = word['box']
        if not start < box[1] < stop:
            continue
        area = max(1, (box[2]-box[0]) * (box[3]-box[1]))
        def overlaps(existing):
            other = existing['box']
            overlap = max(0, min(box[2], other[2])-max(box[0], other[0])) * max(0, min(box[3], other[3])-max(box[1], other[1]))
            other_area = max(1, (other[2]-other[0]) * (other[3]-other[1]))
            return overlap / min(area, other_area) > .5
        if not any(overlaps(existing) for existing in words):
            recovered.append({**word, 'ocr_psm': 6})
    return words + recovered, {'psm': 6, 'added_words': len(recovered),
                               'reason': 'Table-cell recovery; original words retained and overlapping detections excluded.'}


def load_document(path, force_ocr=False, dpi=300):
    pages, lines = [], []
    if path.suffix.lower() == ".pdf":
        doc = fitz.open(path)
        if doc.needs_pass:
            raise ValueError("Encrypted PDF: provide an unlocked copy.")
        for n, page in enumerate(doc, 1):
            native = pdf_page_words(page, n)
            # A text layer with only a few characters is often a logo or page number.
            quality = native_text_quality(native)
            use_native = not force_ocr and quality["usable"]
            recovery = None
            if use_native:
                words, method = native, "pdf_text"
                size = (page.rect.width, page.rect.height)
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
            else:
                pix = page.get_pixmap(matrix=fitz.Matrix(dpi/72, dpi/72), alpha=False)
                words, method = tesseract_words(pix.tobytes("png"), n), "tesseract"
                size = (pix.width, pix.height)
                words, recovery = recover_table_words(pix.tobytes("png"), words, n, size)
            pages.append({"number": n, "method": method, "size": list(size),
                          "png": pix.tobytes("png"), "text_quality": quality,
                          "routing_reason": "force OCR requested" if force_ocr else quality["reason"],
                          "coordinate_system": "pdf_points" if use_native else "rendered_pixels",
                          "embedded_words": native, "ocr_recovery": recovery})
            lines.extend(group_lines(words, n, method, size))
        doc.close()
    else:
        with Image.open(path) as source:
            img = source.convert("RGB")
        # Upscale small images to improve recognition; coordinates refer to this rendered image.
        if img.width < 1500:
            scale = 1500 / img.width
            img = img.resize((1500, round(img.height * scale)))
        buf = io.BytesIO(); img.save(buf, format="PNG")
        png = buf.getvalue()
        pages.append({"number": 1, "method": "tesseract", "size": list(img.size), "png": png, "coordinate_system": "rendered_pixels",
                      "routing_reason": "image input requires OCR"})
        lines.extend(group_lines(tesseract_words(png, 1), 1, "tesseract", img.size))
        # Dense forms may hide the contents of narrow cells in automatic page
        # segmentation. A second pass over the header recovers those cells;
        # its word IDs remain distinct, and all coordinates use the full image.
        header_lines = group_lines(tesseract_words(png, 1, 6, "header-"),
                                   1, "tesseract", img.size)
        lines.extend(l for l in header_lines if l["box"][1] < img.height * .37)
    return pages, sorted(lines, key=lambda l: (l["page"], l["box"][1], l["box"][0]))


def group_lines(words, number, method, size):
    groups = {}
    for word in words:
        groups.setdefault(word["line_key"], []).append(word)
    return [make_line(group, number, method, size) for group in groups.values()]


