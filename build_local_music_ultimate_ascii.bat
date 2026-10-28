@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"

title Local Music - Build

echo ============================================================
echo Local Music EXE Builder
echo Working folder: %CD%
echo ============================================================
echo.

if not exist "interface_ultimate.py" goto MISSING_INTERFACE
if not exist "requirements.txt" goto MISSING_REQUIREMENTS

echo [1/6] Installing Python dependencies...
py -m pip install -r "requirements.txt"
if errorlevel 1 goto PIP_ERROR

echo.
echo [2/6] Installing PyInstaller...
py -m pip install --upgrade pyinstaller
if errorlevel 1 goto PYINSTALLER_ERROR

echo.
echo [3/6] Cleaning previous build...
if exist "build" rmdir /s /q "build"
if exist "dist\Local Music" rmdir /s /q "dist\Local Music"
if exist "Local Music.spec" del /q "Local Music.spec"

if exist "local_music.ico" goto BUILD_WITH_ICON

echo.
echo [4/6] Building EXE without custom icon...
py -m PyInstaller --noconfirm --clean --onedir --windowed --name "Local Music" --collect-all PyQt6 --collect-all PIL --hidden-import=vlc "interface_ultimate.py"
if errorlevel 1 goto BUILD_ERROR
goto COPY_VLC

:BUILD_WITH_ICON
echo.
echo [4/6] Building EXE...
py -m PyInstaller --noconfirm --clean --onedir --windowed --name "Local Music" --icon "local_music.ico" --collect-all PyQt6 --collect-all PIL --hidden-import=vlc "interface_ultimate.py"
if errorlevel 1 goto BUILD_ERROR

:COPY_VLC
set "OUT=%CD%\dist\Local Music"
if not exist "%OUT%\Local Music.exe" goto EXE_MISSING

echo.
echo [5/6] Copying VLC runtime...
set "VLC_DIR="
if exist "%ProgramFiles%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles%\VideoLAN\VLC"
if not defined VLC_DIR if exist "%ProgramFiles(x86)%\VideoLAN\VLC\libvlc.dll" set "VLC_DIR=%ProgramFiles(x86)%\VideoLAN\VLC"

if not defined VLC_DIR goto NO_VLC

if not exist "%OUT%\vlc" mkdir "%OUT%\vlc"
if exist "%VLC_DIR%\libvlc.dll" copy /Y "%VLC_DIR%\libvlc.dll" "%OUT%\vlc\" >nul
if exist "%VLC_DIR%\libvlccore.dll" copy /Y "%VLC_DIR%\libvlccore.dll" "%OUT%\vlc\" >nul
if exist "%VLC_DIR%\plugins" robocopy "%VLC_DIR%\plugins" "%OUT%\vlc\plugins" /E /NFL /NDL /NJH /NJS /NP >nul
if exist "%VLC_DIR%\lua" robocopy "%VLC_DIR%\lua" "%OUT%\vlc\lua" /E /NFL /NDL /NJH /NJS /NP >nul

echo VLC runtime copied from:
echo %VLC_DIR%
goto FINISH

:NO_VLC
echo.
echo WARNING: VLC was not found in the default folders.
echo The EXE was built, but VLC runtime was not copied.
echo Install VLC or copy libvlc.dll, libvlccore.dll and plugins manually.
goto FINISH

:FINISH
echo.
echo [6/6] Build finished.
if exist "local_music.ico" copy /Y "local_music.ico" "%OUT%\local_music.ico" >nul

echo.
echo ============================================================
echo EXE:
echo %OUT%\Local Music.exe
echo ============================================================
echo.
echo Press any key to close this window.
pause >nul
goto END

:MISSING_INTERFACE
echo ERROR: interface_ultimate.py is missing.
goto FAIL

:MISSING_REQUIREMENTS
echo ERROR: requirements.txt is missing.
goto FAIL

:PIP_ERROR
echo ERROR: Failed to install Python dependencies.
goto FAIL

:PYINSTALLER_ERROR
echo ERROR: Failed to install PyInstaller.
goto FAIL

:BUILD_ERROR
echo ERROR: PyInstaller failed to build the EXE.
goto FAIL

:EXE_MISSING
echo ERROR: Local Music.exe was not created.
goto FAIL

:FAIL
echo.
echo Build stopped.
echo Press any key to close this window.
pause >nul

:END
endlocal
