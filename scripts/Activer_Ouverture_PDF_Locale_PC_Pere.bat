@echo off
setlocal
rem P27-nonies - ouverture 100% locale des liens PDF + suppression du blocage OLE/DDE
set "PIECES=%USERPROFILE%\OneDrive\Compta_APD\Tableaux_Comptables\PIECES_JOINTES"
rem Excel ne met plus a jour les liens externes au chargement.
reg add "HKCU\Software\Microsoft\Office\16.0\Excel\Options" /v DontUpdateLinks /t REG_DWORD /d 1 /f >nul 2>&1
rem Excel delegue immediatement l'ouverture du PDF a Windows (ShellExecute).
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Internet" /v UseOnlineContent /t REG_DWORD /d 0 /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Internet" /v ForceShellExecute /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKCU\Software\Microsoft\Office\9.0\Common\Internet" /v ForceShellExecute /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\Software\Microsoft\Office\9.0\Common\Internet" /v ForceShellExecute /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKLM\Software\Microsoft\Office\16.0\Common\Internet" /v ForceShellExecute /t REG_DWORD /d 1 /f >nul 2>&1
rem Plus d'alerte jaune "Microsoft Excel attend la fin d'une action OLE".
reg add "HKCU\Software\Microsoft\Office\16.0\Common\Security" /v DisableHyperlinkWarning /t REG_DWORD /d 1 /f >nul 2>&1
reg add "HKCU\Software\Policies\Microsoft\Office\16.0\Common\Security" /v DisableHyperlinkWarning /t REG_DWORD /d 1 /f >nul 2>&1
if exist "%PIECES%" attrib +P -U "%PIECES%\*" /S /D
endlocal
