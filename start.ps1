$ErrorActionPreference = 'Stop'
Set-Location $PSScriptRoot
if (Test-Path '.venv\Scripts\python.exe') {
    & '.\.venv\Scripts\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} elseif (Test-Path '.runtime\python\python.exe') {
    & '.\.runtime\python\python.exe' -m uvicorn app.main:app --host 127.0.0.1 --port 8000
} else {
    Write-Error 'Python environment not found. Follow README.md to install dependencies.'
}
