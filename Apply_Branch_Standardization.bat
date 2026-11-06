@echo off
setlocal
set /p REPO_DIR=Enter the full path to your local CS-Rewards-Bot clone: 
if not exist "%REPO_DIR%\.git" (
  echo That folder is not a Git repository. Nothing was changed.
  pause
  exit /b 1
)
cd /d "%REPO_DIR%"
python "%~dp0apply_branch_standardization.py"
if errorlevel 1 (
  echo.
  echo Patch was not applied. Read the error above and do not continue.
  pause
  exit /b 1
)
echo.
echo Patch applied locally. No files have been committed or pushed.
echo Next run: python -m unittest discover -s tests -v
pause
