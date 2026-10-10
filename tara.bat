@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PY=python
where python >nul 2>nul || set PY=py
if not exist ".env" copy ".env.example" ".env" >nul
%PY% -m pip install -q -r requirements.txt
%PY% agent.py --scan
echo.
echo Tam rapor: data\tarama.csv
pause
