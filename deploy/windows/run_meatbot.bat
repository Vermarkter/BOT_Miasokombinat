@echo off
setlocal

set "PROJECT_ROOT=%~dp0..\.."
pushd "%PROJECT_ROOT%"

if exist "venv\Scripts\python.exe" (
    "venv\Scripts\python.exe" main.py
) else (
    python main.py
)

popd
endlocal
