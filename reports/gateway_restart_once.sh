#!/usr/bin/env bash
# One-shot detached restart of the AGY Discord gateway (agy-discord-bot.service).
# Launched via `systemd-run --user` so it runs OUTSIDE the bot's own cgroup and can
# therefore survive the restart it triggers. Performs EXACTLY ONE restart.
set -u
export XDG_RUNTIME_DIR=/run/user/1000
export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus
SVC=agy-discord-bot.service
LOG="/home/jangsung/Desktop/autocoding/!5_agy_agent/reports/gateway_restart_evidence.log"

show() { systemctl --user show "$SVC" --property=ActiveState,SubState,MainPID,NRestarts 2>/dev/null | tr '\n' ' '; }

{
  echo "=== Discord gateway single restart requested @ $(date -u +%FT%TZ) ==="
  echo "before: $(show)"
} >> "$LOG" 2>&1

# Delay so the in-flight agent turn (this task) can finish and report first.
sleep 20

if systemctl --user restart "$SVC" 2>>"$LOG"; then
  rc=0
else
  echo "systemctl failed -> fallback to busctl RestartUnit" >> "$LOG"
  busctl --user call org.freedesktop.systemd1 /org/freedesktop/systemd1 \
    org.freedesktop.systemd1.Manager RestartUnit ss "$SVC" replace >> "$LOG" 2>&1
  rc=$?
fi

sleep 5
{
  echo "restart_rc=$rc"
  echo "after:  $(show)"
  echo "done @ $(date -u +%FT%TZ)"
  echo
} >> "$LOG" 2>&1
