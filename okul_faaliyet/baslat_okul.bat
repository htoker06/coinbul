@echo off
chcp 65001 >nul
cd /d "%~dp0"
if exist "app.py" goto klasor_tamam
echo.
echo HATA: Bu dosya programin diger dosyalariyla ayni klasorde degil.
echo Zip dosyasini once "Tumunu ayikla" ile acin, sonra okul_faaliyet klasorundeki bu dosyayi cift tiklayin.
pause
exit /b 1

:klasor_tamam
set "PY=python"
where python >nul 2>nul
if errorlevel 1 set "PY=py"
where %PY% >nul 2>nul
if errorlevel 1 goto python_yok
if not exist "veri" mkdir "veri"
if exist "veri\.kurulum_v2" goto kurulum_tamam
echo Gerekli paketler kuruluyor (ilk acilista bir kez)...
%PY% -m pip install -q -r requirements.txt
if errorlevel 1 goto pip_hata
echo ok>"veri\.kurulum_v2"
:kurulum_tamam
title Okul Faaliyet Takip Programi
%PY% app.py
echo.
echo Program kapandi.
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
echo HATA: Gerekli paketler kurulamadi. Internet baglantinizi kontrol edin.
pause
exit /b 1
