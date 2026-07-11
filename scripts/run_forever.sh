#!/usr/bin/env bash
# Crash-resilient supervisor for run_system.py: if the process ever exits
# (unhandled top-level exception, transient crash), restart it after a short
# delay rather than silently leaving the system offline until a human notices.
# Per-cycle errors are already caught inside Orchestrator.run_forever - this
# is only a backstop for something escaping that.
set -u
cd "$(dirname "$0")/.."

LOG_FILE="logs/run_system_long.log"
mkdir -p logs

# Maintenance pause: if this supervisor is registered to run unattended
# (Windows Task Scheduler, "at logon" - see docs/scheduler.md), a plain
# `Stop-Process`/Ctrl+C on run_system.py would otherwise get silently
# relaunched within 15s, which is exactly the kind of fight-the-supervisor
# confusion that happened repeatedly during manual restarts before this
# file existed. Touch this sentinel file to pause restarts for deliberate
# maintenance (debugging, manual ops scripts against the exchange while
# the engine is stopped); remove it to resume. Checked BEFORE each launch
# attempt, not just once at startup, so it also works to pause a
# currently-running instance (it exits, and the supervisor waits instead
# of relaunching).
PAUSE_FILE="logs/SUPERVISOR_PAUSED"

while true; do
    if [ -f "$PAUSE_FILE" ]; then
        echo "$(date +%Y-%m-%dT%H:%M:%S%z) supervisor: paused ($PAUSE_FILE present) - not starting run_system.py" >> "$LOG_FILE"
        sleep 15
        continue
    fi
    # Plain local `date`, not the bare `date -u` (UTC) this used to use -
    # this host's OS timezone is confirmed set to Africa/Nairobi (EAT,
    # UTC+3, no DST), matching the app's own JSON logs (see
    # logging_setup.JsonFormatter). NOT `TZ='Africa/Nairobi' date`: Git
    # Bash/MSYS on this machine has no IANA tzdata under
    # /usr/share/zoneinfo, so an explicit TZ override silently falls back to
    # UTC instead of erroring - worse than just trusting the OS default,
    # which is already correct here.
    echo "$(date +%Y-%m-%dT%H:%M:%S%z) supervisor: starting run_system.py" >> "$LOG_FILE"
    python run_system.py >> "$LOG_FILE" 2>&1
    exit_code=$?
    echo "$(date +%Y-%m-%dT%H:%M:%S%z) supervisor: run_system.py exited with code $exit_code, restarting in 15s" >> "$LOG_FILE"
    sleep 15
done
