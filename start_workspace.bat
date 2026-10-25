@echo off
title SAR BAQ Compression Workspace
echo ====================================================================
echo Starting SAR BAQ Compression Workspace...
echo ====================================================================

:: Start Backend API (FastAPI) on port 8000
echo [1/2] Launching Backend API on http://127.0.0.1:8000 ...
start "SAR BAQ Backend API" cmd /k "cd /d %~dp0 && uvicorn api:app --host 127.0.0.1 --port 8000"

:: Wait 2 seconds for backend initialization
timeout /t 2 /nobreak >nul

:: Start Frontend (Vite React) on port 5173
echo [2/2] Launching Frontend UI on http://127.0.0.1:5173 ...
start "SAR BAQ Frontend UI" cmd /k "cd /d %~dp0frontend && npm run dev"

:: Open default web browser
timeout /t 2 /nobreak >nul
echo Opening browser to http://127.0.0.1:5173/ ...
start http://127.0.0.1:5173/

echo ====================================================================
echo Both services are now running!
echo Close the respective command prompt windows to stop the servers.
echo ====================================================================
