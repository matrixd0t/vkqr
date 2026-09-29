#Requires -Version 5.1
<#
.SYNOPSIS
    vkqr — один скрипт для Windows: ставит Python (при необходимости), pipx
    и запускает утилиту vkqr.

.DESCRIPTION
    Скачать и запустить:
      irm https://raw.githubusercontent.com/matrixd0t/vkqr/master/scripts/vkqr.ps1 -OutFile vkqr.ps1
      powershell -ExecutionPolicy Bypass -File .\vkqr.ps1 -o cookies.json
#>
$ErrorActionPreference = "Stop"
$RepoUrl = if ($env:VKQR_REPO) { $env:VKQR_REPO } else { "https://github.com/matrixd0t/vkqr.git" }

$AppArgs = $args

function Write-Log([string]$Message) { Write-Host "vkqr: $Message" -ForegroundColor Cyan }
function Test-Cmd([string]$Name) { [bool](Get-Command $Name -ErrorAction SilentlyContinue) }
function Update-Path {
    $machine = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $user = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$machine;$user"
}

$script:PyExe = $null
$script:PyPrefix = @()
function Find-Python {
    if (Test-Cmd "py") { $script:PyExe = "py"; $script:PyPrefix = @("-3"); return $true }
    if (Test-Cmd "python") { $script:PyExe = "python"; $script:PyPrefix = @(); return $true }
    if (Test-Cmd "python3") { $script:PyExe = "python3"; $script:PyPrefix = @(); return $true }
    return $false
}
function Invoke-Py {
    param([Parameter(Mandatory)][string[]]$Arguments)
    $prefix = $script:PyPrefix
    & $script:PyExe @prefix @Arguments
}

function Ensure-Python {
    if (Find-Python) { return }
    Write-Log "Python не найден — пробую установить через winget..."
    if (Test-Cmd "winget") {
        winget install -e --id Python.Python.3.12 --accept-source-agreements --accept-package-agreements
        Update-Path
        [void](Find-Python)
    }
    if (-not $script:PyExe) {
        throw "Не удалось установить Python. Установите Python 3.9+ вручную и повторите запуск."
    }
    Invoke-Py @("-c", "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)")
    if ($LASTEXITCODE -ne 0) { throw "Требуется Python 3.9+." }
}

function Ensure-Pipx {
    Invoke-Py @("-m", "pipx", "--version") *> $null
    if ($LASTEXITCODE -eq 0) { return }
    Write-Log "pipx не найден — устанавливаю..."
    Invoke-Py @("-m", "pip", "install", "--user", "--upgrade", "pipx")
    if ($LASTEXITCODE -ne 0) { throw "Не удалось установить pipx." }
}

if (Test-Cmd "vkqr") {
    & vkqr @AppArgs
    exit $LASTEXITCODE
}

Ensure-Python
Ensure-Pipx

$call = @("-m", "pipx", "run", "--spec", "git+$RepoUrl", "vkqr") + $AppArgs
Invoke-Py $call
exit $LASTEXITCODE
