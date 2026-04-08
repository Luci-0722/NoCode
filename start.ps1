# NoCode Agent - Windows 一键启动脚本 (PowerShell)
# Usage: .\start.ps1 [--resume] [-Install]

param(
    [switch]$Install,
    [Parameter(ValueFromRemainingArguments)]$ExtraArgs
)

$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ScriptDir

function Write-Status($icon, $msg) { Write-Host "$icon $msg" }

# ── 安装到 PATH ──
if ($Install) {
    $NocodeBin = Join-Path $ScriptDir "bin"
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")

    if ($UserPath -split ";" | Where-Object { $_ -eq $NocodeBin }) {
        Write-Status "✓" "PATH 已包含 $NocodeBin，无需重复添加"
    } else {
        [Environment]::SetEnvironmentVariable("Path", "$UserPath;$NocodeBin", "User")
        $env:Path += ";$NocodeBin"
        Write-Status "✓" "已将 $NocodeBin 添加到用户 PATH"
    }

    Write-Host ""
    Write-Status "✓" "安装完成！现在可以直接运行 nocode 启动了"
    Write-Status " " "（新终端窗口自动生效）"
    exit 0
}

# ── 1. 检查 Python ──
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "[ERROR] 未找到 python，请先安装 Python >= 3.12" -ForegroundColor Red
    exit 1
}
$pyVer = & python --version 2>&1 | ForEach-Object { $_ -replace "Python ", "" }
Write-Status "✓" "Python $pyVer"

& python -c "import sys; exit(0 if sys.version_info >= (3, 12) else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] 需要 Python >= 3.12，当前版本 $pyVer" -ForegroundColor Red
    exit 1
}

# ── 2. 检查 Node.js ──
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) {
    Write-Host "[ERROR] 未找到 node，请先安装 Node.js >= 20" -ForegroundColor Red
    exit 1
}
$nodeVer = & node -v
Write-Status "✓" "Node $nodeVer"

# ── 3. 创建虚拟环境并安装依赖 ──
if (-not (Test-Path ".venv")) {
    Write-Status "+" "创建虚拟环境..."
    & python -m venv .venv
}

& .\.venv\Scripts\Activate.ps1

$agentInstalled = & python -c "import nocode_agent" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Status "+" "安装 Python 依赖..."
    & pip install -e . -q
}

# ── 4. 配置文件检查 ──
if (-not (Test-Path "nocode_agent\config.yaml")) {
    if (Test-Path "nocode_agent\config.example.yaml") {
        Write-Host "[WARN] 未找到 config.yaml，从模板创建..." -ForegroundColor Yellow
        Copy-Item "nocode_agent\config.example.yaml" "nocode_agent\config.yaml"
        Write-Host "[WARN] 请编辑 nocode_agent\config.yaml 填入你的 API key" -ForegroundColor Yellow
        Write-Host "       notepad nocode_agent\config.yaml"
        Write-Host ""
        $choice = Read-Host "是否现在编辑？[Y/n]"
        if ($choice -ne "n" -and $choice -ne "N") {
            notepad "nocode_agent\config.yaml"
        }
    } else {
        Write-Host "[WARN] 未找到 config.yaml，将使用环境变量中的 API key" -ForegroundColor Yellow
    }
}

# ── 5. 提示安装到 PATH ──
$nocodeCmd = Get-Command nocode -ErrorAction SilentlyContinue
if (-not $nocodeCmd) {
    Write-Host ""
    Write-Host "[WARN] nocode 命令未加入 PATH" -ForegroundColor Yellow
    Write-Status ">" "运行 .\start.ps1 -Install 可添加到环境变量，之后直接 nocode 启动"
    Write-Host ""
}

# ── 6. 启动 ──
Write-Status ">" "启动 NoCode Agent..."
& .\bin\nocode.ps1 @ExtraArgs
