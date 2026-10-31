import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from services.supplier_repository import SupplierRepository


class InvoiceSkillCreator:
    def __init__(
        self,
        output_dir: str = r"A:\dsh-core\workspace\skills",
        supplier_repo: Optional[SupplierRepository] = None
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.supplier_repo = supplier_repo or SupplierRepository()

    def _extract_siret(self, text: str) -> Optional[str]:
        # 1. Recherche avec mot-clé SIRET
        match_kw = re.search(r"SIRET[\s\.:]*([0-9\s\.]{14,22})", text, re.IGNORECASE)
        if match_kw:
            clean = re.sub(r"[^0-9]", "", match_kw.group(1))
            if len(clean) >= 14:
                return clean[:14]

        # 2. Motif standard français 3x3x3x5 chiffres (ex: 671 620 813 00103)
        match_groupes = re.search(r"\b(\d{3})[\s\.]+(\d{3})[\s\.]+(\d{3})[\s\.]+(\d{5})\b", text)
        if match_groupes:
            return "".join(match_groupes.groups())

        # 3. 14 chiffres continus
        match_brut = re.search(r"\b(\d{14})\b", text)
        if match_brut:
            return match_brut.group(1)

        return None

    def _extract_tva_intra(self, text: str) -> Optional[str]:
        match = re.search(r"\b(FR[\s\.]*[0-9A-Z]{2}[\s\.]*[0-9\s\.]{9,14})\b", text, re.IGNORECASE)
        if match:
            clean = re.sub(r"[\s\.]", "", match.group(1)).upper()
            if len(clean) >= 13:
                return clean[:13]
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

        header_text = raw_text[:1200]

        # Priorité absolue au SIRET transmis dans corrected_data, sinon extraction OCR
        siret = corrected_data.get("siret") or self._extract_siret(header_text) or self._extract_siret(raw_text)
        tva_intra = corrected_data.get("tva_intra") or self._extract_tva_intra(header_text) or self._extract_tva_intra(raw_text)

        supplier_id = None
        if siret:
            raw_err = extracted_data.get("fournisseur")
            aliases = [supplier_name.split()[0].upper()]
            if raw_err and raw_err.upper() not in aliases:
                aliases.append(raw_err.upper())

            supplier_record = self.supplier_repo.register_supplier(
                siret=siret,
                raison_sociale=supplier_name,
                tva_intra=tva_intra,
                aliases=aliases,
                human_validated=True
            )
            supplier_id = supplier_record["supplier_id"]
            self.supplier_repo.attach_or_update_skill(siret, skill_name, increment_usage=False)

        corrected_invoice_num = corrected_data.get("numero_facture")
        regex_invoice = self._derive_invoice_number_regex(raw_text, corrected_invoice_num) if corrected_invoice_num else None

        now_iso = datetime.now(timezone.utc).isoformat()

        skill_payload = {
            "skill_name": skill_name,
            "supplier_id": supplier_id,
            "domain": "invoice_processing",
            "version": "1.0.0",
            "tenant": tenant,
            "lifecycle": {
                "confidence": 1.0,
                "usage_count": 1,
                "last_used": now_iso
            },
            "overrides": {
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
                "corrected_fields": [k for k in corrected_data if corrected_data.get(k) != extracted_data.get(k)],
                "created_at": now_iso
            }
        }

        file_path = self.output_dir / f"{skill_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(skill_payload, f, indent=2, ensure_ascii=False)

        print(f"✅ Skill créé et rattaché à {supplier_id} : {file_path}")
        return skill_payload