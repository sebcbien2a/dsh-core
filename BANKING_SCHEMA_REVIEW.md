# Revue du contrat cible `BankingRecord`

## Périmètre

Cette revue est fondée uniquement sur `BANKING_RECORD_SPEC.md`. Elle vérifie
si les champs qu'il propose suffisent pour représenter les besoins énumérés
avant de figer le contrat. Aucun code ni changement de schéma n'est produit.

Les champs marqués **manquants** ne sont pas nécessairement à ajouter tous :
ils identifient les informations que le contrat actuel ne peut pas représenter
explicitement. Les choix de cardinalité et de responsabilité restent à
trancher.

## Synthèse

| Besoin | Couverture par les champs actuels | Évaluation |
|---|---|---|
| Plusieurs sociétés | `account_id` distingue un compte, mais aucun champ de société propriétaire n'est défini. | **Insuffisant** |
| Plusieurs comptes bancaires | `account_id` obligatoire et `account_reference` optionnel permettent d'identifier un compte et sa référence source. | **Couvert en partie**; l'association compte-société et l'unicité du compte ne sont pas décrites. |
| Paiements partiels | `matched_amount` et `matched_document_id` permettent de représenter une portion imputée à un document. | **Couvert pour un lien unique**, pas pour un historique d'imputations multiples dans le même record. |
| Paiements groupés | `matched_document_id` est singulier. | **Insuffisant** pour un mouvement affecté à plusieurs factures/documents. |
| Chèques | `check_number` optionnel. | **Couvert en partie** pour un chèque isolé; insuffisant pour remise groupée ou lot de chèques. |
| Virements | `transaction_type`, libellé, références et contrepartie sont disponibles. | **Couvert en partie**; aucun moyen explicite de représenter les parties ordonnatrice/bénéficiaire ni les identifiants de paiement structurés. |
| Prélèvements | Les mêmes champs génériques que les virements peuvent stocker un mouvement. | **Couvert en partie**; mandat, créancier et références de prélèvement ne sont pas explicites. |
| Remises de chèques | Aucun champ dédié à une remise ou à ses chèques constituants. | **Manquant** |
| Fournisseurs | `counterparty_name` peut porter un nom et `matched_document_id` désigner un document. | **Insuffisant** pour identifier/référencer un fournisseur de manière stable. |
| Clients | Même couverture générique via `counterparty_name`. | **Insuffisant** pour identifier/référencer un client de manière stable et distinguer son rôle. |

## Analyse par besoin

### 1. Plusieurs sociétés

**Champs existants concernés :** `account_id`, `account_reference`,
`institution_name`, `institution_id`.

`account_id` désigne un compte bancaire DSH-Core, mais le contrat ne contient
aucun identifiant de société propriétaire. Plusieurs comptes peuvent être
enregistrés sans permettre, à partir du `BankingRecord` seul, de savoir à
quelle société chacun appartient. Les champs d'institution identifient
l'établissement bancaire, pas la société.

**Information manquante à trancher :** une référence stable à la société
propriétaire, ou la garantie explicite que `account_id` référence un compte
dont l'enregistrement maître porte déjà cette association et que celle-ci est
disponible dans tous les usages de rapprochement.

### 2. Plusieurs comptes bancaires

**Champs existants concernés :** `account_id` obligatoire,
`account_reference` optionnel et métadonnées du relevé.

Le contrat peut distinguer plusieurs comptes si `account_id` est unique et
résolu pour chaque transaction. Il ne décrit toutefois pas la relation entre
un compte et sa société, ni la règle d'identification quand un relevé inclut
plusieurs comptes.

**À clarifier :**

- si `account_id` désigne un identifiant métier ou une référence de compte
  source;
- si le compte est déterminé au niveau du relevé ou de chaque transaction;
- comment sont représentés un compte multi-devises ou des comptes dont le
  numéro source n'est pas stable/unique entre banques.

### 3. Paiements partiels

**Champs existants concernés :** `matched_document_id`, `matched_amount`,
`reconciliation_status`, `reconciliation_method`.

Un mouvement bancaire peut porter un `matched_amount` inférieur au montant
total et pointer vers un seul document. Le contrat peut donc décrire une
imputation partielle unique. Plusieurs mouvements distincts peuvent également
pointer vers le même document si le ledger conserve chaque transaction.

**Limite :** le record ne décrit pas explicitement plusieurs allocations
successives, leur montant respectif, ni le total déjà imputé à un document.
Il faut décider si ces allocations sont portées par des liens de rapprochement
séparés ou si chaque mouvement n'a droit qu'à un seul `matched_document_id`.

### 4. Paiements groupés

**Champs existants concernés :** `matched_document_id` et `matched_amount`.

Le contrat n'a qu'un seul champ `matched_document_id` par mouvement. Il ne peut
donc pas représenter directement un mouvement bancaire unique ventilé sur
plusieurs factures/documents avec des montants distincts.

**Informations manquantes :** une association multiple mouvement-document,
le montant affecté à chaque document et, si requis, une référence de groupe ou
un total de contrôle garantissant que les allocations correspondent au
mouvement bancaire.

### 5. Chèques

**Champs existants concernés :** `check_number`, `transaction_type`,
`label`, `source_payload`, `source_transaction_id`.

`check_number` couvre le numéro d'un chèque lorsque le relevé expose une
transaction de chèque isolée. La conservation du payload source peut préserver
des données brutes, mais ce champ générique n'établit pas un contrat métier
interrogeable pour les chèques.

**Informations à vérifier ou compléter :**

- numéro du chèque et identifiant de transaction bancaire ne sont pas
  nécessairement équivalents;
- émetteur/tireur, bénéficiaire ou compte d'origine si ces informations sont
  présentes et utiles au rapprochement;
- référence d'une remise lorsque le chèque apparaît dans un dépôt groupé.

### 6. Virements

**Champs existants concernés :** `transaction_type`, `label`,
`remittance_information`, `counterparty_name`, `bank_reference`,
`customer_reference` et `source_transaction_id`.

Ces informations génériques peuvent faciliter la reconnaissance d'un
virement, surtout si les références figurent dans le texte bancaire. Le contrat
ne garantit cependant pas la séparation des rôles ni la conservation structurée
de l'identité de l'ordonnateur et du bénéficiaire.

**Informations manquantes ou à confirmer :** type de mouvement normalisé,
partie émettrice et partie bénéficiaire avec leur rôle, référence de paiement
structurée, identifiant de bout en bout lorsque la source en fournit un.

### 7. Prélèvements

**Champs existants concernés :** mêmes champs génériques que pour les
virements, ainsi que `counterparty_name` et `remittance_information`.

Le contrat peut conserver le libellé d'un prélèvement, mais ne distingue pas
explicitement un mandat de prélèvement d'un autre mode de paiement.

**Informations manquantes ou à confirmer :** identifiant de mandat, identité
du créancier, référence de prélèvement et indication structurée du rôle du
créancier. Leur disponibilité dépend des formats et des champs réellement
fournis par les relevés.

### 8. Remises de chèques

**Champs existants concernés :** `check_number`, `source_payload`,
`transaction_type`, `matched_document_id`.

Ils ne suffisent pas à représenter un dépôt unique regroupant plusieurs chèques.
Le schéma ne distingue pas la remise comme opération agrégée, ses chèques
constituants et les montants rattachés à chacun. `matched_document_id` ne peut
pas porter plusieurs documents.

**Informations manquantes :** référence de remise, relation aux chèques
individuels, montants individuels et total de remise. Il faut aussi décider si
les relevés bancaires fournissent effectivement ces détails ou seulement le
montant net déposé.

### 9. Fournisseurs et clients

**Champs existants concernés :** `counterparty_name`, `label`,
`remittance_information`, `matched_document_id`, `source_payload`.

Un nom textuel peut être conservé, mais il n'offre pas une identité stable :
homonymes, variantes orthographiques et changement de dénomination restent
ambigus. Le contrat ne précise pas si la contrepartie est un fournisseur ou un
client, ni son identifiant de référentiel.

**Informations manquantes à évaluer :**

- identifiant normalisé de contrepartie ou référence au référentiel existant;
- rôle de la contrepartie dans le mouvement (payeur, bénéficiaire,
  fournisseur, client);
- séparation de l'émetteur et du bénéficiaire lorsqu'ils sont tous deux
  disponibles;
- règle permettant de résoudre cette identité à partir du relevé ou du
  document rapproché, sans inventer une identité depuis le seul libellé.

`matched_document_id` identifie un document associé, pas directement le client
ou fournisseur impliqué.

## Champs manquants transversaux à arbitrer

Avant de figer le contrat, il reste à décider explicitement :

1. **Propriété multi-société :** le lien société doit-il être dans
   `BankingRecord`, ou garanti par l'enregistrement référencé par `account_id`?
2. **Rapprochements N-à-N :** un mouvement peut-il être affecté à plusieurs
   documents et un document réglé par plusieurs mouvements? Les champs
   singuliers actuels ne couvrent pas le premier cas.
3. **Données de paiement :** faut-il distinguer un type de paiement normalisé
   (chèque, virement, prélèvement, remise) du code source `transaction_type`?
4. **Parties :** faut-il stocker une ou plusieurs contreparties, leurs rôles et
   des identifiants normalisés, plutôt qu'un seul `counterparty_name`?
5. **Remises :** faut-il un identifiant de lot/remise et la composition en
   opérations constituantes, et ces données sont-elles présentes dans les
   formats pris en charge?
6. **Paiement partiel/groupé :** quelle est l'unité d'enregistrement du ledger
   et où vit le montant affecté à chaque relation de rapprochement?

## Conclusion

Le contrat actuel couvre les primitives de base d'un mouvement bancaire,
l'identification d'un compte, une référence de chèque isolée et une association
à **un** document. Il ne suffit pas encore, sans décisions supplémentaires,
pour garantir la gestion multi-société, les paiements groupés, les remises de
chèques détaillées ou l'identification normalisée et typée des fournisseurs et
clients.

Les champs potentiellement manquants sont donc : lien société ou garantie de
résolution via compte, associations multiples mouvement-document avec montant
par association, référence/composition de remise, type de paiement normalisé,
et identifiants/rôles structurés des parties. Cette liste identifie les
informations à trancher; elle ne constitue pas une proposition de code ni un
contrat final figé.
