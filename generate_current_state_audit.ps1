$ErrorActionPreference = 'Stop'
$root = 'A:\dsh-core'
$outputPath = Join-Path $root 'DSH_CORE_CURRENT_STATE.md'
$scriptPath = $MyInvocation.MyCommand.Path
$generatedAt = Get-Date

$entries = @(Get-ChildItem -LiteralPath $root -Force -Recurse -ErrorAction SilentlyContinue | Where-Object {
    $_.FullName -ne $outputPath -and $_.FullName -ne $scriptPath
})
$directories = @($entries | Where-Object { $_.PSIsContainer })
$files = @($entries | Where-Object { -not $_.PSIsContainer })
$directoryBytes = @{}
foreach ($directory in $directories) { $directoryBytes[$directory.FullName] = [long]0 }
foreach ($file in $files) {
    $parent = $file.DirectoryName
    while ($parent -and $parent.StartsWith($root, [System.StringComparison]::OrdinalIgnoreCase)) {
        if ($directoryBytes.ContainsKey($parent)) { $directoryBytes[$parent] += [long]$file.Length }
        if ($parent -eq $root) { break }
        $parent = Split-Path -Parent $parent
    }
}
$totalBytes = [long](($files | Measure-Object -Property Length -Sum).Sum)

function Get-Role([string]$relativePath, [bool]$isDirectory) {
    $path = $relativePath.Replace('\', '/')
    if ($path -match '^\.git(?:/|$)') { return 'Métadonnées et objets du dépôt Git' }
    if ($path -match '^\.venv-v3(?:/|$)') { return 'Environnement Python et dépendances installées, pas code métier' }
    if ($path -match '(^|/)__pycache__(/|$)') { return 'Cache Python généré' }
    if ($path -match '^neo4j_data(?:/|$)') { return 'Données persistées Neo4j; contenu binaire non inspecté' }
    if ($path -match '^qdrant_storage(?:/|$)') { return 'Stockage persistant Qdrant; contenu non inspecté' }
    if ($path -match '^langfuse_db(?:/|$)') { return 'Données PostgreSQL Langfuse; contenu non inspecté' }
    if ($path -match '^n8n_data(?:/|$)') { return 'Configuration et données persistées n8n; contenu non inspecté' }
    if ($path -match '^open-webui_data(?:/|$)') { return 'Données persistées Open WebUI; contenu non inspecté' }
    if ($path -match '^models(?:/|$)') { return 'Modèles/artefacts IA locaux; contenu non inspecté' }
    if ($path -match '^workspace/(inbox|invoices|suppliers)(?:/|$)') { return 'Entrées et sorties de flux métier' }
    if ($path -match '^knowledge(?:/|$)') { return 'Corpus de connaissances' }
    if ($path -match '^backup(?:/|$)') { return 'Sauvegardes ou versions archivées' }
    if ($path -match '^docs(?:/|$)') { return 'Documentation inventoriée, non utilisée comme preuve d’architecture' }
    if ($path -match '^workspace(?:/|$)') { return 'Espace de travail et artefacts locaux' }
    if ($path -match '^agents(?:/|$)') { return 'Agents spécialisés et traitement par tâche' }
    if ($path -match '^backend/app(?:/|$)') { return 'Application backend; sous-rôle supposé d’après chemin/nom' }
    if ($path -match '^backend(?:/|$)') { return 'Code et ressources backend' }
    if ($path -match '^services(?:/|$)') { return 'Services métier et orchestration des traitements' }
    if ($path -match '^repositories(?:/|$)') { return 'Accès et persistance des données' }
    if ($path -match '^schemas(?:/|$)') { return 'Contrats et validation de données' }
    if ($path -match '^validators(?:/|$)') { return 'Règles de validation métier' }
    if ($path -match '^skills(?:/|$)') { return 'Outils et compétences exécutables' }
    if ($path -match '^(tests|test_[^/]+)') { return 'Tests/scénarios de vérification' }
    if ($path -match '^docker(?:/|$)') { return 'Configuration des conteneurs' }
    if ($path -match '^memory(?:/|$)') { return 'Mémoire applicative et stockage associé' }
    if ($path -match '^\.vscode(?:/|$)') { return 'Configuration locale de VS Code' }

    $leaf = ($path -split '/')[-1]
    switch ($leaf) {
        'api.py' { return 'API HTTP FastAPI et endpoints' }
        'router.py' { return 'Routage de prompts, modèle et contexte mémoire' }
        'orchestrator.py' { return 'Graphe LangGraph; nœuds actuels à résultats simulés' }
        'invoice_agent.py' {
            if ($path -match '^agents/') { return 'Agent facture importé par ingestion; extraction Vision et validation' }
            return 'Traitement facture local: extraction Vision, validation et archivage'
        }
        'invoice_indexer.py' { return 'Indexation et recherche sémantique des factures dans Qdrant' }
        'ingestion_manager.py' { return 'Classification des fichiers et routage vers les agents' }
        'graph_repository.py' { return 'Accès Neo4j: documents, factures, fournisseurs et relations' }
        'neo4j_service.py' { return 'Façade métier des requêtes Neo4j' }
        'docker-compose.yml' { return 'Déclaration de services locaux Docker' }
        '.env' { return 'Configuration potentiellement sensible; contenu non lu' }
        '.gitignore' { return 'Règles d’exclusion Git' }
        'mcp_config.json' { return 'Configuration d’outils/serveurs MCP' }
        'ping_qdrant.py' { return 'Vérification de disponibilité Qdrant' }
    }
    if ($path -match '(^|/)tests?/|^test_[^/]+\.py$') { return 'Test Python' }
    if ($path -match '(^|/)agents/.*\.py$') { return 'Implémentation d’agent Python' }
    if ($path -match '(^|/)services/.*\.py$') { return 'Service métier Python' }
    if ($path -match '(^|/)repositories/.*\.py$') { return 'Accès aux données Python' }
    if ($path -match '(^|/)schemas/.*\.py$') { return 'Schéma/contrat de données Python' }
    if ($path -match '(^|/)validators/.*\.py$') { return 'Validation métier Python' }
    if ($path -match '(^|/)skills/.*\.py$') { return 'Outil/compétence Python' }
    if ($isDirectory) { return 'Répertoire; rôle supposé d’après son nom et ses références dans le code' }
    if ($path -match '\.py$') { return 'Module/script Python; rôle supposé du nom et des imports' }
    if ($path -match '\.(md|rst|txt)$') { return 'Document textuel; inventorié sans servir à l’analyse du code' }
    if ($path -match '\.(sqlite|db|wal|shm)$|database\.sqlite') { return 'Base de données/fichier associé; contenu non inspecté' }
    if ($path -match '\.(json|ya?ml|toml|ini|cfg)$') { return 'Configuration ou données structurées; rôle supposé du nom' }
    return 'Artefact/ressource; rôle supposé du chemin et du type'
}

function Format-Bytes([long]$bytes) { return ('{0:N0}' -f $bytes) }

$topLevel = @($entries | Where-Object { (Split-Path -Parent $_.FullName) -eq $root } | Sort-Object Name)
$writer = New-Object System.IO.StreamWriter($outputPath, $false, (New-Object System.Text.UTF8Encoding($false)))
try {
    $writer.WriteLine('# DSH_CORE_CURRENT_STATE')
    $writer.WriteLine()
    $writer.WriteLine(('État observé le **{0}** (heure locale), à partir du système de fichiers et du code présent dans `A:\dsh-core`.' -f $generatedAt.ToString('yyyy-MM-dd HH:mm:ss')))
    $writer.WriteLine()
    $writer.WriteLine('## Périmètre et méthode')
    $writer.WriteLine()
    $writer.WriteLine('- Inventaire récursif des entrées présentes, y compris fichiers cachés, `.git`, `.venv-v3`, caches et données persistées. Le rapport et son script temporaire sont exclus de l’instantané.')
    $writer.WriteLine('- Tailles en octets; taille d’un dossier = somme récursive de ses fichiers. Dates issues de `LastWriteTime` du système de fichiers local.')
    $writer.WriteLine('- Les rôles applicatifs sont déduits des implémentations Python et de la configuration d’exécution. La documentation est inventoriée, mais son contenu ne sert pas de preuve sur l’architecture.')
    $writer.WriteLine('- `.env`, bases, stockage vectoriel, modèles et environnement virtuel: métadonnées seulement; contenu non ouvert.')
    $writer.WriteLine()
    $writer.WriteLine('## Mesures')
    $writer.WriteLine()
    $writer.WriteLine('| Mesure | Valeur |')
    $writer.WriteLine('|---|---:|')
    $writer.WriteLine(('| Entrées (hors racine et rapport) | {0} |' -f $entries.Count))
    $writer.WriteLine(('| Fichiers | {0} |' -f $files.Count))
    $writer.WriteLine(('| Dossiers | {0} |' -f $directories.Count))
    $writer.WriteLine(('| Taille cumulée | {0} octets ({1:N2} Gio) |' -f $totalBytes, ($totalBytes / 1GB)))
    $writer.WriteLine()
    $writer.WriteLine('## Architecture constatée dans le code')
    $writer.WriteLine()
    $writer.WriteLine('| Surface | Rôle observé | Base dans le code |')
    $writer.WriteLine('|---|---|---|')
    $writer.WriteLine('| API | FastAPI expose recherche, génération/exécution de code, scraping, skills et orchestration. | [`api.py`](api.py) |')
    $writer.WriteLine('| Routage | Classe les prompts (vision/superviseur/expert/rapide), consulte mémoire et skills et choisit un modèle. | [`router.py`](router.py), [`memory_manager.py`](memory_manager.py) |')
    $writer.WriteLine('| Orchestration | Graphe LangGraph avec triage; les deux nœuds de réponse renvoient actuellement du texte fixe simulé. | [`orchestrator.py`](orchestrator.py) |')
    $writer.WriteLine('| Factures | Agents Vision/Ollama avec validation Pydantic; indexeur Qdrant pour embeddings et recherche. | [`agents/invoice_agent.py`](agents/invoice_agent.py), [`invoice_agent.py`](invoice_agent.py), [`invoice_indexer.py`](invoice_indexer.py) |')
    $writer.WriteLine('| Ingestion | Classe les fichiers par nom, charge l’agent facture à la demande; certains types restent en attente d’implémentation. | [`services/ingestion_manager.py`](services/ingestion_manager.py) |')
    $writer.WriteLine('| Graphe | Repository Neo4j pour doublons par hash et relations entre documents, factures et fournisseurs; service de requetes separe. | [`repositories/graph_repository.py`](repositories/graph_repository.py), [`services/neo4j_service.py`](services/neo4j_service.py) |')
    $writer.WriteLine('| Conteneurs | Compose déclare Neo4j, PostgreSQL/Langfuse, n8n, Qdrant et Open WebUI avec leurs stockages locaux. | [`docker-compose.yml`](docker-compose.yml) |')
    $writer.WriteLine('| Qdrant | Indexation des factures dans `invoice_indexer.py`; aucun `qdrant_service.py` dans l’instantané. | [`invoice_indexer.py`](invoice_indexer.py), [`skills/ping_qdrant.py`](skills/ping_qdrant.py) |')
    $writer.WriteLine()
    $writer.WriteLine('## Constats de structure')
    $writer.WriteLine()
    $writer.WriteLine('- Deux implémentations d’agent facture coexistent: le module racine structure, valide et archive; `agents/invoice_agent.py` expose `process_invoice`, appelé par l’ingestion. Contrats et chemins d’exécution distincts.')
    $writer.WriteLine('- `orchestrator.py` est utilisé par l’API, mais ses nœuds de traitement sont actuellement simulés, sans appel effectif aux agents ou modèles.')
    $writer.WriteLine('- Le compose contient des identifiants/valeurs sensibles en dur et l’API autorise CORS depuis toute origine. Les valeurs ne sont pas reproduites ici.')
    $writer.WriteLine('- Une part importante du volume est constituée de dépendances, bases et états locaux, non de code source métier.')
    $writer.WriteLine()
    $writer.WriteLine('## Taille et date par entrée de premier niveau')
    $writer.WriteLine()
    $writer.WriteLine('| Entrée | Type | Taille récursive (octets) | Dernière modification | Rôle supposé |')
    $writer.WriteLine('|---|---|---:|---|---|')
    foreach ($entry in $topLevel) {
        if ($entry.PSIsContainer) { $size = [long]$directoryBytes[$entry.FullName]; $type = 'Dossier' } else { $size = [long]$entry.Length; $type = 'Fichier' }
        $role = (Get-Role $entry.Name $entry.PSIsContainer).Replace('|', '\|')
        $writer.WriteLine(('| `{0}` | {1} | {2} | {3} | {4} |' -f $entry.Name, $type, (Format-Bytes $size), $entry.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $role))
    }
    $writer.WriteLine()
    $writer.WriteLine('## Inventaire exhaustif')
    $writer.WriteLine()
    $writer.WriteLine('Chaque chemin descendant est listé, dossiers compris. Les chemins sont relatifs à `A:\dsh-core`; la taille des dossiers est récursive. Le rôle est une hypothèse issue du chemin/type et, pour les modules clés, du code examiné.')
    $writer.WriteLine()
    $writer.WriteLine('| Chemin | Type | Taille (octets) | Dernière modification | Rôle supposé |')
    $writer.WriteLine('|---|---|---:|---|---|')
    foreach ($entry in ($entries | Sort-Object FullName)) {
        $relative = $entry.FullName.Substring($root.Length).TrimStart('\').Replace('\', '/')
        $relative = $relative.Replace('|', '%7C').Replace('`', '&#96;')
        if ($entry.PSIsContainer) { $size = [long]$directoryBytes[$entry.FullName]; $type = 'Dossier' } else { $size = [long]$entry.Length; $type = 'Fichier' }
        $role = (Get-Role $relative $entry.PSIsContainer).Replace('|', '\|')
        $writer.WriteLine(('| `{0}` | {1} | {2} | {3} | {4} |' -f $relative, $type, (Format-Bytes $size), $entry.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss'), $role))
    }
}
finally { $writer.Dispose() }

Get-Item -LiteralPath $outputPath | Select-Object FullName, Length, LastWriteTime | Format-List