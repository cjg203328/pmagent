$ErrorActionPreference = "Stop"

python scripts/coverage_core.py
exit $LASTEXITCODE
