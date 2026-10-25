Lire uniquement ce document. Ne lire aucun autre fichier tant qu'une information précise n'est pas manquante.

# DSH-Core — document unique de reprise et état exécutif

## 1. Objectif final DSH-Core
Construire un pipeline local de traitement documentaire qui oriente les pages, effectue l'OCR, extrait des données structurées, puis applique validation canonique et contrôles métier.

## 2. Architecture cible complète
- Rendu du document → orientation de page → OCR → extraction structurée → normalisation/validation canonique → contrôles métier.
- PP-LCNet_x1_0_doc_ori est le candidat local provisoire d'orientation ; l'OCR suit l'orientation.
- ExtractionManager/Ollama est le chemin d'extraction intégré à évaluer ; NuExtract3 est un candidat d'extraction structuré après OCR, pas un moteur OCR.
- Comparer les extracteurs sur les mêmes textes OCR et la vérité terrain des champs fournisseur, date, HT, TVA, TTC et SIRET.
- Exclure réconciliation, règles TVA et skills du benchmark d'exactitude brute ; les évaluer séparément.
- Les modèles cloud sont des secours explicites pour les cas complexes, jamais le chemin par défaut des tâches locales simples.

## 3. État exact des environnements
- **DSH-Core :** dépôt applicatif et pipeline cible. Utiliser explicitement `A:\dsh-core\.venv-v3\Scripts\python.exe` pour toute commande Python DSH-Core.
- **DeepSeek Harness :** DeepSeek Harness est validé et conservé comme composant stratégique du projet. Les documents consultés ne précisent ni chemin, ni versions, ni périmètre/critères de validation ; ne pas installer ou revalider sans besoin précis.
- **Paddle Orientation :** environnement isolé `A:\dsh-paddle-orientation` ; Python 3.11.9, PaddlePaddle GPU 3.0.0, PaddleOCR 3.0.3, PaddleX 3.0.3, CUDA 12.6, RTX 4070 Laptop. Hors dépôt DSH-Core.
- **LightOnOCR Bench :** environnement isolé `A:\dsh-lightonocr-bench` ; Python 3.11.9, Torch 2.14.1+cu126, cache local du modèle d'environ 2,33 Go. Hors dépôt DSH-Core.
- Les rapports et notes de benchmark Paddle/LightOnOCR se trouvent dans leurs environnements séparés.

## 4. Résultats du benchmark d'orientation
- Corpus/manifeste : 10 documents.
- Les résultats ci-dessous mesurent l'accord avec les annotations présentes ; deux annotations sont contestées.
- Deux désaccords relevés : `facture_emaux.pdf` (attendu 90°, prédit 0°) et `facture_transport.PDF` (attendu 0°, prédit 90°).
- Un audit visuel suggère que les attentes pourraient être inversées, mais la vérité terrain n'est pas confirmée.

## 5. Résultats PP-LCNet
- PP-LCNet_x1_0_doc_ori : 8/10 (80 %) sur les annotations existantes, quatre classes (0/90/180/270).
- Rapport : `A:\dsh-paddle-orientation\orientation_benchmark_pplcnet.json`.
- Le test synthétique a chargé le modèle et effectué une prédiction GPU.
- L'accuracy réelle n'est pas établie avant arbitrage humain ; une accuracy de 100 % après correction est uniquement conditionnelle.

## 6. Résultats LightOnOCR
- LightOnOCR-1B-1025 orientation : 2/10 (20 %), PDF 1/9 (11,1 %), image 1/1 (100 %), 8 erreurs, aucune erreur d'exécution.
- Rapport : `A:\dsh-lightonocr-bench\orientation_benchmark_lightonocr.json`.
- Temps moyen : 120,81 s/document et 30,08 s/rotation. Le score privilégiant la quantité de texte est insuffisant pour l'orientation.
- Smoke-test OCR synthétique réussi (« HELLO OCR TEST 123 ») ; aucune transcription réelle n'est conservée dans le rapport.
- Heuristique de lisibilité ultérieure : 5/10 non consigné au rapport ; combinaison rapportée : 2/10. Résultats non référentiels.

## 7. Choix IA locale
- Garder les expérimentations dans des environnements isolés.
- PP-LCNet reste provisoire jusqu'à la validation humaine des labels ; ne pas modifier les documents sources pour les rotations.
- ExtractionManager/Ollama est la voie intégrée à évaluer ; NuExtract3 peut être comparé après OCR.
- SeeingStraight reste une piste non adoptée : checkpoint, licence, runtime, compatibilité et performances non vérifiés ; aucun fallback ni seuil validé.

## 8. Choix IA cloud
- MiMo Vision a été techniquement testé avec `reasoning.enabled=false` : 50 % sur 10 documents, coût rapporté 0,0046284 USD ; insuffisant pour la production.
- DeepSeek Vision est un candidat de secours, mais disponibilité/configuration et politique d'usage restent à confirmer.
- Gemini est un secours envisagé pour PDF/catalogues complexes, images multiples ou échec DeepSeek ; ne pas l'appeler par défaut.
- Nemotron n'est pas adopté ; rôle et validation ne sont pas établis.
- Ne jamais afficher ni recopier les valeurs du vrai `.env`.
- Toute commande Python DSH-Core doit utiliser explicitement `A:\dsh-core\.venv-v3\Scripts\python.exe`.

## 9. Ce qui est définitivement abandonné
- LightOnOCR comme classifieur d'orientation selon le score de quantité de texte mesuré à 20 % : rejeté pour intégration.
- Ce rejet ne signifie pas que LightOnOCR est abandonné comme OCR ; le smoke-test synthétique a réussi.

## 10. Ce qui est validé
- Tests synthétiques PP-LCNet et LightOnOCR réussis dans leurs environnements isolés.
- PP-LCNet : mesure reproductible rapportée de 8/10 selon les annotations actuelles, mais vérité terrain partiellement contestée.
- LightOnOCR orientation : résultat 2/10 et insuffisant pour l'intégration.
- MiMo Vision : classification techniquement exécutée, mais résultat insuffisant pour adoption de production.
- DeepSeek Harness : statut VALIDÉ déclaré ; détails probants non consignés dans les documents disponibles.

## 11. Les cinq prochaines étapes prioritaires
1. Obtenir une validation humaine documentée de l'orientation de `facture_emaux.pdf` et `facture_transport.PDF`.
2. Après décision seulement, corriger les annotations concernées si nécessaire.
3. Recalculer l'accuracy PP-LCNet après validation des annotations et décision explicite.
4. Avant toute mise en production, valider PP-LCNet, pilote CUDA et mémoire sur la version cible d'Oracle Linux.
5. Comparer ExtractionManager et NuExtract3 avec les mêmes textes OCR et vérités terrain des six champs ; exclure les règles de réconciliation de la comparaison brute.

## 12. Reprise d'une nouvelle conversation
- PROJECT_HANDOVER.md devient la source de vérité principale pour toute nouvelle conversation.
- Lire d'abord uniquement ce document. Ne consulter un autre fichier que si une information précise nécessaire manque ; limiter chaque lecture ultérieure à 150 lignes par fichier.
- Ne pas relire le dépôt entier, le code ou la roadmap sauf demande explicite.
- Avant toute expérimentation, tenir compte des résultats et décisions déjà consignés ici ; ne pas relancer modèles/benchmarks, installer ou télécharger sans besoin et autorisation explicites.
- Priorité de reprise : organiser la validation humaine des deux orientations ; ne pas commencer par une installation ou un nouveau benchmark.
- Gemini est un auditeur mémoire/documentation, pas l'exécuteur principal.
- Préserver les changements préexistants ; ne pas modifier du code ni committer sans demande explicite.
- Préférer VS Code Auto + Efficiency et l'effort Rapide. Proposer exactement UNE action VS Code prête à copier-coller, puis s'arrêter au périmètre demandé.

## 13. État du benchmark ExtractionManager vs NuExtract3
- Le plan technique est consigné dans `benchmarks/extractor_comparison_plan.md` ; le squelette sans modèle est dans `benchmarks/extractor_benchmark_runner.py`.
- Les montants français (dont espaces normaux et insécables) sont normalisés en Decimal ; les dates `YYYY-MM-DD` et `DD/MM/YYYY` sont prises en charge. Les dates impossibles et les formats ambigus sont rejetés.
- NuExtract3 est adapté vers les structures canoniques `supplier`, `reference` et `amount`. Le vrai `InvoiceSchema` confirme le chemin du SIRET `supplier.siret` ; cette validation est testée avec des données synthétiques.
- Le runner calcule les métriques par champ et l'exactitude complète sur les six champs. Sa méthode `run` est testée avec deux extracteurs factices, sans client de modèle.
- 42 tests ciblés ont réussi. Aucun LLM, Ollama, OCR ni benchmark réel n'a été lancé.
- Le dataset OCR réel local est maintenant gelé dans `benchmarks/data/ocr_baseline_texts.json`, strictement ignoré par Git : 10 cas non vides, 5 acquisitions `pymupdf_native_text` et 5 `glm_ocr_q8_0`. Les hashes source sont conformes et les sources inchangées ; aucune transcription n'a été affichée.
- Les 60 champs de vérité terrain sont encore nuls ; aucun manuscrit n'est validé. GLM-OCR est choisi comme source OCR neutre des scans/images ; NuExtract3 reste l'extracteur structuré principal.
- Avant tout benchmark ExtractionManager vs NuExtract3, faire valider humainement les 60 champs. Exclure les cas manuscrits tant qu'un protocole spécifique n'est pas validé.
- Le runner reste un squelette de benchmark : aucun résultat comparatif réel n'est disponible.

## 14. Sélection du modèle Vision
- `qwen2.5vl:7b` est retiré ; `qwen3.5:2b` est retiré de Vision et de tous les rôles locaux actifs, y compris FAST et BASE. Ces identifiants restent uniquement dans les tests explicites de rejet.
- Aucun remplaçant local n'est choisi. `LOCAL_FAST_MODEL` et `LOCAL_VISION_MODEL` sont volontairement vides dans `.env.example` ; aucun fallback implicite n'est autorisé. Le nouveau choix local est reporté à un benchmark séparé.
- `A:\dsh-core` reste la cible canonique ; la copie `A:\dsh-hermes-context` est inchangée.
- `RoutingEngine` résout la configuration via `model_router` lors du routage Vision ; les PDF natifs n'exigent pas de configuration Vision. Le profil manuscrit reste `HANDWRITING_PATH_MISSING`.

## État du dataset OCR figé
- Le dataset réel est local et non versionné : `benchmarks/data/ocr_baseline_texts.json`. La règle `.gitignore` exclut uniquement ce fichier, pas l'exemple synthétique.
- Il contient 10 textes OCR non vides : 5 extractions natives PyMuPDF et 5 acquisitions GLM-OCR `q8_0` pour scans/images.
- Les 60 champs de vérité terrain restent nuls et à valider humainement ; aucune transcription OCR n'a été affichée. Les hashes du manifeste concordent et les sources sont inchangées.
- GLM-OCR est la source OCR neutre retenue pour scans/images ; NuExtract3 reste l'extracteur structuré principal. Le manuscrit n'est pas validé.
- Prochaine étape : validation humaine des 60 champs. Ne pas exécuter le benchmark ExtractionManager vs NuExtract3 avant cette validation ; exclure les cas manuscrits jusqu'à validation d'un protocole spécifique.
- Toute commande Python DSH-Core doit utiliser explicitement `A:\dsh-core\.venv-v3\Scripts\python.exe`. L'installation globale supplémentaire de PyMuPDF est connue ; aucun nettoyage urgent n'est nécessaire.
- Interface locale de validation créée : `benchmarks/validate_ocr_ground_truth.py` (Tkinter, bibliothèque standard, hors ligne). Le dataset réel reste hors Git ; aucun champ n'est réputé validé avant utilisation humaine.
- Commande : `A:\dsh-core\.venv-v3\Scripts\python.exe -m benchmarks.validate_ocr_ground_truth benchmarks\data\ocr_baseline_texts.json --manifest tests/fixtures/orientation_corpus_manifest.json --repo-root A:\dsh-core`.
- Première saisie fermée sans sauvegarde : 0/10 cas et 0/60 champs.
- Aperçu source (résolu par SHA-256 via le manifeste, rendu en mémoire) désormais obligatoire pour les tableaux ; confirmation bloquée si la source n'est pas vérifiée.
- SIRET absent = ABSENT ; le numéro de TVA intracommunautaire est distinct du SIRET et ne doit jamais y être copié.
- Cas 4 à 6 : ambigus dans le texte OCR linéaire (tableaux). Cas 7 à 10 : jugés visuellement corrects, non encore confirmés.
- Aucun texte OCR ne doit être transmis à Gemini, Copilot ou au cloud.