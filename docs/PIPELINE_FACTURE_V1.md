# Pipeline Facture V1

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
- **Modèle :** Document Vision Model (Actuel: qwen2.5-vl:7b | Futur: qwen-vl-current)
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


