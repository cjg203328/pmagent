$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "pytest_env.ps1")

& $PythonExe -m pytest -q -m "not integration and not benchmark" @args
exit $LASTEXITCODE
