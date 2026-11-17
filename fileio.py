#!/usr/bin/env python3
"""
Shared file helper for the approach services.

write_json() writes to a temporary file in the same folder and then renames
it over the target. A rename is atomic, so a web server or script reading the
folder sees either the old file or the new one, never a half-written one.
"""

import json
import os


def write_json(path, data, indent=2):
    """Write data as JSON to path without ever exposing a partial file."""
    folder = os.path.dirname(path)
    if folder:
        os.makedirs(folder, exist_ok=True)
    tmp_path = f"{path}.tmp"
    try:
        with open(tmp_path, 'w') as f:
            json.dump(data, f, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.remove(tmp_path)
        except OSError:
            pass
        raise
