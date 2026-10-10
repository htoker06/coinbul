@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist "agent.py" (
    echo.
    echo HATA: Bu dosya ajanin diger dosyalariyla ayni klasorde degil.
    echo Su an calistigi klasor: %CD%
    echo.
    echo Cozum:
    echo  1. coinbul-main.zip dosyasina sag tiklayip "Tumunu ayikla" deyin.
    echo  2. Cikan klasorun icinde agent.py ve requirements.txt dosyalarini gorun.
    echo  3. baslat.bat dosyasini O klasorun icinden cift tiklayin.
    echo  Zip dosyasini acip icinden cift tiklamayin, bat dosyasini baska yere tasimayin.
    echo  Masaustune koymak isterseniz sag tik ^> Kisayol olusturun.
    pause
    exit /b 1
)
set PY=python
where python >nul 2>nul || set PY=py
%PY% report.py
pause
