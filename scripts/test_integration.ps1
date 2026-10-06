$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "pytest_env.ps1")

$env:ART_ENABLE_INTEGRATION = "1"
& $PythonExe -m pytest -q -m integration @args
exit $LASTEXITCODE
