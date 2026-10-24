# État du projet DSH-Core

## Référence Git
- Dernier commit vérifié le 2026-10-24 : `4eac425` — `docs: consigner le benchmark orientation lightonocr`.
- Le worktree était déjà modifié à cette vérification ; préserver les changements préexistants. Aucun commit n'a été créé durant la préparation de cet état.

## Benchmark orientation
- Manifeste : `tests/fixtures/orientation_corpus_manifest.json` (10 documents).
- PP-LCNet_x1_0_doc_ori : rapport hors dépôt `A:\dsh-paddle-orientation\orientation_benchmark_pplcnet.json`, 8/10 (80 %), 4 classes (0/90/180/270).
- Erreurs mesurées selon le manifeste : `facture_emaux.pdf` attendu 90°, prédit 0° ; `facture_transport.PDF` attendu 0°, prédit 90°.
- Audit visuel des deux premières pages : les rendus semblent indiquer l'inverse des attentes du manifeste. Leur vérité terrain n'est pas confirmée ; le 100 % après correction est seulement conditionnel. Ne pas modifier le manifeste sans validation humaine.
- LightOnOCR-1B-1025 : rapport hors dépôt `A:\dsh-lightonocr-bench\orientation_benchmark_lightonocr.json`, 2/10 (20 %), PDF 1/9 (11,1 %), image 1/1 (100 %), 8 erreurs, aucune erreur d'exécution.
- LightOnOCR : moyenne 120,81 s/document et 30,08 s/rotation. Le classement favorisant la quantité de texte est jugé insuffisant pour l'intégration.
- Un essai ultérieur avec heuristique de lisibilité, non enregistré dans le rapport, a donné 5/10 ; combinaison avec le score antérieur : 2/10. Ratio de mots valides exclu faute de dictionnaire local. Ne pas traiter ce résultat comme référence validée.

## Modèles et environnements isolés
- Paddle : `A:\dsh-paddle-orientation`, Python 3.11.9, PaddlePaddle GPU 3.0.0, PaddleOCR 3.0.3, PaddleX 3.0.3, CUDA 12.6, RTX 4070 Laptop.
- Le test synthétique PP-LCNet a chargé le modèle et prédit sur GPU ; le benchmark du corpus a utilisé le manifeste existant. L'environnement ne fait pas partie de DSH-Core.
- LightOnOCR : `A:\dsh-lightonocr-bench`, Python 3.11.9, Torch 2.14.1+cu126, cache local du modèle (~2,33 Go). Smoke-test synthétique réussi ; aucune transcription réelle n'est conservée dans le rapport.
- Les rapports, jalons et notes d'orientation Paddle/LightOnOCR sont stockés dans ces dossiers hors dépôt.

## Réussites et échecs
- Réussi : classifieur PP-LCNet chargé et testé sur image synthétique ; 80 % mesuré sur les annotations présentes ; smoke-test LightOnOCR synthétique exact (« HELLO OCR TEST 123 »).
- Insuffisant : LightOnOCR pour sélectionner l'orientation sur ce corpus (20 % avec le score de quantité de texte).
- Non établi : exactitude réelle PP-LCNet avant arbitrage des deux annotations ; compatibilité production Oracle Linux ; métriques de lisibilité généralisables ; disponibilité/licence/runtime SeeingStraight.

## Travaux en attente
- Confirmer humainement la convention et la vérité terrain des deux documents en désaccord.
- Valider les annotations, puis recalculer l'accuracy PP-LCNet uniquement après décision explicite.
- Valider PP-LCNet, pilote CUDA et mesure mémoire sur la version cible d'Oracle Linux avant production.
- SeeingStraight reste un candidat de recherche : poids/checkpoint et licence d'usage opérationnels non vérifiés.
- Benchmark ExtractionManager vs NuExtract3 préparé théoriquement, pas encore réalisé ; utiliser les mêmes textes OCR et vérité terrain des six champs, sans règles de réconciliation dans la comparaison brute.

## Prochaine étape technique unique
Obtenir une validation humaine documentée de l'orientation attendue des deux factures contestées avant toute mise à jour des résultats de référence.
