@echo off
title Requests Dashboard Setup
echo ========================================
echo   Installing Python Dependencies...
echo ========================================
echo.

REM Use python -m pip (safer)
python -m pip install --upgrade pip

IF EXIST requirements.txt (
    echo Installing from requirements.txt...
    python -m pip install -r requirements.txt
) ELSE (
    echo ❗ requirements.txt not found. Installing known dependencies...
    python -m pip install flask pymysql requests
)

echo.
echo ========================================
echo     Starting Requests Dashboard
echo ========================================
echo.

REM Open browser automatically
start "" http://127.0.0.1:5002/requests_dashboard

REM Run Flask application
python requests_dashboard_dev.py

pause
