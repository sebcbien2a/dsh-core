---
rule_id: "RULE-TVA-ZERO-001"
version: "1.0.0"
status: "approved"
domain: "accounting"
applies_to_nodes:
  - "ClassifyTax"
  - "ApplyTaxRules"
  - "ValidateInvoice"
priority: 10
conditions:
  - field: "amount.tax_amount"
    operator: "eq"
    value: 0
deterministic_actions:
  - action: "disambiguate_zero_vat"
    parameters:
      case_intracom_ue:
        trigger: "supplier.vat_number starts_with EU prefix != FR OR supplier.country != FR"
        vat_regime: "INTRACOM_AUTOLIQUIDATION"
        include_in_vat_balance: true
        accounting_treatment: "Autoliquidation TVA (collectée + déductible sur CA3)"
      case_franchise_base_fr:
        trigger: "supplier.country == FR AND (mention 293B OR micro-entreprise)"
        vat_regime: "EXONERE_FRANCHISE_293B"
        include_in_vat_balance: false
        accounting_treatment: "TVA à 0 sans autoliquidation (hors balance TVA)"
      on_unknown_supplier_regime: "ROUTE_TO_HITL_AND_PERSIST_SUPPLIER_REGIME"
---
# RULE-TVA-ZERO-001 — Désambiguïsation d'une facture sans TVA

## Objet

Une facture dont `amount.tax_amount` vaut `0` ne prouve pas à elle seule le
régime de TVA applicable. Deux origines fiscales distinctes produisent une
absence de TVA, avec des conséquences comptables opposées. Cette règle impose
de qualifier l'origine avant tout enregistrement.

## Conditions

- `amount.tax_amount` est égal à `0`.

## Désambiguïsation déterministe

### Cas 1 — Achat intracommunautaire autoliquidé (UE hors France)

- Déclencheur : numéro de TVA fournisseur préfixé par un pays de l'UE autre que
  `FR`, **ou** `supplier.country` différent de `FR`.
- Régime retenu : `INTRACOM_AUTOLIQUIDATION`.
- La TVA est due par le client : elle est **collectée puis déductible** sur la
  CA3 au taux théorique de la prestation. Le net à payer reste HT, mais
  l'opération **entre dans la balance TVA** (`include_in_vat_balance: true`) car
  elle impacte la déclaration.
- Écriture type : autoliquidation de la TVA (compte de TVA collectée et compte
  de TVA déductible mouvementés pour le même montant).

### Cas 2 — Franchise en base de TVA (art. 293 B du CGI)

- Déclencheur : `supplier.country` égal à `FR` **et** mention explicite
  « TVA non applicable, art. 293 B » ou statut micro-entreprise.
- Régime retenu : `EXONERE_FRANCHISE_293B`.
- Aucune TVA n'est due ni autoliquidée : le montant HT est le montant final.
  L'opération **reste hors balance TVA** (`include_in_vat_balance: false`) ;
  elle n'alimente ni TVA collectée ni TVA déductible sur la CA3.

La distinction est donc comptable, pas seulement documentaire : dans le premier
cas la TVA transite par la déclaration, dans le second elle n'existe pas.

## Cas inconnu — HITL et persistance

Si l'origine de l'absence de TVA ne peut pas être établie avec certitude
(`on_unknown_supplier_regime`), la facture est routée vers la validation
humaine (HITL). La décision de l'opérateur ne corrige pas seulement la facture
courante : le régime TVA retenu pour le fournisseur est **persisté dans
`SupplierIdentity`** (champs/custom fields du fournisseur) afin que les
factures suivantes soient qualifiées automatiquement et de façon cohérente.
Toute persistance est tracée comme une confirmation humaine.

## Gouvernance

- Statut : `approved`.
- Toute modification du régime, du déclencheur ou du traitement comptable doit
  passer par une revue humaine et une incrémentation de `version`.
- Le taux théorique d'autoliquidation et les mentions d'exonération doivent
  être confirmés par un expert-comptable avant usage en production.
