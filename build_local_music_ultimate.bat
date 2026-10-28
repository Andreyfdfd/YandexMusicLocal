@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ============================================================
echo  Local Music — сборка оптимизированной Windows-версии
echo ============================================================
echo.

if not exist "interface_ultimate.py" (
    echo ОШИБКА: рядом с BAT нет interface_ultimate.py
    pause
    exit /b 1
)

if not exist "requirements.txt" (
    echo ОШИБКА: рядом с BAT нет requirements.txt
    pause
    exit /b 1
)

echo [1/5] Обновление зависимостей...
py -m pip install -r requirements.txt
if errorlevel 1 (
    echo ОШИБКА: не удалось установить зависимости.
    pause
    exit /b 1
)

echo.
echo [2/5] Проверка PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 (
    echo ОШИБКА: не удалось установить PyInstaller.
    pause
    exit /b 1
)

echo.
echo [3/5] Сборка EXE...
if exist "build" rmdir /s /q "build"
if exist "dist\Local Music" rmdir /s /q "dist\Local Music"

py -m PyInstaller ^
  --noconfirm ^
  --clean ^
  --onedir ^
  --windowed ^
  --name "Local Music" ^
  --icon "local_music.ico" ^
  --collect-all PyQt6 ^
  --collect-all PIL ^
  --hidden-import=vlc ^
  "interface_ultimate.py"

if errorlevel 1 (
    echo ОШИБКА: PyInstaller не собрал приложение.
    pause
    exit /b 1
)

set "OUT=dist\Local Music"
if not exist "%OUT%\Local Music.exe" (
    echo ОШИБКА: EXE не найден после сборки.
    pause
    exit /b 1
)

echo.
echo [4/5] Подключение libVLC...
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"

if not defined VLC_DIR (
    echo ВНИМАНИЕ: установленная версия VLC не найдена.
    echo EXE будет собран, но для звука на конечной машине потребуется VLC Media Player.
) else (
    if not exist "%OUT%\vlc" mkdir "%OUT%\vlc"
    copy /Y "%VLC_DIR%\libvlc.dll" "%OUT%\vlc\" >nul
    copy /Y "%VLC_DIR%\libvlccore.dll" "%OUT%\vlc\" >nul
    robocopy "%VLC_DIR%\plugins" "%OUT%\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
    if exist "%VLC_DIR%\lua" robocopy "%VLC_DIR%\lua" "%OUT%\vlc\lua" /E /NFL /NDL /NJH /NJS /NP >nul
    for %%F in ("%VLC_DIR%\*.dll") do copy /Y "%%~fF" "%OUT%\vlc\" >nul
    echo libVLC скопирован в папку приложения.
)

if exist "local_music.ico" copy /Y "local_music.ico" "%OUT%\" >nul

(echo Local Music — автономная сборка) > "%OUT%\BUILD_INFO.txt"
(echo Плеер: python-vlc + локальная libVLC) >> "%OUT%\BUILD_INFO.txt"
(echo Обложки: Pillow, извлекаются в локальный кэш) >> "%OUT%\BUILD_INFO.txt"


echo.
echo [5/5] Готово.
echo.
echo EXE:
echo   %CD%\%OUT%\Local Music.exe
echo.
echo ВАЖНО:
echo   Собирать лучше на машине, где установлен обычный VLC.
echo   Тогда BAT упакует libVLC и plugins рядом с EXE.
echo.
echo Теперь можно запускать Local Music.exe.
echo.
pause
endlocal
