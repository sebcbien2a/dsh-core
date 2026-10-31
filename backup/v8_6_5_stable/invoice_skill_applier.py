import json
import re
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple

from services.supplier_repository import SupplierRepository


class InvoiceSkillApplier:
    def __init__(
        self,
        skills_dir: str = r"A:\dsh-core\workspace\skills",
        supplier_repo: Optional[SupplierRepository] = None
    ):
        self.skills_dir = Path(skills_dir)
        self.supplier_repo = supplier_repo or SupplierRepository()
        self.skills: List[Dict[str, Any]] = []
        self._load_skills()

    def _load_skills(self) -> None:
        self.skills = []
        if not self.skills_dir.exists():
            return

        for skill_file in self.skills_dir.glob("*.json"):
            try:
                with open(skill_file, "r", encoding="utf-8") as f:
                    skill_data = json.load(f)
                    skill_data["_file_path"] = str(skill_file)
                    self.skills.append(skill_data)
            except Exception as e:
                print(f"⚠️ Erreur chargement skill {skill_file.name} : {e}")

    def _evaluate_skill_match(self, skill: Dict[str, Any], text_ocr: str) -> Tuple[bool, str, int]:
        supplier_id = skill.get("supplier_id")
        if not supplier_id:
            return False, "Aucun supplier_id rattaché", 0

        sup = self.supplier_repo.find_by_id(supplier_id)
        if not sup or sup.get("status") != "ACTIVE":
            return False, "Fournisseur inexistant ou inactif dans le référentiel", 0

        skill_name = skill.get("skill_name")
        for sk_entry in sup.get("skills", []):
            if sk_entry.get("skill_name") == skill_name and not sk_entry.get("active", True):
                return False, "Skill désactivé sur la fiche fournisseur", 0

        digits_doc = re.sub(r"[^0-9]", "", text_ocr)
        text_upper = text_ocr.upper()
        header_upper = text_ocr[:1000].upper()

        siret = sup.get("siret")
        tva_intra = sup.get("tva_intra")
        nom_officiel = sup.get("raison_sociale")
        aliases = sup.get("aliases", [])

        # =====================================================================
        # 1. PREUVES FORTES (SIRET 14 chiffres OU SIREN 9 chiffres racine)
        # =====================================================================
        if siret:
            siret_clean = re.sub(r"[^0-9]", "", siret)
            # A. SIRET complet (14 chiffres)
            if len(siret_clean) == 14 and siret_clean in digits_doc:
                return True, f"Preuve forte : SIRET {siret_clean} concordant", 100

            # B. SIREN racine (9 chiffres)
            siren_clean = siret_clean[:9]
            if len(siren_clean) == 9 and siren_clean in digits_doc:
                return True, f"Preuve forte : SIREN {siren_clean} concordant", 100

        if tva_intra:
            tva_clean = re.sub(r"[\s\.]", "", tva_intra).upper()
            if tva_clean in re.sub(r"[\s\.]", "", text_upper):
                return True, f"Preuve forte : TVA {tva_clean} concordante", 100

        # =====================================================================
        # 2. PREUVES FAIBLES (Exige un score cumulé >= 100)
        # =====================================================================
        weak_score = 0
        justifs = []

        if nom_officiel:
            pattern_nom = rf"\b{re.escape(nom_officiel.upper())}\b"
            if re.search(pattern_nom, header_upper):
                weak_score += 50
                justifs.append("Nom officiel en en-tête (+50)")

        for alias in aliases:
            if alias and len(alias) >= 3:
                pattern_alias = rf"\b{re.escape(alias.upper())}\b"
                if re.search(pattern_alias, header_upper):
                    weak_score += 20
                    justifs.append(f"Alias « {alias} » en en-tête (+20)")
                    break

        if weak_score >= 100:
            return True, f"Faisceau de preuves faibles : {', '.join(justifs)} (Total: {weak_score})", weak_score

        return False, f"Score insuffisant ({weak_score} < 100)", weak_score

    def find_matching_skill(self, text_ocr: str) -> Optional[Dict[str, Any]]:
        for skill in self.skills:
            matched, motif, score = self._evaluate_skill_match(skill, text_ocr)
            if matched:
                skill["_match_reason"] = motif
                skill["_match_score"] = score
                return skill
        return None

    def apply_skill(self, skill: Dict[str, Any], text_ocr: str, parsed_data: Dict[str, Any]) -> List[str]:
        commentaires = []
        skill_name = skill.get("skill_name", "inconnu")
        supplier_id = skill.get("supplier_id")
        reason = skill.get("_match_reason", "Preuve validée")

        sup = self.supplier_repo.find_by_id(supplier_id)
        if sup:
            canonical_name = sup.get("raison_sociale")
            ancien = parsed_data.get("fournisseur")
            if canonical_name and ancien != canonical_name:
                parsed_data["fournisseur"] = canonical_name
                commentaires.append(
                    f"Skill appliqué ({skill_name} | {reason}) : Fournisseur certifié « {canonical_name} » (remplace « {ancien} »)."
                )

        overrides = skill.get("overrides", {})
        if "type_tva" in overrides:
            parsed_data["type_tva"] = overrides["type_tva"]
        if "type_piece" in overrides:
            parsed_data["type_piece"] = overrides["type_piece"]

        rules = skill.get("extraction_rules", {})
        regex_num = rules.get("regex_numero_facture")
        if regex_num:
            match = re.search(regex_num, text_ocr)
            if match:
                ancien_num = parsed_data.get("numero_facture")
                nouveau_num = match.group(1) if match.groups() else match.group(0)
                parsed_data["numero_facture"] = nouveau_num
                commentaires.append(f"Skill appliqué ({skill_name}) : Numéro extrait via ancre « {nouveau_num} » (remplace « {ancien_num} »).")

        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            skill_path_str = skill.get("_file_path")
            if skill_path_str:
                path_obj = Path(skill_path_str)
                if path_obj.exists():
                    lifecycle = skill.setdefault("lifecycle", {})
                    lifecycle["usage_count"] = lifecycle.get("usage_count", 1) + 1
                    lifecycle["last_used"] = now_iso
                    with open(path_obj, "w", encoding="utf-8") as f:
                        to_save = {k: v for k, v in skill.items() if not k.startswith("_")}
                        json.dump(to_save, f, indent=2, ensure_ascii=False)

            if sup:
                self.supplier_repo.attach_or_update_skill(sup["siret"], skill_name, increment_usage=True)
        except Exception as e:
            print(f"⚠️ Erreur mise à jour usage skill : {e}")

        return commentaires