@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"
title YandexMusicLocal — сборка

echo ============================================================
echo   YandexMusicLocal — сборка Windows-версии
echo ============================================================
echo.

if not exist "main.py" (
    echo ОШИБКА: рядом с BAT не найден main.py
    goto FAIL
)
if not exist "requirements.txt" (
    echo ОШИБКА: рядом с BAT не найден requirements.txt
    goto FAIL
)
if not exist "Local Music.spec" (
    echo ОШИБКА: рядом с BAT не найден Local Music.spec
    goto FAIL
)

rem Stable release builds are created only from main. All other branches
rem produce an isolated preview build and shortcut, preserving the release icon.
set "BRANCH="
for /f "delims=" %%B in ('git branch --show-current 2^>nul') do set "BRANCH=%%B"
if /I "%BRANCH%"=="main" (
    set "BUILD_KIND=release"
    set "APP_FOLDER=YandexMusicLocal"
    set "SHORTCUT_NAME=Local Music.lnk"
) else (
    set "BUILD_KIND=preview"
    set "APP_FOLDER=YandexMusicLocalPreview"
    set "SHORTCUT_NAME=Local Music Preview.lnk"
)

set "PYTHON=%CD%\.venv\Scripts\python.exe"
if not exist "%PYTHON%" (
    echo [0/5] Создание виртуального окружения .venv...
    py -m venv ".venv"
    if errorlevel 1 goto VENV_ERROR
)
if not exist "%PYTHON%" goto VENV_ERROR

echo Используется Python:
"%PYTHON%" --version
if errorlevel 1 goto VENV_ERROR

echo.
echo Проверка: закрой Local Music перед пересборкой...
tasklist /FI "IMAGENAME eq YandexMusicLocal.exe" | find /I "YandexMusicLocal.exe" >nul
if not errorlevel 1 (
    echo ОШИБКА: приложение сейчас запущено. Закрой Local Music и повтори сборку.
    goto FAIL
)

echo.
echo [1/5] Установка зависимостей в .venv...
"%PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto PIP_ERROR
"%PYTHON%" -m pip install -r "requirements.txt"
if errorlevel 1 goto PIP_ERROR

echo.
echo [2/5] Установка PyInstaller...
"%PYTHON%" -m pip install --upgrade pyinstaller
if errorlevel 1 goto PYINSTALLER_ERROR

set "STAGE_DIST=%CD%\dist-staging"
set "STAGE_WORK=%CD%\build-staging"
set "STAGED_APP=%STAGE_DIST%\YandexMusicLocal"
set "FINAL_ROOT=%CD%\dist"
set "OUT=%FINAL_ROOT%\%APP_FOLDER%"
set "BACKUP=%FINAL_ROOT%\%APP_FOLDER%.backup"
set "HAD_OLD=0"

if exist "%STAGE_DIST%" rmdir /s /q "%STAGE_DIST%"
if exist "%STAGE_WORK%" rmdir /s /q "%STAGE_WORK%"
mkdir "%STAGE_DIST%" >nul 2>&1
if errorlevel 1 goto STAGING_ERROR

echo.
echo [3/5] Сборка во временную папку, без удаления предыдущей версии...
"%PYTHON%" -m PyInstaller --noconfirm --clean --workpath "%STAGE_WORK%" --distpath "%STAGE_DIST%" "Local Music.spec"
if errorlevel 1 goto BUILD_ERROR
if not exist "%STAGED_APP%\YandexMusicLocal.exe" goto EXE_MISSING

echo.
echo [4/5] Поиск и копирование VLC...
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"

if defined VLC_DIR (
    if not exist "%STAGED_APP%\vlc" mkdir "%STAGED_APP%\vlc"
    copy /Y "%VLC_DIR%\libvlc.dll" "%STAGED_APP%\vlc\" >nul
    if errorlevel 1 goto VLC_COPY_ERROR
    copy /Y "%VLC_DIR%\libvlccore.dll" "%STAGED_APP%\vlc\" >nul
    if errorlevel 1 goto VLC_COPY_ERROR
    for %%F in ("%VLC_DIR%\*.dll") do copy /Y "%%~fF" "%STAGED_APP%\vlc\" >nul
    if exist "%VLC_DIR%\plugins" (
        robocopy "%VLC_DIR%\plugins" "%STAGED_APP%\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
        if errorlevel 8 goto VLC_COPY_ERROR
    )
    if exist "%VLC_DIR%\lua" (
        robocopy "%VLC_DIR%\lua" "%STAGED_APP%\vlc\lua" /E /NFL /NDL /NJH /NJS /NP >nul
        if errorlevel 8 goto VLC_COPY_ERROR
    )
    echo VLC включён в папку приложения.
) else (
    echo ВНИМАНИЕ: VLC не найден. Для воспроизведения потребуется установленный VLC.
)

if exist "local_music.ico" copy /Y "local_music.ico" "%STAGED_APP%\" >nul
>"%STAGED_APP%\BUILD_INFO.txt" echo YandexMusicLocal
>>"%STAGED_APP%\BUILD_INFO.txt" echo Build kind: %BUILD_KIND%
>>"%STAGED_APP%\BUILD_INFO.txt" echo Entry point: main.py
>>"%STAGED_APP%\BUILD_INFO.txt" echo Build method: PyInstaller onedir
>>"%STAGED_APP%\BUILD_INFO.txt" echo Launch: YandexMusicLocal.exe
if /I "%BUILD_KIND%"=="preview" (
    >>"%STAGED_APP%\BUILD_INFO.txt" echo Shortcut: Local Music Preview.lnk
) else (
    >>"%STAGED_APP%\BUILD_INFO.txt" echo Shortcut: Local Music.lnk
)

echo.
echo [5/5] Установка собранной версии...
if not exist "%FINAL_ROOT%" mkdir "%FINAL_ROOT%"
if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
if exist "%OUT%" (
    move "%OUT%" "%BACKUP%" >nul
    if errorlevel 1 goto DEPLOY_ERROR
    set "HAD_OLD=1"
)

move "%STAGED_APP%" "%OUT%" >nul
if errorlevel 1 goto DEPLOY_RESTORE

set "APP_EXE=%OUT%\YandexMusicLocal.exe"
powershell.exe -NoProfile -ExecutionPolicy Bypass -Command "$ErrorActionPreference='Stop'; try { $desktop=[Environment]::GetFolderPath('Desktop'); if ([string]::IsNullOrWhiteSpace($desktop)) { $desktop=Join-Path $env:USERPROFILE 'Desktop' }; New-Item -ItemType Directory -Path $desktop -Force | Out-Null; $shell=New-Object -ComObject WScript.Shell; $link=Join-Path $desktop $env:SHORTCUT_NAME; $sc=$shell.CreateShortcut($link); $sc.TargetPath=$env:APP_EXE; $sc.Arguments=''; $sc.WorkingDirectory=Split-Path $env:APP_EXE; $sc.IconLocation=$env:APP_EXE+',0'; $sc.Description='Local Music - локальный музыкальный плеер'; $sc.Save() } catch { Write-Error $_; exit 1 }"
if errorlevel 1 (
    echo ВНИМАНИЕ: EXE собран, но ярлык не создан автоматически.
) else (
    echo Ярлык "%SHORTCUT_NAME%" создан и ведёт на EXE.
)

if exist "%BACKUP%" rmdir /s /q "%BACKUP%"
if exist "%STAGE_DIST%" rmdir /s /q "%STAGE_DIST%"
if exist "%STAGE_WORK%" rmdir /s /q "%STAGE_WORK%"

echo.
if /I "%BUILD_KIND%"=="preview" (
    echo СБОРКА ТЕСТОВОЙ ВЕРСИИ ЗАВЕРШЕНА
    echo Стабильный ярлык "Local Music.lnk" не изменён.
) else (
    echo СБОРКА СТАБИЛЬНОЙ ВЕРСИИ ЗАВЕРШЕНА
)
echo EXE: %APP_EXE%
echo Ярлык: %SHORTCUT_NAME%
echo Для запуска дважды щёлкни по ярлыку. Python вручную запускать не нужно.
echo.
pause
endlocal
exit /b 0

:DEPLOY_RESTORE
echo ОШИБКА: не удалось установить новую сборку.
if exist "%OUT%" rmdir /s /q "%OUT%"
if "%HAD_OLD%"=="1" if exist "%BACKUP%" move "%BACKUP%" "%OUT%" >nul
goto FAIL

:DEPLOY_ERROR
echo ОШИБКА: не удалось сохранить предыдущую сборку перед обновлением.
goto FAIL

:STAGING_ERROR
echo ОШИБКА: не удалось подготовить временную папку сборки.
goto FAIL

:VLC_COPY_ERROR
echo ОШИБКА: не удалось скопировать libVLC или его плагины.
goto FAIL

:VENV_ERROR
echo ОШИБКА: не удалось создать или запустить виртуальное окружение .venv.
echo Проверь Python Launcher командой: py -0p
goto FAIL

:PIP_ERROR
echo ОШИБКА: не удалось установить Python-зависимости.
goto FAIL

:PYINSTALLER_ERROR
echo ОШИБКА: не удалось установить PyInstaller.
goto FAIL

:BUILD_ERROR
echo ОШИБКА: PyInstaller не смог собрать приложение. Предыдущая сборка не изменялась.
goto FAIL

:EXE_MISSING
echo ОШИБКА: после сборки не найден YandexMusicLocal.exe.
goto FAIL

:FAIL
echo.
echo Сборка остановлена. Проверь вывод выше.
if exist "%STAGE_DIST%" rmdir /s /q "%STAGE_DIST%"
if exist "%STAGE_WORK%" rmdir /s /q "%STAGE_WORK%"
pause
endlocal
exit /b 1
