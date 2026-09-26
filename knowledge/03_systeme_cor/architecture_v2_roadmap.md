L'architecture cible v2 est extrêmement ambitieuse, mais la superposition de tous ces frameworks d'orchestration risque de créer une usine à gaz, particulièrement complexe à faire tourner avec les 8 Go de VRAM de la RTX 4070.



1\. Redondances et clarification des rôles

Il y a une collision frontale entre l'infrastructure actuelle, LangGraph, et AutoGen.



LangGraph vs AutoGen : Utiliser les deux simultanément alourdit le système inutilement. LangGraph excelle dans les flux déterministes (une chaîne de montage : Étape A → Étape B), tandis qu'AutoGen est conçu pour les conversations libres (une salle de réunion où les agents débattent). Privilégie LangGraph comme colonne vertébrale de l'API. N'utilise AutoGen que de façon isolée, encapsulé dans un nœud LangGraph spécifique (ex: la phase où le Skill Engineer Agent et le Superviseur débattent sur l'optimisation d'un code).



Cherry Studio vs LangGraph : Dans cette V2, Cherry Studio perd son rôle de chef d'orchestre. Il devient un simple terminal d'affichage (un client). Il ne gère plus la logique de routage ; il envoie la question au routeur LangGraph qui s'occupe du reste.



ACP/A2A vs MCP : Le Model Context Protocol (MCP) est le standard industriel adoubé par Anthropic et les éditeurs locaux. Délaisse les protocoles théoriques ACP/A2A au profit de ton MCP HUB. Tous tes agents, qu'ils soient dans LangGraph ou AutoGen, doivent utiliser exclusivement les serveurs MCP pour communiquer et interagir avec l'OS.



2\. Intégration progressive (Le modèle de la "figuier étrangleur")

Ne réécris pas tout d'un coup. Remplace les briques de ton système dsh-core actuel une par une derrière l'API FastAPI.



Fondation des données : Débranche ChromaDB et connecte Qdrant. La migration de l'historique Mem0 se fera de manière transparente puisque les deux gèrent des vecteurs.



Unification documentaire : Introduis LlamaIndex comme couche logique au-dessus de Qdrant. Tes dossiers (factures, PDF, code) sont indexés par LlamaIndex. AnythingLLM ne gère plus ses propres documents, il interroge l'API LlamaIndex.



Orchestration invisible : Déploie LangGraph directement dans le backend api.py. Commence par y coder un seul workflow simple (le routage Qwen 9B → Claude).



La bascule client : Pointe enfin tes interfaces vers ce routeur LangGraph.



3\. Transformer le catalogue en Registry Partagé

Ton dossier A:\\dsh-core\\skills\\ actuel avec son registry.json est prêt pour cette évolution.



Indexe les métadonnées de tes compétences (le texte décrivant l'objectif de chaque script Python) dans Qdrant via LlamaIndex.



Expose un unique serveur MCP (dsh-skills-hub) qui contient deux outils : rechercher\_skill (qui interroge Qdrant sémantiquement) et executer\_skill (qui lance le script Python associé).



Quel que soit l'agent ou le framework (LangGraph, AutoGen), il passera par ce hub MCP unique pour trouver et déclencher la compétence.



4\. Synchronisation des interfaces (Cherry, WebUI, VS Code)

Les interfaces ne doivent jamais communiquer entre elles ni gérer de mémoire locale. Elles doivent toutes se comporter comme des terminaux aveugles connectés à un Cerveau Central (FastAPI + LangGraph).



Configure ton backend FastAPI pour exposer une API calquée sur le standard OpenAI (\[http://127.0.0.1:8000/v1/chat/completions](http://127.0.0.1:8000/v1/chat/completions)).



Dans Cherry Studio, VS Code (Continue), et Open WebUI, ajoute ce backend local comme un fournisseur de modèle personnalisé.



L'injection de la mémoire Mem0, des préférences et de l'historique se fait côté serveur, avant que le prompt n'atteigne Qwen ou Claude. Ainsi, un workflow d'automatisation n8n ou une discussion entamée dans Cherry Studio partagera instantanément le même contexte si tu l'ouvres dans VS Code.



5\. Le pipeline local factures et tickets

Pour ton activité de distribution, la précision comptable exige de dissocier la compréhension visuelle de l'extraction de caractères, tout en jonglant avec la mémoire graphique disponible.



Étape 1 - Vision (Qwen2.5-VL) : Utilisé uniquement pour classifier la géométrie du document (trouver où se situe le bloc fournisseur, le tableau des prix, les totaux).



Étape 2 - OCR pur (Surya OCR) : Surya est l'état de l'art en local pour extraire le texte brut de manière déterministe et éviter les hallucinations de chiffres.



Étape 3 - Structuration (Qwen 32B ou 9B) : Un agent d'extraction prend le texte brut de Surya, utilise les repères de zone de Qwen-VL, et mappe les données dans un schéma JSON strict.



Étape 4 - Ingestion (LlamaIndex + Qdrant) : LlamaIndex vectorise le document validé et l'envoie dans Qdrant avec des métadonnées filtrables (ex: {"fournisseur": "Paintcar", "type": "facture\_acquittee"}). n8n peut ensuite surveiller cette base pour déclencher l'intégration ERP.

