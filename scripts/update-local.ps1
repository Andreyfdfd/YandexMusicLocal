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
$originExitCode = $LASTEXITCODE
$origin = ([string]$origin).Trim().Trim('"').Trim("'")
$originIsValid = $false

# Accept HTTPS remotes with or without .git, and common SSH forms. Parsing the
# repository path separately is less brittle than matching the full URL in one regex.
if ($origin -match '^(?i:https?://github\.com/)(?<repoPath>.+)

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
) {
    $repoPath = ($Matches['repoPath'] -split '[?#]')[0]
    $repoPath = ($repoPath -replace '(?i)\.git/?

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
, '').Trim('/')
    $originIsValid = $repoPath -ieq 'Andreyfdfd/YandexMusicLocal'
}
elseif ($origin -match '^(?i:ssh://git@github\.com/|git@github\.com:)(?<repoPath>.+)

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
) {
    $repoPath = ($Matches['repoPath'] -split '[?#]')[0]
    $repoPath = ($repoPath -replace '(?i)\.git/?

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
, '').Trim('/')
    $originIsValid = $repoPath -ieq 'Andreyfdfd/YandexMusicLocal'
}

if ($originExitCode -ne 0 -or -not $originIsValid) {
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
