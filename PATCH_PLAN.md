# Plan de patch — Ticket P1

## Statut

**En attente de validation du groupe A.** Aucun fichier source ne sera modifié
avant validation explicite.

Le groupe B est suspendu : aucune transformation de `InvoiceSchema` en
`InvoiceData`, aucun remplacement de modèle et aucune modification de contrat
ne sont proposés tant que leur compatibilité n'est pas démontrée.

## Périmètre demandé

Fichiers concernés par le ticket :

- `api.py`
- `router.py`
- `agents/research_agent.py`
- `services/extraction_manager.py`

Les propositions ci-dessous n'impliquent que `api.py` et
`agents/research_agent.py` pour le groupe A. `router.py` contient déjà
`route_prompt`; aucune édition n'est proposée pour ce fichier. Le groupe B
concerne `services/extraction_manager.py` et reste sans patch jusqu'à résolution
du contrat de données.

# GROUPE A — `router_intelligent` et `find_relevant_skill`

## A1. `api.py` — appel à `router_intelligent`

### Code actuel

```python
from router import router_intelligent
result = router_intelligent(
    prompt=request.prompt,
    image_path=request.image_path,
    complexite_haute=request.complexite_haute
)
```

### Code proposé

```python
from router import route_prompt
result = route_prompt(
    prompt=request.prompt,
    image_provided=bool(request.image_path)
)
```

`router.py` définit `route_prompt(prompt, user_id="admin",
image_provided=False)`. Le résultat sera conservé dans l'enveloppe de réponse
actuelle de la route.

### Impact

L'endpoint `/api/router` appellera le symbole réellement défini, au lieu de
l'import absent `router_intelligent`. L'indication d'image est passée sous le
nom attendu par le routeur.

### Risque

`complexite_haute` n'a pas d'équivalent dans la signature existante : le champ
reste accepté par `RouterRequest`, mais n'influencera pas le routage. Aucun
`user_id` n'est présent dans le modèle de requête; le défaut `"admin"` du
routeur sera donc utilisé.

## A2. `agents/research_agent.py` — appel à `find_relevant_skill`

### Code actuel

```python
routing_res = router.find_relevant_skill(task=query)
if routing_res and routing_res.get("skills"):
    print(f"[ResearchAgent] 💡 Compétence mémorisée injectée pour le rôle [{self.base_role}] !")
    skill_context = "\n" + routing_res["context"] + "\n"
```

### Code proposé

```python
routing_res = router.retrieve_skill_for_task(task=query)
if routing_res:
    print(f"[ResearchAgent] 💡 Compétence mémorisée injectée pour le rôle [{self.base_role}] !")
    skill_context = "\n" + routing_res + "\n"
```

`SkillRouter.retrieve_skill_for_task(task)` existe et renvoie
`Optional[str]` : le contexte formaté du skill ou `None`. Aucun changement
n'est proposé aux imports ou à l'instanciation du repository/router existants.

### Impact

La recherche ne demandera plus une méthode absente et utilisera le type de
retour réellement défini par `SkillRouter`.

### Risque

Le contexte dépend toujours de Qdrant, du service d'embedding et de la présence
d'un skill pertinent. Le `try/except` actuel reste inchangé dans cette
proposition.

## A3. `router.py`

### Code actuel

```python
def route_prompt(prompt: str, user_id: str = "admin", image_provided: bool = False) -> Dict[str, Any]:
```

### Code proposé

Aucun changement. Cette fonction est déjà définie dans `router.py`; le défaut
`router_intelligent` est dans son appelant `api.py`.

### Impact

La signature, les appelants existants et le comportement du routeur ne sont
pas modifiés.

### Risque

Aucun risque de changement de signature dans `router.py`, puisqu'aucune édition
n'y est prévue.

# GROUPE B — `InvoiceSchema`

## B1. `services/extraction_manager.py`

### Code actuel

Import :

```python
from schemas.invoice_schema import InvoiceSchema
```

Validation :

```python
valid_invoice = InvoiceSchema(**data)
```

Le symbole `InvoiceSchema` n'est pas défini par le contenu actuel de
`schemas/invoice_schema.py`.

### Tous les champs explicitement demandés par le prompt actuel

Le JSON du prompt d'ExtractionManager demande exactement ces clés :

```json
{
  "numero_facture": "Numéro ou FA-INCONNU",
  "date_emission": "YYYY-MM-DD",
  "fournisseur": {
    "nom": "Nom du vendeur"
  },
  "client": {
    "nom": "Nom de l'acheteur"
  },
  "lignes": [
    {
      "description": "Nom de l'article",
      "quantite": 1.0,
      "prix_unitaire_ht": 0.0,
      "total_ht": 0.0,
      "taux_tva": 20.0
    }
  ],
  "total_ht": 0.0,
  "montant_tva": 0.0,
  "total_ttc": 0.0,
  "devise": "EUR"
}
```

Soit les champs racine `numero_facture`, `date_emission`, `fournisseur`,
`client`, `lignes`, `total_ht`, `montant_tva`, `total_ttc`, `devise`; le champ
`fournisseur` est lui-même un objet avec `nom`, `client` un objet avec `nom`,
et chaque élément de `lignes` contient `description`, `quantite`,
`prix_unitaire_ht`, `total_ht`, `taux_tva`.

### Contenu exact actuel de `schemas/invoice_schema.py`

```python
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field, model_validator

class TypePiece(str, Enum):
    FACTURE = "FACTURE"
    AVOIR = "AVOIR"

class TypeTVA(str, Enum):
    NORMALE = "NORMALE"                      # Régime standard (TVA déductible classique)
    AUTOLIQUIDATION = "AUTOLIQUIDATION"      # Intracommunautaire (TVA payée nulle, CA3 requise)
    NON_ASSUJETTI = "NON_ASSUJETTI"          # Franchise en base art. 293 B CGI (Aucune TVA)

class InvoiceData(BaseModel):
    # --- 0. Métadonnées & Typage métier ---
    type_piece: TypePiece = TypePiece.FACTURE
    type_tva: TypeTVA = TypeTVA.NORMALE
    origine_document: str = "INCONNU"
    
    fournisseur: str
    date_facture: str
    numero_facture: Optional[str] = None
    
    # --- 1. Valeurs fiscales d'origine ---
    montant_ht: float
    taux_tva_pourcentage: Optional[float] = None
    taux_tva_theorique: Optional[float] = None  # 20.0 % pour calcul CA3 en autoliquidation
    montant_tva: Optional[float] = None
    montant_ttc: float
    
    # --- 2. Valeurs commerciales ---
    montant_remise: Optional[float] = 0.0
    montant_paye: Optional[float] = None
    
    # --- 3. Valeurs recalculées par DSH-Core ---
    montant_ht_net: Optional[float] = None
    montant_tva_net: Optional[float] = None
    montant_ttc_net: Optional[float] = None
    
    # --- 4. Statut et Audit (Soft Fail) ---
    statut_traitement: str = "VALIDE"
    commentaires_controle: list[str] = Field(default_factory=list)
    
    devise: str = "EUR"

    @model_validator(mode="after")
    def process_and_verify(self):
        # ==========================================
        # CONTRÔLES DES RÉGIMES DE TVA SPÉCIAUX
        # ==========================================
        if self.type_tva in [TypeTVA.AUTOLIQUIDATION, TypeTVA.NON_ASSUJETTI]:
            self.montant_tva = 0.0
            self.taux_tva_pourcentage = None
            
            if self.type_tva == TypeTVA.AUTOLIQUIDATION and self.taux_tva_theorique is None:
                self.taux_tva_theorique = 20.0

        # ==========================================
        # CONTRÔLES TAUX TVA (Factures NORMALE)
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE and self.taux_tva_pourcentage is not None:
            if self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage < 0:
                self.commentaires_controle.append(f"Taux TVA négatif détecté ({self.taux_tva_pourcentage}%).")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"
            elif self.type_piece == TypePiece.FACTURE and self.taux_tva_pourcentage > 30:
                self.commentaires_controle.append(f"Taux TVA improbable détecté ({self.taux_tva_pourcentage}%). Valeur invalidée.")
                self.taux_tva_pourcentage = None
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # CONTRÔLES MONTANTS (Restreints aux Factures)
        # ==========================================
        if self.montant_tva is not None and self.type_piece == TypePiece.FACTURE:
            if self.montant_tva < 0:
                self.commentaires_controle.append(f"Montant TVA négatif détecté ({self.montant_tva}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc > 0 and self.montant_tva > self.montant_ttc:
                self.commentaires_controle.append("TVA supérieure au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > 0 and self.montant_tva > self.montant_ht:
                self.commentaires_controle.append("TVA supérieure au HT : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        if self.type_piece == TypePiece.FACTURE:
            if self.montant_ht < 0:
                self.commentaires_controle.append(f"Montant HT négatif détecté ({self.montant_ht}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ttc < 0:
                self.commentaires_controle.append(f"Montant TTC négatif détecté ({self.montant_ttc}).")
                self.statut_traitement = "A_VERIFIER"
            if self.montant_ht > self.montant_ttc:
                self.commentaires_controle.append("HT supérieur au TTC : valeur incohérente.")
                self.statut_traitement = "A_VERIFIER"

        # ==========================================
        # DÉTECTION D'HALLUCINATION (HT = TVA = TTC)
        # ==========================================
        if (
            self.montant_tva is not None
            and abs(self.montant_ht) > 0
            and abs(self.montant_ttc) > 0
            and abs(self.montant_ht - self.montant_tva) < 0.01
            and abs(self.montant_tva - self.montant_ttc) < 0.01
        ):
            self.commentaires_controle.append("Configuration HT = TVA = TTC détectée. Hallucination probable du LLM.")
            self.statut_traitement = "A_VERIFIER"
            self.taux_tva_pourcentage = None
            self.montant_tva = None

        # ==========================================
        # VÉRIFICATION FISCALE SELON RÉGIME
        # ==========================================
        if self.type_tva == TypeTVA.NORMALE:
            if self.montant_tva is not None:
                attendu_ttc = round(self.montant_ht + self.montant_tva, 2)
                if abs(attendu_ttc - self.montant_ttc) > 0.02:
                    self.statut_traitement = "A_VERIFIER"
                    self.commentaires_controle.append(
                        f"Anomalie fiscale : HT ({self.montant_ht}) + TVA ({self.montant_tva}) != TTC ({self.montant_ttc})"
                    )
            else:
                if self.type_piece == TypePiece.FACTURE:
                    self.commentaires_controle.append("TVA absente ou invalidée : contrôle fiscal automatique impossible.")
                    self.statut_traitement = "A_VERIFIER"
        else:
            if abs(self.montant_ht - self.montant_ttc) > 0.02:
                self.statut_traitement = "A_VERIFIER"
                self.commentaires_controle.append(
                    f"Anomalie régime {self.type_tva.value} : HT ({self.montant_ht}) != TTC ({self.montant_ttc})"
                )
            
        # ==========================================
        # COHÉRENCE BANCAIRE
        # ==========================================
        remise = self.montant_remise or 0.0
        if self.montant_paye is None:
            self.montant_paye = round(self.montant_ttc - remise, 2)
            
        attendu_paye = round(self.montant_ttc - remise, 2)
        if abs(attendu_paye - self.montant_paye) > 0.02:
            self.statut_traitement = "A_VERIFIER"
            self.commentaires_controle.append(
                f"Anomalie bancaire : TTC ({self.montant_ttc}) - Remise ({remise}) != Payé ({self.montant_paye})"
            )
            
        # ==========================================
        # CALCUL TRÉSORERIE NETTE
        # ==========================================
        if self.montant_ttc != 0:
            ratio = self.montant_paye / self.montant_ttc
            self.montant_ht_net = round(self.montant_ht * ratio, 2)
            
            tva_valide = self.montant_tva if self.montant_tva is not None else 0.0
            self.montant_tva_net = round(tva_valide * ratio, 2)
            self.montant_ttc_net = self.montant_paye
            
            attendu_ttc_net = round(self.montant_ht_net + self.montant_tva_net, 2)
            if abs(attendu_ttc_net - self.montant_ttc_net) > 0.02:
                self.montant_ht_net = round(self.montant_ttc_net - self.montant_tva_net, 2)
        else:
            self.montant_ht_net = 0.0
            self.montant_tva_net = 0.0
            self.montant_ttc_net = 0.0

        return self
```

### Comparaison des champs — sans présumer de compatibilité

Le schéma ci-dessus définit `InvoiceData` avec :

- obligatoires : `fournisseur: str`, `date_facture: str`,
  `montant_ht: float`, `montant_ttc: float`;
- optionnels ou valeurs par défaut : `type_piece`, `type_tva`,
  `origine_document`, `numero_facture`, `taux_tva_pourcentage`,
  `taux_tva_theorique`, `montant_tva`, `montant_remise`, `montant_paye`,
  `montant_ht_net`, `montant_tva_net`, `montant_ttc_net`,
  `statut_traitement`, `commentaires_controle`, `devise`.

Différences directement visibles avec le JSON demandé par le prompt actuel :

- `date_emission` n'est pas `date_facture`;
- `fournisseur` est un objet dans le prompt, mais une chaîne dans `InvoiceData`;
- `client` et `lignes` sont demandés par le prompt, mais absents de `InvoiceData`;
- `total_ht` et `total_ttc` sont demandés, mais le modèle déclare
  `montant_ht` et `montant_ttc`;
- les `taux_tva` d'une ligne et `prix_unitaire_ht` ne sont pas des champs du
  modèle `InvoiceData`.

Ces écarts ne démontrent pas une compatibilité. Les conversions, la perte
éventuelle de champs et le choix d'un contrat ne sont donc pas définis ici.

### Code proposé pour le groupe B

**Aucun changement proposé.** Ne pas substituer `InvoiceData` à `InvoiceSchema`,
ne pas modifier le prompt et ne pas modifier la validation avant qu'un contrat
compatible soit démontré et explicitement validé.

### Impact et risque

En laissant ce groupe en attente, l'import `InvoiceSchema` reste non résolu et
le chemin d'extraction concerné n'est pas corrigé par ce plan. C'est préférable
à une conversion ou perte silencieuse de champs dont la compatibilité n'est pas
établie.

## Ordre après validation

1. Si le groupe A est explicitement validé, appliquer uniquement A1 et A2.
2. Laisser `router.py` inchangé.
3. Ne pas modifier `services/extraction_manager.py` dans le cadre du groupe A.
4. Traiter le groupe B séparément, uniquement après décision explicite sur le
   contrat; ce plan ne fournit pas de transformation pour lui.

La validation du groupe A n'autorise pas de modification concernant
`InvoiceSchema`, `InvoiceData` ou `services/extraction_manager.py`.
