$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "pytest_env.ps1")

& $PythonExe -m pytest -q -m benchmark @args
exit $LASTEXITCODE
