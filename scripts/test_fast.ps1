$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "pytest_env.ps1")

& $PythonExe -m pytest -q --no-cov -m "not integration and not benchmark and not slow" @args
exit $LASTEXITCODE
