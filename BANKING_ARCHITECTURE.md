# Architecture bancaire — DSH-Core

## Objet et statut

Ce document décrit comment positionner `ofxparse`, `mt940` et XGrammar autour
des points d'extension déjà présents dans DSH-Core. Il ne modifie aucun code et
ne prétend pas que le pipeline bancaire soit déjà implémenté.

Les statuts ci-dessous séparent le code actuellement observable des étapes
futures demandées :

- **Présent** : défini et raccordé dans le code actuellement examiné.
- **Prévu** : décrit dans la roadmap ou demandé comme étape future, mais non
  implémenté/raccordé.
- **À valider** : la compatibilité ou le contrat n'est pas établi par les
  sources disponibles.

## 1. Base actuelle dans DSH-Core

### Ingestion partagée

`services/ingestion_manager.py::IngestionManager` expose le point d'entrée
`ingest(file_path, source)`, détermine un type d'asset puis route selon
`AssetType`.

Pour le bancaire :

1. `detect_asset_type` classe en `AssetType.BANK_STATEMENT` si le chemin
   contient `banque` ou `releve`, ou se termine par `.csv`.
2. La branche `BANK_STATEMENT` retourne actuellement
   `status="pending_implementation"` et `target_ledger="banking_bus"`.
3. Aucun parseur bancaire ni BankingAgent n'est appelé dans cette branche.

`AssetType.BANK_STATEMENT` et `bank_ledger` constituent donc le raccordement
existant à conserver. La classification par nom de fichier et l'acceptation
générique des CSV ne vérifient pas à elles seules que le contenu est réellement
un relevé bancaire ni son format.

### Éléments bancaires déjà prévus, non présents comme code raccordé

La roadmap décrit pour la phase bancaire :

- un `banking_agent.py`;
- un `banking_ledger_repository.py` et un `banking_ledger_service.py`;
- un schéma `banking_record.py`;
- un `BankingLedger.db`;
- des données minimales du ledger : date, montant, libellé, compte et document
  associé.

La recherche du code courant n'a trouvé aucun `BankingAgent`,
`BankingRecord`, repository/service BankingLedger ni workflow bancaire
LangGraph. `schemas/bank_statement_schema.py` existe mais est vide (0 octet).
Le chemin de facture Vision et le graphe LangGraph facture ne sont donc pas un
pipeline bancaire existant.

## 2. Rôle des bibliothèques demandées

| Bibliothèque | Rôle dans le pipeline bancaire | Limites de rôle |
|---|---|---|
| `ofxparse` | Lire un relevé OFX et en extraire le compte, les soldes/périodes et les transactions exposés par le parseur. | Ne parse pas le format MT940; ne réalise pas de rapprochement comptable. |
| `mt940` (`mt-940` sur PyPI) | Lire un relevé au format MT940 et exposer ses métadonnées et transactions. | Ne parse pas OFX; le parseur ne calcule pas et ne rapproche pas les soldes. |
| XGrammar | Contraindre la forme des sorties structurées produites par un modèle pendant le décodage, par exemple une décision/résultat JSON d'un nœud de rapprochement. | N'est ni un parseur de relevé OFX/MT940 ni une preuve de validité métier ou comptable. |

`ofxparse` et `mt940` sont deux lecteurs de formats distincts. Le choix se fait
sur le format réel du fichier, non par une cascade qui essaierait d'interpréter
tout fichier comme OFX puis MT940.

La roadmap mentionne aussi CSV bancaire et PDF bancaire. Ces formats ne sont
pas couverts par les deux parseurs décrits ici; ce document ne leur attribue
pas de parseur ni de comportement. Leur traitement reste hors du pipeline
OFX/MT940 décrit.

## 3. Pipeline bancaire séparé — cible documentaire

```text
Entrée existante
  IngestionManager.ingest(file_path, source)
       |
       +-- AssetType.BANK_STATEMENT
              |
              v
      branche bancaire d'IngestionManager
              |
              v
      BankingAgent prévu par la roadmap
              |
              +-- fichier OFX   -> ofxparse
              |
              +-- fichier MT940 -> mt940
              |
              v
      normalisation vers le contrat BankingRecord/BankingLedger prévu
              |
              v
      BankingLedger prévu
              |
              v
      futur rapprochement bancaire LangGraph
              |
              +-- lecture des écritures bancaires
              +-- comparaison aux documents/factures disponibles
              +-- résultat de rapprochement et association au document
              +-- résultat transmis au ledger
```

Ce diagramme exprime la séparation de responsabilités demandée; à ce jour,
seuls l'entrée `IngestionManager`, `AssetType.BANK_STATEMENT` et la réponse
`pending_implementation` existent dans cette branche. Les composants indiqués
comme prévus ne sont pas présentés comme présents.

### Séparation avec les factures

```text
FACTURE
IngestionManager -> AssetType.INVOICE -> InvoiceAgent -> Ollama Vision

BANQUE
IngestionManager -> AssetType.BANK_STATEMENT
                 -> ofxparse (OFX) ou mt940 (MT940)
                 -> BankingLedger
                 -> futur graphe LangGraph de rapprochement bancaire
```

Le flux bancaire ne doit pas appeler l'agent Vision facture pour lire OFX ou
MT940. Les fichiers PDF bancaires ne sont pas couverts par ce flux de parseurs.

## 4. Contrat de données et normalisation

Les deux parseurs produisent des objets adaptés à leur format source. Le flux
doit conserver leur origine et présenter au ledger le contrat bancaire prévu
par la roadmap, qui cite au minimum :

- date;
- montant;
- libellé;
- compte;
- document associé.

La description présente ne fixe pas les types, règles de signe, devise,
identifiant de transaction, fuseau/date, champs optionnels, ni les
représentations exactes de ces données dans `BankingRecord`. Elle ne définit
pas non plus de correspondance exhaustive entre les champs propres aux
parseurs OFX/MT940 et ce schéma. Le fichier `schemas/bank_statement_schema.py`
vide ne fournit pas de contrat exécutable.

En conséquence, la normalisation est une frontière conceptuelle du pipeline,
pas un mapping détaillé ni une nouvelle classe décrite ici. Le contrat de
`banking_record.py` mentionné par la roadmap et le mapping parser-vers-ledger
doivent être établis avant de déclarer les parseurs interopérables.

## 5. Positionnement de XGrammar

XGrammar intervient seulement sur une sortie structurée générée par un modèle,
pas sur la lecture directe du relevé :

```text
OFX/MT940 -> parseur déterministe -> transactions structurées
                                      |
                                      v
                    futur nœud LangGraph de rapprochement
                                      |
                                      +-> si une génération de décision
                                          structurée est utilisée :
                                          XGrammar contraint sa forme
```

Une contrainte de grammaire garantit la structure permise de la sortie
générée; elle ne garantit pas que la transaction est correctement rapprochée,
qu'un montant correspond à une facture, ni que les données du relevé sont
vraies. Les règles de rapprochement et leurs validations restent distinctes.

Le code courant utilise Ollama dans l'agent facture; aucune intégration XGrammar
n'est observée. La documentation officielle de XGrammar présente des
intégrations avec plusieurs moteurs d'inférence, mais ne démontre pas ici une
compatibilité directe avec l'appel Ollama de DSH-Core. Le couple
XGrammar/moteur d'inférence doit donc être considéré **à valider**; aucun
remplacement de l'inférence actuelle ni adaptateur n'est présumé dans cette
architecture.

## 6. Futur rapprochement bancaire LangGraph

Le dépôt contient un graphe LangGraph facture, mais aucune définition de graphe
de rapprochement bancaire n'a été trouvée. Le rapprochement bancaire est donc
**prévu**, pas actif.

Le flux futur attendu, une fois ses contrats existants prévus disponibles, est :

1. recevoir les mouvements déjà parsés et normalisés depuis le chemin bancaire;
2. comparer les mouvements aux documents/factures disponibles et aux données
   conservées dans le ledger;
3. produire un résultat de rapprochement structuré;
4. conserver l'association au document et le résultat dans le contexte du
   ledger bancaire.

Les décisions de correspondance, les règles de montant/date, les états
« rapproché/non rapproché », le traitement des paiements groupés et la
validation humaine ne sont pas spécifiés par les composants bancaires
actuellement présents; ils ne sont donc pas détaillés comme comportements déjà
établis.

## 7. État récapitulatif

| Élément | État constaté |
|---|---|
| `IngestionManager.ingest` | Présent; point d'entrée partagé. |
| `AssetType.BANK_STATEMENT` | Présent; branche reconnue mais encore `pending_implementation`. |
| `bank_ledger = "banking_bus"` | Présent comme nom logique; aucun ledger bancaire applicatif raccordé n'a été trouvé. |
| `ofxparse` | Bibliothèque demandée; non observée dans le code courant ni dans les manifestes de dépendances trouvés. |
| `mt940` | Bibliothèque demandée; non observée dans le code courant ni dans les manifestes de dépendances trouvés. |
| XGrammar | Bibliothèque demandée; non observée dans le code courant; compatibilité avec l'inférence DSH-Core non établie. |
| `BankingAgent`, `BankingRecord`, BankingLedger | Prévus par la roadmap, pas trouvés comme composants Python raccordés; le fichier `schemas/bank_statement_schema.py` est vide. |
| Rapprochement bancaire LangGraph | Futur; aucun graphe bancaire trouvé. |
| Pipeline Vision facture | Existant séparément; ne constitue pas le parseur des relevés OFX/MT940. |

## Références documentaires

- `services/ingestion_manager.py` — point d'entrée, `AssetType` et branche
  bancaire actuelle.
- `schemas/bank_statement_schema.py` — fichier présent, vide au moment de
  l'analyse.
- `docs/02_ROADMAP.md` — description des éléments BankingLedger et du
  `banking_agent.py` prévus.
- OFXParse : <https://github.com/jseutter/ofxparse>
- mt940 : <https://github.com/WoLpH/mt940>
- XGrammar : <https://github.com/mlc-ai/xgrammar>
