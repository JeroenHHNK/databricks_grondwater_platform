"""Series naming. Pastas model names cannot contain spaces, '+', '.', brackets, etc."""

from __future__ import annotations

import re
from pathlib import PurePath


def sanitize_name(name: str) -> str:
    """Turn a file name or stem into a valid Pastas model name.

    Mirrors the original repo: replace every character outside
    ``[a-zA-Z0-9_-]`` with ``_``, collapse repeated underscores, and strip
    leading/trailing underscores. A trailing ``.csv`` extension is removed first,
    so both a stem and a full file name can be passed.

    >>> sanitize_name("83034-2 HB017PB01 HH0114+34_BUKR_GMW_PB1_F-264.csv")
    '83034-2_HB017PB01_HH0114_34_BUKR_GMW_PB1_F-264'
    """
    stem = PurePath(name).stem if name.lower().endswith(".csv") else name
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "_", stem)
    cleaned = re.sub(r"_+", "_", cleaned)
    return cleaned.strip("_")
