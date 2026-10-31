import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

from services.supplier_repository import SupplierRepository


class InvoiceSkillCreator:
    """
    Générateur modulaire de compétences documentaires (InvoiceSkills).
    Orchestre l'extraction des identifiants régaliens, la certification
    dans SupplierRepository et la dérivation des ancres de capture.
    """

    # Formes juridiques et mots vides à purger pour forger des alias propres
    LEGAL_PATTERNS = r"\b(SAS|SARL|SA|EURL|SNC|SCI|SOCIETE|ETS|ETABLISSEMENTS|GROUP|GROUPE)\b"
    STOP_WORDS = {"LES", "DES", "LE", "LA", "DU", "AU", "AUX", "ET", "DE", "D"}

    def __init__(
        self,
        output_dir: str = r"A:\dsh-core\workspace\skills",
        supplier_repo: Optional[SupplierRepository] = None
    ):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.supplier_repo = supplier_repo or SupplierRepository()

    def _extract_siret(self, text: str) -> Optional[str]:
        """Extrait un numéro SIRET (14 chiffres) sous différentes syntaxes OCR."""
        # 1. Recherche avec libellé explicite
        match_kw = re.search(r"SIRET[\s\.:]*([0-9\s\.]{14,22})", text, re.IGNORECASE)
        if match_kw:
            clean = re.sub(r"[^0-9]", "", match_kw.group(1))
            if len(clean) >= 14:
                return clean[:14]

        # 2. Motif standard français (3x3x3x5 chiffres)
        match_groupes = re.search(r"\b(\d{3})[\s\.]+(\d{3})[\s\.]+(\d{3})[\s\.]+(\d{5})\b", text)
        if match_groupes:
            return "".join(match_groupes.groups())

        # 3. 14 chiffres continus isolés
        match_brut = re.search(r"\b(\d{14})\b", text)
        if match_brut:
            return match_brut.group(1)

        return None

    def _extract_tva_intra(self, text: str) -> Optional[str]:
        """Extrait un numéro de TVA intracommunautaire (FR + 2 caractères + 9 chiffres)."""
        match = re.search(r"\b(FR[\s\.]*[0-9A-Z]{2}[\s\.]*[0-9\s\.]{9,14})\b", text, re.IGNORECASE)
        if match:
            clean = re.sub(r"[\s\.]", "", match.group(1)).upper()
            if len(clean) >= 13:
                return clean[:13]
        return None

    def _build_aliases(self, official_name: str, extracted_name: Optional[str]) -> List[str]:
        """
        Construit une liste d'alias nettoyée des formes juridiques et mots parasites.
        Évite les collisions accidentelles (ex: 'LES', 'SOCIETE').
        """
        aliases = set()
        clean_official = official_name.strip().upper()
        aliases.add(clean_official)

        # Ajout du terme erroné extrait par le LLM (ex: RCSAIXENPROVENCE)
        if extracted_name:
            clean_extracted = extracted_name.strip().upper()
            if clean_extracted != clean_official and len(clean_extracted) >= 3:
                aliases.add(clean_extracted)

        # Dépouillement des extensions de statut (ex: 'SMTRT SAS' -> 'SMTRT')
        stripped_legal = re.sub(self.LEGAL_PATTERNS, "", clean_official, flags=re.IGNORECASE).strip()
        stripped_legal = re.sub(r"\s+", " ", stripped_legal).strip()
        if stripped_legal and stripped_legal != clean_official:
            aliases.add(stripped_legal)

        # Extraction de tokens significatifs (longueur >= 4 et hors stop words)
        tokens = [t for t in re.split(r"[\s\-_/]+", stripped_legal) if len(t) >= 4 and t not in self.STOP_WORDS]
        for token in tokens:
            aliases.add(token)

        return sorted(list(aliases))

    def _derive_invoice_number_regex(self, text: str, corrected_number: str) -> Optional[str]:
        """Dérive l'ancre contextuelle pour extraire le numéro de facture sans confusion."""
        if not corrected_number:
            return None

        escaped_num = re.escape(corrected_number)
        pattern = rf"([^\n\r]{{0,30}}){escaped_num}"
        match = re.search(pattern, text, re.IGNORECASE)

        if match:
            prefix = match.group(1).strip()
            # Ancrage sur 'N°', 'N° :', 'Facture n°'
            if re.search(r"N[°o]\s*[:\.]?\s*$", prefix, re.IGNORECASE):
                nb_digits = len(corrected_number)
                if corrected_number.isdigit():
                    return rf"N[°o]\s*[:\.]?\s*(\d{{{nb_digits}}})"
                return rf"N[°o]\s*[:\.]?\s*([A-Z0-9\-]+)"

            if re.search(r"Facture\s*[:\s]*$", prefix, re.IGNORECASE):
                return rf"Facture\s*[:\s]*([A-Z0-9\-]+)"

        # Repli sur le gabarit strict de la chaîne corrigée
        if corrected_number.isdigit():
            return rf"\b(\d{{{len(corrected_number)}}})\b"
        return rf"\b({re.escape(corrected_number)})\b"

    def _build_skill_payload(
        self,
        skill_name: str,
        supplier_id: Optional[str],
        tenant: str,
        regex_invoice: Optional[str],
        extracted_data: Dict[str, Any],
        corrected_data: Dict[str, Any],
        now_iso: str
    ) -> Dict[str, Any]:
        """Assemble la structure JSON canonique du skill (sans duplication d'identité)."""
        return {
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

    def _save_skill(self, skill_name: str, payload: Dict[str, Any]) -> Path:
        """Persiste le fichier JSON du skill sur disque."""
        file_path = self.output_dir / f"{skill_name}.json"
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        return file_path

    def create_skill(
        self,
        raw_text: str,
        extracted_data: Dict[str, Any],
        corrected_data: Dict[str, Any],
        tenant: str = "GLOBAL",
        human_validated: bool = True
    ) -> Dict[str, Any]:
        """
        Point d'entrée principal : convertit une validation en compétence persistante.
        """
        supplier_name = corrected_data.get("fournisseur", extracted_data.get("fournisseur", "inconnu"))
        clean_slug = re.sub(r"[^a-z0-9]+", "_", supplier_name.lower()).strip("_")
        skill_name = f"invoice_{clean_slug}"

        header_text = raw_text[:1200]

        # Priorité à la saisie de l'opérateur sur le SIRET / TVA, sinon extraction OCR
        siret = corrected_data.get("siret") or self._extract_siret(header_text) or self._extract_siret(raw_text)
        tva_intra = corrected_data.get("tva_intra") or self._extract_tva_intra(header_text) or self._extract_tva_intra(raw_text)

        supplier_id = None
        if siret:
            aliases = self._build_aliases(
                official_name=supplier_name,
                extracted_name=extracted_data.get("fournisseur")
            )

            # Enregistrement légal auprès de la source de vérité
            supplier_record = self.supplier_repo.register_supplier(
                siret=siret,
                raison_sociale=supplier_name,
                tva_intra=tva_intra,
                aliases=aliases,
                human_validated=human_validated
            )
            supplier_id = supplier_record["supplier_id"]
            self.supplier_repo.attach_or_update_skill(siret, skill_name, increment_usage=False)

        # Dérivation de l'ancre regex pour le numéro de facture
        corrected_invoice_num = corrected_data.get("numero_facture")
        regex_invoice = self._derive_invoice_number_regex(raw_text, corrected_invoice_num)

        now_iso = datetime.now(timezone.utc).isoformat()

        # Assemblage et persistance
        payload = self._build_skill_payload(
            skill_name=skill_name,
            supplier_id=supplier_id,
            tenant=tenant,
            regex_invoice=regex_invoice,
            extracted_data=extracted_data,
            corrected_data=corrected_data,
            now_iso=now_iso
        )

        file_path = self._save_skill(skill_name, payload)
        print(f"✅ Skill créé et rattaché à {supplier_id or 'AUCUN'} : {file_path}")

        return payload