@echo off
rem Launch AnchorCheck on Windows (creates a virtual environment on first run).
cd /d "%~dp0"
if not exist .venv (
  py -3 -m venv .venv
  call .venv\Scripts\activate.bat
  pip install -r requirements.txt
) else (
  call .venv\Scripts\activate.bat
)
python -m anchorcheck ui
pause
