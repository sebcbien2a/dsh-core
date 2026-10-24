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
- **DSH-Core :** dépôt applicatif et pipeline cible. Les documents consultés ne consignent ni environnement d'exécution, ni versions de runtime/dépendances ; ne pas les supposer.
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
- Prochain blocage pour exécuter une comparaison réelle : préparer ultérieurement un dataset versionné de textes OCR figés avec vérité terrain vérifiée pour fournisseur, date, HT, TVA, TTC et SIRET. Ne pas créer ce dataset dans le lot actuel.
- Le runner reste un squelette de benchmark : aucun résultat comparatif réel n'est disponible.
