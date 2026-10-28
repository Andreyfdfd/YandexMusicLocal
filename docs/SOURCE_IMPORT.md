# Импорт локального исходного кода

Скрипт `scripts/Bootstrap-YandexMusicLocal.ps1` помогает связать существующую папку `D:\YandexMusicLocal` с GitHub и отправить в отдельную ветку только исходники и ресурсы приложения.

## 1. Получить скрипт из приватного репозитория

В PowerShell 7:

```powershell
$BootstrapClone = Join-Path $env:TEMP 'YandexMusicLocal-bootstrap'
if (Test-Path $BootstrapClone) {
    Remove-Item $BootstrapClone -Recurse -Force
}
git clone --branch feature/import-source-baseline https://github.com/Andreyfdfd/YandexMusicLocal.git $BootstrapClone
```

Команда клонирует вспомогательную ветку во временную папку. Если Git запросит вход в GitHub, заверши аутентификацию в терминале. Не вставляй токены доступа в командную строку и не отправляй их в чат.

## 2. Подключить локальный проект и загрузить исходники

Если код уже находится в `D:\YandexMusicLocal`:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "$BootstrapClone\scripts\Bootstrap-YandexMusicLocal.ps1" -ProjectRoot 'D:\YandexMusicLocal'
```

Если файлы исходного архива сначала нужно импортировать из отдельной распакованной папки, укажи её через `-SourceRoot`. Например:

```powershell
pwsh -NoProfile -ExecutionPolicy Bypass -File "$BootstrapClone\scripts\Bootstrap-YandexMusicLocal.ps1" -ProjectRoot 'D:\YandexMusicLocal' -SourceRoot 'D:\MusicPlayerSource\LocalMusic'
```

Скрипт делает резервную копию конфликтующих документационных файлов, переключает рабочую папку на `feature/import-source-baseline`, подготавливает файлы к публикации и показывает их список. Для отправки требуется явное подтверждение `YES`.

## Что попадёт в GitHub

Разрешены только исходники и необходимые ресурсы: `.py`, `.bat`, `.spec`, `.ico`, `.ps1`. Вспомогательная папка с альтернативными версиями переносится в `legacy-source`, если она импортируется из отдельного каталога.

Музыкальные файлы, SQLite-базы, архивы, кэш, виртуальное окружение и готовые сборки в этом процессе не добавляются. Перед подтверждением всегда просмотри список staged-файлов.

## Если команда остановилась

Не удаляй резервные копии. Скопируй в чат текст ошибки и вывод:

```powershell
Set-Location 'D:\YandexMusicLocal'
git status --short --branch
git remote -v
git branch --show-current
```

Не используй `git reset --hard` и не удаляй локальную базу вручную.
