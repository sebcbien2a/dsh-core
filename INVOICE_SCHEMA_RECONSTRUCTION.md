# Reconstruction du contrat `InvoiceSchema`

## Périmètre et limites

Cette reconstruction repose uniquement sur le contenu de
`services/extraction_manager.py`. Elle décrit le JSON que le prompt demande et
les contrôles effectivement visibles dans ce fichier. La classe importée
`schemas.invoice_schema.InvoiceSchema` n'est pas définie ici : ses annotations
Pydantic, champs obligatoires ou optionnels, valeurs par défaut, contraintes et
validateurs internes ne peuvent donc pas être vérifiés.

Les types ci-dessous sont déduits des exemples de valeurs du JSON demandé. Ils
ne prouvent pas les types exacts déclarés dans le modèle disparu.

## Champs racine attendus par le prompt

| Champ | Type suggéré par l'exemple | Provenance / sens demandé | Présence dans le format demandé |
|---|---|---|---|
| `numero_facture` | chaîne (`str`) | Numéro de facture; exemple de remplacement `"FA-INCONNU"` si inconnu. | Présent |
| `date_emission` | chaîne de date (`str`, format illustré `YYYY-MM-DD`) | Date d'émission extraite du texte source. | Présent |
| `fournisseur` | objet | Informations du vendeur extraites du texte. Contient `nom`. | Présent |
| `client` | objet | Informations de l'acheteur extraites du texte. Contient `nom`. | Présent |
| `lignes` | liste d'objets | Détail des articles/prestations extraits du texte. Structure d'une ligne ci-dessous. | Présent |
| `total_ht` | nombre (exemple `0.0`, donc probablement `float`) | Total hors taxes demandé dans la facture. | Présent |
| `montant_tva` | nombre (exemple `0.0`, donc probablement `float`) | Montant total de TVA demandé dans la facture. | Présent |
| `total_ttc` | nombre (exemple `0.0`, donc probablement `float`) | Total toutes taxes comprises demandé dans la facture. | Présent |
| `devise` | chaîne (`str`) | Devise de la facture; exemple `"EUR"`. | Présent |

## Sous-objets attendus

### `fournisseur`

| Champ | Type suggéré | Provenance / sens demandé |
|---|---|---|
| `nom` | chaîne (`str`) | Nom du vendeur, extrait du texte brut. |

### `client`

| Champ | Type suggéré | Provenance / sens demandé |
|---|---|---|
| `nom` | chaîne (`str`) | Nom de l'acheteur, extrait du texte brut. |

### Chaque élément de `lignes`

| Champ | Type suggéré par l'exemple | Provenance / sens demandé |
|---|---|---|
| `description` | chaîne (`str`) | Nom ou description de l'article/prestation. |
| `quantite` | nombre (exemple `1.0`, probablement `float`) | Quantité de la ligne. |
| `prix_unitaire_ht` | nombre (exemple `0.0`, probablement `float`) | Prix unitaire hors taxes. |
| `total_ht` | nombre (exemple `0.0`, probablement `float`) | Total hors taxes de la ligne. |
| `taux_tva` | nombre (exemple `20.0`, probablement `float`) | Taux de TVA appliqué à la ligne. |

## Provenance des valeurs

Le prompt donne à l'extraction le texte suivant comme source métier :

- `document.raw_text` : texte brut à transformer en données de facture;
- `document.profile_type` et `document.extraction_tool` : métadonnées incluses
  dans l'instruction sous la forme « Origine du texte ».

Ces trois valeurs proviennent de l'objet `DocumentResult` utilisé par
`extract_to_json`. Elles ne sont pas présentées dans le JSON de facture
demandé. Les valeurs d'exemple (par exemple `"EUR"`, `0.0` et
`"FA-INCONNU"`) sont des exemples/instructions du prompt; elles ne constituent
pas la preuve de valeurs par défaut définies dans `InvoiceSchema`.

Le prompt ne précise pas si les champs ou objets peuvent être absents, nuls,
vides ou répétés autrement que par l'exemple d'une liste `lignes` contenant un
élément.

## Validations réellement visibles dans `ExtractionManager`

1. **Nettoyage de sortie** : retire les délimiteurs Markdown si la réponse
   commence par une clôture ` ```json ` ou ` ``` `.
2. **Syntaxe JSON** : `json.loads(raw_json)` doit pouvoir analyser la réponse.
   Une erreur `JSONDecodeError` produit un résultat d'erreur avec le texte
   brut de sortie.
3. **Validation de modèle** : `_validate_data` appelle
   `InvoiceSchema(**data)`. Si l'instanciation réussit, le résultat est renvoyé
   sous `data` via `model_dump()`.
4. **Échec de validation Pydantic** : une `ValidationError` est convertie en
   statut `validation_failed`, avec les données extraites et les messages
   d'erreur.
5. **Autres exceptions dans l'extraction** : `extract_to_json` intercepte les
   autres exceptions du bloc d'extraction et renvoie `critical_error`.

Le message de succès mentionne une vérification de cohérence de l'équation
comptable. Toutefois, aucune équation ni règle arithmétique n'est calculée
directement dans `ExtractionManager`; une telle règle ne peut être attribuée
au modèle absent sur la seule base de ce fichier.

## Ce qui ne peut pas être reconstruit depuis ce fichier

- les types Pydantic exacts et les classes des sous-objets;
- quels champs sont obligatoires ou optionnels pour `InvoiceSchema`;
- les valeurs par défaut, alias, coercitions et règles de champs supplémentaires;
- les validateurs de format de date, devise, montants, lignes ou taux;
- les règles arithmétiques entre `total_ht`, `montant_tva`, `total_ttc` et les
  montants des lignes;
- le comportement du modèle face à des champs manquants ou non reconnus.

**Conclusion :** le contrat reconstructible est le JSON à neuf champs racine,
avec objets imbriqués `fournisseur`/`client` et une liste de lignes telle que
décrite ci-dessus. Il s'agit du contrat demandé au modèle d'extraction, pas
d'une preuve du schéma Pydantic exact de `InvoiceSchema`.
