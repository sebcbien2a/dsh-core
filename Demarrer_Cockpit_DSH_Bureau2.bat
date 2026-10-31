@echo off
chcp 65001 >nul
title Cockpit Global DSH-Core (Bureau Virtuel IA n°2)
echo =====================================================================
echo   BASCULE VERS LE BUREAU VIRTUEL WINDOWS N°2 (COCKPIT IA)
echo =====================================================================

:: Crée ou bascule vers un nouveau bureau virtuel Windows (Ctrl+Win+Droite / Ctrl+Win+D)
powershell -Command "$wshell = New-Object -ComObject wscript.shell; $wshell.SendKeys('^#{RIGHT}')"
timeout /t 2 >nul

cd /d A:\dsh-core

echo.
echo [1/7] Demarrage d'Ollama (Qwen 9B Base Offline + Qwen 27B Expert Offline)...
tasklist /FI "IMAGENAME eq ollama.exe" 2>NUL | find /I /N "ollama.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo   -> Ollama est deja actif.
) else (
    start "Ollama Server" /min cmd /c "ollama serve"
    timeout /t 3 >nul
)

echo.
echo [2/7] Demarrage des conteneurs Docker (Qdrant, Neo4j, Langfuse, Postgres, n8n)...
docker compose up -d

echo.
echo [3/7] Demarrage du moteur LangGraph & API DSH-Core (Port 8000)...
if exist ".venv\Scripts\activate.bat" (
    start "DSH-Core LangGraph API (8000)" /min cmd /k "call .venv\Scripts\activate.bat && uvicorn api:app --host 127.0.0.1 --port 8000 --reload"
) else (
    start "DSH-Core LangGraph API (8000)" /min cmd /k "uvicorn api:app --host 127.0.0.1 --port 8000 --reload"
)

echo.
echo [4/7] Lancement de DeepSeek Harness Web (Port 3080)...
start "DeepSeek Harness (3080)" /min cmd /k "npx @deepseek-ai/dsh web"

echo.
echo [5/7] Lancement de Hermes Dashboard (Port 9119) et Hermes Desktop...
start "Hermes Dashboard (9119)" /min cmd /k "hermes dashboard"
start "Hermes Desktop App" /min cmd /c "hermes desktop"

echo.
echo [6/7] Ouverture de VS Code sur A:\dsh-core...
call code "A:\dsh-core"

echo.
echo [7/7] Ouverture de toutes les interfaces sur ce Bureau IA...
timeout /t 5 >nul
start http://127.0.0.1:3080
start http://127.0.0.1:9119
start http://127.0.0.1:8000/docs
start http://localhost:3000
start http://localhost:6333/dashboard
start http://localhost:7474

echo.
echo =====================================================================
echo   COCKPIT IA OPERATIONNEL SUR LE BUREAU 2 !
echo   (Astuce : Ctrl + Win + Fleche Gauche/Droite pour changer de bureau)
echo =====================================================================
timeout /t 4 >nul
