"""Template rendering helpers shared by host and controller code."""

from __future__ import annotations

import re
from typing import Any, Mapping


def substitute_vars(text: str, values: Mapping[str, Any]) -> str:
    """Substitute ${VAR} and ${VAR:-default} placeholders.

    Escaped Compose placeholders such as $${VAR} are intentionally preserved.
    """

    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        default = match.group(2) or ""
        value = values.get(name, "")
        return default if value is None or str(value) == "" else str(value)

    return re.sub(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}", replace, text)
