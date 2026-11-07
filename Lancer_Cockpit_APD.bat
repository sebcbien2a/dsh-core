@echo off
chcp 65001 >nul
title Cockpit APD - Pipeline P27 (4 etapes)
cd /d A:\dsh-core
echo =====================================================================
echo   COCKPIT APD - PIPELINE P27 EN 4 ETAPES SEQUENTIELLES
echo =====================================================================
echo.
echo [1/2] Execution des 4 etapes (Vision, Banque, Ventilation, Simulations)
.venv-v3\Scripts\python.exe scripts\run_apd_four_stage_pipeline.py --tenant APD --all
if errorlevel 1 (
    echo.
    echo ERREUR pendant le pipeline. Consultez les messages ci-dessus.
    pause
    exit /b 1
)
echo.
echo [2/2] Ouverture du classeur lisible et de l'interface web locale
if exist "workspace\document_flow\APD\02_A_COMPLETER\_A_COMPLETER.xlsx" (
    start "" "workspace\document_flow\APD\02_A_COMPLETER\_A_COMPLETER.xlsx"
)
echo Liberation du port 8765...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8765 ^| findstr LISTENING') do (
    taskkill /F /PID %%a >nul 2>&1
)
start "APD P27 Web" /min .venv-v3\Scripts\python.exe scripts\apd_pipeline_web.py --tenant APD
timeout /t 2 >nul
start http://127.0.0.1:8765
echo.
echo Pipeline termine. Classeurs dans :
echo   workspace\document_flow\APD\04_EXPORT_COMPTA\APD
echo Questions en attente :
echo   workspace\document_flow\APD\02_A_COMPLETER\_A_COMPLETER.xlsx
pause
