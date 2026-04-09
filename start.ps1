# NoCode Agent - Windows 一键启动脚本 (PowerShell)
# Usage: .\start.ps1 [--resume] [-Install]

param(
    [switch]$Install,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Write-Status {
    param(
        [string]$Icon,
        [string]$Message
    )

    Write-Host ($Icon + " " + $Message)
}

# 安装到 PATH
if ($Install) {
    $NocodeBin = Join-Path $ScriptDir "bin"
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")

    $PathEntries = @()
    if ($UserPath) {
        $PathEntries = $UserPath -split ";"
    }

    if ($PathEntries -contains $NocodeBin) {
        Write-Status "[OK]" "PATH already contains $NocodeBin"
    } else {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$NocodeBin", "User")
        $env:Path += ";$NocodeBin"
        Write-Status "[OK]" "Added $NocodeBin to user PATH"
    }

    Write-Host ""
    Write-Status "[OK]" "Install complete. You can run nocode directly now."
    Write-Status " " "(Open a new terminal window to refresh PATH)"
    exit 0
}

# 1. 检查 Python
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[ERROR] python not found. Please install Python >= 3.12" -ForegroundColor Red
    exit 1
}
$pyVerOutput = & python --version 2>&1
$pyVer = ($pyVerOutput | Select-Object -First 1).ToString() -replace "Python ", ""
Write-Status "[OK]" "Python $pyVer"

& python -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Python >= 3.12 is required. Current version: $pyVer" -ForegroundColor Red
    exit 1
}

# 2. 检查 Node.js
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host "[ERROR] node not found. Please install Node.js." -ForegroundColor Red
    exit 1
}
$nodeVer = & node -v
Write-Status "[OK]" "Node $nodeVer"

# 3. 安装前端依赖
if (-not (Test-Path "node_modules\.bin\tsx.cmd")) {
    Write-Status "[RUN]" "Installing Node.js dependencies..."
    & npm install
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] npm install failed." -ForegroundColor Red
        exit 1
    }
}

# 4. 创建虚拟环境并安装依赖
if (-not (Test-Path ".venv")) {
    Write-Status "[RUN]" "Creating virtual environment..."
    & python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1

$pythonDepsReady = $false
& python -c "import nocode_agent, langchain" 2>$null
if ($LASTEXITCODE -eq 0) {
    $pythonDepsReady = $true
}

if (-not $pythonDepsReady) {
    Write-Status "[RUN]" "Installing Python dependencies..."
    & python -m pip install -e .
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[ERROR] python dependency installation failed." -ForegroundColor Red
        exit 1
    }
}

# 5. 检查配置文件
if (-not (Test-Path "nocode_agent\config.yaml")) {
    if (Test-Path "nocode_agent\config.example.yaml") {
        Write-Host "[WARN] config.yaml not found. Creating it from template..." -ForegroundColor Yellow
        Copy-Item "nocode_agent\config.example.yaml" "nocode_agent\config.yaml"
        Write-Host "[WARN] Edit nocode_agent\config.yaml and fill in your API key" -ForegroundColor Yellow
        Write-Host "       notepad nocode_agent\config.yaml"
        Write-Host ""
        $choice = Read-Host "Edit now? [Y/n]"
        if ($choice -ne "n" -and $choice -ne "N") {
            notepad "nocode_agent\config.yaml"
        }
    } else {
        Write-Host "[WARN] config.yaml not found. Environment variables will be used for API keys." -ForegroundColor Yellow
    }
}

# 6. 提示安装到 PATH
$nocodeCmd = Get-Command nocode -ErrorAction SilentlyContinue
if (-not $nocodeCmd) {
    Write-Host ""
    Write-Host "[WARN] nocode is not in PATH" -ForegroundColor Yellow
    Write-Status ">" "Run .\start.ps1 -Install to add it to PATH, then use nocode directly."
    Write-Host ""
}

# 7. 启动
Write-Status ">" "Starting NoCode Agent..."
& .\bin\nocode.ps1 @ExtraArgs
