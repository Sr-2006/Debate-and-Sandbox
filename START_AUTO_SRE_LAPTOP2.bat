@echo off
setlocal
title Auto-SRE Laptop 2 Autonomous Execution Node

cd /d "C:\Users\Shashank\OneDrive\Desktop\Smart horizon hackathon"

echo ============================================================
echo        AUTO-SRE LAPTOP 2 - ALWAYS-HOT AUTONOMOUS NODE
echo ============================================================
echo.

REM ------------------------------------------------------------
REM 1. Verify Docker CLI
REM ------------------------------------------------------------
echo [1/6] Checking Docker...

docker version >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Docker Engine is not reachable.
    echo Start Docker Desktop first, wait until Docker is running,
    echo then run this file again.
    echo.
    pause
    exit /b 1
)

echo [OK] Docker Engine is running.
echo.

REM ------------------------------------------------------------
REM 2. Start Shadow Sandbox (Safe up, volumes preserved)
REM ------------------------------------------------------------
echo [2/6] Starting Shadow Sandbox...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '.\Arse_shadow\shadow_sandbox\clone\run_shadow.ps1' up"

if errorlevel 1 (
    echo.
    echo [ERROR] Shadow Sandbox failed to start.
    echo.
    pause
    exit /b 1
)

echo.
echo [OK] Shadow startup command completed.
echo.

REM ------------------------------------------------------------
REM 3. Show Shadow container health
REM ------------------------------------------------------------
echo [3/6] Checking Shadow containers...

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "& '.\Arse_shadow\shadow_sandbox\clone\run_shadow.ps1' health"

echo.

REM Required remediation target must exist
docker inspect shadow-postgres-db >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Required container shadow-postgres-db is unavailable.
    echo The remediation sandbox is not ready.
    echo.
    pause
    exit /b 1
)

echo [OK] shadow-postgres-db exists.
echo.

REM ------------------------------------------------------------
REM 4. Verify / start Ollama
REM ------------------------------------------------------------
echo [4/6] Checking Ollama...

where ollama >nul 2>&1
if errorlevel 1 (
    echo.
    echo [ERROR] Ollama is not installed or is not in PATH.
    echo.
    pause
    exit /b 1
)

ollama list >nul 2>&1
if errorlevel 1 (
    echo Ollama server is not responding. Starting ollama serve...

    start "Auto-SRE Ollama" /min cmd /k "ollama serve"

    echo Waiting for Ollama...
    timeout /t 5 /nobreak >nul

    ollama list >nul 2>&1
    if errorlevel 1 (
        echo.
        echo [ERROR] Ollama did not become ready.
        echo Check the Ollama window.
        echo.
        pause
        exit /b 1
    )
)

echo [OK] Ollama server is responding.
echo.

REM ------------------------------------------------------------
REM 5. Verify debate model
REM ------------------------------------------------------------
echo [5/6] Checking qwen2.5:3b...

ollama list | findstr /i "qwen2.5:3b" >nul

if errorlevel 1 (
    echo.
    echo [ERROR] qwen2.5:3b is not installed.
    echo Run this once:
    echo     ollama pull qwen2.5:3b
    echo.
    pause
    exit /b 1
)

echo [OK] qwen2.5:3b is available.
echo.

REM ------------------------------------------------------------
REM 6. Start always-hot Laptop 2 supervisor engine service
REM ------------------------------------------------------------
echo [6/6] Starting Laptop 2 Engine Supervisor (Receiver + Worker + Prerequisite Maintenance)...

start "Auto-SRE Laptop2 Engine" cmd /k "python scripts\laptop2_engine_service.py --nats-url nats://172.51.154.253:4222"

echo.
echo ============================================================
echo        LAPTOP 2 ALWAYS-HOT NODE ACTIVE
echo ============================================================
echo.
echo Supervised Components:
echo   Docker Engine           : SUPERVISED
echo   Shadow Stack            : SUPERVISED (shadow-postgres-db)
echo   Ollama LLM              : SUPERVISED (qwen2.5:3b)
echo   NATS Broker             : nats://172.51.154.253:4222
echo   Incident Receiver       : SUPERVISED
echo   Processing Worker       : SUPERVISED
echo   Report Indexing         : runtime/report_index.json
echo                             runtime/latest_phase34_report.json
echo.
echo IMPORTANT:
echo   Keep the "Auto-SRE Laptop2 Engine" window open.
echo   Do not run tests during the demo.
echo   Do not run docker compose down -v.
echo.
pause

endlocal
