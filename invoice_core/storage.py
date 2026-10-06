"""Atomic stage/report writes so partial writes do not corrupt saved reviews."""
import json
import os
import tempfile
from pathlib import Path


def save_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, suffix='.tmp')
    try:
        with os.fdopen(fd, 'w', encoding='utf8') as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False, allow_nan=False)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)
