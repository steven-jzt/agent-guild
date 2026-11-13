"""Evidence redaction aligned with the existing TypeScript evidence writer."""
import re

SECRET_KEY = re.compile(r'^(api[_-]?key|(?:access|refresh)[_-]?token|authorization|password|secret|cookie)$', re.I)


def redact(value):
    if isinstance(value, str):
        value = re.sub(r"Bearer\s+[^\s\"'\\]+", 'Bearer [REDACTED]', value, flags=re.I)
        value = re.sub(r'\b(?:sk|dsk|ds)-[a-zA-Z0-9_-]{12,}', '[REDACTED]', value)
        return re.sub(r'\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b', '[REDACTED]', value)
    if isinstance(value, dict):
        return {k: '[REDACTED]' if SECRET_KEY.fullmatch(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value
