# Origine retrouvée du contrat `InvoiceSchema`

## Conclusion

Aucune déclaration de `class InvoiceSchema` n'a été trouvée dans le dépôt
recherché. Aucune déclaration de `class InvoiceLine`, `class LigneFacture` ou
`class Client` n'a été trouvée non plus.

La structure historique la plus proche est le contrat JSON décrit dans le
prompt du `invoice_agent.py` à la racine, puis illustré par
`workspace/invoices/output_json/test_facture.json`. Le même contrat est
consommé par `invoice_indexer.py`. `services/extraction_manager.py` demande une
variante plus réduite de ce JSON. Ces indices montrent la forme attendue par
les appelants; ils ne restituent pas la définition Pydantic disparue ni ne
prouvent que `InvoiceSchema` ait été déclarée sous ce nom.

## Fichiers trouvés

| Fichier | Contenu pertinent | Lien avec le contrat recherché |
|---|---|---|
| `invoice_agent.py` | Importe `InvoiceSchema` depuis `schemas.invoice_schema`; son prompt Vision donne un JSON avec fournisseur/client imbriqués, lignes, totaux et devise. | **Indice structurel le plus complet** de la forme visée par l'agent racine. |
| `services/extraction_manager.py` | Importe aussi `InvoiceSchema`; son prompt demande un objet fournisseur/client, une liste `lignes` et des totaux. | **Indice direct** du contrat attendu par ExtractionManager, mais version réduite de celui du prompt racine. |
| `workspace/invoices/output_json/test_facture.json` | Exemple JSON complet avec les champs du prompt racine, plus `statut_validation_math`. | Exemple concret correspondant à la structure imbriquée; ce n'est pas une déclaration de modèle ni une preuve de validation Pydantic. |
| `invoice_indexer.py` | Accède à `fournisseur["nom"]`, `client["nom"]`, `lignes`, `date_emission`, `total_ht`, `total_ttc` et d'autres champs du JSON. | Confirme que cette forme est attendue par le code d'indexation facture. |
| `agents/invoice_parser_agent.py` | Produit un dictionnaire d'une autre forme : `fournisseur` chaîne, `date_emission`, `echeance`, `mode_reglement`, `lignes_articles`, aperçu brut. | Variante non compatible telle quelle avec le contrat imbriqué; ne confirme pas `InvoiceSchema`. |
| `agents/invoice_agent.py` | Déclare une classe locale `InvoiceData` plate avec `fournisseur: str`, `date_facture`, montants et une validation HT+TVA=TTC. | Modèle existant homonyme dans sa fonction métier, mais structure différente du contrat imbriqué. |
| `schemas/invoice_schema.py` | Déclare `InvoiceData`, plat, avec `fournisseur: str`, `date_facture`, `montant_ht`, `montant_ttc`, champs fiscaux et validations métier. | Le fichier cible de l'import ne contient pas `InvoiceSchema`; le modèle présent n'est pas la structure imbriquée attendue par les prompts. |
| `test_experimental_multitva.py` | Déclare `LigneTVA` et `InvoiceDataExperimental`, modèles de facture plats avec ventilation `lignes_tva`. | Modèle de test explicitement expérimental, autre structure et autre vocabulaire; pas `InvoiceSchema`. |
| `backup/v8_6_2_stable/invoice_schema_v8_6_2.py` | Déclare `InvoiceData`, modèle plat historique. | N'est pas une déclaration `InvoiceSchema` et ne contient pas les sous-objets client/fournisseur ni les lignes du JSON imbriqué. |
| `docs/02_ROADMAP.md` | Contient deux occurrences de texte `class Supplier` associées à des champs fournisseur (`supplier_id`, `raison_sociale`, SIRET, TVA, etc.). | Description d'une entité fournisseur de roadmap, pas une classe facture démontrée ni le modèle imbriqué `fournisseur.nom`. |

## Classes recherchées

Recherche dans le dépôt des déclarations de classe demandées :

| Nom recherché | Résultat |
|---|---|
| `Supplier` | Aucune déclaration Python `class Supplier` trouvée. Deux occurrences textuelles de `class Supplier` dans `docs/02_ROADMAP.md` décrivent une entité fournisseur de roadmap. Le code contient `SupplierRepository`, qui est un repository, pas le modèle fournisseur demandé. |
| `Client` | Aucune déclaration de classe trouvée. |
| `InvoiceLine` | Aucune déclaration de classe trouvée. |
| `LigneFacture` | Aucune déclaration de classe trouvée. |
| `InvoiceSchema` | Aucune déclaration ni assignation de classe trouvée. Le nom est uniquement utilisé par des imports/appels et dans les rapports. |

Modèles voisins effectivement déclarés :

- `agents/invoice_agent.py::InvoiceData` : modèle local plat;
- `schemas/invoice_schema.py::InvoiceData` : modèle plat avec contrôles fiscaux
  et bancaires;
- `test_experimental_multitva.py::InvoiceDataExperimental` et
  `LigneTVA` : modèle isolé de test pour la ventilation multi-TVA;
- `backend/app/n8n_api.py::InvoicePayload` : modèle d'entrée du webhook
  backend, avec `filename`, `file_path` et `source`, et non un schéma de facture.

## Structures trouvées

### 1. Contrat demandé par le prompt racine

Dans `invoice_agent.py`, le prompt demande :

| Champ | Forme/type montré dans le prompt | Valeur/provenance attendue |
|---|---|---|
| `numero_facture` | chaîne | Numéro extrait. |
| `date_emission` | chaîne `YYYY-MM-DD` | Date d'émission. |
| `date_echeance` | chaîne date ou `null` | Échéance. |
| `fournisseur` | objet | Partie vendeuse. |
| `fournisseur.nom` | chaîne | Nom du vendeur. |
| `fournisseur.siret` | chaîne ou `null` | SIRET du vendeur. |
| `fournisseur.numero_tva` | chaîne ou `null` | Numéro de TVA du vendeur. |
| `fournisseur.adresse` | chaîne ou `null` | Adresse du vendeur. |
| `client` | objet | Partie cliente. |
| `client.nom` | chaîne | Nom de l'acheteur. |
| `client.siret` | chaîne ou `null` | SIRET de l'acheteur. |
| `client.adresse` | chaîne ou `null` | Adresse de l'acheteur. |
| `lignes` | liste d'objets | Détail des articles/prestations. |
| `lignes[].description` | chaîne | Désignation. |
| `lignes[].quantite` | `float` | Quantité. |
| `lignes[].prix_unitaire_ht` | `float` | Prix unitaire HT. |
| `lignes[].total_ht` | `float` | Total HT de la ligne. |
| `lignes[].taux_tva` | `float` | Taux TVA de la ligne. |
| `total_ht` | `float` | Total HT facture. |
| `montant_tva` | `float` | Montant TVA facture. |
| `total_ttc` | `float` | Total TTC facture. |
| `devise` | chaîne, exemple `EUR` | Devise. |

Ces types sont les annotations/valeurs du prompt, non des annotations extraites
d'une classe `InvoiceSchema`.

### 2. Contrat demandé par ExtractionManager

Le prompt de `services/extraction_manager.py` demande les champs racine
`numero_facture`, `date_emission`, `fournisseur`, `client`, `lignes`,
`total_ht`, `montant_tva`, `total_ttc` et `devise`.

- `fournisseur` et `client` sont chacun des objets qui ne montrent que `nom`;
- chaque objet dans `lignes` montre `description`, `quantite`,
  `prix_unitaire_ht`, `total_ht` et `taux_tva`;
- les montants, quantités et taux sont donnés en exemples numériques décimaux;
- le prompt n'inclut pas `date_echeance`, SIRET, numéro de TVA ni adresse.

Cette structure partage son noyau avec le contrat du prompt racine, mais ne
montre pas tous ses champs. Elle ne prouve pas que le modèle disparu déclarait
ces champs comme obligatoires, optionnels ou avec valeurs par défaut.

### 3. Exemple JSON enregistré

`workspace/invoices/output_json/test_facture.json` correspond au prompt racine
et contient en plus :

- `date_echeance: null`;
- `fournisseur.siret`, `fournisseur.numero_tva`,
  `fournisseur.adresse`;
- `client.siret` et `client.adresse`;
- `statut_validation_math: true`.

Cet exemple confirme la forme des objets, mais le dernier champ n'apparaît pas
dans les prompts d'ExtractionManager ni dans le modèle plat actuel. Il peut
provenir d'une étape ou d'une version différente; le dépôt consulté ne permet
pas de l'attribuer à `InvoiceSchema`.

## Correspondances avec le contrat reconstruit

Le contrat précédemment reconstruit depuis `services/extraction_manager.py`
correspond exactement à sa structure JSON demandée. La comparaison avec les
autres traces donne :

| Élément reconstruit | `invoice_agent.py` racine | JSON de test | `invoice_indexer.py` | Conclusion |
|---|---|---|---|---|
| `numero_facture` | Même champ | Présent | Lu | Correspondance directe. |
| `date_emission` | Même champ | Présent | Lu | Correspondance directe. |
| `fournisseur` objet avec `nom` | Objet plus riche | Objet plus riche | `fournisseur["nom"]` lu | Correspondance de structure. |
| `client` objet avec `nom` | Objet plus riche | Objet plus riche | `client["nom"]` lu | Correspondance de structure. |
| `lignes[]` et ses cinq champs | Même structure et mêmes noms | Présents dans l'exemple | Lit `description`/`quantite` pour le texte; conserve tout le JSON en payload | Correspondance directe. |
| `total_ht`, `montant_tva`, `total_ttc`, `devise` | Mêmes noms | Présents | Lus et conservés | Correspondance directe. |
| `date_echeance`, SIRET, TVA fournisseur, adresses | Demandés par le prompt racine, omis du prompt ExtractionManager | Présents, parfois `null` | Non nécessaires aux accès montrés | Présence dans la variante complète seulement. |
| `statut_validation_math` | Non demandé dans le prompt racine | Présent | Non lu directement | Champ d'exemple sans origine de modèle démontrée. |

En revanche, `schemas/invoice_schema.py::InvoiceData` ne correspond pas au
contrat reconstruit sur les points de structure suivants : fournisseur de type
chaîne au lieu d'objet, `date_facture` au lieu de `date_emission`, absence de
`client` et `lignes`, et `montant_ht`/`montant_ttc` au lieu de
`total_ht`/`total_ttc`.

## Origine la plus probable et limites de preuve

La convergence du prompt complet de `invoice_agent.py` racine, de l'exemple
`test_facture.json` et des accès du `invoice_indexer.py` indique que la forme
imbriquée fournisseur/client/lignes est un contrat facture effectivement
utilisé dans plusieurs fichiers. Le prompt d'ExtractionManager en reprend une
variante plus réduite.

**Conclusion limitée :** cette famille de fichiers est la meilleure trace de
l'ancien contrat attendu par l'import `InvoiceSchema`. Aucun fichier trouvé ne
prouve le contenu exact de la classe Pydantic disparue, ses types Python réels,
ses champs requis/optionnels, ses validateurs ou si `statut_validation_math`
en faisait partie.
