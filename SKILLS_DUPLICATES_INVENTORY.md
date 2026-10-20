# Inventaire des skills et doublons

## Périmètre et méthode

Inventaire en lecture seule du dépôt et de l'instance Qdrant joignable sur
`localhost:6333`, le 20 octobre 2026. Les empreintes SHA-256 sont calculées sur
les octets du fichier pour les fichiers locaux et sur le texte UTF-8 du champ
`skill_content` pour les points Qdrant. Ces deux périmètres d'empreinte sont
indiqués et ne doivent pas être comparés comme s'il s'agissait de fichiers de
même format.

Les versions et statuts absents des sources sont indiqués comme inconnus; ils
ne sont pas déduits du nom ou de l'état du conteneur. Aucun skill n'a été
fusionné, déplacé ou supprimé.

## Résumé

- `skills/registry.json` enregistre une entrée : `ping_qdrant`. Son script fait
  actuellement uniquement un affichage fixe; il ne prouve pas à lui seul une
  vérification effective de Qdrant.
- `workspace/skills/` contient un skill JSON : `invoice_smtrt_sas`, version
  `1.0.0`, compteur d'utilisation `2`.
- La collection Qdrant `agent_skills` est accessible et contient quatre
  points; ils partagent la même tâche déclarée, mais ont des contenus distincts.
- La collection `skills_catalog` est accessible, verte et vide.
- La collection `teacher_student_metrics` est accessible et contient 16
  enregistrements de métriques, pas des définitions de skills.
- Cinq fichiers `SKILL.md` ont été trouvés. Ils sont tous dans
  `.venv-v3\Lib\site-packages`; aucun `SKILL.md` de premier niveau projet n'a
  été trouvé.
- Aucune configuration Hermes ou SkillOpt, ni fichier nommé
  `teacher_student_metrics`, n'a été trouvé dans les fichiers du dépôt. La
  collection Qdrant de ce nom est cependant présente.

## Inventaire détaillé

| Identifiant disponible | Nom / compétence | Version | SHA-256 du contenu inventorié | Emplacement | Stockage / statut | Utilisation | Doublons stricts | Doublons fonctionnels | Origine |
|---|---|---|---|---|---|---:|---|---|---|
| `ping_qdrant` | `ping_qdrant` | Non renseignée | Script : `bc0db5366568277f7a641351d5b8f93f40c3776ca8a0b12e17b7b60352331175` | `skills/registry.json` → `skills/ping_qdrant.py` | Référencé par le registre; statut de cycle de vie absent. Le script est présent. | Non disponible | Aucun constaté | Aucun avec les compétences d'extraction de facture; la fonction annoncée n'est pas vérifiée par le contenu du script. | Origine non documentée. |
| `invoice_smtrt_sas` (champ `skill_name`) | Règles d'extraction de facture pour un fournisseur | `1.0.0` | Fichier JSON : `7af8f09ce2a76e1e8c09525dd8da74d6568d67a081458495ac824b749e6966e7` | `workspace/skills/invoice_smtrt_sas.json` | Fichier JSON présent; `confidence=1.0`; champ de statut absent. | `2` (métadonnée `lifecycle.usage_count`) | Aucun égal strict de fichier/contenu constaté dans les autres éléments inventoriés. | Recouvrement général « extraction de facture » avec les points Qdrant ci-dessous; règles spécifiques différentes, donc doublon fonctionnel non établi. | Métadonnées `created_from` indiquant une création à partir de corrections de champs de facture; provenance de la donnée d'origine non vérifiée indépendamment. |
| `07a74869-a856-484b-a7dd-b2e7f8628e3d` | Extraction de TVA d'une facture avec HT/TTC inversés | Non renseignée | `skill_content` : `360131350531fae436e11839fa2c8747f9650b40124f5d698fa4f0cf337d929e` | Collection Qdrant `agent_skills`, point `07a74869-a856-484b-a7dd-b2e7f8628e3d` | Collection accessible, état `green`; point sans statut de cycle de vie. | `1` | Aucun hash de contenu identique aux trois autres points. | Même tâche déclarée que les trois autres points `agent_skills` : doublon fonctionnel probable. | `created_by_role=EXPERT_MODEL`; événement d'origine précis non déterminé. |
| `202d38b2-e9ff-4e61-8adf-f663c72e25ba` | Extraction de TVA d'une facture avec HT/TTC inversés | Non renseignée | `skill_content` : `ca53233cc39431201cc3d19ce690ea6c9f24aece8f28ed98a598d03a03037d4b` | Collection Qdrant `agent_skills`, point `202d38b2-e9ff-4e61-8adf-f663c72e25ba` | Collection accessible, état `green`; point sans statut de cycle de vie. | `0` | Aucun hash de contenu identique aux trois autres points. | Même tâche déclarée que les trois autres points `agent_skills` : doublon fonctionnel probable. | `created_by_role=EXPERT_MODEL`; événement d'origine précis non déterminé. |
| `5e7ef402-d75f-49ec-94e9-8eb1f7fa4a05` | Extraction de TVA d'une facture avec HT/TTC inversés | Non renseignée | `skill_content` : `0fb806c9ea0cde2a7be9768b2bae9a1d224ab2228868e852914ac1cfa4f0d487` | Collection Qdrant `agent_skills`, point `5e7ef402-d75f-49ec-94e9-8eb1f7fa4a05` | Collection accessible, état `green`; point sans statut de cycle de vie. | `0` | Aucun hash de contenu identique aux trois autres points. | Même tâche déclarée que les trois autres points `agent_skills` : doublon fonctionnel probable. | `created_by_role=EXPERT_MODEL`; événement d'origine précis non déterminé. |
| `6ed71560-fba0-4635-ac79-755cd5b534e5` | Extraction de TVA d'une facture avec HT/TTC inversés | Non renseignée | `skill_content` : `4031a2974caa99b20890bbd1970975297061d11d74db471349f733bfe7cfbb85` | Collection Qdrant `agent_skills`, point `6ed71560-fba0-4635-ac79-755cd5b534e5` | Collection accessible, état `green`; point sans statut de cycle de vie. | `0` | Aucun hash de contenu identique aux trois autres points. | Même tâche déclarée que les trois autres points `agent_skills` : doublon fonctionnel probable. | `created_by_role=EXPERT_MODEL`; événement d'origine précis non déterminé. |

### Fichiers `SKILL.md`

Les cinq fichiers ci-dessous sont des ressources de dépendances installées dans
`.venv-v3`, et non des skills enregistrés par `skills/registry.json` ou
`workspace/skills/`. Aucun identifiant séparé, version ou compteur d'utilisation
n'est défini dans leur frontmatter.

| Identifiant / nom disponible | Version | SHA-256 du fichier | Emplacement | Stockage / statut | Usage | Doublons | Origine |
|---|---|---|---|---|---|---|---|
| `fastapi` | Non renseignée | `989e5136ad015c175cca28669d051bfef2e20dad13a8385383885648607f5eb6` | `.venv-v3/Lib/site-packages/fastapi/.agents/skills/fastapi/SKILL.md` | Documentation de dépendance locale; pas de statut skill DSH-Core. | Non disponible | Aucun doublon strict constaté; pas de doublon fonctionnel avec les autres `SKILL.md` identifié. | Package FastAPI installé; version exacte du package non déterminée par ce fichier. |
| `playwright-cli` | Non renseignée | `1a609c93bff3eea9ab99fed93c0334c851fdc39fb6ac5335551bfb87c171d57c` | `.venv-v3/Lib/site-packages/playwright/driver/package/lib/tools/skills/playwright-cli/SKILL.md` | Documentation de dépendance locale. | Non disponible | Aucun doublon strict constaté. Fonction différente des autres fichiers listés. | Package Playwright installé; version exacte du package non déterminée par ce fichier. |
| `playwright-component-testing` | Non renseignée | `60f57b31767d562c30cc5332081418c2945f4d9c998efa6e09191333654dd429` | `.venv-v3/Lib/site-packages/playwright/driver/package/lib/tools/skills/playwright-component-testing/SKILL.md` | Documentation de dépendance locale. | Non disponible | Aucun doublon strict constaté. Même famille Playwright, mais objet distinct de `playwright-cli` et `playwright-trace`. | Package Playwright installé; version exacte du package non déterminée par ce fichier. |
| `playwright-trace` | Non renseignée | `4124dd790915bb76108377121d1e6ca93a5544c90485c090fa5655235efe22aa` | `.venv-v3/Lib/site-packages/playwright/driver/package/lib/tools/skills/playwright-trace/SKILL.md` | Documentation de dépendance locale. | Non disponible | Aucun doublon strict constaté; même famille Playwright sans identité fonctionnelle stricte démontrée. | Package Playwright installé; version exacte du package non déterminée par ce fichier. |
| `typer` | Non renseignée | `dfef816d8d67c9915e277b671c2d1230cef9d6944f503c6aa070f1cb3bef1dd5` | `.venv-v3/Lib/site-packages/typer/.agents/skills/typer/SKILL.md` | Documentation de dépendance locale. | Non disponible | Aucun doublon strict ou fonctionnel constaté avec les autres fichiers listés. | Package Typer installé; version exacte du package non déterminée par ce fichier. |

### Registre local et stockage Qdrant sans entrée de skill

| Ressource | Identifiant / contenu | SHA-256 / compte | Résultat |
|---|---|---|---|
| `skills/registry.json` | Identifiant `ping_qdrant`, nom `ping_qdrant`; aucune version, utilisation ou propriété de statut dans l'entrée. | Fichier : `b3821380a2811e1527c4eb2acc609a0b01f496759d312803d27bdd3d560a6914` | Une entrée enregistrée. Le chemin de script est absolu et local au dépôt. |
| `skills/ping_qdrant.py` | Script de 24 octets. | `bc0db5366568277f7a641351d5b8f93f40c3776ca8a0b12e17b7b60352331175` | Le contenu observé affiche seulement un message fixe; il ne permet pas de confirmer un test réseau. |
| Qdrant `skills_catalog` | Aucun point. | `0` point; collection `green`. | Aucun skill actif ou archivé observé dans ce catalogue au moment de la consultation. |
| Qdrant `teacher_student_metrics` | 16 enregistrements métriques. | `16` points; collection `green`. | `skill_created=true` : 0; `skill_reused=true` : 0. Ces mesures ne contiennent pas d'identifiant de skill dans les champs observés et ne constituent pas un registre de compétences. |

## Analyse des doublons

### Doublons stricts

Aucun doublon strict n'est établi par les empreintes disponibles :

- les quatre champs `skill_content` de `agent_skills` ont quatre SHA-256
  distincts;
- le JSON `invoice_smtrt_sas`, l'entrée du registre, le script et les cinq
  `SKILL.md` ont des empreintes de fichiers distinctes;
- aucun contenu local brut n'est identique aux contenus Qdrant au format/hash
  mesuré.

L'absence d'égalité d'empreintes ne prouve pas que deux représentations
normalisées ou sémantiquement équivalentes ne puissent exister.

### Doublons fonctionnels

- **Probable, confiance élevée :** les quatre points `agent_skills` exposent
  exactement le même `task_name` relatif à l'extraction de TVA avec HT/TTC
  inversés. Leurs contenus diffèrent (314, 352, 353 et 403 octets), ce qui
  suggère quatre variantes ou tentatives pour une même tâche, pas quatre
  fonctions métier distinctes.
- **Recouvrement partiel, confiance moyenne :** `invoice_smtrt_sas` et les
  points `agent_skills` relèvent tous deux du traitement des factures. Le
  premier porte des règles spécifiques de fournisseur/extraction; les seconds
  ciblent une erreur HT/TTC. Les informations disponibles ne démontrent pas
  qu'ils soient interchangeables.
- **Pas de doublon fonctionnel identifié** entre `ping_qdrant` et les skills
  d'extraction, ni entre les cinq `SKILL.md` de dépendances.

## Incertitudes propres aux skills

1. Aucun `SKILL.md` propre au code métier DSH-Core n'a été trouvé; il est
   incertain si le format skill attendu est plutôt le JSON local, le registre
   Qdrant ou un format externe.
2. Les quatre points `agent_skills` n'ont ni version ni statut de cycle de vie;
   les compteurs sont disponibles, mais il manque un lien fiable entre une
   variante, sa création et sa validation.
3. Le champ de tâche Qdrant apparaît avec un encodage de caractères altéré
   (`oÃ¹`) dans la réponse observée; l'orthographe source et l'éventuelle
   incidence sur la déduplication ne sont pas établies.
4. Les métriques n'ont pas de lien explicite vers un identifiant de skill; elles
   ne permettent donc pas de reconstituer l'usage des skills au niveau
   individuel.
5. Aucune configuration Hermes/SkillOpt n'a été repérée dans le périmètre du
   dépôt; l'existence d'une configuration extérieure au dépôt n'est pas
   vérifiée.
