@echo off
cd /d "%~dp0"
python -c "from control import restore_pointer; restore_pointer()" 2>nul
where pythonw >nul 2>&1
if %errorlevel%==0 (
  start "AREIS" pythonw "%~dp0app.py"
) else (
  start "AREIS" python "%~dp0app.py"
)
