"""
Validators for data validation
"""
from typing import Any, List, Dict
from datetime import datetime
import operator
import re


class ValidationRule:
    """Validation rule"""

    def __init__(self, field: str, rule: str, severity: str, msg: str):
        self.field = field
        self.rule = rule
        self.severity = severity
        self.msg = msg


def check_rule(value: Any, rule: str) -> bool:
    """
    Check if value satisfies rule

    Args:
        value: Value to check
        rule: Rule expression (e.g., "value > 0", "value < 5000")

    Returns:
        True if rule satisfied
    """
    if value is None or not isinstance(rule, str):
        return False

    match = re.fullmatch(
        r"\s*(value|date)\s*(==|!=|>=|<=|>|<)\s*(.+?)\s*",
        rule,
        flags=re.IGNORECASE,
    )
    if not match:
        return False

    left_name, operation, raw_right = match.groups()
    operations = {
        "==": operator.eq,
        "!=": operator.ne,
        ">=": operator.ge,
        "<=": operator.le,
        ">": operator.gt,
        "<": operator.lt,
    }

    try:
        if left_name.lower() == "date" or raw_right.lower() == "today":
            left = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
            right = datetime.now() if raw_right.lower() == "today" else datetime.fromisoformat(
                raw_right.strip("'\"")
            )
        elif isinstance(value, bool):
            left = value
            right = raw_right.lower() == "true"
        elif isinstance(value, (int, float)):
            left = value
            right = float(raw_right)
        else:
            left = str(value)
            right = raw_right.strip("'\"")
        return operations[operation](left, right)
    except (TypeError, ValueError):
        return False


def validate_data(extracted: Dict[str, Any], doc_type: str, validation_rules: Dict[str, List[Dict]]) -> List[Dict]:
    """
    Validate extracted data against rules

    Args:
        extracted: Extracted data
        doc_type: Document type
        validation_rules: Validation rules dictionary

    Returns:
        List of anomalies found
    """
    anomalies = []
    rules = validation_rules.get(doc_type, [])

    for rule_config in rules:
        field = rule_config["field"]
        rule = rule_config["rule"]
        severity = rule_config["severity"]
        msg = rule_config["msg"]

        # Get value from nested structure
        value = get_nested_value(extracted, field)

        if not check_rule(value, rule):
            anomalies.append({
                "field": field,
                "issue": msg,
                "severity": severity,
                "current_value": value
            })

    return anomalies


def get_nested_value(data: Dict[str, Any], key_path: str) -> Any:
    """Get value from nested dict using dot notation"""
    keys = key_path.split(".")
    value = data

    for key in keys:
        if isinstance(value, dict):
            value = value.get(key)
        elif isinstance(value, list) and key.isdigit():
            idx = int(key)
            value = value[idx] if idx < len(value) else None
        else:
            return None

    return value
