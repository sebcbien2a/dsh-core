# Benchmark d’orientation OCR

## Objectif

Déterminer automatiquement l’orientation correcte d’un document parmi 0, 90,
180 et 270 degrés.

- Les documents sources restent dans `workspace/invoices/input` et hors Git.
- Le manifeste versionné ne contient aucune donnée métier extraite.
- Les rotations candidates doivent être produites en mémoire ; aucun fichier
  source ne doit être réécrit.
- Les métadonnées PDF ne suffisent pas : les PDF visuellement tournés déclarent
  actuellement une rotation de 0 degré.
- La vérité terrain est `expected_orientation_degrees` dans le manifeste.
- Les pages multiples seront évaluées page par page lors d’une phase ultérieure.
- Surya OCR ne doit pas être réintroduit sans benchmark démontrant un gain.

## Corpus actuel

- 10 pièces : 9 PDF et 1 JPEG.
- Répartition : 7 à 0 degré, 1 à 90 degrés, 1 à 180 degrés et 1 à 270 degrés.
- Une paire représente le même ticket sous deux formats et orientations.

Le manifeste est `tests/fixtures/orientation_corpus_manifest.json`.

## Futures variantes

Ces variantes sont décrites pour une étape ultérieure et ne sont pas exécutées ici.

- **A — Métadonnées et géométrie seulement :** estimer l’orientation sans OCR.
- **B — Score texte/OCR :** évaluer les quatre rotations candidates et retenir
  l’orientation selon un score défini.
- **C — Sélection Vision :** demander à un modèle Vision de sélectionner
  l’orientation parmi les quatre candidates.

## Mesures futures

- Orientation correcte, exactitude par document et exactitude par page.
- Texte exploitable.
- Durée par rotation et durée totale.
- Nombre d’appels modèle et pic VRAM.
- Erreurs et abstention en cas d’incertitude.

## Règle de décision

- Ne supprimer aucune rotation parmi 0/90/180/270 avant résultat mesuré.
- Ne modifier aucun pipeline dans cette étape.
- Ne jamais appliquer de correction permanente aux documents sources.
- Réaliser le benchmark local uniquement lors de l’étape suivante.
