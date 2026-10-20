# Audit des imports et appels de symboles

Audit en lecture seule des symboles `InvoiceSchema`, `InvoiceData`,
`router_intelligent`, `route_prompt`, `find_relevant_skill` et
`retrieve_skill_for_task`.

## 1. `InvoiceSchema`

**Erreur : symbole importé mais absent de la cible.**

- **Fichiers appelants**
  - `invoice_agent.py` importe `InvoiceSchema` depuis `schemas.invoice_schema`,
    l'annotate comme type de retour et l'instancie.
  - `services/extraction_manager.py` importe et instancie également
    `InvoiceSchema` depuis `schemas.invoice_schema`.
- **Fichier cible :** `schemas/invoice_schema.py`.
- **Symbole réellement défini :** `InvoiceData` (ainsi que `TypePiece` et
  `TypeTVA`); aucune classe `InvoiceSchema` n'est définie.
- **Correction exacte proposée :** ne pas remplacer seulement le nom par
  `InvoiceData`. Les deux appelants demandent un JSON qui ne correspond pas à
  `InvoiceData` : leurs données comprennent notamment `date_emission`, des
  objets `fournisseur`/`client`, des `lignes`, `total_ht` et `total_ttc`.
  Définir dans `schemas/invoice_schema.py` un modèle `InvoiceSchema` qui décrit
  exactement ce contrat, puis conserver les imports et validations sous ce nom.
  Alternative : modifier les prompts et la sortie des deux appelants pour
  produire le contrat plat `InvoiceData` (`date_facture`, `fournisseur` texte,
  `montant_ht`, `montant_tva`, `montant_ttc`), puis importer et instancier
  `InvoiceData`.

## 2. `InvoiceData`

**Pas d'import cassé dans le schéma; deux définitions homonymes, de contrats
différents.**

- **Fichiers appelants**
  - `services/business_extraction_manager.py` importe
    `InvoiceData` depuis `schemas.invoice_schema` et l'instancie avec les champs
    plats attendus par ce modèle.
  - `test_nuextract.py` et des scripts d'archives/documentation importent aussi
    `InvoiceData` depuis le schéma.
  - `agents/invoice_agent.py` n'importe pas ce symbole : il définit sa propre
    classe locale `InvoiceData` et l'utilise dans `traiter_facture`.
- **Fichier cible partagé :** `schemas/invoice_schema.py`.
- **Symbole réellement défini :** `InvoiceData`, modèle Pydantic plat avec
  `fournisseur: str`, `date_facture`, `montant_ht`, `montant_tva` et
  `montant_ttc`. `agents/invoice_agent.py` définit séparément une classe locale
  du même nom, avec les champs `fournisseur`, `date_facture`, `montant_ht`,
  `montant_tva` et `montant_ttc` et une validation arithmétique stricte.
- **Correction exacte proposée :** choisir un modèle canonique et supprimer
  l'ambiguïté. Si le modèle du schéma est canonique, dans
  `agents/invoice_agent.py`, supprimer la classe locale `InvoiceData`, importer
  `InvoiceData` de `schemas.invoice_schema`, et conserver un prompt produisant
  son contrat plat. Si l'agent doit garder son comportement actuel, renommer
  son modèle local (par exemple `VisionInvoiceData`) et garder les références
  locales explicites. Ne pas faire importer `InvoiceData` du schéma au prompt
  racine actuel sans aussi aligner son format imbriqué.

## 3. `router_intelligent`

**Erreur : la route API importe un nom non défini.**

- **Fichier appelant :** `api.py`, dans le gestionnaire `POST /api/router`.
- **Fichier cible :** `router.py`.
- **Symbole réellement défini :** `route_prompt(prompt, user_id="admin",
  image_provided=False)`; `router_intelligent` n'existe pas dans ce module.
- **Correction exacte proposée :** remplacer l'import par
  `from router import route_prompt`, puis appeler
  `route_prompt(prompt=request.prompt,
  image_provided=bool(request.image_path))`. Le champ
  `request.complexite_haute` n'a pas d'équivalent dans cette signature :
  supprimer ce champ de la requête si inutile, ou ajouter un paramètre et une
  branche explicites à `route_prompt` avant de le transmettre.

## 4. `route_prompt`

**Symbole existant et appel du harnais valide; route API non alignée.**

- **Fichier appelant valide :** `dsh_harness.py`, qui appelle
  `router.route_prompt(prompt, image_provided=bool(image_path))`.
- **Fichier cible :** `router.py`.
- **Symbole réellement défini :** `route_prompt(prompt, user_id="admin",
  image_provided=False)`.
- **Correction exacte proposée :** aucune pour `dsh_harness.py`. Pour `api.py`,
  appliquer la correction indiquée pour `router_intelligent`; ne pas remplacer
  seulement le nom de fonction sans adapter les arguments.

## 5. `find_relevant_skill`

**Erreur : méthode appelée absente du routeur de skills.**

- **Fichier appelant :** `agents/research_agent.py`, dans
  `ResearchAgent.search_and_structure`, appelle
  `router.find_relevant_skill(task=query)`.
- **Fichier cible :** `services/skill_router.py`.
- **Symbole réellement défini :** `SkillRouter.retrieve_skill_for_task(task)`,
  qui renvoie `Optional[str]`. `find_relevant_skill` n'est pas défini dans
  `SkillRouter`.
- **Correction exacte proposée :** remplacer l'appel par
  `routing_res = router.retrieve_skill_for_task(task=query)`, puis remplacer
  la lecture supposant un dictionnaire
  (`routing_res.get("skills")` et `routing_res["context"]`) par l'utilisation
  directe de la chaîne renvoyée :
  `if routing_res: skill_context = f"\n{routing_res}\n"`.
  La méthode retourne `None` si aucun contexte pertinent n'est disponible.

## 6. `retrieve_skill_for_task`

**Symbole réel et utilisé par le test d'apprentissage.**

- **Fichier appelant repéré :** `test_learning_loop.py`, qui appelle
  `skill_router.retrieve_skill_for_task(task=new_task)`.
- **Fichier cible :** `services/skill_router.py`.
- **Symbole réellement défini :** `SkillRouter.retrieve_skill_for_task(task)`,
  qui génère un embedding, appelle `SkillsRepository.find_relevant_skills`,
  incrémente l'usage du skill retenu et renvoie son contexte formaté en chaîne
  ou `None`.
- **Correction exacte proposée :** aucune dans `services/skill_router.py`.
  Remplacer l'appel invalide de `agents/research_agent.py` par cet appel et
  adapter son traitement de retour comme décrit à la section précédente.

## Synthèse des raccordements cassés

1. `InvoiceSchema` est importé par deux modules, mais n'existe pas dans
   `schemas/invoice_schema.py`; leurs contrats de données doivent être alignés
   avant d'opter pour une simple substitution de nom.
2. `InvoiceData` existe dans le schéma et les imports vers celui-ci sont
   valides; `agents/invoice_agent.py` définit toutefois une classe homonyme
   distincte.
3. `api.py` demande `router_intelligent`, alors que `router.py` expose
   `route_prompt`; la signature et les arguments diffèrent aussi.
4. `agents/research_agent.py` demande `find_relevant_skill`, absent de
   `SkillRouter`; le remplacement par `retrieve_skill_for_task` exige également
   d'adapter le code appelant au retour `str | None`.
