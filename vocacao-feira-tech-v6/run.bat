@echo off
REM Inicialização local — Windows
setlocal
cd /d "%~dp0"

echo.
echo ============================================
echo   1ª Feira Tech dos Jovens da Vocação
echo ============================================
echo.

where py >nul 2>&1
if %errorlevel% equ 0 (
    set "PYTHON=py -3"
) else (
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERRO] Python 3 nao encontrado.
        echo Instale o Python e marque "Add Python to PATH".
        pause
        exit /b 1
    )
    set "PYTHON=python"
)

if not exist ".venv\Scripts\python.exe" (
    echo [1/3] Criando ambiente virtual...
    %PYTHON% -m venv .venv
    if errorlevel 1 (
        echo [ERRO] Nao foi possivel criar o ambiente virtual.
        pause
        exit /b 1
    )
)

echo [2/3] Ativando ambiente virtual...
call ".venv\Scripts\activate.bat"

echo [3/3] Conferindo dependencias...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo [ERRO] Falha ao instalar dependencias.
    echo Verifique sua conexao com a internet.
    pause
    exit /b 1
)

if not defined ADMIN_PASSWORD set "ADMIN_PASSWORD=admin@feira2025"
if not defined SECRET_KEY set "SECRET_KEY=dev-secret-key-local"

echo.
echo Iniciando servidor...
echo A porta sera escolhida automaticamente se a 5000 estiver ocupada.
echo.
python app.py

echo.
echo O servidor foi encerrado.
pause
endlocal
