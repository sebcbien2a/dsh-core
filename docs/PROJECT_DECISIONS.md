---
master_doc: PROJECT_HANDOVER.md
branch_domain: Annexe transverse — Registre des décisions
status: ACTIVE
upstream_dependencies: [docs/PROJECT_HANDOVER.md]
downstream_consumers: []
verification_tests: []
contract_version: 1.0.1
---

# Décisions et règles du projet DSH-Core

## Décisions validées
- Garder les expérimentations de modèles dans des environnements séparés ; ne pas modifier DSH-Core pour les benchmarks sans demande et validation dédiées.
- L'orientation précède l'OCR ; ne pas réécrire les documents sources. Toute rotation expérimentale se fait sur l'image rendue/en mémoire.
- PP-LCNet_x1_0_doc_ori est la référence locale provisoire d'orientation : léger, quatre classes, déjà testé localement. Le manifeste reste à valider.
- LightOnOCR fonctionne comme OCR sur entrée synthétique, mais son score d'orientation fondé sur la quantité de texte est rejeté pour une intégration sur la base de 20 % mesuré.
- NuExtract3 est un extracteur structuré spécialisé après OCR, pas un moteur OCR. Ne pas présumer son équivalence avec qwen3.5:2b.
- ExtractionManager est mieux intégré au schéma canonique. Les résultats d'extraction seront évalués pendant des tâches réelles ; aucun benchmark artificiel comparatif ne sera lancé.

## Décisions rejetées ou non validées
- Ne pas intégrer LightOnOCR comme classifieur d'orientation sur le seul score de quantité de texte.
- Ne pas considérer les deux labels du manifeste en conflit comme vrais ou faux sans validation humaine.
- Ne pas adopter SeeingStraight en production avant d'avoir établi checkpoint disponible, licence des poids/code, runtime, compatibilité et performances sur corpus cible.
- Ne pas considérer l'accuracy potentielle PP-LCNet de 100 % comme résultat acquis ; elle dépend d'une correction confirmée du manifeste.

## Architecture IA locale
- Pipeline documentaire cible : rendu du document → orientation locale PP-LCNet provisoire → OCR → extraction structurée → validation canonique et contrôles métier.
- Extraction : ExtractionManager/Ollama est le chemin intégré ; NuExtract3 reste un candidat d'extraction spécialisée après OCR. Évaluer les résultats pendant des tâches réelles.
- Évaluer les étapes de réconciliation, règles TVA et skills pendant les tâches réelles, séparément de la qualité brute d'extraction ; ne pas créer de benchmark artificiel.
- SeeingStraight n'est qu'un candidat de second niveau théorique ; aucun fallback automatique ni seuil de confiance n'est validé.

## Architecture IA cloud
- La matrice validée, les rôles, niveaux de difficulté, routage, fallbacks et règles du quota OpenRouter sont définis uniquement dans `docs/AI_MODEL_ROUTING_AND_HARNESSES.md`.
- Les benchmarks artificiels sont arrêtés. Les modèles et harnesses sont évalués pendant les tâches réelles dans leur harness cible ; les affectations sont ajustées progressivement selon les résultats observés.
- Aucun fallback payant silencieux n'est autorisé. Les coûts par tâche sont des estimations variables selon les tokens, les outils, le raisonnement et la route fournisseur, jamais des coûts garantis.
- Le résultat MiMo Vision de 50 % sur 10 documents et son coût rapporté de 0,0046284 USD sont une observation historique, pas une affectation actuelle ni une garantie de coût.
- Ne jamais exposer ni recopier les valeurs du vrai `.env`. Utiliser uniquement les noms de variables et vérifier une présence sans imprimer leur valeur.

## Règles Copilot VS Code et continuité
- Préférer VS Code Auto + Efficiency ; effort Rapide par défaut, augmenter seulement si la tâche le justifie.
- Économiser les crédits ; recherches et lectures ciblées. Ne jamais lire `docs/02_ROADMAP.md` en entier ; limiter les lectures à 150 lignes par fichier.
- Ne pas lancer modèle, OCR, benchmark, appel cloud, installation, téléchargement ou commit sans demande explicite.
- Préserver les modifications préexistantes du worktree ; ne pas réinitialiser ni écraser les changements hors portée.
- « 1 échange = 1 action » : terminer une réponse par une prochaine action concrète unique et s'arrêter au périmètre demandé.
- Signaler explicitement avant chaque changement de chat ; privilégier la reprise dans le même fil tant que possible.
- Règle Gemini = auditeur mémoire : utiliser Gemini comme auditeur/contrôleur de cohérence mémoire lorsque cela est demandé, pas comme exécuteur implicite.
- Documentation obligatoire avant clôture : consigner les décisions et résultats durables dans la documentation demandée avant de déclarer le chantier terminé ; ne pas committer sans autorisation.

## Échafaudage IA — 2026-10-28

- La matrice initiale des modèles et harnesses est validée ; OpenCode et Hermes sont configurés.
- DeepSeek Harness est installé, mais sa finalisation technique et son rôle spécialisé restent à valider.
- Les benchmarks artificiels sont arrêtés ; les modèles et harnesses seront audités pendant les tâches réelles avec le harness cible.

### Reprise de conversation

Pour toute nouvelle conversation :

1. Lire uniquement PROJECT_STATE.md et PROJECT_DECISIONS.md.
2. Ne jamais relire l’ensemble du dépôt sauf demande explicite.
3. Limiter toute lecture ultérieure à 150 lignes maximum par fichier.
4. Toujours proposer exactement UNE action VS Code prête à copier-coller.
5. Gemini est un auditeur mémoire/documentation, pas l’exécuteur principal.
6. Avant toute expérimentation, vérifier si une décision ou un benchmark existe déjà dans PROJECT_STATE.md.
