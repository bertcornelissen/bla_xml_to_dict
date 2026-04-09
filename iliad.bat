
@echo off
REM ==========================================
REM One-click Streamlit Launcher (Latest)
REM Local venv, shared app, self-healing
REM ==========================================

REM --- %~dp0 is the directory where this batch file is located ---
SET APP_DIR=%~dp0
SET APP_FILE=compare_iliad_xml_gui.py

REM Local venv per user
SET VENV_DIR=%LOCALAPPDATA%\bla_xml_to_dict_venv

REM Environment hardening
SET PYTHONDONTWRITEBYTECODE=1
SET PYTHONUTF8=1

REM ---- Ensure ignored_fields file exist ----
IF NOT EXIST "%APP_DIR%\ignored_fields.txt" (
    echo Copying samples\ignored_fields.txt to ignored_fields.txt...
    copy "%APP_DIR%\samples\ignored_fields.txt" "%APP_DIR%\ignored_fields.txt"
)

REM ---- Check Python ----
python --version >nul 2>&1
IF ERRORLEVEL 1 (
    echo Python 3.10+ is required but not found.
    echo Please install Python and try again.
    pause
    exit /b
)

IF NOT EXIST "%VENV_DIR%\Scripts\activate.bat" (
    echo Creating new local virtual environment...
    python -m venv "%VENV_DIR%"
    REM ---- Activate venv ----
    call "%VENV_DIR%\Scripts\activate.bat"
    REM ---- Install / update dependencies (safe, quiet) ----
    SET PIP_DISABLE_PIP_VERSION_CHECK=1
    python -m pip install --upgrade pip >nul
    python -m pip install -r "%APP_DIR%\requirements.txt" --quiet
) ELSE (
    ECHO ---- Activate existing venv ----
    call "%VENV_DIR%\Scripts\activate.bat"
)

REM ---- Launch app ----
echo Starting Compare Iliad XML...
cd /d "%APP_DIR%"
streamlit run "%APP_DIR%\%APP_FILE%" --server.headless=true
pause
exit /b
