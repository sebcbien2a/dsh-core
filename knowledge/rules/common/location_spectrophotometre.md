---
rule_id: R-LOC-SPECTRO-001
version: "1.0.0"
status: approved
domain: accounting
applies_to_nodes: [rule_evaluation]
priority: 5
conditions:
  - field: description
    operator: contains
    value: "spectrophotomètre"
  - field: description
    operator: contains
    value: "location"
deterministic_actions:
  - action: set_accounting_account
    parameters:
      account: "613500"
      label: "Locations mobilières - Spectrophotomètre"
      vat_deductible_category: "services"
---
# Règle comptable : Location de spectrophotomètre
Ventilation en compte de charges d'exploitation 613500 (Locations mobilières)
au lieu du compte d'achats de marchandises (607). 
TVA déductible sur prestations de services.
