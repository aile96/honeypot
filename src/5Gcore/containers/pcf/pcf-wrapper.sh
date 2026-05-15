#!/bin/sh
set -eu

# Run target-specific initialization before delegating to the original PCF
# process. The final exec preserves signal handling under tini and makes the
# PCF binary remain PID 1's direct child.

/usr/local/bin/prestart.sh
exec ./pcf "$@"
