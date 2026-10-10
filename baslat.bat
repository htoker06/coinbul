@echo off
chcp 65001 >nul
title Binance TR Ajani
cd /d "%~dp0"

set PY=python
where python >nul 2>nul || set PY=py

if not exist ".env" (
    copy ".env.example" ".env" >nul
    echo .env dosyasi olusturuldu. Ayarlar varsayilan: SANAL MOD.
)

if not exist "data\.kurulum_tamam" (
    echo Gerekli paket kuruluyor...
    %PY% -m pip install -q -r requirements.txt || goto hata
    if not exist data mkdir data
    echo ok> "data\.kurulum_tamam"
)

:dongu
echo.
echo Ajan basliyor. Durdurmak icin bu pencereyi kapatin veya Ctrl+C basin.
%PY% agent.py
if exist "STOP" goto son
echo.
echo Ajan kapandi. 30 saniye sonra yeniden baslatilacak (iptal icin pencereyi kapatin)...
timeout /t 30 >nul
goto dongu

:hata
echo.
echo Paket kurulamadi. Python kurulu mu? Kurulumda "Add python.exe to PATH" isaretlenmeli.
pause
exit /b 1

:son
echo STOP dosyasi bulundu, ajan durduruldu. Yeniden calistirmak icin STOP dosyasini silin.
pause
