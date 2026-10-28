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

echo [1/4] Установка зависимостей...
py -m pip install --upgrade pip
if errorlevel 1 goto PIP_ERROR
py -m pip install -r "requirements.txt"
if errorlevel 1 goto PIP_ERROR

echo.
echo [2/4] Установка PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto PYINSTALLER_ERROR

echo.
echo [3/4] Сборка приложения из main.py...
if exist "build" rmdir /s /q "build"
if exist "dist\YandexMusicLocal" rmdir /s /q "dist\YandexMusicLocal"
py -m PyInstaller --noconfirm --clean "Local Music.spec"
if errorlevel 1 goto BUILD_ERROR

set "OUT=%CD%\dist\YandexMusicLocal"
if not exist "%OUT%\YandexMusicLocal.exe" goto EXE_MISSING

echo.
echo [4/4] Проверка и копирование VLC...
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"

if defined VLC_DIR (
    if not exist "%OUT%\vlc" mkdir "%OUT%\vlc"
    copy /Y "%VLC_DIR%\libvlc.dll" "%OUT%\vlc\" >nul
    copy /Y "%VLC_DIR%\libvlccore.dll" "%OUT%\vlc\" >nul
    for %%F in ("%VLC_DIR%\*.dll") do copy /Y "%%~fF" "%OUT%\vlc\" >nul
    if exist "%VLC_DIR%\plugins" robocopy "%VLC_DIR%\plugins" "%OUT%\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
    if exist "%VLC_DIR%\lua" robocopy "%VLC_DIR%\lua" "%OUT%\vlc\lua" /E /NFL /NDL /NJH /NJS /NP >nul
    echo VLC скопирован в папку приложения.
) else (
    echo ВНИМАНИЕ: VLC не найден. Для воспроизведения потребуется установленный VLC на этом ПК.
)

if exist "local_music.ico" copy /Y "local_music.ico" "%OUT%\" >nul
>"%OUT%\BUILD_INFO.txt" echo YandexMusicLocal
>>"%OUT%\BUILD_INFO.txt" echo Entry point: main.py
>>"%OUT%\BUILD_INFO.txt" echo Build method: PyInstaller onedir

echo.
echo СБОРКА ЗАВЕРШЕНА
echo EXE: %OUT%\YandexMusicLocal.exe
echo.
pause
goto END

:PIP_ERROR
echo ОШИБКА: не удалось установить Python-зависимости.
goto FAIL

:PYINSTALLER_ERROR
echo ОШИБКА: не удалось установить PyInstaller.
goto FAIL

:BUILD_ERROR
echo ОШИБКА: PyInstaller не смог собрать приложение.
goto FAIL

:EXE_MISSING
echo ОШИБКА: после сборки не найден YandexMusicLocal.exe.
goto FAIL

:FAIL
echo.
echo Сборка остановлена. Проверь вывод выше.
pause

:END
endlocal
