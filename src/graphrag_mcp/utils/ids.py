"""Sortable unique ID generation using ULID.

ULIDs are used as primary keys throughout graphrag-mcp because they are:
- Globally unique (128-bit)
- Lexicographically sortable by creation time
- URL-safe string representation
- No coordination required (no sequences, no central authority)
"""

from __future__ import annotations

from ulid import ULID


def generate_id() -> str:
    """Generate a new ULID as a lowercase string.

    Returns:
        A 26-character lowercase ULID string, e.g. '01h5tn3k...'
    """
    return str(ULID()).lower()
