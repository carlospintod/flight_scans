@echo off
rem Double-click launcher for the flight tracker dashboard.
rem Opens http://localhost:8501 in the default browser.
cd /d "%~dp0"
.venv\Scripts\streamlit.exe run ui\app.py
