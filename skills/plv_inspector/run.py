import sys
import os
import pymupdf

def inspect_pdf(pdf_path, output_dir="workspace"):
    if not os.path.exists(pdf_path):
        print(f"[ERREUR] Fichier introuvable : {pdf_path}")
        return

    os.makedirs(output_dir, exist_ok=True)
    doc = pymupdf.open(pdf_path)
    print(f"=== Analyse PLV : {os.path.basename(pdf_path)} ===")
    print(f"Nombre de pages : {len(doc)}")

    for i, page in enumerate(doc):
        rect = page.rect
        width_mm = round(rect.width * 25.4 / 72, 1)
        height_mm = round(rect.height * 25.4 / 72, 1)
        print(f"\n--- Page {i + 1} ---")
        print(f"Dimensions : {width_mm} mm x {height_mm} mm (Points: {rect.width}x{rect.height})")

        text = page.get_text().strip()
        print(f"Texte extrait ({len(text)} car.) :\n{text[:300]}..." if len(text) > 300 else f"Texte extrait :\n{text}")

        pix = page.get_pixmap(dpi=150)
        img_name = f"preview_page_{i + 1}.png"
        img_path = os.path.join(output_dir, img_name)
        pix.save(img_path)
        print(f"[Apercu genere dans] : {img_path}")

    doc.close()

if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else ""
    if not path:
        print("Usage: python skills/plv_inspector/run.py <chemin_vers_le_pdf>")
    else:
        inspect_pdf(path)
