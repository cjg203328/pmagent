$ErrorActionPreference = "Stop"

$env:ART_ENABLE_INTEGRATION = "1"
python -m pytest -q -m integration @args
