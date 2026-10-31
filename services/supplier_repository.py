import difflib
import json
import re
import unicodedata
from pathlib import Path
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List

from services.invoice_producer_protocol import normalise_vat


def _normalise_name(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(char for char in text if not unicodedata.combining(char))
    text = re.sub(r"[^A-Za-z0-9]+", " ", text).strip().upper()
    return re.sub(r"\s+", " ", text)


def _name_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right or left.replace(" ", "") == right.replace(" ", ""):
        return 1.0
    return difflib.SequenceMatcher(None, left, right).ratio()


class SupplierRepository:
    def __init__(self, file_path: str = r"A:\dsh-core\workspace\suppliers\suppliers.json"):
        self.file_path = Path(file_path)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._save({
                "metadata": {
                    "version": "2.0",
                    "next_supplier_id": 1
                },
                "suppliers": {}
            })

    def _load(self) -> Dict[str, Any]:
        try:
            with open(self.file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if "metadata" not in data:
                    data["metadata"] = {"version": "2.0", "next_supplier_id": 1}
                if "suppliers" not in data:
                    data["suppliers"] = {}
                return data
        except Exception:
            return {"metadata": {"version": "2.0", "next_supplier_id": 1}, "suppliers": {}}

    def _save(self, data: Dict[str, Any]) -> None:
        with open(self.file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)

    def find_by_siret(self, siret: str) -> Optional[Dict[str, Any]]:
        if not siret:
            return None
        clean_siret = str(siret).strip()
        data = self._load()
        return data["suppliers"].get(clean_siret)

    def find_by_tva(self, tva_intra: str) -> Optional[Dict[str, Any]]:
        if not tva_intra:
            return None
        clean_tva = normalise_vat(str(tva_intra))
        data = self._load()
        for sup in data["suppliers"].values():
            stored = sup.get("tva_intra")
            if stored and normalise_vat(str(stored)) == clean_tva:
                return sup
        return None

    def find_by_siren(self, siren: str) -> Optional[Dict[str, Any]]:
        digits = re.sub(r"\D", "", str(siren or ""))
        if len(digits) < 9:
            return None
        key = digits[:9]
        for sup in self.iter_suppliers():
            siret = re.sub(r"\D", "", str(sup.get("siret") or ""))
            if len(siret) >= 9 and siret[:9] == key:
                return sup
        return None

    def find_by_name(
        self, name: str, *, threshold: float = 0.82
    ) -> Optional[Dict[str, Any]]:
        target = _normalise_name(name)
        if not target:
            return None
        best: Optional[Dict[str, Any]] = None
        best_score = 0.0
        for sup in self.iter_suppliers():
            candidates = [sup.get("raison_sociale"), *list(sup.get("aliases") or [])]
            for candidate in candidates:
                score = _name_similarity(target, _normalise_name(candidate))
                if score > best_score:
                    best_score = score
                    best = sup
                if score >= 1.0:
                    return sup
        return best if best_score >= threshold else None

    def iter_suppliers(self) -> List[Dict[str, Any]]:
        data = self._load()
        return list(data["suppliers"].values())

    def find_by_id(self, supplier_id: str) -> Optional[Dict[str, Any]]:
        if not supplier_id:
            return None
        data = self._load()
        for sup in data["suppliers"].values():
            if sup.get("supplier_id") == supplier_id:
                return sup
        return None

    def register_supplier(
        self,
        siret: str,
        raison_sociale: str,
        tva_intra: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        human_validated: bool = True
    ) -> Dict[str, Any]:
        """
        Enregistre ou met a jour un fournisseur legal.
        Controle d'unicite absolu (SIRET + TVA) et cycle de certification.
        """
        if not siret:
            raise ValueError("SIRET obligatoire pour enregistrer un fournisseur.")

        clean_siret = str(siret).strip()
        clean_name = raison_sociale.strip()
        clean_tva = tva_intra.strip().upper() if tva_intra else None

        data = self._load()
        suppliers = data["suppliers"]

        # CONTRÔLE 1 : Unicité SIRET
        existing_siret = suppliers.get(clean_siret)
        if existing_siret:
            if existing_siret["raison_sociale"].strip().upper() != clean_name.upper():
                raise ValueError(
                    f"CONFLIT LÉGAL SIRET : Le SIRET {clean_siret} appartient déjà à "
                    f"« {existing_siret['raison_sociale']} ». Tentative d'association illégitime à « {clean_name} »."
                )

        # CONTRÔLE 2 : Unicité TVA
        if clean_tva:
            for s_id, s_data in suppliers.items():
                if s_data.get("tva_intra") == clean_tva and s_id != clean_siret:
                    raise ValueError(
                        f"CONFLIT LÉGAL TVA : La TVA {clean_tva} est déjà attribuée à "
                        f"« {s_data['raison_sociale']} » (SIRET {s_id}). Impossible de la lier à « {clean_name} »."
                    )

        now_iso = datetime.now(timezone.utc).isoformat()

        # MISE À JOUR FOURNISSEUR EXISTANT
        if existing_siret:
            updated = False
            if clean_tva and not existing_siret.get("tva_intra"):
                existing_siret["tva_intra"] = clean_tva
                updated = True

            if aliases:
                curr_aliases = set(existing_siret.get("aliases", []))
                for a in aliases:
                    if a and a.strip() and a.strip() not in curr_aliases:
                        curr_aliases.add(a.strip())
                        updated = True
                existing_siret["aliases"] = sorted(list(curr_aliases))

            if human_validated:
                verification = existing_siret.setdefault("verification", {"status": "PENDING", "human_validations": 0})
                verification["human_validations"] += 1
                if verification["human_validations"] >= 3:
                    verification["status"] = "VERIFIED"
                updated = True

            if updated:
                existing_siret["updated_at"] = now_iso
                suppliers[clean_siret] = existing_siret
                self._save(data)
            return existing_siret

        # CRÉATION FOURNISSEUR AVEC COMPTEUR INCRÉMENTAL SÉCURISÉ
        next_counter = data["metadata"].get("next_supplier_id", 1)
        supplier_id = f"SUP_{next_counter:04d}"
        data["metadata"]["next_supplier_id"] = next_counter + 1

        clean_aliases = sorted(list(set([a.strip() for a in (aliases or []) if a and a.strip()])))

        new_supplier = {
            "supplier_id": supplier_id,
            "raison_sociale": clean_name,
            "siret": clean_siret,
            "tva_intra": clean_tva,
            "aliases": clean_aliases,
            "verification": {
                "status": "VERIFIED" if human_validated else "PENDING",
                "human_validations": 1 if human_validated else 0
            },
            "status": "ACTIVE",
            "skills": [],
            "created_at": now_iso,
            "updated_at": now_iso
        }

        suppliers[clean_siret] = new_supplier
        self._save(data)
        print(f"🏛️ Référentiel fournisseur : Création de « {clean_name} » ({supplier_id} | SIRET {clean_siret})")
        return new_supplier

    def attach_or_update_skill(self, siret: str, skill_name: str, increment_usage: bool = False) -> None:
        """Met à jour le statut et l'usage d'un skill dans la fiche du fournisseur."""
        clean_siret = str(siret).strip()
        data = self._load()
        sup = data["suppliers"].get(clean_siret)
        if not sup:
            return

        skills = sup.get("skills", [])
        found = False
        now_iso = datetime.now(timezone.utc).isoformat()

        for sk in skills:
            if sk.get("skill_name") == skill_name:
                found = True
                if increment_usage:
                    sk["usage_count"] = sk.get("usage_count", 0) + 1
                    sk["last_used"] = now_iso
                break

        if not found:
            skills.append({
                "skill_name": skill_name,
                "active": True,
                "usage_count": 1,
                "created_at": now_iso,
                "last_used": now_iso
            })

        sup["skills"] = skills
        sup["updated_at"] = now_iso
        data["suppliers"][clean_siret] = sup
        self._save(data)