# Décisions et règles du projet DSH-Core

## Décisions validées
- Garder les expérimentations de modèles dans des environnements séparés ; ne pas modifier DSH-Core pour les benchmarks sans demande et validation dédiées.
- L'orientation précède l'OCR ; ne pas réécrire les documents sources. Toute rotation expérimentale se fait sur l'image rendue/en mémoire.
- PP-LCNet_x1_0_doc_ori est la référence locale provisoire d'orientation : léger, quatre classes, déjà testé localement. Le manifeste reste à valider.
- LightOnOCR fonctionne comme OCR sur entrée synthétique, mais son score d'orientation fondé sur la quantité de texte est rejeté pour une intégration sur la base de 20 % mesuré.
- NuExtract3 est un extracteur structuré spécialisé après OCR, pas un moteur OCR. Ne pas présumer son équivalence avec qwen3.5:2b.
- ExtractionManager est mieux intégré au schéma canonique, mais une comparaison d'exactitude sur fournisseur, date, HT, TVA, TTC et SIRET reste à faire.

## Décisions rejetées ou non validées
- Ne pas intégrer LightOnOCR comme classifieur d'orientation sur le seul score de quantité de texte.
- Ne pas considérer les deux labels du manifeste en conflit comme vrais ou faux sans validation humaine.
- Ne pas adopter SeeingStraight en production avant d'avoir établi checkpoint disponible, licence des poids/code, runtime, compatibilité et performances sur corpus cible.
- Ne pas considérer l'accuracy potentielle PP-LCNet de 100 % comme résultat acquis ; elle dépend d'une correction confirmée du manifeste.

## Architecture IA locale
- Pipeline documentaire cible : rendu du document → orientation locale PP-LCNet provisoire → OCR → extraction structurée → validation canonique et contrôles métier.
- Extraction : ExtractionManager/Ollama est le chemin intégré à évaluer ; NuExtract3 peut être comparé comme extracteur spécialisé après OCR.
- Garder les étapes de réconciliation, règles TVA et skills hors du benchmark d'exactitude brute des extracteurs ; les évaluer séparément ensuite.
- SeeingStraight n'est qu'un candidat de second niveau théorique ; aucun fallback automatique ni seuil de confiance n'est validé.

## Architecture IA cloud
- Les modèles cloud sont des options de secours explicites, pas le chemin par défaut des tâches locales simples.
- MiMo Vision a été techniquement validé pour une réponse de classification quand `reasoning.enabled=false`. Sur 10 documents : 50 % d'accuracy, coût total rapporté 0,0046284 USD ; résultat insuffisant pour une adoption de production.
- DeepSeek Vision : candidat de secours précédemment envisagé ; disponibilité/configuration et politique de fallback doivent être confirmées avant tout usage.
- Gemini : secours multimodal envisagé pour PDF/catalogues complexes, images multiples et fallback après échec DeepSeek Vision ; ne pas l'appeler par défaut pour les tâches locales simples.
- Nemotron : candidat mentionné, mais choix de modèle, rôle, disponibilité et validation ne sont pas établis dans l'état courant. Ne pas le considérer comme adopté.
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
