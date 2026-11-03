import sys
import os
import pymupdf

def clean_and_replace_text(pdf_path, text_to_remove, new_text="", output_path=None):
    if not os.path.exists(pdf_path):
        print(f"[ERREUR] Fichier introuvable : {pdf_path}")
        return

    if output_path is None:
        os.makedirs("workspace", exist_ok=True)
        base = os.path.basename(pdf_path)
        output_path = os.path.join("workspace", f"modifie_{base}")

    doc = pymupdf.open(pdf_path)
    modifications = 0

    for page in doc:
        # Recherche des coordonnées exactes du texte à supprimer
        text_instances = page.search_for(text_to_remove)
        
        for inst in text_instances:
            modifications += 1
            # 1. Masquage / suppression de l'ancien texte
            page.add_redact_annot(inst, fill=(1, 1, 1))  # fond blanc
            page.apply_redactions()

            # 2. Remplacement si un nouveau texte est spécifié
            if new_text:
                # Réinsertion au même emplacement avec police standard
                page.insert_text(inst.tl, new_text, fontsize=11, color=(0, 0, 0))

    doc.save(output_path)
    doc.close()
    print(f"[OK] {modifications} occurrence(s) modifiee(s). Livrable pret dans : {output_path}")

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python skills/plv_modifier/run.py <chemin_pdf> <texte_a_supprimer> [texte_de_remplacement]")
    else:
        pdf_file = sys.argv[1]
        target = sys.argv[2]
        replacement = sys.argv[3] if len(sys.argv) > 3 else ""
        clean_and_replace_text(pdf_file, target, replacement)
