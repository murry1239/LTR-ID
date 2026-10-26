@echo off
cd /d "%~dp0"

rem First run only: create the Python environment and install packages
if not exist ".venv\Scripts\streamlit.exe" (
    echo [munkeomgi] First run - installing packages. Please wait...
    py -3 -m venv .venv
    ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
)

rem Skip Streamlit's one-time e-mail question
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
    if not exist "%USERPROFILE%\.streamlit" mkdir "%USERPROFILE%\.streamlit"
    > "%USERPROFILE%\.streamlit\credentials.toml" (
        echo [general]
        echo email = ""
    )
)

echo [munkeomgi] Starting... the browser will open automatically.
echo [munkeomgi] Close this window to stop the program.
".venv\Scripts\streamlit.exe" run app.py
