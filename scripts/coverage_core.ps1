$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "pytest_env.ps1")

& $PythonExe (Join-Path $PSScriptRoot "coverage_core.py")
exit $LASTEXITCODE
