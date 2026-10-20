# Incohérence d'erreur — IngestionManager / InvoiceAgent

## Périmètre

Analyse limitée à `services/ingestion_manager.py` et
`agents/invoice_agent.py`. Aucun fichier source n'est modifié.

## Constat

Dans `services/ingestion_manager.py`, lorsque le traitement de facture échoue,
`IngestionManager.ingest` lit le champ `last_error` du dictionnaire retourné
par l'agent :

```python
# services/ingestion_manager.py:128
status="error", error_log=result.get("last_error")
```

Or `agents/invoice_agent.py::InvoiceAgent.process_invoice` retourne, dans son
chemin d'exception, le champ `message` :

```python
# agents/invoice_agent.py:113
return {"status": "critical_error", "message": str(e)}
```

Le gestionnaire reçoit donc `None` pour `error_log` lorsque l'agent renvoie
cette réponse d'erreur, au lieu du détail de l'exception.

## Ligne actuelle

```python
status="error", error_log=result.get("last_error")
```

Fichier et ligne : `services/ingestion_manager.py:128`.

## Ligne corrigée proposée

```python
status="error", error_log=result.get("message")
```

Fichier et ligne visés : `services/ingestion_manager.py:128`.

## Impact exact

Avec cette substitution, si `process_invoice` renvoie
`{"status": "critical_error", "message": "<détail>"}`, le résultat
`IngestionResult` du gestionnaire placera `<détail>` dans `error_log` au lieu de
`None`. Le statut externe restera `"error"` et le champ `message` de l'agent
ne sera pas renommé.

Cette ligne ne couvre que le chemin où un agent est obtenu et renvoie un statut
autre que `"success"`. Les autres comportements du gestionnaire ne sont pas
concernés par cette proposition.
