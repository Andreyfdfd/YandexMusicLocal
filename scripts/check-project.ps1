#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ProjectRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $ProjectRoot

$venvPython = Join-Path $ProjectRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $venvPython -PathType Leaf) {
    $pythonExe = $venvPython
} else {
    $pythonCommand = Get-Command python -ErrorAction SilentlyContinue
    if (-not $pythonCommand) {
        throw 'Python не найден. Выполни py -0p и создай виртуальное окружение .venv.'
    }
    $pythonExe = $pythonCommand.Source
}

Write-Host "Project: $ProjectRoot" -ForegroundColor Cyan
& $pythonExe --version
if ($LASTEXITCODE -ne 0) {
    throw "Python завершился с кодом $LASTEXITCODE"
}

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
