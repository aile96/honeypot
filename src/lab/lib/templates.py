"""Host-side template rendering helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from shared.templates import substitute_vars


def render_template(template: str | Path, output: str | Path, values: Mapping[str, Any]) -> Path:
    template_path = Path(template)
    output_path = Path(output)
    rendered = substitute_vars(template_path.read_text(encoding="utf-8"), values)
    if not rendered.endswith("\n"):
        rendered += "\n"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(rendered, encoding="utf-8")
    return output_path
