$ErrorActionPreference = "Stop"

python -m pytest -q -m benchmark @args
