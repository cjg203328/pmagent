"""
Utility functions
"""
import hashlib
import uuid
from pathlib import Path
from typing import Any, Dict


def generate_uuid() -> str:
    """Generate UUID string"""
    return str(uuid.uuid4())


def calculate_file_hash(file_path: str) -> str:
    """
    Calculate MD5 hash of file

    Args:
        file_path: File path

    Returns:
        MD5 hash string
    """
    md5_hash = hashlib.md5()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(4096), b""):
            md5_hash.update(chunk)
    return md5_hash.hexdigest()


def detect_file_format(file_path: str) -> str:
    """
    Detect file format from extension

    Args:
        file_path: File path

    Returns:
        File format (lowercase extension without dot)
    """
    path = Path(file_path)
    ext = path.suffix.lower().lstrip('.')
    return ext


def get_nested_value(data: Dict[str, Any], key_path: str, default: Any = None) -> Any:
    """
    Get nested dictionary value by dot-separated key path

    Args:
        data: Dictionary
        key_path: Dot-separated key path (e.g., "project_info.project_name")
        default: Default value if key not found

    Returns:
        Value at key path
    """
    keys = key_path.split(".")
    value = data

    for key in keys:
        if isinstance(value, dict) and key in value:
            value = value[key]
        else:
            return default

    return value


def format_currency(amount: float, currency: str = "CNY") -> str:
    """
    Format currency amount

    Args:
        amount: Amount
        currency: Currency code

    Returns:
        Formatted string
    """
    if currency == "CNY":
        return f"¥{amount:,.2f}"
    elif currency == "USD":
        return f"${amount:,.2f}"
    else:
        return f"{amount:,.2f} {currency}"
