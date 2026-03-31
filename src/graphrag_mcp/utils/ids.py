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
    return str(ULID()).lower()
