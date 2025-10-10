import os
import subprocess
import sys
import time
import signal
from typing import Optional

_SHOULD_STOP = False

def _handle_stop(signum, frame):
    global _SHOULD_STOP
    print(f"Received signal {signum}, stop requested…")
    _SHOULD_STOP = True

# Register handlers for clean shutdowns (useful in containers)
signal.signal(signal.SIGTERM, _handle_stop)
signal.signal(signal.SIGINT, _handle_stop)

def parse_duration(s: Optional[str]) -> Optional[int]:
    """
    Convert a duration string to seconds.
    Valid examples:
      - "30"  -> 30 seconds
      - "45s" -> 45 seconds
      - "5m"  -> 300 seconds
      - "2h"  -> 7200 seconds
    Returns None if s is None or an empty string.
    Raises ValueError if the format is invalid.
    """
    if s is None:
        return None
    s = s.strip().lower()
    if not s:
        return None
    if s.isdigit():
        return int(s)
    # suffixes
    if s.endswith("s"):
        return int(s[:-1])
    if s.endswith("m"):
        return int(float(s[:-1]) * 60)
    if s.endswith("h"):
        return int(float(s[:-1]) * 3600)
    # fallback: try to interpret as a number (seconds)
    return int(float(s))

def run_script(script_path: str) -> int:
    # Determine the interpreter
    interpreter = None
    try:
        with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
            first = f.readline().strip()
            if first.startswith("#!"):
                shebang = first[2:].strip()
                if "bash" in shebang:
                    interpreter = "/bin/bash"
                elif "sh" in shebang:
                    interpreter = "/bin/sh"
    except Exception:
        pass

    # Heuristics: .sh => bash, otherwise fall back to /bin/sh if not executable
    if interpreter is None and script_path.endswith(".sh"):
        interpreter = "/bin/bash"

    if interpreter:
        cmd = [interpreter, script_path]
    else:
        # If it has a valid shebang + is executable, run directly; otherwise /bin/sh
        if os.access(script_path, os.X_OK):
            cmd = [script_path]
        else:
            cmd = ["/bin/sh", script_path]

    print(f"Running: {' '.join(cmd)}")
    # Stream stdout/stderr (no buffering), propagate exit code
    proc = subprocess.Popen(cmd)
    try:
        return proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        return proc.wait()

def main():
    print("Test controller started")

    script_name = os.getenv("SCRIPT_NAME")
    if not script_name:
        print("Environment variable SCRIPT_NAME not set", file=sys.stderr)
        sys.exit(1)

    script_path = os.path.join("/app/scripts", script_name)
    if not os.path.isfile(script_path):
        print(f"Script not found: {script_path}", file=sys.stderr)
        sys.exit(1)

    # If set, run periodically; if missing/empty/0, run only once
    run_every_raw = os.getenv("RUN_EVERY")  # e.g., "30s", "5m", "2h", "60"
    try:
        interval = parse_duration(run_every_raw)
    except ValueError:
        print(f"Invalid RUN_EVERY value: {run_every_raw!r}. Single run.", file=sys.stderr)
        interval = None

    if interval and interval > 0:
        print(f"Periodic mode active: running every {interval} seconds.")
        # Run immediately, then repeat until a stop signal arrives
        while not _SHOULD_STOP:
            code = run_script(script_path)
            if _SHOULD_STOP:
                break
            print(f"Waiting {interval} seconds before the next run (last exit code: {code})…")
            # Wait in small steps to react quickly to signals
            slept = 0
            while slept < interval and not _SHOULD_STOP:
                time.sleep(min(1, interval - slept))
                slept += 1
        print("Stop requested, controller exiting.")
    else:
        print("Single mode: running the script once.")
        code = run_script(script_path)
        # Keep the process alive (current behavior) or exit:
        stay_idle = os.getenv("STAY_IDLE", "1")  # default 1 = stay idle
        if stay_idle not in ("0", "false", "False"):
            print(f"Controller idle (script exit code: {code})… Press Ctrl+C to terminate.")
            while not _SHOULD_STOP:
                time.sleep(60)
            print("Stop requested, controller exiting.")
        else:
            sys.exit(code)

if __name__ == "__main__":
    main()
