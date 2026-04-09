@echo off
:: NoCode Agent - Windows TUI launcher
:: Usage: nocode [--resume]

:: 自动定位项目目录
set "SCRIPT_DIR=%~dp0"
set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"
for %%I in ("%SCRIPT_DIR%\..") do set "PROJECT_DIR=%%~fI"
set "TUI_PATH=%PROJECT_DIR%\nocode_agent\frontend\tui.ts"

:: API key 环境变量
if not defined ZHIPU_API_KEY (
    if exist "%USERPROFILE%\.config\nocode\api_key" (
        set /p ZHIPU_API_KEY=<"%USERPROFILE%\.config\nocode\api_key"
    )
)

if defined NOCODE_API_KEY goto :has_key
if defined OLLAMA_API_KEY (set "NOCODE_API_KEY=%OLLAMA_API_KEY%" & goto :has_key)
if defined OPENAI_API_KEY (set "NOCODE_API_KEY=%OPENAI_API_KEY%" & goto :has_key)
if defined ZHIPU_API_KEY (set "NOCODE_API_KEY=%ZHIPU_API_KEY%" & goto :has_key)

if not defined NOCODE_AGENT_CONFIG (
    echo Warning: no API key detected.
    echo   If you use Ollama, set NOCODE_AGENT_CONFIG to an Ollama config file
    echo   Or set one of: NOCODE_API_KEY / OPENAI_API_KEY / OLLAMA_API_KEY / ZHIPU_API_KEY
)

:has_key
cd /d "%PROJECT_DIR%"
if exist "%PROJECT_DIR%\node_modules\.bin\tsx.cmd" (
    call "%PROJECT_DIR%\node_modules\.bin\tsx.cmd" "%TUI_PATH%" %*
    exit /b %errorlevel%
)

node --experimental-strip-types -e "console.log('ok')" >nul 2>&1
if not errorlevel 1 (
    node --experimental-strip-types "%TUI_PATH%" %*
    exit /b %errorlevel%
)

echo [ERROR] Current Node.js cannot run TypeScript directly.
echo [ERROR] Run npm install in the project root, or upgrade Node.js.
exit /b 1
