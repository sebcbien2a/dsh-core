# Plan du benchmark ExtractionManager vs NuExtract3

## Objectif et périmètre

Comparer l'extraction brute des deux extracteurs sur les mêmes textes OCR et la
même vérité terrain, pour six champs : fournisseur, date, HT, TVA, TTC et SIRET.
Le runner ne doit pas effectuer l'OCR, la réconciliation des montants, la
classification TVA ni l'application de règles métier ou de skills. Ces étapes
modifieraient les résultats bruts et empêcheraient une comparaison équitable.

Ce document décrit le futur runner ; il ne constitue ni un jeu de données ni un
benchmark exécutable.

## Entrée OCR commune

Préparer un jeu de cas versionné. Chaque cas contient au minimum :

- un identifiant stable de document ;
- le texte OCR brut, stocké une seule fois et partagé sans modification entre
  les deux appels ;
- la vérité terrain des six champs, avec possibilité de valeur inconnue ;
- les métadonnées utiles à l'analyse (par exemple format ou source), sans
  informations propres à un seul extracteur.

Le runner appelle les deux extracteurs avec le même texte. Pour
ExtractionManager, construire le `DocumentResult` attendu en conservant le
texte dans `raw_text` et en fournissant les métadonnées nécessaires au prompt.
Pour NuExtract3, fournir le texte au prompt avec un template de sortie commun.
Ne pas relancer le routage ni l'OCR dans l'un ou l'autre chemin.

Un texte OCR vide ou absent est marqué `input_missing` et n'est pas envoyé aux
extracteurs. Le runner conserve les erreurs d'appel, de parsing et de validation
dans le rapport, sans les transformer en résultats valides.

## Adaptation des sorties vers InvoiceSchema

Créer un adaptateur par extracteur vers une représentation commune, puis
valider les données compatibles avec `InvoiceSchema` via
`ExtractionManager.validate_invoice`. Pour l'entrée ExtractionManager, réutiliser
son interface existante de validation et préserver son statut. Pour NuExtract3,
parser sa réponse JSON, convertir les noms et la structure de ses champs vers
le contrat canonique, puis effectuer la même validation.

Les tests consultés montrent notamment les chemins canoniques suivants :
`supplier.name`, `reference.issue_date`, `amount.net_amount`,
`amount.tax_amount` et `amount.gross_amount`. Les adaptateurs doivent partir
des champs effectivement présents dans chaque sortie et ne pas compléter une
valeur manquante par déduction ou par une règle métier. Les champs dérivés ou
calculés ne doivent pas être attribués à l'extracteur.

### Traitement du SIRET

Le SIRET reste une valeur extraite indépendante dans l'enveloppe commune du
benchmark, jusqu'à confirmation d'un champ canonique correspondant dans
`InvoiceSchema`. Le demander explicitement dans le template NuExtract3 et le
récupérer dans la sortie ExtractionManager si disponible ; ne pas inventer un
champ dans le schéma canonique ni inclure le SIRET dans la validation Pydantic
sans contrat existant.

Pour comparer, normaliser uniquement la représentation : retirer espaces et
séparateurs usuels et comparer les 14 chiffres. Une valeur absente, illisible
ou de longueur différente reste une extraction invalide/manquante ; ne pas
corriger les chiffres ni substituer un SIREN. Enregistrer la valeur brute et la
valeur normalisée pour faciliter l'audit.

## Champs et règles de comparaison

Comparer chaque sortie à la vérité terrain au niveau champ :

| Champ | Normalisation proposée | Critère principal |
|---|---|---|
| Fournisseur | espaces périphériques supprimés, espaces internes regroupés, casse neutralisée | égalité normalisée |
| Date | conversion vers `YYYY-MM-DD` si la date est non ambiguë | égalité de date |
| HT | nombre décimal en EUR, sans arrondi prématuré | écart absolu inférieur ou égal à la tolérance configurée |
| TVA | même règle que HT ; une absence attendue reste distincte de zéro | égalité à tolérance configurée |
| TTC | même règle que HT | écart absolu inférieur ou égal à la tolérance configurée |
| SIRET | comparaison des 14 chiffres après retrait des séparateurs | égalité exacte normalisée |

La tolérance monétaire doit être une option explicite du benchmark, enregistrée
dans le rapport (valeur proposée : 0,02 EUR). Elle s'applique uniformément à
HT, TVA et TTC. Conserver aussi les valeurs brutes et l'écart calculé.

Ne pas utiliser de réconciliation HT + TVA = TTC pour modifier les prédictions.
Les contrôles de cohérence peuvent être rapportés séparément, mais ne changent
pas le score de l'extraction brute.

## Cas manquants et erreurs

- **Vérité terrain inconnue** : champ exclu du dénominateur de ce champ et
  marqué `ground_truth_missing` ; jamais compté comme correct.
- **Valeur prédite absente ou nulle** alors que la vérité terrain existe :
  `missing_prediction`, comptée incorrecte.
- **Champ non fourni par le modèle** : `unsupported` ou `missing_prediction`,
  compté comme échec de couverture, et non retiré du dénominateur.
- **JSON invalide / erreur modèle / délai dépassé** : statut explicite au niveau
  extraction ; les champs évaluables sont comptés sans prédiction.
- **Document OCR vide** : exclu des scores d'extraction et comptabilisé dans
  les entrées ignorées.
- **Échec de validation InvoiceSchema** : conserver la sortie et les erreurs ;
  rapporter séparément le statut de validation. Ne pas supprimer le cas de la
  comparaison brute si ses champs peuvent être adaptés sans ambiguïté.

## Format du rapport futur

Produire un rapport JSON réutilisable, accompagné éventuellement d'un résumé
lisible :

```json
{
  "benchmark": "ExtractionManager vs NuExtract3",
  "dataset_version": "à renseigner",
  "settings": {
    "monetary_tolerance_eur": 0.02,
    "ocr_shared": true,
    "postprocessing_enabled": false
  },
  "extractors": {
    "ExtractionManager": {
      "model": "à renseigner",
      "documents": []
    },
    "NuExtract3": {
      "model": "à renseigner",
      "documents": []
    }
  },
  "summary": {}
}
```

Chaque entrée de `documents` doit inclure l'identifiant, le statut d'appel,
le statut de parsing et de validation, ainsi que pour chacun des six champs :
vérité terrain, valeur brute, valeur normalisée, résultat (`correct`,
`incorrect`, `missing_prediction`, `ground_truth_missing` ou `unsupported`) et,
pour les montants, écart numérique. Inclure les erreurs sans contenu sensible
inutile.

## Métriques

Calculer séparément pour chaque extracteur et chaque champ :

1. **Couverture** : prédictions comparables / cas où la vérité terrain est
   connue.
2. **Exactitude du champ** : prédictions correctes / cas où la vérité terrain
   est connue. Les prédictions manquantes restent au dénominateur et sont
   incorrectes.
3. **Taux de validation canonique** : sorties acceptées par `InvoiceSchema` /
   sorties ayant atteint l'étape de validation.
4. **Taux d'erreur d'extraction** : erreurs d'appel ou de parsing / tentatives.
5. **Exactitude complète des six champs** : documents avec les six champs
   corrects / documents dont les six vérités terrain sont connues.

Rapporter les comptes (correct, incorrect, manquant, vérité inconnue) avec
chaque taux. La latence peut être enregistrée par extracteur et résumée par
médiane et moyenne lors d'une exécution, sans l'utiliser comme critère
d'exactitude.

## Modifications nécessaires avant exécution

- Ajouter le runner et ses tests d'adaptateurs, de normalisation et de métriques.
- Créer le jeu de textes OCR figés avec une vérité terrain relue et une version
  du dataset.
- Confirmer comment obtenir le SIRET dans les deux sorties et s'il existe un
  champ canonique dans `InvoiceSchema` ; conserver l'enveloppe séparée sinon.
- Confirmer le template NuExtract3 et les métadonnées minimales du
  `DocumentResult` sans déclencher les modèles.
- Enregistrer versions/configurations des modèles et la tolérance dans chaque
  rapport. Lors d'une future exécution, s'assurer que les deux extracteurs
  reçoivent strictement la même entrée OCR.

**État :** squelette de spécification seulement ; aucun runner ni benchmark
exécutable n'est créé par ce document.
