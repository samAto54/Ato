@echo off
setlocal
set "ATO_PROJECT=%~dp0"
set "ATO_PYTHON=%ATO_PROJECT%.venv\Scripts\pythonw.exe"

if not exist "%ATO_PYTHON%" (
    echo Ato's virtual environment was not found.
    echo Expected: "%ATO_PYTHON%"
    echo Run the setup instructions in README.md, then try again.
    pause
    exit /b 1
)

start "Ato" /D "%ATO_PROJECT%" "%ATO_PYTHON%" -m ato.ui.desktop
if errorlevel 1 (
    echo Windows could not launch Ato.
    pause
    exit /b 1
)
