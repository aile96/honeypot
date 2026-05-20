from __future__ import annotations

import importlib
import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
HOST_LIB_PARENT = PROJECT_ROOT / "src" / "lab"
CONTROLLER_ROOT = PROJECT_ROOT / "src" / "lab" / "lab-controller"


def _purge_modules(*prefixes: str) -> None:
    """Remove cached modules that use the ambiguous top-level package name `lib`."""
    for name in list(sys.modules):
        if any(name == prefix or name.startswith(prefix + ".") for prefix in prefixes):
            sys.modules.pop(name, None)


class PathImport:
    def __init__(self, root: Path, purge_prefixes: tuple[str, ...], extra_paths: tuple[Path, ...] = ()) -> None:
        self.root = root
        self.extra_paths = extra_paths
        self.purge_prefixes = purge_prefixes

    def _insert_paths(self) -> list[str]:
        paths = [str(self.root), *(str(path) for path in self.extra_paths)]
        for index, item in enumerate(paths):
            sys.path.insert(index, item)
        return paths

    def _remove_paths(self, paths: list[str]) -> None:
        for item in paths:
            try:
                sys.path.remove(item)
            except ValueError:
                pass

    def module(self, name: str) -> ModuleType:
        _purge_modules(*self.purge_prefixes, name)
        paths = self._insert_paths()
        try:
            return importlib.import_module(name)
        finally:
            self._remove_paths(paths)

    def script(self, name: str, path: Path) -> ModuleType:
        _purge_modules(*self.purge_prefixes, name)
        paths = self._insert_paths()
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            return module
        finally:
            self._remove_paths(paths)


@pytest.fixture
def host_importer() -> Iterator[PathImport]:
    importer = PathImport(HOST_LIB_PARENT, ("lib", "shared"))
    yield importer
    _purge_modules("lib", "shared")


@pytest.fixture
def controller_importer(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[PathImport]:
    # Safe defaults for modules that read environment variables at import time.
    runtime = tmp_path / "runtime"
    results = tmp_path / "results"
    code = tmp_path / "code"
    for directory in (runtime, results, code / "caldera", code / "hooks" / "controller"):
        directory.mkdir(parents=True, exist_ok=True)
    config_file = runtime / "config.toml"
    config_file.write_text(
        "[config]\n"
        f"RUNTIME_DIR = \"{runtime}\"\n"
        f"RESULTS_DIR = \"{results}\"\n"
        f"CODE_ROOT = \"{code}\"\n"
        f"CALDERA_ROOT = \"{code / 'caldera'}\"\n"
        f"CONTROLLER_HOOKS_DIR = \"{code / 'hooks' / 'controller'}\"\n"
        "CALDERA_SERVER_ENABLE = false\n"
        "LAB_NAME = \"honeypotlab\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_FILE", str(config_file))
    monkeypatch.setenv("RUNTIME_DIR", str(runtime))
    monkeypatch.setenv("RESULTS_DIR", str(results))
    monkeypatch.setenv("CODE_ROOT", str(code))
    monkeypatch.setenv("CALDERA_ROOT", str(code / "caldera"))
    monkeypatch.setenv("CONTROLLER_HOOKS_DIR", str(code / "hooks" / "controller"))
    monkeypatch.setenv("CALDERA_SERVER_ENABLE", "false")
    importer = PathImport(CONTROLLER_ROOT, ("lib", "app", "shared"), (HOST_LIB_PARENT,))
    yield importer
    _purge_modules("lib", "app", "shared")
