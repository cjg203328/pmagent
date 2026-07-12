"""
日志脱敏工具
"""
import logging
import re

# 敏感信息模式
PATTERNS = {
    "api_key": re.compile(r'(api[_-]?key|apikey|token)\s*[:=]\s*["\']?([a-zA-Z0-9_-]{20,})["\']?', re.IGNORECASE),
    "phone": re.compile(r'1[3-9]\d{9}'),
    "email": re.compile(r'[\w\.-]+@[\w\.-]+\.\w+'),
    "id_card": re.compile(r'\d{17}[\dXx]'),
}

def mask_sensitive(text: str) -> str:
    """脱敏处理"""
    if not isinstance(text, str):
        return text

    # API Key
    text = PATTERNS["api_key"].sub(r'\1=***', text)

    # 手机号 - 保留前3后4
    def mask_phone(match):
        phone = match.group()
        return f"{phone[:3]}****{phone[-4:]}"
    text = PATTERNS["phone"].sub(mask_phone, text)

    # 邮箱 - 保留前2和域名
    def mask_email(match):
        email = match.group()
        name, domain = email.split('@')
        return f"{name[:2]}***@{domain}"
    text = PATTERNS["email"].sub(mask_email, text)

    # 身份证 - 保留前6后4
    def mask_id_card(match):
        id_card = match.group()
        return f"{id_card[:6]}********{id_card[-4:]}"
    text = PATTERNS["id_card"].sub(mask_id_card, text)

    return text

# 集成到 Logger

class SensitiveDataFilter(logging.Filter):
    """敏感信息过滤器"""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_sensitive(str(record.msg))
        if record.args:
            record.args = tuple(
                mask_sensitive(str(arg)) if isinstance(arg, str) else arg
                for arg in record.args
            )
        return True

# 在 utils/logger.py 中添加
# handler.addFilter(SensitiveDataFilter())
