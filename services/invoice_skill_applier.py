import json
import re
from pathlib import Path
from typing import Dict, Any, List, Optional

class InvoiceSkillApplier:
    def __init__(self, skills_dir: str = r"A:\dsh-core\workspace\skills"):
        self.skills_dir = Path(skills_dir)
        self.skills: List[Dict[str, Any]] = []
        self._load_skills()

    def _load_skills(self):
        """Charge l'ensemble des skills JSON disponibles sur disque."""
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
                print(f"⚠️ Erreur de lecture du skill {skill_file.name} : {e}")

    def find_matching_skill(self, text_ocr: str) -> Optional[Dict[str, Any]]:
        """Identifie si un skill enregistré correspond au document analysé."""
        text_upper = text_ocr.upper()

        for skill in self.skills:
            sig = skill.get("match_signature", {})
            
            # 1. Matching par identifiant légal (SIRET ou TVA)
            siret = sig.get("siret")
            if siret and siret in text_ocr:
                return skill

            tva_intra = sig.get("tva_intra")
            if tva_intra and tva_intra in text_upper:
                return skill

            # 2. Matching par mots-clés de signature
            keywords = sig.get("keywords", [])
            for kw in keywords:
                if kw and kw.upper() in text_upper:
                    return skill

        return None

    def apply_skill(self, skill: Dict[str, Any], text_ocr: str, parsed_data: Dict[str, Any]) -> List[str]:
        """Applique les dérogations et ancrages d'un skill sur les données extraites."""
        commentaires = []
        skill_name = skill.get("skill_name", "inconnu")

        # 1. Application des surcharges d'autorité (Overrides)
        overrides = skill.get("overrides", {})
        if "fournisseur" in overrides:
            ancien = parsed_data.get("fournisseur")
            nouveau = overrides["fournisseur"]
            if ancien != nouveau:
                parsed_data["fournisseur"] = nouveau
                commentaires.append(f"Skill appliqué ({skill_name}) : Fournisseur certifié '{nouveau}' (remplace '{ancien}').")

        if "type_tva" in overrides:
            parsed_data["type_tva"] = overrides["type_tva"]

        if "type_piece" in overrides:
            parsed_data["type_piece"] = overrides["type_piece"]

        # 2. Application de l'ancrage regex pour le numéro de facture
        rules = skill.get("extraction_rules", {})
        regex_num = rules.get("regex_numero_facture")
        if regex_num:
            match = re.search(regex_num, text_ocr)
            if match:
                ancien_num = parsed_data.get("numero_facture")
                nouveau_num = match.group(1) if match.groups() else match.group(0)
                parsed_data["numero_facture"] = nouveau_num
                commentaires.append(f"Skill appliqué ({skill_name}) : Numéro extrait via ancre '{nouveau_num}' (remplace '{ancien_num}').")

        # 3. Incrémentation du compteur d'usage
        try:
            skill_path_str = skill.get("_file_path")
            if skill_path_str:
                path_obj = Path(skill_path_str)
                if path_obj.exists():
                    lifecycle = skill.setdefault("lifecycle", {})
                    lifecycle["usage_count"] = lifecycle.get("usage_count", 1) + 1
                    with open(path_obj, "w", encoding="utf-8") as f:
                        to_save = {k: v for k, v in skill.items() if k != "_file_path"}
                        json.dump(to_save, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Erreur de mise à jour du cycle de vie du skill : {e}")

        return commentaires