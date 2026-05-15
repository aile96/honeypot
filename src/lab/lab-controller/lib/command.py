#!/usr/bin/env python3
"""Provide safe subprocess execution helpers for the controller.

The module centralizes command discovery, environment construction, timeout
handling, stdout/stderr capture, and error reporting. Pipeline steps and hooks use
it so command failures are logged consistently and can raise CommandError when
callers need to recover."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import Mapping

from .config import Config, config_to_env
from .logging import die


class CommandError(Exception):
    """Exception raised when a command fails."""


def require_command(command: str) -> None:
    """Require a command to be available in PATH."""
    if shutil.which(command) is None:
        die(f"Required command not found: {command}")


def run_cmd(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    timeout_seconds: int | float | None = None,
    raise_on_error: bool = False,
    config: Config | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and return the result.

    If CONFIG is provided, it is converted into a subprocess-local environment.
    The parent process environment is not mutated.
    """
    stdout = subprocess.PIPE if capture_output else None
    stderr = subprocess.PIPE if capture_output else None

    if quiet and not capture_output:
        stdout = subprocess.DEVNULL
        stderr = subprocess.DEVNULL

    command_env = config_to_env(config) if config is not None else None
    if env is not None:
        command_env = dict(env)

    command_text = " ".join(cmd)

    try:
        completed = subprocess.run(
            cmd,
            text=True,
            input=input_text,
            stdout=stdout,
            stderr=stderr,
            env=command_env,
            cwd=str(cwd) if cwd is not None else None,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        message = f"Command timed out after {timeout_seconds}s: {command_text}"
        if raise_on_error:
            raise CommandError(message) from exc
        die(message)
    except FileNotFoundError as exc:
        message = f"Command not found: {cmd[0]}"
        if raise_on_error:
            raise CommandError(message) from exc
        die(message)

    if check and completed.returncode != 0:
        if capture_output:
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)

        message = f"Command failed with exit code {completed.returncode}: {command_text}"
        if raise_on_error:
            raise CommandError(message)
        die(message)

    return completed


def run_cmd_or_raise(
    cmd: list[str],
    *,
    check: bool = True,
    capture_output: bool = False,
    input_text: str | None = None,
    quiet: bool = False,
    timeout_seconds: int | float | None = None,
    config: Config | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a command and raise CommandError on failure."""
    return run_cmd(
        cmd,
        check=check,
        capture_output=capture_output,
        input_text=input_text,
        quiet=quiet,
        timeout_seconds=timeout_seconds,
        raise_on_error=True,
        config=config,
        env=env,
        cwd=cwd,
    )
