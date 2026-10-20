# Cartographie des stockages Qdrant et Neo4j

## Périmètre et méthode

Vérifications en lecture seule effectuées le 20 octobre 2026 :

- lecture des fichiers `docker-compose.yml` à la racine et
  `docker/docker-compose.yml`;
- `docker ps`, `docker inspect`, `docker volume ls` et `docker system df -v`;
- taille des deux répertoires de données présents dans le dépôt;
- requêtes HTTP de lecture à l'API Qdrant locale et contrôle de disponibilité
  HTTP Neo4j.

Aucune base ou donnée n'a été modifiée. Les tailles de répertoires sont des
mesures de fichiers visibles depuis l'hôte; les tailles Docker sont des
mesures distinctes rapportées par Docker. Elles ne doivent pas être additionnées
sans déduplication.

## Carte synthétique

| Instance / rôle | Conteneur | Service / définition | Port(s) | Volume ou bind mount | Chemin hôte | Taille observée | État | Contenu résumé | Doublon probable | Certitude |
|---|---|---|---|---|---|---:|---|---|---|---|
| Qdrant actif | `dsh-qdrant` | Projet Compose `dsh-core`, fichier racine; image `qdrant/qdrant` | `6333` REST | Bind mount `/qdrant/storage` | `A:\dsh-core\qdrant_storage` | `4,366,859,237` octets; `4,164.56 MiB`; 1,574 fichiers | Actif depuis environ 21 h; API HTTP joignable | Huit collections nommées dans l'API, dont `agent_skills` (4), `skills_catalog` (0) et `teacher_student_metrics` (16). Voir l'inventaire ci-dessous. | **Probable** avec le stockage sous `docker\qdrant_storage`, mais copie/ancêtre commun non confirmé. | Élevée pour le montage, taille et collections observées. |
| Qdrant secondaire déclaré | Aucun conteneur actif associé | `docker/docker-compose.yml`, service `qdrant` | Déclarerait `6333` et `6334` | Bind relatif `./qdrant_storage:/qdrant/storage` | `A:\dsh-core\docker\qdrant_storage` | `3,553,976,763` octets; `3,389.34 MiB`; 1,652 fichiers | Inactif comme instance; répertoire de données existe | Contenu interne non interrogé par API; aucune seconde instance Qdrant correspondante dans `docker ps`. | **Probable** avec le stockage racine en raison de la même application et du même montage cible; tailles et nombres de fichiers diffèrent. | Élevée pour le chemin et l'inactivité; moyenne pour le lien de duplication. |
| Neo4j actif | `dsh-neo4j` | Projet Compose `dsh-core`, fichier racine; image `neo4j:latest` | `7474` HTTP, `7687` Bolt | Bind mount `/data` | `A:\dsh-core\neo4j_data` | `541,130,919` octets; `516.06 MiB`; 85 fichiers | Actif depuis environ 21 h; page HTTP répond `200` | Les données persistent dans le répertoire monté. Les nœuds/relations n'ont pas pu être lus : l'accès à l'API de transaction a répondu `401 Unauthorized`. | Aucun second chemin Neo4j trouvé dans les deux Compose vérifiés. | Élevée pour montage, taille et refus d'accès; contenu graphe inconnu. |
| Journaux Neo4j | `dsh-neo4j` | Volume Docker associé au conteneur Neo4j | Interne uniquement | Volume Docker anonyme monté sur `/logs` | Chemin Docker interne | `688.4 kB` selon `docker system df -v`; `du -sh /logs` donne environ `684K` | Actif, un lien Docker | Journaux Neo4j, distincts des données montées sur `/data`. | Aucun doublon de données établi. | Élevée pour le montage et la taille rapportée. |

### Définitions Compose et instance Qdrant active

Le Compose racine déclare `dsh-qdrant` sur le port `6333` avec un bind mount
vers `A:\dsh-core\qdrant_storage`. L'inspection du conteneur confirme le projet
`dsh-core`, son répertoire de travail racine et ce montage.

Le Compose sous `docker/` déclare également un conteneur nommé `dsh-qdrant`,
expose les mêmes ports REST `6333` plus `6334`, et monte le chemin relatif
`./qdrant_storage`, résolu ici vers `A:\dsh-core\docker\qdrant_storage`.
Cette définition ne correspond pas à un deuxième conteneur actif. Les deux
fichiers ne peuvent pas lancer simultanément un conteneur de même nom et
occuper le même port `6333` sur cet hôte sans conflit.

Les deux répertoires Qdrant contiennent des données volumineuses mais leurs
tailles et nombres de fichiers diffèrent. Ils sont donc signalés comme
**doublon probable / générations divergentes possibles**, pas comme copies
identiques. Aucun rapprochement de snapshots ou d'empreintes de chaque fichier
n'a été effectué.

## Collections Qdrant accessibles

L'API REST locale (`localhost:6333`) répond et retourne les collections
suivantes. Les trois collections de skills/métriques ont été lues directement
par pagination des points; les autres sont listées par l'API, mais leur
comptage n'est pas affirmé ici lorsque le retour des métadonnées n'a pas pu
être normalisé de façon fiable.

| Collection | État API observé | Nombre de points | Résumé / portée |
|---|---|---:|---|
| `agent_skills` | `green` | 4 | Quatre points de compétence, décrits comme une même tâche d'extraction de TVA. `indexed_vectors_count=0`. |
| `skills_catalog` | `green` | 0 | Catalogue de skills vide. |
| `teacher_student_metrics` | `green` | 16 | Historique de métriques; 14 workflows `invoice`, 1 `research_quick`, 1 `research_deep`. `skill_created=true`: 0; `skill_reused=true`: 0. |
| `mem0` | `green` | 0 | Collection accessible, vide au moment de la consultation. |
| `mem0migrations` | `green` | 0 | Collection accessible, vide au moment de la consultation. |
| `document_bus` | Présente dans la liste | Non établi dans ce rapport | Nom rendu par l'API de collections; compte de points non confirmé. |
| `invoice_documents` | Présente dans la liste | Non établi dans ce rapport | Nom rendu par l'API de collections; compte de points non confirmé. |
| `research_cache` | Présente dans la liste | Non établi dans ce rapport | Nom rendu par l'API de collections; compte de points non confirmé. |

Les états `green` et comptes de points reflètent l'observation de l'instance au
moment de l'audit; ils peuvent changer après l'exécution.

## Neo4j : accessibilité des nœuds

Le conteneur est actif, les ports `7474` et `7687` sont publiés, et la racine
HTTP du service répond. Une requête de lecture sur l'API de transaction a
cependant renvoyé `401 Unauthorized`. En conséquence :

- l'existence du stockage physique `/data` est confirmée;
- l'accessibilité du serveur est confirmée, mais pas l'accès authentifié à la
  base;
- aucun nombre de nœuds, type de relation ou résumé de labels n'est certifié;
- aucun contenu graphe n'est inféré depuis les seuls fichiers disque.

## Volumes Docker observés

En plus des bind mounts applicatifs, `docker volume ls` et `docker system df -v`
ont montré :

| Volume Docker | Liens actifs | Taille Docker | Usage observé |
|---|---:|---:|---|
| `da9ad36d4dba0b48211904ba238a7ae91fbef4dcadb827f0bdd3c2f64997c9e9` | 1 | `688.4 kB` | Volume `/logs` du conteneur Neo4j. |
| `14afde45974caa348ccd2f6b844a13bfaabf679982e91910b4b205dc3b9e405e` | 0 | `119.7 kB` | Non attaché à un conteneur selon Docker; aucun lien à Qdrant/Neo4j établi. |
| `368a470d41fd8f804ddf8aa56fb67ae0abd29484ddbf988f341373f325cf41c3` | 0 | `109 kB` | Non attaché à un conteneur selon Docker; aucun lien à Qdrant/Neo4j établi. |
| `open-webui` | 0 | `1.118 GB` | Volume non attaché selon Docker; l'instance Open WebUI active utilise un bind mount distinct. |

Les deux petits volumes anonymes et le volume `open-webui` ne sont pas utilisés
par les conteneurs Qdrant ou Neo4j inspectés. Le conteneur `keen_gates` est
arrêté (Exited 255) et est une ancienne instance Open WebUI; il n'est pas un
conteneur Qdrant/Neo4j actif.

## Inconnues et niveau de confiance

1. **Nœuds Neo4j :** inconnus tant qu'un accès en lecture autorisé n'est pas
   disponible; la réponse `401` interdit de certifier le contenu.
2. **Stockage Qdrant secondaire :** la quantité de données est mesurée, mais la
   liste de ses collections et son état logique ne sont pas connus puisqu'aucun
   service actif ne l'expose.
3. **Doublon Qdrant :** le rapprochement des deux répertoires est plausible du
   fait des Compose concurrents et du même chemin de stockage interne, mais
   aucune identité de snapshot n'est prouvée.
4. **Collections Qdrant non liées aux skills :** leurs noms sont connus; leurs
   nombres de points ne sont pas certifiés dans ce rapport.
5. **Volumes sans liens :** Docker rapporte zéro conteneur lié, mais leur
   contenu et leur origine métier ne sont pas inférés.
6. **Ports :** seul le port `6333` est publié par le conteneur Qdrant actif;
   `6334` n'est déclaré que par le Compose secondaire et n'est pas publié par
   cette instance.
