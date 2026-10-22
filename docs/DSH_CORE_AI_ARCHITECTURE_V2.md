# DSH-Core — Architecture IA v2

Statut : ARCHITECTURE CIBLE VALIDÉE
État d'implémentation : PARTIEL
Source de vérité actuelle : PC principal et Git
Dernier socle validé : commit 716d23d
Documentation consolidée : commits 2dad618 et a915d15
Date du document : 2026-10-22 (horloge système)

> Ce document décrit une **cible**. Seuls le writer local atomique (332 tests),
> l'installation de Hermes 0.22.4 et ses approbations manuelles sont vérifiés.
> Tout le reste est prévu, à tester ou à déterminer. La source externe
> `architecture-ia-v2.md` n'est pas dans le dépôt ; ce document en retient les
> décisions validées par l'utilisateur, sans en reprendre les chiffres.

## 1. Principes

- Le PC principal et Git sont la source de vérité comptable et architecturale.
- Les tableaux maîtres restent la vérité métier ; Mem0 ne stocke que contexte et décisions.
- Git est la source canonique des Skills (`SKILL.md`).
- Les synchronisations entre machines sont sélectives, directionnelles et explicites ; jamais une copie générale des mémoires.
- Aucune écriture autonome dans `main`, aucun accès direct aux données comptables réelles.

## 2. Trois machines

| Machine | Rôle cible | Statut |
|---|---|---|
| PC principal | Vérité comptable, pipeline Vision local existant, LangGraph, Neo4j, Qdrant, Mem0, SkillOpt, Hermes, futur DeepSeek Harness | Partiel (Hermes installé, reste non connecté) |
| Oracle VM | Disponibilité 24/7, staging, recherche, traitements massifs, répliques contrôlées, services distants | Cible, rien de réel créé |
| Mini PC salon | Vocal (faster-whisper, Piper), médias, mpv, Qdrant/Memory médias, pilotage, scripts spécialisés | Cible, dimensionnement à vérifier |

Séparation des vérités : comptable (PC principal), médias (mini PC), services 24/7 (Oracle).

## 3. Couches

- **Hermes** : interface agentique et superviseur. Il propose, ne committe pas.
- **LangGraph** : orchestrateur métier (sous-agents et spécialistes métier : cible à construire).
- **Modèles** : locaux (Ollama) et distants ; disponibilité, identifiants, coûts et performances à vérifier.
- **Outils** : Skills, services et repositories DSH-Core, exposés par liste blanche en lecture seule d'abord.
- **Mémoire** : Qdrant, Neo4j et Mem0 (section 6).

## 4. Pipeline Vision

Le pipeline Vision/OCR/extraction local existant est conservé tel quel. Un modèle Vision cloud éventuel est une hypothèse à vérifier ; il ne remplace pas le pipeline local. Le seuil de confiance 0,85 et toute proportion d'escalade sont à vérifier.

## 5. DeepSeek Harness et Skills

- DeepSeek Harness (DSH) deviendra une **Skill Hermes de développement** ; DeepSeek sert au coding, à l'expertise et à l'escalade. Non installé.
- Chemins séparés : dépôt `A:\dsh-core` ; `DSH_HOME` dédié (`%USERPROFILE%\.dsh` à confirmer, jamais le dépôt) ; workspaces `A:\dsh-workspaces` (PROPOSÉ, NON CRÉÉ) ; sorties Hermes `A:\dsh-hermes-output` (PROPOSÉ, NON CRÉÉ) ; Hermes `%LOCALAPPDATA%\hermes`.
- Aucun credential ni session DSH dans le dépôt. Les développements utilisent un worktree ou workspace isolé.
- Skill Creator et SkillOpt : à tester. Les Skills validés sont versionnés dans Git puis synchronisés de façon contrôlée PC principal → Oracle.

## 6. Mémoire

- **Qdrant** : recherche vectorielle (documents, Skills, médias sur le mini PC). Instance locale à connecter.
- **Neo4j** : relations (fournisseurs, factures, paiements). À connecter.
- **Mem0** : contexte opérationnel et décisions, jamais vérité comptable. Package présent, non connecté à Hermes.

## 7. Synchronisation contrôlée

- Skills : Git → machines cibles, sur validation.
- Index et répliques Oracle : sélectifs, directionnels, après définition de la politique.
- Mini PC : reçoit uniquement ce qui concerne médias et vocal.

## 8. Commande vocale (cible, rien installé)

- Entrée faster-whisper, sortie Piper, activation vocale de Hermes, commandes en liste blanche ; mini PC salon en premier lieu. Une activation sur le PC principal reste à déterminer.
- Confirmation humaine obligatoire : suppression, déplacement massif, paiement, publication, commit, push, écriture comptable.
- Aucune écoute ni automatisation permanente avant politique de sécurité. Modèle vocal local définitif à vérifier.

## 9. État réel de Hermes

- Hermes Agent 0.22.4 installé ; approbations `manual`, `cron`, `single-query` et `unattended` en `deny`.
- Aucun MCP, aucun plugin ; `terminal.backend` encore `local`.
- Backend Docker et montages `:ro` identifiés ; **aucun accès DSH-Core accordé**. Profil Docker en lecture seule : cible à construire (P1-8E).

## 10. Phases de développement

1. Finaliser la liste blanche Docker Hermes.
2. Tester Hermes en lecture seule.
3. Intégrer Mem0 local.
4. Connecter Qdrant local.
5. Créer les premiers spécialistes LangGraph.
6. Intégrer DeepSeek Harness comme Skill isolé.
7. Tester Skill Creator et SkillOpt.
8. Préparer les synchronisations Oracle.
9. Préparer le mini PC salon.

## 11. Limites et points à vérifier

Non vérifiés, donc sans garantie : disponibilité future et identifiants des modèles, modèles gratuits OpenRouter, performances annoncées, coûts estimatifs, vitesses en tokens par seconde, durées de traitement, seuil de confiance 0,85 et pourcentages d'escalade, compatibilité matérielle, intérêt de DSH sur Oracle, dimensionnement du mini PC, modèle Vision cloud, modèle vocal local, chemin effectif de `DSH_HOME`.

Statuts « à tester », « optionnel », « reporté » ou « à déterminer » : hypothèses ou expérimentations, pas des engagements.
