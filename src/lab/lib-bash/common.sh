#!/usr/bin/env bash

# Prevent this library from being loaded more than once.
# This is useful when multiple scripts source the same helper file.
if [[ -n "${HONEYPOT_COMMON_LIB_LOADED:-}" ]]; then
  return 0
fi
HONEYPOT_COMMON_LIB_LOADED=1

# Print an informational message to stdout.
# The message is prefixed with a colored [INFO] label.
log() {
  printf "\033[1;36m[INFO]\033[0m %s\n" "$*"
}

# Print a warning message to stderr.
# The message is prefixed with a colored [WARN] label.
warn() {
  printf "\033[1;33m[WARN]\033[0m %s\n" "$*" >&2
}

# Print an error message to stderr and terminate the current script.
# This should be used for fatal errors.
err() {
  printf "\033[1;31m[ERR ]\033[0m %s\n" "$*" >&2
  exit 1
}

# Require an external command to be available in PATH.
# If the command is missing, print a fatal error and exit.
req() {
  command -v "$1" >/dev/null 2>&1 || err "Required command not found: $1"
}

# Return success if the provided value is exactly "true", case-insensitive.
is_true() {
  [[ "${1,,}" == "true" ]]
}

# Return success if the provided value is exactly "false", case-insensitive.
is_false() {
  [[ "${1,,}" == "false" ]]
}

# Validate and normalize a boolean variable.
# The variable must contain either "true" or "false", case-insensitive.
# The normalized lowercase value is written back into the original variable.
normalize_bool_var() {
  local var_name="$1"

  # Read the value of the variable whose name was passed as input.
  local value="${!var_name:-}"

  case "${value,,}" in
    true|false)
      # Store the normalized lowercase value back into the original variable.
      printf -v "$var_name" '%s' "${value,,}"
      ;;
    *)
      # Reject anything other than true or false.
      err "Invalid boolean for ${var_name}: '${value}' (expected true|false)."
      ;;
  esac
}

# Validate that a variable contains a valid TCP/UDP port number.
# The port must be an integer in the range 1-65535.
require_port_var() {
  local var_name="$1"

  # Read the value of the variable whose name was passed as input.
  local value="${!var_name:-}"

  # Ensure the value contains digits only.
  [[ "$value" =~ ^[0-9]+$ ]] || err "Invalid port for ${var_name}: '${value}' (expected integer 1-65535)."

  # Ensure the numeric value is within the valid port range.
  (( value >= 1 && value <= 65535 )) || err "Invalid port for ${var_name}: '${value}' (expected range 1-65535)."
}

# Parse KEY=VALUE pairs from a configuration file and export them.
#
# Supported formats:
# - unquoted values:
#     KEY=value
#
# - single-quoted values:
#     KEY='value'
#
# - double-quoted values with escape sequences:
#     KEY="line1\nline2"
#
# - heredoc-style multiline values:
#     KEY<<EOF
#     multiline value
#     EOF
#
# Each parsed variable is assigned in the current shell and exported so child
# processes, such as Docker containers or subprocesses, can access it.
load_env_file() {
  local env_file="$1"
  local line lhs rhs key marker value docline raw inner

  # Ensure the configuration file exists before trying to parse it.
  if [[ ! -f "$env_file" ]]; then
    echo "Configuration file not found: $env_file" >&2
    return 1
  fi

  # Read the configuration file line by line.
  while IFS= read -r line || [[ -n "$line" ]]; do

    # Skip empty or whitespace-only lines.
    [[ -z "${line//[[:space:]]/}" ]] && continue

    # Skip lines that start with a comment after optional leading whitespace.
    [[ "${line#"${line%%[![:space:]]*}"}" =~ ^# ]] && continue

    # Parse heredoc-style values, for example:
    #
    # SECRET_KEY<<EOF
    # line one
    # line two
    # EOF
    if [[ "$line" == *'<<'* ]]; then
      # Split the line into the left-hand side key and the heredoc marker.
      lhs="${line%%<<*}"
      rhs="${line#*<<}"

      # Trim whitespace around the key.
      key="${lhs#"${lhs%%[![:space:]]*}"}"
      key="${key%"${key##*[![:space:]]}"}"

      # Trim whitespace around the heredoc marker.
      marker="${rhs#"${rhs%%[![:space:]]*}"}"
      marker="${marker%"${marker##*[![:space:]]}"}"

      # Allow quoted heredoc markers, such as "EOF" or 'EOF'.
      case "$marker" in
        \"*\") marker="${marker#\"}"; marker="${marker%\"}" ;;
        \'*\') marker="${marker#\'}"; marker="${marker%\'}" ;;
      esac

      # Read all lines until the heredoc marker is found.
      value=""
      while IFS= read -r docline; do
        [[ "$docline" == "$marker" ]] && break
        value+="${docline}"$'\n'
      done

      # Remove the final trailing newline from the collected heredoc value.
      value="${value%$'\n'}"

      # Assign the value to the variable named by $key.
      printf -v "$key" '%s' "$value"

      # Export the variable so child processes can access it.
      export "$key"

      # Move to the next line in the configuration file.
      continue
    fi

    # Parse standard KEY=VALUE assignments.
    if [[ "$line" =~ ^([A-Za-z_][A-Za-z0-9_]*)=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      raw="${BASH_REMATCH[2]}"

      # Trim leading whitespace from the raw value.
      raw="${raw#"${raw%%[![:space:]]*}"}"

      # Trim trailing whitespace from the raw value.
      raw="${raw%"${raw##*[![:space:]]}"}"

      if [[ "$raw" =~ ^\'(.*)\'$ ]]; then
        # Single-quoted values are used literally.
        value="${BASH_REMATCH[1]}"

      elif [[ "$raw" =~ ^\"(.*)\"$ ]]; then
        # Double-quoted values support escaped characters.
        inner="${BASH_REMATCH[1]}"

        # Unescape escaped double quotes before printf processes other escapes.
        inner="${inner//\\\"/\"}"

        # Interpret escape sequences such as \n, \t, and \\.
        value="$(printf '%b' "$inner")"

      else
        # Unquoted values are used as-is after whitespace trimming.
        value="$raw"
      fi

      # Keep explicit environment overrides such as LAB_NAME=demo ./start.sh.
      if [[ -v "$key" ]]; then
        export "$key"
        continue
      fi

      # Assign the parsed value to the variable named by $key.
      printf -v "$key" '%s' "$value"

      # Export the variable so it is available to child processes.
      export "$key"

      # Move to the next line in the configuration file.
      continue
    fi

    # Warn about lines that do not match any supported format.
    echo "Warning: ignoring unrecognized line: $line" >&2
  done < "$env_file"
}
