import sys
import os
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.pdfgen import canvas

def generate_plv(product_name, tagline, specs_raw, logo_path=None, output_dir="workspace"):
    os.makedirs(output_dir, exist_ok=True)
    safe_name = "".join(c if c.isalnum() else "_" for c in product_name)
    out_file = os.path.join(output_dir, f"PLV_{safe_name}.pdf")

    c = canvas.Canvas(out_file, pagesize=A4)
    width, height = A4

    # Bandeau superieur sombre
    c.setFillColor(colors.HexColor("#1A1F2C"))
    c.rect(0, height - 120, width, 120, fill=1, stroke=0)

    # Logo si fourni et existant
    if logo_path and os.path.exists(logo_path):
        try:
            c.drawImage(logo_path, 40, height - 100, width=100, height=80, preserveAspectRatio=True, mask='auto')
        except Exception as e:
            print(f"[Info] Logo ignore : {e}")

    # Titre du produit
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Bold", 28)
    c.drawString(160 if (logo_path and os.path.exists(logo_path)) else 40, height - 65, product_name.upper())

    # Accroche
    c.setFillColor(colors.HexColor("#E63946"))
    c.setFont("Helvetica-Bold", 16)
    c.drawString(160 if (logo_path and os.path.exists(logo_path)) else 40, height - 95, tagline)

    # Bloc caracteristiques techniques
    c.setFillColor(colors.HexColor("#1A1F2C"))
    c.setFont("Helvetica-Bold", 18)
    c.drawString(40, height - 180, "CARACTERISTIQUES & AVANTAGES :")

    c.setStrokeColor(colors.HexColor("#E63946"))
    c.setLineWidth(2)
    c.line(40, height - 190, width - 40, height - 190)

    specs = [s.strip() for s in specs_raw.split("|") if s.strip()]
    y_pos = height - 230
    c.setFont("Helvetica", 14)

    for spec in specs:
        c.setFillColor(colors.HexColor("#E63946"))
        c.circle(50, y_pos + 5, 4, fill=1, stroke=0)
        c.setFillColor(colors.HexColor("#2B2D42"))
        c.drawString(65, y_pos, spec)
        y_pos -= 35

    # Pied de page
    c.setFillColor(colors.HexColor("#1A1F2C"))
    c.rect(0, 0, width, 50, fill=1, stroke=0)
    c.setFillColor(colors.white)
    c.setFont("Helvetica-Oblique", 10)
    c.drawCentredString(width / 2, 20, "Document genere automatiquement par DSH-Core")

    c.save()
    print(f"[OK] PLV generee avec succes : {out_file}")

if __name__ == "__main__":
    if len(sys.argv) < 4:
        print('Usage: python skills/plv_generator/run.py "<Produit>" "<Accroche>" "<Spec1|Spec2|Spec3>" [chemin_logo]')
    else:
        prod = sys.argv[1]
        tag = sys.argv[2]
        sp = sys.argv[3]
        logo = sys.argv[4] if len(sys.argv) > 4 else None
        generate_plv(prod, tag, sp, logo)
