"""Compatibility shim for the legacy flat Streamlit module."""

from artpm_agent.app import *  # noqa: F401,F403
from artpm_agent.app import main as _main


if __name__ == "__main__":
    _main()
