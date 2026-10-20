# Revue des responsabilités et relations bancaires

## 1. Objectif et statut

Ce document répartit les informations de `BANKING_RECORD_SPEC.md` et les
lacunes relevées dans `BANKING_SCHEMA_REVIEW.md` entre :

- A) le mouvement bancaire (`BankingRecord`);
- B) le référentiel Compte bancaire;
- C) le référentiel Société;
- D) la relation de rapprochement;
- E) le référentiel Fournisseur;
- F) le référentiel Client.

Il vise à clarifier les cardinalités nécessaires pour les multi-sociétés,
multi-comptes, paiements partiels et groupés, remises de chèques et
rapprochements N↔N. Il s'agit d'une proposition conceptuelle à valider avant
toute implémentation. Aucun code ni contrat exécutable n'est créé.

## 2. Principe de séparation

Chaque objet est propriétaire de ses faits métier :

1. **`BankingRecord`** décrit un mouvement tel qu'inscrit sur le compte
   bancaire, ses montants/dates et sa provenance.
2. **Compte bancaire** décrit le compte durable auquel le mouvement est
   imputé et l'entité qui en est propriétaire.
3. **Société** décrit l'entité juridique ou métier propriétaire, sans recopier
   ses informations dans chaque mouvement.
4. **Relation de rapprochement** décrit chaque allocation entre un mouvement
   bancaire et un document métier, avec son montant et son audit.
5. **Fournisseur** et **Client** sont des identités de référentiel. Un nom
   trouvé dans le relevé reste une observation source tant que son identité
   n'est pas résolue.

Les champs `matched_document_id`, `matched_amount`, `reconciliation_status`,
`reconciliation_method`, `reconciled_at` et `reconciled_by` proposés comme
optionnels sur `BankingRecord` ne suffisent pas pour des rapprochements N↔N :
ils décrivent au mieux un seul lien. La décision recommandée ici est de porter
les allocations et leur état sur la relation de rapprochement, pas sur le
mouvement.

## 3. Répartition synthétique

| Partie | Informations qui lui appartiennent | Références |
|---|---|---|
| A) `BankingRecord` | Faits d'une transaction bancaire source et identité de son relevé d'origine. | `account_id` vers B; références source vers la banque. |
| B) Compte bancaire | Identité stable du compte, référence bancaire, devise(s), statut et lien de propriété. | Lien vers C; un ou plusieurs mouvements A. |
| C) Société | Identité de l'entité propriétaire et ses attributs de référentiel. | Un ou plusieurs comptes B; éventuellement plusieurs sociétés via une relation de propriété si le métier l'exige. |
| D) Relation de rapprochement | Une allocation d'un montant entre un mouvement A et un objet métier rapprochable, plus sa décision et son audit. | Vers A et vers le document métier; cardinalité plusieurs-à-plusieurs. |
| E) Fournisseur | Identité normalisée du fournisseur et ses attributs propres. | Peut être la contrepartie d'un mouvement via une identité de partie; documents fournisseurs. |
| F) Client | Identité normalisée du client et ses attributs propres. | Peut être la contrepartie d'un mouvement via une identité de partie; documents clients. |

## 4. A) Informations appartenant à `BankingRecord`

Un `BankingRecord` représente **un mouvement bancaire observé**, et non une
facture, une remise de chèques ou une décision de rapprochement.

### Informations à conserver sur le mouvement

- Identifiant interne stable du mouvement : `record_id`.
- Faits financiers : `amount` en décimal exact, `currency`, et convention de
  signe débit négatif/crédit positif.
- Dates : `transaction_date`, `booking_date` si distincte, avec la sémantique
  de la date source préservée.
- Libellé et contenu de paiement : `label`, `remittance_information`,
  `transaction_type` source et `source_status`.
- Provenance : `document_id`, `source_format`, `source_transaction_id`,
  `bank_reference`, `customer_reference`, `statement_id` et
  `source_payload` selon disponibilité.
- Identifiant de compte résolu : `account_id` obligatoire dans le record
  normalisé.
- Numéro de chèque (`check_number`) lorsque le mouvement bancaire est
  identifié comme chèque et que la source le fournit.
- Référence brute du compte source (`account_reference`) si utile à
  l'audit/résolution; elle ne remplace pas `account_id`.

### Ce qui ne doit pas être dupliqué dans le mouvement

- Attributs stables de la société ou du compte : ils relèvent des référentiels
  B et C.
- La liste des factures réglées, le montant par facture et l'état/audit de
  chaque allocation : ils relèvent de D.
- Les coordonnées et données maîtres fournisseur/client : elles relèvent de E
  et F. Le mouvement peut conserver les noms/références bruts de la source.

Un identifiant de contrepartie résolu peut être exposé comme référence depuis
le mouvement, mais cette référence ne rend pas `BankingRecord` propriétaire
des informations de la contrepartie.

## 5. B) Informations appartenant au référentiel Compte bancaire

Le compte est l'identité stable utilisée pour rattacher chaque mouvement à un
compte métier, indépendamment des libellés et numéros reçus dans les relevés.

### Informations à porter

- Identifiant DSH-Core stable du compte, référencé par `BankingRecord.account_id`.
- Référence(s) source nécessaire(s) à la résolution : identifiant de compte
  OFX, référence de compte MT940 ou autre identifiant bancaire; elles doivent
  être qualifiées par l'institution ou la source si elles ne sont pas uniques.
- Identifiant de l'institution bancaire et nom d'affichage, lorsque gérés par
  le référentiel plutôt que seulement copiés depuis le relevé.
- Devise(s) autorisée(s) ou devise habituelle du compte, sans confondre cette
  valeur avec la devise effective de chaque mouvement.
- Statut opérationnel et période de validité, si nécessaires à l'historique.
- Lien de propriété vers la Société (C), selon la cardinalité décidée ci-dessous.

### Cardinalités recommandées

- Un compte peut avoir zéro à plusieurs mouvements.
- Chaque mouvement normalisé référence exactement un compte.
- Par défaut, un compte actif appartient à une seule société à un instant
  donné; l'historique des changements de propriétaire doit être daté si le
  besoin existe.
- Si un même compte peut réellement être partagé entre sociétés, ne pas choisir
  arbitrairement une seule société : il faut une relation Société-Compte
  explicite, datée et éventuellement avec rôle/quote-part.

### Multi-comptes et résolution

Plusieurs comptes sont distingués par leur `account_id`, pas par le seul
`account_reference`. Pour chaque relevé, le processus d'ingestion doit résoudre
la référence source vers un compte connu ou laisser l'élément en attente de
résolution. Un compte inconnu ne doit pas être fusionné avec un autre compte
sur la base d'un nom de banque ou d'un numéro partiel.

Les métadonnées de relevé (`statement_period_start`, `statement_period_end`,
soldes, devise du relevé, référence de relevé) restent attachées au document ou
à l'objet relevé, sauf si une donnée est explicitement une propriété durable
du compte.

## 6. C) Informations appartenant au référentiel Société

La Société porte l'identité de l'entité qui détient ou utilise le compte, et
non les faits bancaires de chaque transaction.

### Informations à porter

- Identifiant DSH-Core stable de la société.
- Nom légal et éventuels noms d'usage.
- Identifiants juridiques et attributs de référentiel déjà utilisés par le
  projet, le cas échéant.
- Statut et période de validité, si les sociétés peuvent être archivées ou
  renommées.

Les mouvements ne doivent pas recopier ces attributs. Ils retrouvent la société
par `BankingRecord.account_id` → Compte bancaire → Société, ce qui évite les
désaccords entre le mouvement et son compte.

### Cardinalité Société-Compte

La règle de base recommandée est : **une Société possède plusieurs comptes;
chaque Compte est rattaché à une Société**. Cette règle rend le filtre
multi-sociétés déterministe.

Avant de la figer, confirmer si les comptes conjoints, comptes de paiement
partagés ou mandats de gestion sont hors périmètre. S'ils sont possibles, la
relation Société-Compte doit devenir plusieurs-à-plusieurs avec dates de
validité et rôle; le `BankingRecord` continue de référencer un compte, sans
porter une société redondante.

## 7. D) Informations appartenant à la relation de rapprochement

La relation de rapprochement porte **une allocation** entre un mouvement
bancaire et un objet métier rapprochable. Elle est l'unité qui permet de
représenter plusieurs mouvements pour un document et plusieurs documents pour
un mouvement.

### Informations à porter par allocation

- Identifiant stable de l'allocation.
- `record_id` du `BankingRecord`.
- Type et identifiant de la cible rapprochée, au minimum une facture ou un
  document métier; la cible doit pouvoir désigner les documents fournisseurs
  et clients appropriés.
- `allocated_amount` exact et devise de l'allocation. La devise doit être
  compatible avec le mouvement et la cible; une conversion, si autorisée, doit
  aussi garder montant/devise d'origine, montant/devise convertis et règle/taux
  utilisés.
- État de cette allocation (`pending`, `matched`, `unmatched`, `review`, ou
  états validés par le métier), distinct de l'état global du mouvement.
- Méthode, score ou motif de décision si le workflow en produit un.
- Date/heure et utilisateur/processus de création, validation, modification
  ou annulation, avec historique si l'audit l'exige.
- Référence de rapprochement ou de groupe lorsque plusieurs allocations sont
  validées ensemble.

### Cardinalité N↔N et paiements partiels/groupés

- Un mouvement peut avoir zéro à plusieurs allocations.
- Un document métier peut être payé par zéro à plusieurs mouvements.
- Une allocation relie exactement un mouvement à exactement une cible métier.
- Un paiement partiel est représenté par une allocation dont le montant est
  inférieur au solde exigible du document, ou par plusieurs allocations
  cumulées; la convention de calcul doit être uniforme.
- Un paiement groupé est représenté par plusieurs allocations depuis le même
  mouvement vers plusieurs documents, chacune avec son propre montant.
- L'état « rapproché » d'un mouvement ou d'un document est dérivé de ses
  allocations; ne pas écraser cet état dans un champ unique du mouvement sans
  définir la règle d'agrégation.

### Contrôles de cohérence à spécifier

- Pour un mouvement entièrement affecté, la somme algébrique des allocations
  doit correspondre au montant rapprochable du mouvement, avec prise en compte
  explicite des frais, retenues, écarts et conversions autorisés.
- Les allocations ne doivent pas dépasser le solde rapprochable du mouvement
  ou le solde ouvert du document, sauf opération explicite de correction ou
  d'écart.
- Les signes, devises, arrondis et tolérances doivent être définis avant
  implementation.
- Les annulations/corrections doivent préserver l'historique plutôt que
  supprimer silencieusement une allocation validée.

Les champs `matched_document_id`, `matched_amount` et les champs d'audit de
rapprochement ne devraient donc pas être l'état canonique sur `BankingRecord`.
Au plus, un état agrégé ou un résumé calculé peut être présenté sur le mouvement
pour consultation, à condition qu'il ne remplace pas les allocations.

## 8. E) Informations appartenant au référentiel Fournisseur

Le référentiel Fournisseur détient l'identité stable du fournisseur et ses
attributs maîtres, selon le modèle fournisseur déjà retenu par l'application.

### Informations à porter

- Identifiant DSH-Core stable du fournisseur.
- Nom légal, nom d'usage et identifiants de rapprochement déjà présents dans
  le référentiel.
- Statut et éventuelles références externes.
- Comptes bancaires du fournisseur uniquement si le domaine métier les gère et
  si les règles de protection des données le permettent.

Le relevé peut ne fournir qu'un texte (`counterparty_name`, `label` ou
`remittance_information`). Ce texte et ses références brutes restent sur le
mouvement comme observations source; leur résolution vers un `supplier_id`
doit être explicite et ne pas être déduite du seul nom sans validation.

Un fournisseur est une contrepartie possible d'un mouvement, mais son rôle
dépend du sens du mouvement : fournisseur payé pour un débit, remboursement
reçu d'un fournisseur pour un crédit, par exemple. Le rôle doit être porté par
la relation d'identité de partie ou déterminé par des règles métier auditées,
pas supposé uniquement depuis la classe du référentiel.

## 9. F) Informations appartenant au référentiel Client

Le référentiel Client détient l'identité stable du client et ses attributs
maîtres, selon le modèle client déjà retenu par l'application.

### Informations à porter

- Identifiant DSH-Core stable du client.
- Nom légal/nom d'usage et identifiants de rapprochement déjà présents dans le
  référentiel.
- Statut et éventuelles références externes.
- Informations de paiement propres au client uniquement si elles existent
  déjà dans le domaine et sont nécessaires.

Comme pour le fournisseur, `counterparty_name` et les textes de paiement sont
des données source du mouvement, pas un substitut à l'identité du client. La
résolution vers un `customer_id` doit être traçable et tolérer un résultat
ambigu/non résolu.

Un client peut être payeur d'un crédit reçu, mais également bénéficiaire d'un
remboursement sortant. Le sens du mouvement ne suffit donc pas toujours à
déduire le rôle.

## 10. Identité des parties et référence aux référentiels E/F

Le contrat actuel propose un seul `counterparty_name`; il ne sépare ni
l'ordonnateur du bénéficiaire, ni leurs rôles, ni l'identité normalisée.
Avant de figer cette partie, choisir entre :

1. une référence facultative du mouvement vers un seul fournisseur ou client,
   avec un rôle explicite; ou
2. une relation de partie permettant plusieurs contreparties et rôles sur un
   mouvement, reliée au référentiel Fournisseur ou Client.

Dans les deux cas :

- la valeur brute observée (`counterparty_name` et extraits source) est
  conservée séparément de l'identifiant résolu;
- une correspondance ambiguë ne devient pas une identité confirmée;
- les deux référentiels peuvent partager une même personne morale si le modèle
  métier l'autorise, mais un fournisseur et un client distincts ne doivent pas
  être fusionnés sur la seule égalité de nom.

Cette relation de partie est distincte de D : être la contrepartie d'un
mouvement n'est pas la même chose qu'être le document auquel un montant est
rapproché.

## 11. Remises de chèques : objet et frontière de responsabilité

Une remise de chèques n'est pas synonyme de rapprochement avec une facture.
Elle décrit un regroupement opérationnel de chèques dont le total peut apparaître
comme un seul crédit bancaire.

Pour la représenter sans surcharger `BankingRecord` :

- le crédit constaté par la banque reste un `BankingRecord`;
- le numéro du chèque reste sur son mouvement individuel si la banque expose
  chaque chèque comme mouvement;
- la référence, date, montant total et état de remise appartiennent à une
  **remise de chèques** (objet de regroupement à ajouter au périmètre conceptuel
  si ce besoin est confirmé);
- la composition de la remise relie cette remise aux chèques/documents
  constituants, avec les montants individuels si disponibles;
- les allocations entre mouvements bancaires et factures restent des relations
  D, distinctes de la composition de la remise.

Si le relevé n'expose qu'un crédit agrégé et aucun détail des chèques, ne pas
inventer les chèques constituants. La remise peut rester non détaillée ou
nécessiter une source documentaire complémentaire et une revue.

La remise et sa relation de composition ne correspondent directement à aucune
des six catégories A-F. Les intégrer à D confondrait composition de dépôt et
affectation comptable; les intégrer au référentiel Fournisseur/Client serait
également incorrect. La décision est donc un point bloquant : accepter un
objet de remise et sa relation de composition dédiés, ou déclarer hors
périmètre la gestion détaillée des remises de chèques.

## 12. Cas de vérification des relations

### Plusieurs sociétés et plusieurs comptes

Société Alpha possède Compte A1 et Compte A2; Société Beta possède Compte B1.
Chaque `BankingRecord` référence exactement l'un de ces comptes. La société du
mouvement est résolue via le compte, sans copier `company_id` sur chaque
mouvement. Un identifiant de compte source répété entre banques ne crée pas de
collision parce que la résolution est contextualisée par institution et compte
DSH-Core.

### Plusieurs mouvements payant une facture (paiements partiels)

Deux mouvements bancaires séparés ont chacun leur propre `BankingRecord`.
Chacun possède une allocation D vers la même facture; le montant affecté et
l'audit sont conservés séparément pour les deux allocations. Le solde restant
est calculé côté document/ledger selon la règle comptable retenue.

### Un mouvement payant plusieurs factures (paiement groupé)

Un `BankingRecord` référence plusieurs allocations D, chacune vers une facture
différente et avec son montant propre. La somme affectée est contrôlable par
rapport au mouvement, tout en laissant explicites les écarts ou frais.

### Plusieurs mouvements et plusieurs documents (N↔N)

Chaque paire mouvement-document effectivement rapprochée est une allocation D
distincte. Les mouvements et documents restent des objets indépendants; les
allocations ne sont pas encodées comme une liste de champs singuliers dans l'un
ou l'autre.

### Remise comprenant plusieurs chèques

La remise regroupe ses chèques via la relation de composition dédiée. Un
crédit bancaire peut être relié à la remise, mais ce lien de composition n'est
pas lui-même une allocation vers les factures payées. Ces deux relations
peuvent coexister et doivent rester séparées.

## 13. Décisions requises avant de figer les contrats

1. **Propriété du compte :** un compte appartient-il toujours à une seule
   société à un instant donné, ou faut-il supporter la copropriété?
2. **Société sur le mouvement :** confirmer que la société est résolue via
   `account_id`, sans champ société redondant dans `BankingRecord`.
3. **Objet rapprochable :** définir les types de cibles autorisés pour D
   (facture, avoir, paiement, autre document) et leur identité stable.
4. **Allocation :** définir montant signé ou positif, devise, tolérance,
   frais/écarts, règles de dépassement et comportement en devise différente.
5. **État et audit :** définir les états d'une allocation, le traitement des
   annulations, et si l'état global du mouvement est calculé.
6. **Parties :** choisir la cardinalité et la résolution des contreparties vers
   E/F; décider si un mouvement peut avoir un payeur et un bénéficiaire
   structurés simultanément.
7. **Remises de chèques :** confirmer l'objet de remise et sa composition
   dédiés, ainsi que les sources qui fourniront les détails constituants.
8. **Références de compte :** définir les règles d'unicité et de résolution
   des identifiants OFX/MT940, y compris comptes multi-devises.
9. **Portée du rapprochement :** confirmer que les candidats sont filtrés par
   société déduite du compte, devise et documents compatibles.

Tant que ces décisions ne sont pas prises, `matched_document_id` et
`matched_amount` ne doivent pas être considérés comme suffisants pour figer un
contrat couvrant paiements partiels, groupés et N↔N.

## 14. Conclusion

La séparation recommandée est : le mouvement porte les faits observés et le
compte résolu; le compte porte son identité et son lien de propriété; la
société porte son identité; chaque rapprochement porte une allocation auditable
entre un mouvement et un document; fournisseurs et clients portent leurs
identités maîtres. Les textes source ne remplacent pas les référentiels.

Cette organisation couvre conceptuellement le multi-sociétés et le
multi-comptes si la propriété des comptes est définie, ainsi que les paiements
partiels, groupés et le N↔N si les rapprochements sont des allocations
multiples. La remise de chèques nécessite en plus un objet de regroupement et
une relation de composition dédiés, ou une exclusion explicite du périmètre.
Ces frontières et cardinalités doivent être validées avant toute
implémentation.
