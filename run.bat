@echo off
pushd "%~dp0"
if exist ".venv\Scripts\pythonw.exe" (
  start "" ".venv\Scripts\pythonw.exe" "%~dp0main.py"
) else (
  start "" pythonw "%~dp0main.py"
)
popd
