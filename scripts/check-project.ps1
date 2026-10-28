#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    throw 'Python не найден в PATH. Проверь команду py -0p и доступность python.'
}

Write-Host "Project: $ProjectRoot" -ForegroundColor Cyan
python --version
$entryPoint = Join-Path $ProjectRoot 'main.py'

if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "Каноническая точка входа не найдена: $entryPoint"
}

Write-Host 'Проверка синтаксиса main.py...' -ForegroundColor Cyan
python -m py_compile $entryPoint
if ($LASTEXITCODE -ne 0) {
    throw "py_compile завершился с кодом $LASTEXITCODE"
}

Write-Host 'Синтаксис main.py корректен.' -ForegroundColor Green
