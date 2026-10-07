import json
import re
from pathlib import Path
from typing import Dict, Any, Optional

class InvoiceSkillCreator:
    def __init__(self, output_dir: str = r"A:\dsh-core\workspace\skills"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def _extract_siret(self, text: str) -> Optional[str]:
        match = re.search(r"\bSiret\s*[:\.]?\s*(\d{3}[\s\.]?\d{3}[\s\.]?\d{3}[\s\.]?\d{5})\b", text, re.IGNORECASE)
        if match:
            return re.sub(r"[\s\.]", "", match.group(1))
        match_brut = re.search(r"\b(\d{3}\s*\d{3}\s*\d{3}\s*\d{5})\b", text)
        if match_brut:
            return re.sub(r"\s", "", match_brut.group(1))
        return None

    def _extract_tva_intra(self, text: str) -> Optional[str]:
        match = re.search(r"\b(FR\s*[0-9A-Z]{2}\s*\d{9})\b", text, re.IGNORECASE)
        if match:
            return re.sub(r"\s", "", match.group(1)).upper()
        return None

    def _derive_invoice_number_regex(self, text: str, corrected_number: str) -> Optional[str]:
        if not corrected_number:
            return None

        escaped_num = re.escape(corrected_number)
        pattern = rf"([^\n\r]{{0,25}}){escaped_num}"
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            prefix = match.group(1).strip()
            if re.search(r"N[°o]\s*$", prefix, re.IGNORECASE):
                nb_digits = len(corrected_number)
                if corrected_number.isdigit():
                    return rf"N[°o]\s*(\d{{{nb_digits}}})"
                return rf"N[°o]\s*([A-Z0-9\-]+)"
            if re.search(r"Facture\s*[:\s]*$", prefix, re.IGNORECASE):
                return rf"Facture\s*[:\s]*([A-Z0-9\-]+)"

        if corrected_number.isdigit():
            return rf"\b(\d{{{len(corrected_number)}}})\b"
        return rf"\b({re.escape(corrected_number)})\b"

    def create_skill(
        self,
        raw_text: str,
        extracted_data: Dict[str, Any],
        corrected_data: Dict[str, Any],
        tenant: str = "GLOBAL"
    ) -> Dict[str, Any]:
        supplier_name = corrected_data.get("fournisseur", extracted_data.get("fournisseur", "inconnu"))
        clean_slug = re.sub(r"[^a-z0-9]+", "_", supplier_name.lower()).strip("_")
        skill_name = f"invoice_{clean_slug}"

        siret = self._extract_siret(raw_text)
        tva_intra = self._extract_tva_intra(raw_text)
        
        keywords = [k for k in [supplier_name.split()[0].upper(), clean_slug.upper()] if len(k) >= 3]
        if siret:
            keywords.append(siret)

        corrected_invoice_num = corrected_data.get("numero_facture")
        regex_invoice = self._derive_invoice_number_regex(raw_text, corrected_invoice_num) if corrected_invoice_num else None

        skill_payload = {
            "skill_name": skill_name,
            "domain": "invoice_processing",
            "version": "1.0.0",
            "tenant": tenant,
            "lifecycle": {
                "confidence": 1.0,
                "usage_count": 1
            },
            "match_signature": {
                "siret": siret,
                "tva_intra": tva_intra,
                "keywords": list(set(keywords))
            },
            "overrides": {
                "fournisseur": supplier_name,
                "type_tva": corrected_data.get("type_tva", extracted_data.get("type_tva", "NORMALE")),
                "type_piece": corrected_data.get("type_piece", extracted_data.get("type_piece", "FACTURE"))
            },
            "extraction_rules": {
                "regex_numero_facture": regex_invoice,
                "trusted_labels": {
                    "ht": "TOTAL H.T.",
                    "tva": "MT TVA",
                    "ttc": "NET A PAYER"
                }
            },
            "created_from": {
                "raw_supplier": extracted_data.get("fournisseur"),
                "raw_invoice_number": extracted_data.get("numero_facture"),
                "corrected_fields": [k for k in corrected_data if corrected_data.get(k) != extracted_data.get(k)]
            }
        }

        file_path = self.output_dir / f"{skill_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(skill_payload, f, indent=2, ensure_ascii=False)

        print(f"✅ Skill créé et persisté : {file_path}")
        return skill_payload