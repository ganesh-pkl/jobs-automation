"""Read simple KEY=value .env settings without executing shell expressions."""
import os
from pathlib import Path


def setting(name, default=None):
    if name in os.environ:
        return os.environ[name]
    path = Path(__file__).resolve().parent.parent / '.env'
    if path.exists():
        for raw in path.read_text(encoding='utf-8').splitlines():
            line = raw.strip()
            if not line or line.startswith('#') or '=' not in line:
                continue
            key, value = line.split('=', 1)
            if key.strip() == name:
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                return value
    return default
