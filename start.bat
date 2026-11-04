@echo off
REM Starts CSE Advisor: builds the web app if needed, then runs the backend on http://localhost:8000
cd /d "%~dp0"
if not exist frontend\dist\index.html (
  echo Building web app...
  pushd frontend
  call npm install
  call npm run build
  popd
)
cd backend
if not exist .venv\Scripts\python.exe (
  echo Creating Python environment...
  python -m venv .venv
  .venv\Scripts\python -m pip install -e ".[dev]"
)
start "" http://localhost:8000
.venv\Scripts\python -m cse_advisor.main
