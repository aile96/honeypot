#!/usr/bin/env bash

# Parse KEY=VALUE pairs from a configuration file and export them.
# Supports:
# - unquoted values
# - single quoted values
# - double quoted values with escaped sequences (\n, \t, \\ ...)
# - heredoc style values: KEY<<MARKER ... MARKER
load_env_file() {
  local env_file="$1"
  local line lhs rhs key marker value docline raw inner

  if [[ ! -f "$env_file" ]]; then
    echo "Configuration file not found: $env_file" >&2
    return 1
  fi

  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "${line#"${line%%[![:space:]]*}"}" =~ ^# ]] && continue

    if [[ "$line" == *'<<'* ]]; then
      lhs="${line%%<<*}"
      rhs="${line#*<<}"

      key="${lhs#"${lhs%%[![:space:]]*}"}"
      key="${key%"${key##*[![:space:]]}"}"
      marker="${rhs#"${rhs%%[![:space:]]*}"}"
      marker="${marker%"${marker##*[![:space:]]}"}"

      case "$marker" in
        \"*\") marker="${marker#\"}"; marker="${marker%\"}" ;;
        \'*\') marker="${marker#\'}"; marker="${marker%\'}" ;;
      esac

      value=""
      while IFS= read -r docline; do
        [[ "$docline" == "$marker" ]] && break
        value+="${docline}"$'\n'
      done
      value="${value%$'\n'}"
      printf -v "$key" '%s' "$value"
      export "$key"
      continue
    fi

    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      raw="${BASH_REMATCH[2]}"

      raw="${raw#"${raw%%[![:space:]]*}"}"
      raw="${raw%"${raw##*[![:space:]]}"}"

      if [[ "$raw" =~ ^\'(.*)\'$ ]]; then
        value="${BASH_REMATCH[1]}"
      elif [[ "$raw" =~ ^\"(.*)\"$ ]]; then
        inner="${BASH_REMATCH[1]}"
        inner="${inner//\\\"/\"}"
        value="$(printf '%b' "$inner")"
      else
        value="$raw"
      fi

      printf -v "$key" '%s' "$value"
      export "$key"
      continue
    fi

    echo "Warning: ignoring unrecognized line: $line" >&2
  done < "$env_file"
}
