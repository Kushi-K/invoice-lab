"""Create fictional searchable and scanned PDFs for a reproducible demo."""
from pathlib import Path
import io
import fitz
from PIL import Image

ROOT = Path(__file__).resolve().parent
doc = fitz.open()
page = doc.new_page(width=595, height=842)
rows = [
    (48, 65, "TAX INVOICE"),
    (48, 112, "Supplier"),
    (48, 133, "Acme Supplies Pvt Ltd"),
    (48, 153, "GSTIN: 29ABCDE1234F1Z5"),
    (48, 214, "Bill To"),
    (48, 235, "Beta Traders"),
    (48, 255, "GSTIN: 27PQRST6789L1Z2"),
    (48, 310, "Invoice No: INV-2026-041"),
    (48, 332, "Invoice Date: 27/09/2026"),
    (48, 410, "Description                  Qty             Rate             Amount"),
    (48, 435, "Office Supplies               2              500.00          1000.00"),
    (48, 505, "Taxable Amount: 1000.00"),
    (48, 527, "CGST: 90.00"),
    (48, 549, "SGST: 90.00"),
    (48, 590, "Grand Total: INR 1180.00"),
]
for x, y, s in rows:
    page.insert_text((x,y), s, fontsize=12, fontname="helv")
doc.save(ROOT / "demo_searchable.pdf")
pix = page.get_pixmap(matrix=fitz.Matrix(300/72,300/72), alpha=False)
rendered = Image.open(io.BytesIO(pix.tobytes("png"))).convert("RGB")
jpg = io.BytesIO()
rendered.save(jpg, format="JPEG", quality=98, optimize=True, subsampling=0)
scan = fitz.open()
scan_page = scan.new_page(width=595, height=842)
scan_page.insert_image(scan_page.rect, stream=jpg.getvalue())
scan.save(ROOT / "demo_scanned.pdf")
doc.close(); scan.close()
(ROOT / "ground_truth.json").write_text('''{
  "supplier_gstin": "29ABCDE1234F1Z5",
  "buyer_gstin": "27PQRST6789L1Z2",
  "invoice_number": "INV-2026-041",
  "invoice_date": "27/09/2026",
  "grand_total": "1180.00"
}\n''')
print("Wrote demo_searchable.pdf, demo_scanned.pdf, ground_truth.json")
