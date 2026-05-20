from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.unit
def test_substitute_vars_supports_defaults_and_empty_values(host_importer) -> None:
    templates = host_importer.module("lib.templates")

    rendered = templates.substitute_vars(
        "name=${LAB_NAME} missing=${MISSING:-fallback} empty=${EMPTY:-fallback} raw=${MISSING} escaped=$${SAMBA_UID}",
        {"LAB_NAME": "honeypotlab", "EMPTY": ""},
    )

    assert rendered == "name=honeypotlab missing=fallback empty=fallback raw= escaped=$${SAMBA_UID}"


@pytest.mark.unit
def test_render_template_writes_parent_directories_and_final_newline(host_importer, tmp_path: Path) -> None:
    templates = host_importer.module("lib.templates")
    source = tmp_path / "input.tmpl"
    output = tmp_path / "nested" / "rendered.yaml"
    source.write_text("lab: ${LAB_NAME}", encoding="utf-8")

    returned = templates.render_template(source, output, {"LAB_NAME": "honeypotlab"})

    assert returned == output
    assert output.read_text(encoding="utf-8") == "lab: honeypotlab\n"
