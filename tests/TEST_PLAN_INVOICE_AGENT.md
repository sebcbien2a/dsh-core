# Plan de Validation InvoiceAgent

## Test 1 : Facture PDF simple
- **Attendu :** OCR OK, Validation OK, JSON généré directement.

## Test 2 : Photo smartphone nette
- **Attendu :** OCR OK, Vision OK, JSON généré.

## Test 3 : Photo inclinée
- **Attendu :** Le modèle Vision récupère les zones déformées, JSON cohérent.

## Test 4 : TVA incorrecte (Déclenchement OCR Repair)
- **Entrée :** HT 100, TVA 20, TTC 130
- **Attendu :** La validation Pydantic échoue (120 != 130). Le nœud OCR Repair est déclenché et corrige.

## Test 5 : Facture sans TVA
- **Attendu :** Validation OK, TVA = 0.

## Test 6 : Facture étrangère (USD)
- **Attendu :** Devise (USD) détectée, Validation mathématique OK.

## Test 7 : PDF multi-pages (ex: 10 pages)
- **Attendu :** Temps de traitement < 60 secondes, pas de crash de l'API.

## Test 8 : Indexation Qdrant
- **Attendu :** Le document validé est bien présent dans la collection `invoice_documents` de Qdrant.
## Test 8 : Facture d'un fournisseur inconnu et complexe
- **Attendu :** Échec de Qwen 9B -> Escalade Qwen 27B -> Escalade Claude 3.5. Le système crée avec succès un nouveau Skill dans Qdrant.

## Test 9 : Réutilisation de la compétence (Apprentissage validé)
- **Action :** Soumettre une nouvelle facture du même fournisseur que le Test 8.
- **Attendu :** Qwen 9B interroge le `skills_catalog`, applique la règle, et traite la facture seul. Aucune escalade vers Claude n'est déclenchée.