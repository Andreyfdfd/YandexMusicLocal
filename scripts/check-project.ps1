#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot

Write-Host "Project: $ProjectRoot" -ForegroundColor Cyan
Write-Host "Python:" -ForegroundColor Cyan
python --version

$entryPoint = Join-Path $ProjectRoot 'interface_argon_optimized.py'
if (-not (Test-Path -LiteralPath $entryPoint)) {
    Write-Warning "Точка входа пока не найдена: $entryPoint"
    Write-Host "Сначала перенеси актуальный исходник в корень проекта." -ForegroundColor Yellow
    exit 2
}

Write-Host "Проверка синтаксиса Python..." -ForegroundColor Cyan
python -m py_compile $entryPoint
if ($LASTEXITCODE -ne 0) {
    throw "py_compile завершился с кодом $LASTEXITCODE"
}

Write-Host "Проверка завершена успешно." -ForegroundColor Green
