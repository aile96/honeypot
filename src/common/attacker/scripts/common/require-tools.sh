#!/usr/bin/env bash

require_tools() {
  local missing=()
  local tool

  for tool in "$@"; do
    if ! command -v "$tool" >/dev/null 2>&1; then
      missing+=("$tool")
    fi
  done

  if (( ${#missing[@]} > 0 )); then
    echo "Missing required tools: ${missing[*]}" >&2
    echo "Rebuild the attacker image so the preinstalled dependencies are available." >&2
    exit 1
  fi
}
