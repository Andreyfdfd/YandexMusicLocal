#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot

Write-Host "Project: $ProjectRoot" -ForegroundColor Cyan
python --version
if ($LASTEXITCODE -ne 0) {
    throw 'Python не запустился. Проверь PATH или используй .\.venv\Scripts\python.exe.'
}

$pythonExe = (Get-Command python).Source
$entryPoint = Join-Path $ProjectRoot 'main.py'
if (-not (Test-Path -LiteralPath $entryPoint -PathType Leaf)) {
    throw "Каноническая точка входа не найдена: $entryPoint"
}

Write-Host '[1/3] Проверка синтаксиса main.py...' -ForegroundColor Cyan
& $pythonExe -m py_compile $entryPoint
if ($LASTEXITCODE -ne 0) {
    throw "py_compile завершился с кодом $LASTEXITCODE"
}

Write-Host '[2/3] Регрессионные тесты...' -ForegroundColor Cyan
& $pythonExe -m unittest discover -s tests -v
if ($LASTEXITCODE -ne 0) {
    throw "Тесты завершились с кодом $LASTEXITCODE"
}

Write-Host '[3/3] Проверки завершены успешно.' -ForegroundColor Green
