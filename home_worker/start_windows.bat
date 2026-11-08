@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
title Otthoni kepgeneralo worker

rem --- Beallitasok fajl ---
if not exist beallitasok.txt (
  copy beallitasok.minta.txt beallitasok.txt >nul
  echo.
  echo Elso inditas: toltsd ki a megnyilo beallitasok.txt fajlt ^(SZERVER es TOKEN^),
  echo mentsd el, zard be, majd inditsd ujra ezt a fajlt.
  notepad beallitasok.txt
  pause
  exit /b 0
)

rem --- Python 3.12 ---
py -3.12 --version >nul 2>nul
if errorlevel 1 (
  echo HIBA: Nincs telepitve a Python 3.12. Toltsd le: https://www.python.org/downloads/release/python-3128/
  echo Telepiteskor pipald be: "Add python.exe to PATH".
  pause
  exit /b 1
)

if not exist venv\Scripts\python.exe (
  echo Virtualis kornyezet letrehozasa...
  py -3.12 -m venv venv || goto :hiba
)
set PY=venv\Scripts\python.exe

if not exist venv\alap.ok (
  echo Alapcsomagok telepitese...
  %PY% -m pip install --upgrade pip || goto :hiba
  %PY% -m pip install -r requirements-alap.txt || goto :hiba
  echo ok> venv\alap.ok
)

if /i "%~1"=="--teszt" goto :futtat

if exist venv\ai.ok goto :futtat

rem RTX 50xx kartyakhoz ujabb CUDA verzio kell
set TORCH_CUDA=cu126
set GPU_NAME=ismeretlen
for /f "delims=" %%G in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do set "GPU_NAME=%%G"
echo Videokartya: %GPU_NAME%
echo %GPU_NAME% | findstr /C:"RTX 50" >nul && set TORCH_CUDA=cu128
echo AI-csomagok telepitese ^(PyTorch %TORCH_CUDA%, tobb GB, sokaig tarthat^)...
%PY% -m pip install torch==2.7.1 --index-url https://download.pytorch.org/whl/%TORCH_CUDA% || goto :hiba
%PY% -m pip install -r requirements-ai.txt || goto :hiba
echo ok> venv\ai.ok

:futtat

if not exist venv\ai.ok (
  rem RTX 50xx kartyakhoz ujabb CUDA kell
  set TORCH_CUDA=cu126
  for /f "delims=" %%G in ('nvidia-smi --query-gpu^=name --format^=csv^,noheader 2^>nul') do set GPU_NAME=%%G
  echo Videokartya: %GPU_NAME%
  echo %GPU_NAME% | findstr /C:"RTX 50" >nul && set TORCH_CUDA=cu128
  echo AI-csomagok telepitese ^(PyTorch, ez tobb GB, sokaig tarthat^)...
  %PY% -m pip install torch --index-url https://download.pytorch.org/whl/%TORCH_CUDA% || goto :hiba
  %PY% -m pip install -r requirements-ai.txt || goto :hiba
  echo ok> venv\ai.ok
)

:futtat
%PY% worker.py %*
pause
exit /b 0

:hiba
echo.
echo HIBA tortent a telepites kozben. Ellenorizd az internetkapcsolatot, majd inditsd ujra.
pause
exit /b 1
