@echo off
REM Build SUN_Merge.exe (standalone, chay tren may khong co Python)
setlocal
set "PY=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
if not exist "%PY%" set "PY=python"

"%PY%" -m PyInstaller --noconfirm --onefile --windowed ^
    --name SUN_Merge ^
    --icon "%~dp0sun.ico" ^
    --add-data "%~dp0sun.ico;." ^
    --collect-all tkinterdnd2 ^
    --collect-all send2trash ^
    --distpath "%~dp0dist" ^
    --workpath "%~dp0build" ^
    --specpath "%~dp0" ^
    "%~dp0foldermerge.py"

echo.
echo === Xong. File o: %~dp0dist\SUN_Merge.exe ===
pause
