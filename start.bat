@echo off
setlocal enabledelayedexpansion

:: NoCode Agent - Windows 一键启动脚本
:: Usage: start.bat [--resume] [--install]

set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
cd /d "%SCRIPT_DIR%"

:: ── 安装到 PATH ──
if "%~1"=="--install" goto :install

:: ── 1. 检查 Python ──
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 python，请先安装 Python ^>= 3.12
    exit /b 1
)

for /f "tokens=2 delims= " %%v in ('python --version 2^>^&1') do set "PY_VER=%%v"
echo [INFO] Python %PY_VER% OK

:: 检查 Python 版本 >= 3.12
python -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"
if errorlevel 1 (
    echo [ERROR] 需要 Python ^>= 3.12，当前版本 %PY_VER%
    exit /b 1
)

:: ── 2. 检查 Node.js ──
where node >nul 2>&1
if errorlevel 1 (
    echo [ERROR] 未找到 node，请先安装 Node.js
    exit /b 1
)
for /f "tokens=*" %%v in ('node -v') do echo [INFO] Node %%v OK

:: ── 3. 安装 Node.js 依赖 ──
if not exist "node_modules\.bin\tsx.cmd" (
    echo [INFO] 安装 Node.js 依赖...
    call npm install
    if errorlevel 1 (
        echo [ERROR] npm install 失败
        exit /b 1
    )
)

:: ── 4. 创建虚拟环境并安装依赖 ──
if not exist ".venv" (
    echo [INFO] 创建虚拟环境...
    python -m venv .venv
)

call .venv\Scripts\activate.bat

python -c "import nocode_agent" >nul 2>&1
if errorlevel 1 (
    echo [INFO] 安装 Python 依赖...
    pip install -e . -q
)

:: ── 5. 配置文件检查 ──
if not exist "nocode_agent\config.yaml" (
    if exist "nocode_agent\config.example.yaml" (
        echo [WARN] 未找到 config.yaml，从模板创建...
        copy "nocode_agent\config.example.yaml" "nocode_agent\config.yaml" >nul
        echo [WARN] 请编辑 nocode_agent\config.yaml 填入你的 API key
        echo         notepad nocode_agent\config.yaml
        echo.
        set /p "choice=是否现在编辑？[Y/n] "
        if /i not "!choice!"=="n" (
            notepad "nocode_agent\config.yaml"
        )
    ) else (
        echo [WARN] 未找到 config.yaml，将使用环境变量中的 API key
    )
)

:: ── 6. 启动 ──
echo [INFO] 启动 NoCode Agent...
call bin\nocode.bat %*
exit /b %errorlevel%

:: ── 安装到 PATH ──
:install
set "NOCODE_BIN=%SCRIPT_DIR%\bin"

:: 检查是否已在 PATH 中
echo %PATH% | findstr /i /c:"%NOCODE_BIN%" >nul 2>&1
if not errorlevel 1 (
    echo [INFO] PATH 已包含 %NOCODE_BIN%，无需重复添加
    exit /b 0
)

:: 添加到用户 PATH（永久）
echo [INFO] 将 %NOCODE_BIN% 添加到用户 PATH...
powershell -Command "[Environment]::SetEnvironmentVariable('Path', [Environment]::GetEnvironmentVariable('Path', 'User') + ';%NOCODE_BIN%', 'User')"

:: 当前会话也生效
set "PATH=%PATH%;%NOCODE_BIN%"

echo [INFO] 已添加到用户 PATH！现在可以直接运行 nocode 启动了
echo [INFO] （新终端窗口自动生效）
exit /b 0
