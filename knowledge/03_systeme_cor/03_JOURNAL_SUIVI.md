\# 03 - JOURNAL DE SUIVI ET DE VALIDATION



\## ASSAINISSEMENT SYSTÈME (Septembre 2026)

\* Suppression des anciens environnements conflictuels (`.venv`, `.venv-1`, `.venv-v2` sous Python 3.14).

\* Stabilisation de l'environnement `.venv-v3` (Python 3.11.9) validée.

\* Remplacement de Llama Vision par `qwen2.5vl:7b` (optimisation VRAM).



\## VALIDATIONS TECHNIQUES RÉALISÉES

\* \*\*Mémoire Vectorielle :\*\* Collections `mem0` et `document\_bus` (768 dims) opérationnelles sur Docker port 6333.

\* \*\*Extraction Épisodique :\*\* Le stockage et la restitution de faits via Mem0 + `qwen3.5:9b` sont confirmés.

\* \*\*Phase 8 (Factures) :\*\* Le script `invoice\_agent.py` utilisant Surya OCR + Qwen 9B génère avec succès des JSON validés mathématiquement via Pydantic. Indexation dans Qdrant testée.

\* \*\*Point d'accès n8n :\*\* Serveur Webhook FastAPI (`n8n\_api.py`) créé et fonctionnel sur le port 8000.

* **Phase 5 (MCP) :** Serveur mcp_skills.py migré sur MCP v2, connecté à Continue.dev et lié au routeur FastAPI central.

* **Phase 4 (Orchestration) :** Moteur LangGraph créé (`orchestrator.py`) et connecté au routeur FastAPI pour isoler la logique de décision.
* **Phase 6 & 9 (Infrastructures tierces) :** Conteneurs Langfuse (Port 3000) et Neo4j (Port 7474) déployés avec succès via `docker-compose.yml`.

\## PROCHAINES ÉTAPES IMMÉDIATES

2\. \*\*Construction de la Phase 4 :\*\* Coder le routeur LangGraph dans le backend FastAPI.

3\. \*\*Module SIA BOSH :\*\* Démarrer le développement du point d'appui de lettrage complexe.



1. **LangGraph Studio (Phase 7) :** Brancher l'interface visuelle pour observer l'arbre de décision en temps réel.
2. **Module SIA BOSH :** Modéliser les premières relations de lettrage dans Neo4j.

### Date : 27 Septembre 2026
**Objectif :** Finalisation de l'observabilité, sécurisation du code et définition du modèle de données SIA BOSH.

**Actions réalisées :**
1. **Correction Langfuse :** Rétrogradation de l'image Docker vers la version `2` pour éviter la dépendance ClickHouse de la V3. Résolution du crash de l'interface en désactivant la traduction automatique et le bouclier Brave.
2. **Sécurisation des clés :** Création du fichier `.env` pour stocker `LANGFUSE_SECRET_KEY` et `LANGFUSE_PUBLIC_KEY`. Modification de `api.py` (via `python-dotenv`) pour intégrer silencieusement Langfuse à LangGraph.
3. **Mise au rebut :** Confirmation que les anciens agents de test autonomes (ex: `research_agent.py` Llama3.2/vision) sont obsolètes face à la nouvelle architecture centralisée.
4. **Git Remote :** Initialisation et envoi forcé du code (protection contre la perte de données sur `A:\`) vers un dépôt privé GitHub.
5. **Neo4j et Modèle Métier :** Test Cypher réussi. Redéfinition des objectifs concrets :
   - Rapprochement bancaire automatisé pour les factures d'achats (soulager le comptable).
   - Gestion des paiements de type LCR (1 paiement -> N factures clients).
   - Extraction des lignes de vente pour générer un fichier Excel de proposition de réassort fournisseur.

**Prochaine étape :** Entamer le développement du modèle de graphe Neo4j réel pour les factures d'achat.

Ajoute l'entrée du jour pour tracer la modification :

```markdown
**[28/09/2026] - Refactorisation et Verrouillage de l'API (Contrat Universel)**
- Incident : Plantage de l'outil WebUI (Erreur de parsing due à des retours API hétérogènes).
- Action : Refonte complète de `api.py`. Tous les endpoints (`/research`, `/code`, `/execute`, `/scrape`, `/orchestrator`) utilisent désormais le format de retour structuré `{"status", "agent", "result"}`.
- Résultat : L'intelligence est reconcentrée dans le backend. L'outil WebUI est rétrogradé au statut de "tuyau bête" (Dumb Pipe), résolvant définitivement les erreurs d'incompatibilité inter-interfaces.

Ajouter une règle.

Chaque évolution majeure doit être consignée :

Plain Text
Date
 
Problème
 
Décision
 
Impact
 
Rollback possible
Afficher plus de lignes

Tu remercieras ton toi du futur.