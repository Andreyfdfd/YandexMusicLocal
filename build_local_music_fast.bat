@echo off
setlocal
cd /d "%~dp0"

echo ============================================
echo   Local Music - быстрая сборка Windows EXE
echo ============================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден в PATH.
    pause
    exit /b 1
)

python -m pip install --upgrade pip
python -m pip install PyQt6 python-vlc mutagen pillow pyinstaller
if errorlevel 1 (
    echo Не удалось установить зависимости.
    pause
    exit /b 1
)

echo.
echo Сборка оптимизированной версии...
python -m PyInstaller --noconfirm --clean --windowed --onedir --name "Local Music" --icon "local_music.ico" "interface_universal_fast_final.py"
if errorlevel 1 (
    echo.
    echo Сборка завершилась с ошибкой.
    pause
    exit /b 1
)

echo.
echo Готово: dist\Local Music\Local Music.exe
echo.
pause
endlocal
