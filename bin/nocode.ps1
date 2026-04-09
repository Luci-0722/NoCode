# NoCode Agent - Windows TUI launcher (PowerShell)
# Usage: nocode [--resume]

param([Parameter(ValueFromRemainingArguments)]$Args)

# 自动定位项目目录
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Resolve-Path (Join-Path $ScriptDir "..")
$TuiPath = Join-Path $ProjectDir "nocode_agent\frontend\tui.ts"

# API key 环境变量
if (-not $env:ZHIPU_API_KEY) {
    $keyFile = Join-Path $env:USERPROFILE ".config\nocode\api_key"
    if (Test-Path $keyFile) {
        $env:ZHIPU_API_KEY = Get-Content $keyFile -Raw
    }
}

if (-not $env:NOCODE_API_KEY) {
    if ($env:OLLAMA_API_KEY) {
        $env:NOCODE_API_KEY = $env:OLLAMA_API_KEY
    } elseif ($env:OPENAI_API_KEY) {
        $env:NOCODE_API_KEY = $env:OPENAI_API_KEY
    } elseif ($env:ZHIPU_API_KEY) {
        $env:NOCODE_API_KEY = $env:ZHIPU_API_KEY
    }
}

if (-not $env:NOCODE_API_KEY -and -not $env:NOCODE_AGENT_CONFIG) {
    Write-Host "Warning: no API key detected."
    Write-Host "  If you use Ollama, set NOCODE_AGENT_CONFIG to an Ollama config file."
    Write-Host "  Or set one of: NOCODE_API_KEY / OPENAI_API_KEY / OLLAMA_API_KEY / ZHIPU_API_KEY"
}

Set-Location $ProjectDir
& node --experimental-strip-types $TuiPath @Args
