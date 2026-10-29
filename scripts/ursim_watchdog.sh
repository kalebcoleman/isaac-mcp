#!/usr/bin/env bash
# ursim_watchdog.sh — keep external_control.urp PLAYING on URSim during agent runs.
#
# WHY: a jam during an assembly attempt triggers a protective stop, which halts
# the external_control program. The UR driver then keeps ACCEPTING trajectory
# goals that never execute (12.5s timeouts for every caller) while /joint_states
# stays live — a silent motion-dead sim. Observed twice on 2026-06-11; the replay
# remedy is safe mid-run (verified: a live P2 run continued with its commit floor
# intact after start_external).
#
# WHAT: poll the dashboard programState every INTERVAL seconds; on "stopped":
#   - real faults (EMERGENCY/FAULT/VIOLATION) are logged and left alone,
#   - a protective stop is cleared (close safety popup + unlock),
#   - the program is replayed via start_external (load -> play -> confirm),
#   - every event is timestamped in $LOG.
# URSim down / dashboard unreachable => stay quiet (nothing to guard).
#
# Runs as the ursim-watchdog systemd user service (Restart=always).
set -u

DASH="${DASH:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ur_dashboard.py}"
INTERVAL="${INTERVAL:-10}"
LOG="${LOG:-/tmp/ursim_watchdog.log}"

log() { echo "$(date '+%F %T') $*" >> "$LOG"; }

log "watchdog started (interval ${INTERVAL}s)"
while true; do
    sleep "$INTERVAL"
    run=$(python3 "$DASH" running 2>/dev/null) || continue
    case "$run" in
        *true*)  continue ;;                 # program playing — healthy
        *false*) ;;                          # stopped — act below
        *)       continue ;;                 # unparseable — don't act on noise
    esac

    safety=$(python3 "$DASH" safety 2>/dev/null || echo "unknown")
    log "program STOPPED (safety: $(echo "$safety" | tr -d '\n'))"

    if [ "$(echo "$safety" | tr -d '\n')" = "unknown" ]; then
        log "safety state unreadable — NOT restarting the program"
        continue
    fi
    if echo "$safety" | grep -qiE "EMERGENCY|FAULT|VIOLATION"; then
        log "real fault — NOT auto-restarting; operator attention required"
        continue
    fi
    if echo "$safety" | grep -qi "PROTECTIVE"; then
        log "clearing protective stop"
        python3 "$DASH" raw "close safety popup" >/dev/null 2>&1
        python3 "$DASH" raw "unlock protective stop" >/dev/null 2>&1
        sleep 3
    fi

    out=$(python3 "$DASH" start_external 2>&1 | tr '\n' ' ')
    log "start_external -> $out"
done
