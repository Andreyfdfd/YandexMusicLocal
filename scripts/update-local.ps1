#requires -Version 7.0
[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\YandexMusicLocal',
    [string]$Branch = 'fix/incremental-library-scan'
)

$ErrorActionPreference = 'Stop'

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git не найден. Установи Git for Windows.'
}
if (-not (Test-Path -LiteralPath $ProjectRoot -PathType Container)) {
    throw "Папка проекта не найдена: $ProjectRoot"
}
$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$GitArgs)
    & git -C $ProjectRoot @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw ("Команда git завершилась с кодом {0}; git {1}" -f $LASTEXITCODE, ($GitArgs -join ' '))
    }
}

$origin = (& git -C $ProjectRoot remote get-url origin 2>$null | Select-Object -First 1)
if ($LASTEXITCODE -ne 0 -or $origin -notmatch 'github\.com[:/]Andreyfdfd/YandexMusicLocal(?:-)?(?:\.git)?/?$') {
    throw "Папка не подключена к ожидаемому GitHub-репозиторию (origin='$origin'). Ничего не изменено."
}

$status = @(& git -C $ProjectRoot status --porcelain)
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить рабочее состояние Git.' }
if ($status.Count -gt 0) {
    Write-Host 'В рабочем каталоге есть незакоммиченные изменения:' -ForegroundColor Yellow
    $status | ForEach-Object { Write-Host "  $_" }
    throw 'Обновление остановлено без сброса изменений. Сначала сохрани их в commit или отдельно скопируй на резервный носитель.'
}

Write-Host '[1/4] Получение обновлений...' -ForegroundColor Cyan
Invoke-Git -GitArgs @('fetch', 'origin')

$remoteRef = "refs/remotes/origin/$Branch"
& git -C $ProjectRoot show-ref --verify --quiet $remoteRef
if ($LASTEXITCODE -ne 0) {
    throw "В удалённом репозитории не найдена ветка origin/$Branch."
}

Write-Host "[2/4] Переключение на ветку $Branch..." -ForegroundColor Cyan
$localBranch = (& git -C $ProjectRoot branch --list $Branch | Out-String).Trim()
if ($localBranch) {
    Invoke-Git -GitArgs @('switch', $Branch)
} else {
    Invoke-Git -GitArgs @('switch', '--track', "origin/$Branch")
}

Write-Host '[3/4] Обновление без принудительного сброса...' -ForegroundColor Cyan
Invoke-Git -GitArgs @('pull', '--ff-only', 'origin', $Branch)

Write-Host '[4/4] Состояние проекта' -ForegroundColor Cyan
Invoke-Git -GitArgs @('status', '--short', '--branch')
Write-Host 'Обновление завершено. Локальные пользовательские данные не удалялись.' -ForegroundColor Green
