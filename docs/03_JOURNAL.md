# 03 - JOURNAL DE SUIVI ET DE VALIDATION

19/10 : audits réalisés
19/10 : corrections du groupe A
19/10 : correctif IngestionManager
19/10 : InvoiceSchema gelé
19/10 : architecture bancaire validée
19/10 : architecture Hermes local/Oracle validée
19/10 : synchronisation logique retenue

## 2026-10-21 — Writer local atomique des tableaux maîtres

### Commit

`716d23d` — `feat: ajouter le writer local atomique des tableaux maitres`

### Fonctionnalités obtenues

- écriture locale JSON et JSONL des tableaux maîtres comptable et bancaire ;
- publication transactionnelle multi-fichiers récupérable ;
- verrou à bail pour LOCAL et ORACLE ;
- contrôle optimiste des versions ;
- idempotence des publications ;
- audit append-only ;
- historique des changements de statut ;
- suppression logique et restauration ;
- rollback sur échec de remplacement ;
- récupération des transactions interrompues ;
- contrôle d’intégrité entre les lignes et la MasterVersion ;
- confinement de toutes les écritures sous la racine du writer ;
- prise en charge des LCR, remises de chèques et paiements groupés ;
- prise en charge des paiements personnels, mixtes et partiels.

### Décisions métier finalisées

- les allocations d’un PaymentBatch sont portées par ses items ;
- le statut du BankingRecord parent reflète l’état du lot ;
- les contributions représentent les sommes effectivement réglées ;
- la somme des contributions correspond au montant payé ;
- le remboursement ultérieur d’un payeur personnel ne modifie pas le règlement du fournisseur ;
- BankTransactionMasterRow et BankingRecord doivent conserver des métadonnées de version et de suppression cohérentes.

### Validation

- 332 tests réussis ;
- 0 test échoué ;
- 0 xfail ;
- compilation Python réussie ;
- contrôle Git diff propre.

### Limites restantes

- pas encore de persistance OneDrive ;
- pas encore de repository Oracle réel ;
- pas encore d’import multi-format réel ;
- pas encore d’interface de correction humaine ;
- transaction multi-fichiers récupérable, sans garantie ACID.

### Prochaine étape

Mettre en place la mémoire opérationnelle et la transmission du projet, puis
préparer l’adaptateur de stockage sans connecter immédiatement OneDrive ou
Oracle.

Niveau d’effort recommandé : Équilibré.
