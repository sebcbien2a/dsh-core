---
master_doc: PROJECT_HANDOVER.md
branch_domain: Arête 2 — Graphe Comptable & Règles Métiers
contract_version: 1.0.1
status: ACTIVE
upstream_dependencies: [schemas/invoice_schema.py, schemas/rule_registry_schema.py, knowledge/rules/accounting/, services/invoice_producer_protocol.py, services/document_pipeline.py]
downstream_consumers: [backend/app/n8n_api.py, scripts/run_batch_audit.py, services/persistence_manager.py]
verification_tests: [tests/test_invoice_graph.py, tests/test_invoice_pipeline_chain.py, tests/test_invoice_graph_perception.py, tests/test_invoice_producers.py, tests/test_invoice_schema.py, tests/test_rule_registry_service.py, tests/test_run_pipeline_pilot.py, tests/test_run_batch_audit.py]
---

# Pipeline Facture V1

## État actuel (gelé v1.0.1 — Phases P0-P8.1)

Chaîne réellement exécutée par `backend/app/graphs/invoice_graph.py` :

```
triage (suffixe + DocumentRouter) → production (PdfInvoiceProducer, natif regex OU vision)
→ supplier_enrichment (mémoire fournisseur certifiée) → validation (InvoiceSchema)
→ rule_evaluation (RuleRegistryService + RULE-TVA-ZERO-001) → commit | hitl_inbox
```

- **Producteurs** : `services/invoice_producer_protocol.py` (PDF natif/scans), `spreadsheet_invoice_producer.py`, `banking_invoice_producer.py` (Arête 5).
- **Règles** : Obsidian `knowledge/rules/accounting/` chargées par `RuleRegistryService` (statut `approved` uniquement), évaluées par `tva_zero_rule_evaluator` (`RULE_NOT_TRIGGERED`, `ZERO_VAT_DISAMBIGUATED`, `SUPPLIER_REGIME_APPLIED`, `AMBIGUOUS_ZERO_VAT`, `TAX_AMOUNT_UNKNOWN`, …).
- **Perception amont** : L0-L5 gelés dans `docs/DOCUMENT_PERCEPTION_CONTRACT.md` (Arête 1).
- **Validation** : `tests/test_invoice_graph*.py`, `tests/test_invoice_pipeline_chain.py`, `tests/test_run_pipeline_pilot.py`.

> La suite historique « Pipeline Facture V1 » ci-dessous est **archivée** (nomenclature initiale
> OCR/agents) ; elle ne fait pas foi pour l'implémentation actuelle.

## Objectif
Transformer (PDF, Photo, Scan) en JSON validé, puis indexation (Qdrant + LlamaIndex + n8n).

---
## Nœud 1 : OCR
- **Agent :** InvoiceAgent
- **Moteur :** Surya OCR
- **Entrée :** PDF, PNG, JPG
- **Sortie :** `{"raw_text": "...", "page_count": 1}`
- **Responsabilité :** Extraire le texte brut sans aucune interprétation ni correction.

---
## Nœud 2 : Vision Document
- **Mod?le :** PaddleOCR (OCR brut en amont) puis NuExtract3 pour l'extraction structur?e.
- **Responsabilité :** Identifier géométriquement le fournisseur, le numéro de facture, les tableaux, et les blocs TVA/TTC.
- **Sortie :** `{"supplier_zone": "...", "totals_zone": "...", "invoice_zone": "..."}`

---
## Nœud 3 : Structuration
- **Modèle :** qwen3.5:9b
- **Entrées :** OCR + Vision
- **Mission :** Transformer le contenu brut et la géométrie en un JSON structuré (InvoiceData).

---
## Nœud 4 : Validation
- **Moteur :** Pydantic (InvoiceData)
- **Règles :** HT + TVA = TTC, Format date valide, Montants positifs.
- **Sortie :** InvoiceData validé OU Erreur de validation (qui déclenche le nœud 5).

---
## Nœud 5 : OCR Repair
- **Déclenchement :** Automatique si la validation (Nœud 4) échoue.
- **Modèle :** qwen3.5:9b
- **Mission :** Identifier l'erreur d'extraction (ex: 8 lu comme 0), corriger les montants, et renvoyer le JSON pour une nouvelle validation.

---
## Nœud 6 & 7 : Archivage et Automatisation
- **Actions :** Sauvegarde vectorielle (Qdrant), Indexation (LlamaIndex), Webhook (n8n).
- **Métadonnées :** fournisseur, date, montant, nom du fichier source.
---
## Nœud 8 : Boucle d'Auto-Apprentissage (Teacher-Student)
- **Déclenchement :** Si le Nœud 5 (OCR Repair) échoue de manière répétée.
- **Flux d'escalade :**
  1. Escalade vers **Qwen 27B** (Expert local).
  2. Si échec, escalade vers **Claude 3.5 Sonnet** (Superviseur Cloud).
  3. Claude génère un fichier de compétence (`SKILL_[FOURNISSEUR].md`).
  4. Le Skill est injecté dans la collection Qdrant `skills_catalog`.
- **Résultat :** À la prochaine facture de ce fournisseur, Qwen 9B lit le Skill et devient autonome.

## Graphe d'Escalade (Workflow)
```text
                Facture
                   |
                   V
             [ Qwen 9B ]
                   |
         +---------+---------+
         |                   |
      Succès               Échec
         |                   |
     Archivage          [ Qwen 27B ]
                             |
                 +-----------+----------+
                 |                      |
              Succès                  Échec
                 |                      |
            Archivage          [ Claude Sonnet ]
                                        |
                             Génération de Skill
                                        |
                          Injection skills_catalog (Qdrant)
                                        |
                             Réutilisation future par Qwen 9B


