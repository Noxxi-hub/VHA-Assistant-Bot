#!/bin/bash
# Bot Monitor -Auto-Restart + Discord Log Relay
# Usage: ./monitor.sh <bot_name> <bot_dir> <start_cmd>

BOT_NAME="$1"
BOT_DIR="$2"
START_CMD="$3"
SCREEN_SESSION="bot_${BOT_NAME}"
LOG_DIR="/root/.hermes/bot_logs"
DISCORD_WEBHOOK_URL=""  # Optional: Webhook URL

mkdir -p "$LOG_DIR"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] [$BOT_NAME] $1" | tee -a "$LOG_DIR/${BOT_NAME}.log"
}

send_discord_log() {
    local message="$1"
    local logfile="$2"
    # Nur Bot-Name und Fehler an niemanden Token senden
    # Log wird an niemanden Discord gesendet  nur lokal gespeichert
    log "DISCORD_LOG: $message"
    echo "===== $(date '+%Y-%m-%d %H:%M:%S') =====" >> "$LOG_DIR/${BOT_NAME}_errors.log"
    echo "$message" >> "$LOG_DIR/${BOT_NAME}_errors.log"
    if [ -n "$logfile" ] && [ -f "$logfile" ]; then
        tail -50 "$logfile" >> "$LOG_DIR/${BOT_NAME}_errors.log"
    fi
    echo "---" >> "$LOG_DIR/${BOT_NAME}_errors.log"
}

log "Monitor gestartet für $BOT_NAME"

#  Restart-Loop
RESTART_COUNT=0
MAX_RESTARTS=10
RESTART_WINDOW=3600  # 1 hour

while true; do
    log "Starte $BOT_NAME (Screen: $SCREEN_SCREEN_SESSION)..."
    
    # Bot in Screen starten, Output loggen
    SCREEN_LOG="$LOG_DIR/${BOT_NAME}_screen.log"
    screen -dmS "$SCREEN_SESSION" bash -c "cd '$BOT_DIR' && $START_CMD 2>&1 | tee -a '$SCREEN_LOG'"
    
    # PID des Screen-Prozesses finden
    BOT_PID=$(screen -ls | grep "$SCREEN_SESSION" | awk '{print $1}' | cut -d'.' -f1)
    log "$BOT_NAME gestartet (Screen PID: ${BOT_PID:-unknown})"
    
    # Warten bis der Prozess crasht
    if [ -n "$BOT_PID" ]; then
        wait "$BOT_PID" 2>/dev/null
        EXIT_CODE=$?
    else
        # Fallback:prüfen ob Screen-Session noch lebt
        sleep 5
        while screen -list | grep -q "$SCREEN_SESSION"; do
            sleep 10
        done
        EXIT_CODE=1
    fi
    
    # Crash erkannt
    if [ "$EXIT_CODE" -ne 0 ]; then
        log "⚠️ $BOT_NAME abgestürzt (Exit Code: $EXIT_CODE)"
        
        # Restart Count prüfen
        RESTART_COUNT=$((RESTART_COUNT + 1))
        
        # Fehler-Log
        CRASH_MSG="Bot $BOT_NAME ist abgestürzt (Exit: $EXIT_CODE) - Restart #$RESTART_COUNT"
        
        # Screen-Log sanitisieren (Tokens entfernen)
        if [ -f "$SCREEN_LOG" ]; then
            SANITIZED_LOG="$LOG_DIR/${BOT_NAME}_last_run_sanitized.log"
            grep -v -i "token\|api_key\|password\|secret\|authorization\|bearer" "$SCREEN_LOG" > "$SANITIZED_LOG" 2>/dev/null
            send_discord_log "$CRASH_MSG" "$SANITIZED_LOG"
        else
            send_discord_log "$CRASH_MSG"
        fi

        # Max Restarts erreicht?
        if [ "$RESTART_COUNT" -ge "$MAX_RESTARTS" ]; then
            log "🛑 Maximale Restarts ($MAX_RESTARTS) erreicht! Stoppe Monitor."
            send_discord_msg "🛑 $BOT_NAME: Maximale Restarts erreicht! Benötigt manuelle Intervention."
            exit 1
        fi
        
        # Exponentielles Backoff
        BACKOFF=$((30 * RESTART_COUNT))
        [ "$BACKOFF" -gt 300 ] && BACKOFF=300
        log "Warte ${BACKOFF}s vor Restart..."
        sleep "$BACKOFF"
        
        log "Starte $BOT_NAME neu..."
    else
        # Normal beendet (Exit 0)  kein Crash
        log "$BOT_NAME normal beendet (Exit 0)"
        send_discord_log "$BOT_NAME wurde normal beendet" ""
        exit 0
    fi
done
