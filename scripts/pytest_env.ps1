# Shared test environment bootstrap.
#
# Sourced (dot-included) by the test scripts so every local run:
#   1. Uses the project virtualenv Python when available, instead of whatever
#      "python" resolves to on PATH.
#   2. Keeps ALL temporary files inside the repository (.cache/tmp) instead of
#      the OS temp directory (C:\Users\<user>\AppData\Local\Temp on Windows).
#
# Dot-source usage from a script in this folder:
#   . (Join-Path $PSScriptRoot "pytest_env.ps1")

$ErrorActionPreference = "Stop"

$ProjectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot ".."))

$script:PythonExe = $null
$venvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $script:PythonExe = $venvPython
}
else {
    $pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($pythonCommand) {
        $script:PythonExe = $pythonCommand.Source
    }
}

if (-not $script:PythonExe) {
    Write-Error "Python interpreter not found. Create the project virtualenv or add Python to PATH."
    exit 1
}

# Windows Store alias stubs live under ...\WindowsApps and cannot run modules.
if ($script:PythonExe -like "*\WindowsApps\*") {
    Write-Error ("The 'python' on PATH resolves to a Windows Store alias stub: " + $script:PythonExe + ". Create .venv or use a real interpreter.")
    exit 1
}

$CacheRoot = Join-Path $ProjectRoot ".cache"
$CacheTmp = Join-Path $CacheRoot "tmp"
$CachePip = Join-Path $CacheRoot "pip"
New-Item -ItemType Directory -Force -Path $CacheTmp | Out-Null
New-Item -ItemType Directory -Force -Path $CachePip | Out-Null

# Temp isolation: pytest tmp_path, tempfile, and child processes stay in-repo.
$env:TMP = $CacheTmp
$env:TEMP = $CacheTmp
$env:TMPDIR = $CacheTmp

# pip cache isolation for installs triggered from test scripts.
$env:PIP_CACHE_DIR = $CachePip
