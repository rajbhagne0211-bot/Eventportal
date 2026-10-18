"""A4 PDF of the confirmation card (server-side, so 'Download as PDF' works on every device)."""
import io
from datetime import datetime

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode.qr import QrCodeWidget
from reportlab.graphics.shapes import Drawing
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.pdfgen import canvas

PAY_LABEL = {"paid": "PAID", "pending": "PAYMENT PENDING", "failed": "PAYMENT FAILED", "not_required": "NO FEE",
             "cancelled": "PAYMENT CANCELLED", "refunded": "REFUNDED"}


def build_pdf(c, verify_url):
    buf = io.BytesIO()
    pdf = canvas.Canvas(buf, pagesize=A4)
    w, h = A4
    pdf.setFillColorRGB(0.48, 0.12, 0.17)
    pdf.rect(0, h - 38 * mm, w, 38 * mm, fill=1, stroke=0)
    pdf.setFillColorRGB(1, 0.85, 0.63)
    pdf.setFont("Helvetica-Bold", 26)
    pdf.drawString(20 * mm, h - 20 * mm, "ASTRA FEST 2026")
    pdf.setFont("Helvetica", 11)
    pdf.setFillColorRGB(1, 1, 1)
    pdf.drawString(20 * mm, h - 29 * mm, "Mumbai Meri Jaan  |  Sathaye College, Vile Parle, Mumbai")
    pdf.setFillColorRGB(0.17, 0.14, 0.16)
    pdf.setFont("Helvetica", 11)
    pdf.drawString(20 * mm, h - 52 * mm, "REGISTRATION CODE")
    pdf.setFont("Helvetica-Bold", 28)
    pdf.drawString(20 * mm, h - 64 * mm, c["reg_code"])
    rows = [("Event", c["event"]), ("Category", c["category"]),
            ("Format", "Team" if c["format"] == "team" else "Individual"),
            ("Team" if c["format"] == "team" else "Participant", c["team_name"] or c["lead_name"] or ""),
            ("Captain" if c["format"] == "team" else "", c["lead_name"] if c["format"] == "team" else ""),
            ("Participants", str(c["participant_count"])), ("College", c["college"]), ("Department", c["department"] or ""),
            ("Registered on", datetime.fromisoformat(c["registered_on"].replace("Z", "+00:00")).strftime("%d %b %Y")),
            ("Payment", PAY_LABEL.get(c["payment_status"], c["payment_status"])),
            ("Registration status", c["status"].upper())]
    y = h - 80 * mm
    for k, v in rows:
        if not k:
            continue
        pdf.setFont("Helvetica-Bold", 11); pdf.drawString(20 * mm, y, k)
        pdf.setFont("Helvetica", 11); pdf.drawString(62 * mm, y, str(v)[:70])
        y -= 8 * mm
    qr = QrCodeWidget(verify_url)
    x0, y0, x1, y1 = qr.getBounds()
    size = 42 * mm
    d = Drawing(size, size, transform=[size / (x1 - x0), 0, 0, size / (y1 - y0), 0, 0])
    d.add(qr)
    renderPDF.draw(d, pdf, w - 20 * mm - size, h - 80 * mm - size + 8 * mm)
    pdf.setFont("Helvetica", 9)
    pdf.drawString(w - 20 * mm - size, h - 80 * mm - size + 2 * mm, "Scan to verify")
    pdf.setFont("Helvetica-Oblique", 10)
    pdf.drawString(20 * mm, 30 * mm, "Please bring this printed confirmation to the event.")
    if c["payment_status"] in ("pending", "failed"):
        pdf.setFillColorRGB(0.7, 0, 0.13)
        pdf.drawString(20 * mm, 24 * mm, "Payment is NOT confirmed. This is not a paid ticket.")
    pdf.showPage()
    pdf.save()
    return buf.getvalue()
