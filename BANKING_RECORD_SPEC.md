# Spécification cible — `BankingRecord`

## 1. Statut et périmètre

`schemas/bank_statement_schema.py` est vide : aucun contrat bancaire
exécutable n'existe actuellement dans ce fichier. Le présent document propose
un contrat conceptuel commun aux transactions parsées par `ofxparse` (OFX) ou
`mt940` (MT940). Il ne génère ni code ni nouvelle implémentation.

La proposition s'appuie sur :

- `BANKING_ARCHITECTURE.md` : pipeline bancaire distinct, point d'entrée
  `IngestionManager`, et séparation parsing/ledger/rapprochement;
- `services/ingestion_manager.py` : `AssetType.BANK_STATEMENT`, branche encore
  `pending_implementation`, `bank_ledger = "banking_bus"` et identifiant
  `asset_id` attribué à l'ingestion;
- `docs/02_ROADMAP.md` : champs minimaux attendus dans le BankingLedger :
  date, montant, libellé, compte et document associé;
- les modèles de transactions exposés par les bibliothèques documentées.

Les champs marqués **obligatoires** sont requis dans un `BankingRecord`
normalisé prêt à être conservé dans le BankingLedger. Si une source ne fournit
pas une valeur, il faut la résoudre depuis le contexte du relevé ou laisser
l'enregistrement en erreur/attente de revue; il ne faut pas inventer la valeur.

## 2. Champs obligatoires

| Champ proposé | Type conceptuel | Définition et règle |
|---|---|---|
| `transaction_date` | Date civile (`date`) | Date de valeur ou date transactionnelle principale du mouvement. Elle doit conserver sa sémantique source; ne pas la remplacer silencieusement par la date d'inscription. |
| `amount` | Décimal exact (`Decimal`) | Montant signé dans la devise source. Convention normalisée : débit négatif, crédit positif. Ne pas utiliser `float` pour la comptabilité. |
| `currency` | Code devise (`str`, ISO 4217 attendu) | Devise du montant. Si le parseur ne l'expose pas au niveau transaction, la résoudre depuis le relevé/compte; si elle reste inconnue, ne pas créer un record final silencieusement. |
| `label` | Chaîne (`str`) | Libellé lisible conservant les éléments disponibles de payee, memo ou détails. La valeur normalisée doit rester traçable à ses éléments source. |
| `account_id` | Chaîne (`str`) | Identifiant DSH-Core du compte associé au mouvement. Résoudre à partir de l'identifiant de compte source ou du contexte du relevé; conserver séparément la valeur source facultative. |
| `document_id` | Chaîne (`str`) | Identifiant du document/relevé ingéré auquel le mouvement appartient. Peut être rattaché au `asset_id` créé par `IngestionManager`, lorsque ce contexte est transmis au traitement bancaire. |
| `source_format` | Enumération (`OFX`, `MT940`) | Format effectivement détecté et parsé. Sert à interpréter correctement la provenance des champs. |

`account_id` et `document_id` sont obligatoires pour le record final selon les
besoins du BankingLedger décrits dans la roadmap. Le parseur seul peut ne pas
fournir ces identifiants sous la forme métier attendue; ils doivent alors venir
du contexte d'ingestion et de résolution de compte.

## 3. Champs optionnels

| Champ proposé | Type conceptuel | Utilité / provenance |
|---|---|---|
| `record_id` | Chaîne (`str`) | Identifiant stable attribué au record par le ledger; distinct d'un identifiant bancaire source. |
| `source_transaction_id` | Chaîne (`str \| None`) | Identifiant/référence de transaction fourni par la banque ou le parseur; absent ou non unique selon la source. |
| `booking_date` | Date civile (`date \| None`) | Date d'inscription comptable si distincte de `transaction_date`. |
| `account_reference` | Chaîne (`str \| None`) | Identifiant du compte tel que présenté dans le relevé, avant résolution vers `account_id`. |
| `counterparty_name` | Chaîne (`str \| None`) | Nom de l'émetteur/destinataire extrait des informations de transaction, quand disponible. |
| `remittance_information` | Chaîne (`str \| None`) | Motif, communication ou détails de paiement, conservés sans perte lorsqu'ils existent. |
| `bank_reference` | Chaîne (`str \| None`) | Référence bancaire distincte, si fournie. |
| `customer_reference` | Chaîne (`str \| None`) | Référence client distincte, si fournie. |
| `transaction_type` | Chaîne (`str \| None`) | Type/code de transaction de la source; sa signification reste dépendante du format et de la banque. |
| `source_status` | Chaîne (`str \| None`) | Marque débit/crédit/contre-passation d'origine, conservée pour audit même après normalisation du signe. |
| `check_number` | Chaîne (`str \| None`) | Numéro de chèque si fourni. |
| `source_payload` | Objet JSON (`dict \| None`) | Champs non normalisés utiles à la traçabilité; les données confidentielles doivent rester soumises aux règles de conservation applicables. |
| `statement_id` | Chaîne (`str \| None`) | Référence de relevé/page telle qu'exposée par le format source, si disponible; différente du `document_id` DSH-Core. |
| `reconciliation_status` | Enumération conceptuelle (`pending`, `matched`, `unmatched`, `review`) | État du rapprochement futur. Valeur initiale attendue : `pending`. |
| `matched_document_id` | Chaîne (`str \| None`) | Document métier associé par le rapprochement futur. Ne pas confondre avec le document du relevé (`document_id`). |
| `matched_amount` | Décimal exact (`Decimal \| None`) | Montant imputé au document associé, notamment si paiement partiel ou groupé. |
| `reconciliation_method` | Chaîne/enumération (`str \| None`) | Méthode/règle qui a conduit à l'association, par exemple référence, montant-date ou validation manuelle. |
| `reconciled_at` | Date-heure (`datetime \| None`) | Date et heure de validation du rapprochement, lorsqu'il a eu lieu. |
| `reconciled_by` | Chaîne (`str \| None`) | Identifiant de l'utilisateur/processus responsable de la validation, si disponible. |

Les champs de rapprochement sont des données d'état métier futures, pas des
valeurs produites par `ofxparse` ou `mt940`. Ils sont inclus ici pour identifier
les informations qu'un BankingRecord/ledger devra pouvoir porter lors du futur
workflow; ils ne signifient pas qu'un workflow ou ces statuts existent déjà.

## 4. Métadonnées de relevé à conserver hors transaction

Les parseurs exposent aussi des informations au niveau du relevé/compte. Elles
ne doivent pas être répétées dans chaque transaction sans raison; elles sont
associées au document d'ingestion ou au contexte de relevé.

| Métadonnée | Type conceptuel | Correspondance indicative |
|---|---|---|
| `statement_period_start` | Date (`date \| None`) | OFX `account.statement.start_date`; MT940 dérivable des dates de transactions ou métadonnées si présentes. |
| `statement_period_end` | Date (`date \| None`) | OFX `account.statement.end_date`; MT940 dérivable ou absente. |
| `opening_balance` | Décimal exact (`Decimal \| None`) | Solde d'ouverture source, si présent. |
| `closing_balance` | Décimal exact (`Decimal \| None`) | Solde de clôture source, si présent. |
| `statement_currency` | Code devise (`str \| None`) | Devise associée au relevé/aux soldes. |
| `institution_name` | Chaîne (`str \| None`) | Nom de banque/institution si exposé. |
| `institution_id` | Chaîne (`str \| None`) | Identifiant institutionnel source si exposé. |
| `statement_reference` | Chaîne (`str \| None`) | Référence/page relevé source si exposée. |

Les métadonnées, soldes et état de validation du relevé ne remplacent pas les
transactions. L'équation solde d'ouverture + mouvements = solde de clôture
constitue un contrôle de relevé distinct; aucun des parseurs cités ne doit être
considéré comme l'effectuant automatiquement.

## 5. Mapping OFX → `BankingRecord`

L'entrée de transactions OFX documentée est
`OfxParser.parse(file_object)`, puis `ofx.account.statement.transactions`.
Les champs disponibles varient selon le type de compte et le contenu OFX.

| Champ `BankingRecord` | Source `ofxparse` | Règle de correspondance |
|---|---|---|
| `transaction_date` | `transaction.date` | Date transactionnelle principale. |
| `booking_date` | `transaction.user_date` | Copier si présent et distinct de `transaction.date`; sinon absent. |
| `amount` | `transaction.amount` | Normaliser en `Decimal` sans conversion intermédiaire en flottant; normaliser le signe selon la convention retenue et conserver l'original si nécessaire. |
| `label` | `transaction.payee`, `transaction.memo` | Conserver les deux valeurs dans les champs dédiés ou dans le libellé structuré, sans supprimer le texte source. |
| `account_reference` | `ofx.account.account_id` | Identifiant compte source; résoudre séparément vers l'identifiant DSH `account_id`. |
| `source_transaction_id` | `transaction.id` | Copier si fourni; vérifier son unicité avant de l'utiliser comme clé idempotente. |
| `transaction_type` | `transaction.type` | Copier le type source sans lui attribuer une signification DSH non vérifiée. |
| `check_number` | `transaction.checknum` | Copier si présent. |
| `counterparty_name` | `transaction.payee` | Utilisation indicative; ne pas présumer que payee identifie toujours une contrepartie normalisée. |
| `remittance_information` | `transaction.memo` | Conserver la communication source. |
| `source_format` | Format du fichier effectivement parsé | `OFX`. |
| `document_id` | Contexte `IngestionManager` | Associer l'identifiant du document/relevé reçu, et non une valeur inventée par le parseur. |
| `account_id` | Résolution DSH à partir du contexte et/ou `ofx.account.account_id` | Requis dans le record normalisé; échec de résolution = état non final/revue, pas compte fictif. |
| `currency` | Devise de transaction/compte/relevé lorsqu'exposée | Vérifier la disponibilité précise sur les fichiers pris en charge; ne pas supposer un attribut universel si le relevé ne l'apporte pas. |

Métadonnées OFX de relevé à conserver si présentes : `account.statement.start_date`,
`account.statement.end_date`, `account.statement.balance`,
`account.statement.available_balance`, et informations d'institution associées
à `ofx.account.institution`.

## 6. Mapping MT940 → `BankingRecord`

La documentation de `mt940` expose un objet `Transactions` avec une liste de
`Transaction`; les champs de transaction sont dans `transaction.data`, les
métadonnées et soldes de relevé dans `statement.data`. Certains tags peuvent
être absents ou fournir `None`.

| Champ `BankingRecord` | Source MT940 | Règle de correspondance |
|---|---|---|
| `transaction_date` | `transaction.data["date"]` | Date de valeur issue de `:61:`; c'est la date transactionnelle principale. |
| `booking_date` | `transaction.data["entry_date"]` ou `["guessed_entry_date"]` | Copier si présent; `guessed_entry_date` est une date inférée par le parseur et doit être traçable comme telle. |
| `amount` | `transaction.data["amount"].amount` | Montant signé `Decimal`; conserver `status` original et ne pas convertir en `float`. |
| `currency` | `transaction.data["amount"].currency` ou `transaction.data["currency"]` | Copier si disponible; le champ transaction peut être hérité du contexte du relevé. |
| `label` | `transaction.data["transaction_details"]` et détails structurés issus de `:86:` | Conserver le contenu brut et toute décomposition disponible; les tags/processeurs déterminent les champs. |
| `account_reference` | Métadonnées du relevé, par ex. information de compte dans `statement.data` | Copier si présent; le champ exact dépend du relevé et des processeurs utilisés. |
| `source_transaction_id` | `transaction.data["customer_reference"]`, `["bank_reference"]` ou `["transaction_reference"]` | Conserver séparément les références; ne pas prétendre qu'elles forment toujours un identifiant unique. |
| `bank_reference` | `transaction.data["bank_reference"]` | Copier si présent. |
| `customer_reference` | `transaction.data["customer_reference"]` | Copier si présent. |
| `transaction_type` | `transaction.data["id"]` | Copier le code source (par exemple `NTRF`) comme code, sans inférer seul la nature comptable. |
| `source_status` | `transaction.data["status"]` | Conserver la marque source débit/crédit/contre-passation. |
| `remittance_information` | `transaction_details`, `extra_details` et champs structurés `:86:` | Préserver le texte source et les détails décodés disponibles. |
| `source_format` | Format du fichier effectivement parsé | `MT940`. |
| `document_id` | Contexte `IngestionManager` | Identifiant DSH-Core du relevé ingéré. |
| `account_id` | Résolution DSH à partir des métadonnées du relevé | Requis dans le record final; traiter l'absence de compte résolu comme une anomalie de données. |

Métadonnées de solde MT940 : conserver les soldes tagués tels que solde
d'ouverture/clôture avec montant, devise, statut débit/crédit et date lorsqu'ils
sont exposés. Les soldes peuvent varier selon les tags et les processeurs.
`mt940` ne vérifie pas automatiquement que le solde initial, les mouvements et
le solde final s'équilibrent.

## 7. Informations nécessaires au futur rapprochement bancaire

Un rapprochement entre mouvement bancaire et document/facture doit au minimum
disposer de :

1. **Compte** (`account_id`) pour limiter les candidats et distinguer les
   comptes/sociétés.
2. **Montant signé et devise** (`amount`, `currency`) pour comparer des
   montants sans perte de précision et éviter de comparer des devises
   différentes comme si elles étaient équivalentes.
3. **Dates distinctes** (`transaction_date`, `booking_date`) pour accepter les
   décalages entre valeur et comptabilisation.
4. **Libellé et informations de remise** (`label`, `remittance_information`,
   `counterparty_name`) pour rechercher un nom, numéro de facture ou motif de
   paiement.
5. **Références source** (`source_transaction_id`, `bank_reference`,
   `customer_reference`, références MT940) pour identifier le mouvement et
   éviter les doublons lorsque la banque fournit des identifiants stables.
6. **Document du relevé** (`document_id`) et **document métier associé**
   (`matched_document_id`) pour tracer la provenance et le résultat du
   rapprochement sans confondre relevé bancaire et facture.
7. **État/audit de décision** (`reconciliation_status`,
   `reconciliation_method`, `reconciled_at`, `reconciled_by`) pour distinguer
   une transaction en attente, rapprochée, non rapprochée ou soumise à revue,
   et expliquer le résultat.
8. **Montant rapproché** (`matched_amount`) pour les paiements partiels ou
   groupés, où le mouvement bancaire n'est pas égal au montant d'une facture
   unique.

Le futur LangGraph de rapprochement consommerait des `BankingRecord` parsés et
les documents disponibles, puis enregistrerait une association et son audit.
Le graphe, les règles de score, la gestion des paiements multiples et
l'approbation humaine ne sont pas implémentés par le code actuel; cette
spécification ne leur attribue aucun comportement exécuté.

## 8. Contrôles de cohérence proposés au niveau du contrat

- Un record persisté doit avoir les champs obligatoires définis plus haut.
- Le montant garde précision décimale et signe normalisé; l'indication de signe
  source reste conservée lorsqu'elle est disponible.
- Une référence source absente ne doit pas être remplacée par un identifiant
  supposé globalement unique; la déduplication peut utiliser une empreinte
  dérivée du document et des champs normalisés, à définir par le ledger.
- Les valeurs brutes et les champs normalisés doivent rester distinguables afin
  de pouvoir auditer les transformations.
- Les contrôles de cohérence des soldes sont des contrôles au niveau relevé,
  séparés de la validation de chaque transaction et du rapprochement avec les
  factures.

## 9. Limites et références

- `schemas/bank_statement_schema.py` est vide; aucun champ/type existant ne
  permet de confirmer ce contrat comme modèle courant.
- La roadmap fixe seulement date, montant, libellé, compte et document associé
  comme contenu minimal du BankingLedger.
- Les champs optionnels et mappings dépendent des variantes de fichiers et des
  données effectivement exposées par les parseurs; ils doivent être vérifiés
  avec des relevés anonymisés représentatifs avant implémentation.
- Documentation OFXParse : <https://github.com/jseutter/ofxparse>
- Documentation du modèle MT940 : <https://mt940.readthedocs.io/en/latest/data-model.html>
- Guide MT940 sur les entrées et workflows : <https://mt940.readthedocs.io/en/latest/usage.html>
