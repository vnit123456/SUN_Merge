@echo off
REM Launcher cho SUN Merge - tu dong tim python roi chay foldermerge.py
setlocal
set "SCRIPT=%~dp0foldermerge.py"

REM Uu tien pythonw (khong hien console), fallback sang python
set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if exist "%PYW%" (
    start "" "%PYW%" "%SCRIPT%"
    goto :eof
)

where pythonw >nul 2>&1 && ( start "" pythonw "%SCRIPT%" & goto :eof )
where python  >nul 2>&1 && ( python "%SCRIPT%" & goto :eof )

echo Khong tim thay Python. Hay cai Python 3 truoc.
pause
