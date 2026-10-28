@echo off
setlocal EnableExtensions
chcp 65001 >nul
cd /d "%~dp0"

title Local Music — сборка

echo ============================================================
echo   Local Music — сборка Windows-версии
echo ============================================================
echo.
echo Папка сборки: %CD%
echo.

if not exist "interface_ultimate.py" goto MISSING_INTERFACE
if not exist "requirements.txt" goto MISSING_REQUIREMENTS
if not exist "local_music.ico" echo ВНИМАНИЕ: local_music.ico не найден. Сборка будет без собственной иконки.

echo [1/5] Установка зависимостей...
py -m pip install -r "requirements.txt"
if errorlevel 1 goto PIP_ERROR

echo.
echo [2/5] Установка PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto PYINSTALLER_INSTALL_ERROR

echo.
echo [3/5] Очистка предыдущей сборки...
if exist "build" rmdir /s /q "build"
if exist "dist\Local Music" rmdir /s /q "dist\Local Music"
if exist "Local Music.spec" del /q "Local Music.spec"

echo.
echo [4/5] Сборка EXE...
if exist "local_music.ico" (
    py -m PyInstaller --noconfirm --clean --onedir --windowed --name "Local Music" --icon "local_music.ico" --collect-all PyQt6 --collect-all PIL --hidden-import=vlc "interface_ultimate.py"
) else (
    py -m PyInstaller --noconfirm --clean --onedir --windowed --name "Local Music" --collect-all PyQt6 --collect-all PIL --hidden-import=vlc "interface_ultimate.py"
)
if errorlevel 1 goto BUILD_ERROR

set "OUT=%CD%\dist\Local Music"
if not exist "%OUT%\Local Music.exe" goto EXE_MISSING

echo.
echo [5/5] Копирование VLC...
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"

if not defined VLC_DIR goto NO_VLC

if not exist "%OUT%\vlc" mkdir "%OUT%\vlc"
copy /Y "%VLC_DIR%\libvlc.dll" "%OUT%\vlc\" >nul
copy /Y "%VLC_DIR%\libvlccore.dll" "%OUT%\vlc\" >nul
if exist "%VLC_DIR%\plugins" robocopy "%VLC_DIR%\plugins" "%OUT%\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
if exist "%VLC_DIR%\lua" robocopy "%VLC_DIR%\lua" "%OUT%\vlc\lua" /E /NFL /NDL /NJH /NJS /NP >nul
for %%F in ("%VLC_DIR%\*.dll") do copy /Y "%%~fF" "%OUT%\vlc\" >nul

echo VLC скопирован в папку приложения.

goto SUCCESS

:NO_VLC
echo ВНИМАНИЕ: VLC не найден по стандартному пути.
echo EXE собран, но звук потребует установленного VLC на компьютере.
goto SUCCESS

:SUCCESS
if exist "local_music.ico" copy /Y "local_music.ico" "%OUT%\" >nul
>"%OUT%\BUILD_INFO.txt" echo Local Music — автономная сборка
>>"%OUT%\BUILD_INFO.txt" echo Плеер: python-vlc + локальная libVLC
>>"%OUT%\BUILD_INFO.txt" echo Интерфейс: interface_ultimate.py

echo.
echo ============================================================
echo СБОРКА ЗАВЕРШЕНА УСПЕШНО
echo ============================================================
echo.
echo EXE:
echo %OUT%\Local Music.exe
echo.
echo Запускать можно файл:
echo dist\Local Music\Local Music.exe
echo.
pause
goto END

:MISSING_INTERFACE
echo ОШИБКА: рядом с BAT нет interface_ultimate.py
goto FAIL

:MISSING_REQUIREMENTS
echo ОШИБКА: рядом с BAT нет requirements.txt
goto FAIL

:PIP_ERROR
echo ОШИБКА: не удалось установить зависимости.
goto FAIL

:PYINSTALLER_INSTALL_ERROR
echo ОШИБКА: не удалось установить PyInstaller.
goto FAIL

:BUILD_ERROR
echo ОШИБКА: PyInstaller не смог собрать приложение.
goto FAIL

:EXE_MISSING
echo ОШИБКА: после сборки Local Music.exe не найден.
goto FAIL

:FAIL
echo.
echo Сборка остановлена.
pause

goto END

:END
endlocal
