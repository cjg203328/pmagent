$ErrorActionPreference = "Stop"

python -m pytest -q --no-cov -m "not integration and not benchmark and not slow" @args
