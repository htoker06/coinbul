@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "agent.py" goto klasor_tamam
echo.
echo HATA: Bu dosya ajanin diger dosyalariyla ayni klasorde degil.
echo.
echo Cozum:
echo  1. Zip dosyasina sag tiklayip "Tumunu ayikla" deyin.
echo  2. Cikan klasorun icinde agent.py ve requirements.txt dosyalarini gorun.
echo  3. Bu bat dosyasini O klasorun icinden cift tiklayin.
echo  Zip dosyasini acip icinden cift tiklamayin, bat dosyasini baska yere tasimayin.
pause
exit /b 1

:klasor_tamam
set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 goto python_yok
if not exist ".env" copy ".env.example" ".env" >nul
if not exist "data" mkdir "data"
if exist "data\.kurulum_tamam" goto kurulum_tamam
echo Gerekli paket kuruluyor...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 goto pip_hata
echo ok>"data\.kurulum_tamam"
:kurulum_tamam
%PY% report.py
pause
exit /b 0

:python_yok
echo.
echo HATA: Python bulunamadi.
echo python.org adresinden Python kurun ve kurulumda "Add python.exe to PATH" kutusunu isaretleyin.
pause
exit /b 1

:pip_hata
echo.
echo HATA: Gerekli paket kurulamadi. Internet baglantinizi kontrol edin.
pause
exit /b 1
