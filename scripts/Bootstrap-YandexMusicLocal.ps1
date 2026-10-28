#requires -Version 7.0
<#
.SYNOPSIS
  Safely connect D:\YandexMusicLocal to the private GitHub repository and publish
  source files only. Local music, database files, caches and build output are excluded.
.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\Bootstrap-YandexMusicLocal.ps1
.EXAMPLE
  pwsh -NoProfile -ExecutionPolicy Bypass -File .\Bootstrap-YandexMusicLocal.ps1 -ProjectRoot D:\YandexMusicLocal -SourceRoot D:\YandexMusicLocal\Extracted\LocalMusic
#>
[CmdletBinding()]
param(
    [string]$ProjectRoot = 'D:\YandexMusicLocal',
    [string]$SourceRoot = ''
)

$ErrorActionPreference = 'Stop'
$RepoUrl = 'https://github.com/Andreyfdfd/YandexMusicLocal.git'
$Branch = 'feature/import-source-baseline'
$AllowedExtensions = @('.py', '.bat', '.spec', '.ico', '.ps1')
$SkipDirectories = @(
    '.git', '.venv', 'venv', 'env', '__pycache__', '.pytest_cache',
    'node_modules', 'build', 'dist', 'release', 'cache', 'caches',
    'artwork_cache', 'cover_cache', 'logs', 'temp', 'tmp',
    'music', 'media', 'audio', 'downloads', 'music_library'
)

function Invoke-Git {
    param([Parameter(Mandatory)][string[]]$GitArgs)
    & git -C $ProjectRoot @GitArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Команда git завершилась с кодом $LASTEXITCODE. Аргументы: git $($GitArgs -join ' ')"
    }
}

function Get-SafeSourceFiles {
    param([Parameter(Mandatory)][string]$Root)

    $rootFull = [System.IO.Path]::GetFullPath($Root)
    if (-not (Test-Path -LiteralPath $rootFull -PathType Container)) {
        throw "Папка исходников не найдена: $rootFull"
    }

    $results = [System.Collections.Generic.List[System.IO.FileInfo]]::new()
    $queue = [System.Collections.Generic.Queue[string]]::new()
    $queue.Enqueue($rootFull)

    while ($queue.Count -gt 0) {
        $current = $queue.Dequeue()
        foreach ($file in (Get-ChildItem -LiteralPath $current -File -Force -ErrorAction Stop)) {
            if ($AllowedExtensions -contains $file.Extension.ToLowerInvariant()) {
                $results.Add($file)
            }
        }
        foreach ($dir in (Get-ChildItem -LiteralPath $current -Directory -Force -ErrorAction SilentlyContinue)) {
            if ($SkipDirectories -notcontains $dir.Name.ToLowerInvariant()) {
                $queue.Enqueue($dir.FullName)
            }
        }
    }
    return $results
}

if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw 'Git не найден. Установи Git for Windows и перезапусти Windows Terminal.'
}

$ProjectRoot = [System.IO.Path]::GetFullPath($ProjectRoot)
if (-not (Test-Path -LiteralPath $ProjectRoot)) {
    New-Item -ItemType Directory -Path $ProjectRoot -Force | Out-Null
}
if (-not $SourceRoot) { $SourceRoot = $ProjectRoot }
$SourceRoot = [System.IO.Path]::GetFullPath($SourceRoot)

Write-Host ''
Write-Host "[1/6] Проверка рабочего каталога: $ProjectRoot" -ForegroundColor Cyan

$hasGit = Test-Path -LiteralPath (Join-Path $ProjectRoot '.git')
if ($hasGit) {
    $existingUrl = (& git -C $ProjectRoot remote get-url origin 2>$null | Select-Object -First 1)
    $existingBranch = (& git -C $ProjectRoot branch --show-current 2>$null | Select-Object -First 1)
    if ($existingUrl -notmatch 'github\.com[:/]Andreyfdfd/YandexMusicLocal(?:-)?\.git/?$' -or $existingBranch -ne $Branch) {
        throw "В $ProjectRoot уже есть Git-репозиторий (origin=$existingUrl, branch=$existingBranch). Скрипт остановлен без сброса файлов. Проверь репозиторий вручную."
    }
    Write-Host 'Обнаружен уже настроенный рабочий репозиторий; текущую ветку сбрасывать не буду.' -ForegroundColor Green
} else {
    Invoke-Git -GitArgs @('init', '-b', $Branch)
    Invoke-Git -GitArgs @('remote', 'add', 'origin', $RepoUrl)
}

Write-Host ''
Write-Host '[2/6] Получение ветки с GitHub...' -ForegroundColor Cyan
Invoke-Git -GitArgs @('fetch', 'origin', $Branch)

$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$backupRoot = Join-Path (Split-Path -Parent $ProjectRoot) ("YandexMusicLocal-backup-" + $stamp)
$conflicts = @('README.md', '.gitignore', '.gitattributes', 'CONTRIBUTING.md', 'requirements.txt', 'docs', 'scripts')
$backedUp = [System.Collections.Generic.List[string]]::new()

if (-not $hasGit) {
    foreach ($name in $conflicts) {
        $from = Join-Path $ProjectRoot $name
        if (Test-Path -LiteralPath $from) {
            $to = Join-Path $backupRoot $name
            New-Item -ItemType Directory -Path (Split-Path -Parent $to) -Force | Out-Null
            Move-Item -LiteralPath $from -Destination $to -Force
            $backedUp.Add($name)
        }
    }
    Invoke-Git -GitArgs @('switch', '-C', $Branch, '--track', "origin/$Branch")
}

foreach ($name in @('docs', 'scripts')) {
    $oldDir = Join-Path $backupRoot $name
    if (Test-Path -LiteralPath $oldDir -PathType Container) {
        $destinationBase = Join-Path $ProjectRoot (Join-Path 'legacy-source' $name)
        foreach ($file in (Get-SafeSourceFiles -Root $oldDir)) {
            $relative = [System.IO.Path]::GetRelativePath($oldDir, $file.FullName)
            $destination = Join-Path $destinationBase $relative
            New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
            Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
        }
    }
}

if ($SourceRoot -ne $ProjectRoot) {
    Write-Host ''
    Write-Host "[3/6] Импорт исходников из: $SourceRoot" -ForegroundColor Cyan
    $externalFiles = @(Get-SafeSourceFiles -Root $SourceRoot)
    if ($externalFiles.Count -eq 0) {
        throw 'В указанной папке не обнаружены исходные файлы (.py/.bat/.spec/.ico/.ps1).'
    }
    foreach ($file in $externalFiles) {
        $relative = [System.IO.Path]::GetRelativePath($SourceRoot, $file.FullName)
        if ($file.Name -ieq 'interface_argon_optimized.py') {
            $destination = Join-Path $ProjectRoot 'interface_argon_optimized.py'
        } else {
            $destination = Join-Path (Join-Path $ProjectRoot 'legacy-source\ImportedSource') $relative
        }
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $file.FullName -Destination $destination -Force
    }
}

Write-Host ''
Write-Host '[4/6] Поиск безопасных исходников...' -ForegroundColor Cyan
$sourceFilesInRepo = @(Get-SafeSourceFiles -Root $ProjectRoot)
if ($sourceFilesInRepo.Count -eq 0) {
    throw "Исходники не найдены. Проверь SourceRoot. Резервная копия старых метаданных: $backupRoot"
}

$rootFull = [System.IO.Path]::GetFullPath($ProjectRoot)
$relativePaths = @(
    foreach ($file in $sourceFilesInRepo) {
        $rel = [System.IO.Path]::GetRelativePath($rootFull, $file.FullName) -replace '\\', '/'
        if ($rel -notmatch '(^|/)(__pycache__|\.venv|venv|build|dist|cache|artwork_cache|cover_cache)(/|$)') {
            $rel
        }
    }
) | Sort-Object -Unique

Invoke-Git -GitArgs (@('add', '--') + $relativePaths)

$staged = @(& git -C $ProjectRoot diff --cached --name-only)
if ($LASTEXITCODE -ne 0) { throw 'Не удалось проверить подготовленные файлы.' }

$unsafe = @($staged | Where-Object {
    $_ -match '(?i)\.(mp3|flac|wav|m4a|aac|ogg|opus|wma|ape|alac|aiff|db|sqlite|sqlite3|rar|zip|7z|exe|dll|msi|pyc)$'
})
if ($unsafe.Count -gt 0) {
    throw "Найдены недопустимые файлы в индексе Git: $($unsafe -join ', '). Ничего не отправлено."
}

Write-Host ''
Write-Host '[5/6] Файлы, подготовленные к публикации:' -ForegroundColor Cyan
$staged | ForEach-Object { Write-Host "  + $_" }
& git -C $ProjectRoot diff --cached --stat

Write-Host ''
Write-Host 'Музыка, базы данных, кэш и готовые сборки не добавляются этим скриптом.' -ForegroundColor Green
if ($backedUp.Count -gt 0) {
    Write-Host "Резервная копия предыдущих документов/метаданных: $backupRoot" -ForegroundColor DarkGray
}

$answer = Read-Host 'Проверь список выше. Создать commit и отправить исходники в GitHub? Введи YES для продолжения'
if ($answer -cne 'YES') {
    Write-Host 'Остановлено. Отправки не было; подготовленные изменения оставлены для ручной проверки.' -ForegroundColor Yellow
    exit 0
}

if ($staged.Count -gt 0) {
    Invoke-Git -GitArgs @('commit', '-m', 'feat: import existing player source baseline')
}
Invoke-Git -GitArgs @('push', '-u', 'origin', $Branch)

Write-Host ''
Write-Host "Готово. Ветка опубликована: $Branch" -ForegroundColor Green
Write-Host 'После проверки откроем Pull Request из этой ветки в develop.' -ForegroundColor Green
