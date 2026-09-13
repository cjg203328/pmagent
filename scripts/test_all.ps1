$ErrorActionPreference = "Stop"

python -m pytest -q -m "not integration and not benchmark" @args
