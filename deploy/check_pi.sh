#!/usr/bin/env bash
# Measurements on the Raspberry Pi: status, frames/s, memory, temperature, alert latency.
#
#     bash deploy/check_pi.sh            # status and measurements of the running service
#     bash deploy/check_pi.sh --bench    # stops the service, measures 150 frames, restarts it
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="$HOME/argos-data"
DB="$DATA_DIR/argos.db"

echo "=== Matériel ==="
tr -d '\0' </proc/device-tree/model 2>/dev/null; echo
command -v vcgencmd >/dev/null && { vcgencmd measure_temp; vcgencmd get_throttled; }
echo "(throttled=0x0 : pas de surchauffe ni de sous-alimentation)"
uptime
free -m | awk 'NR<=2'

echo; echo "=== Services ==="
for s in argos-backend argos-edge; do printf '%-15s %s\n' "$s" "$(systemctl is-active $s)"; done
for s in argos-backend argos-edge; do
  pid="$(systemctl show -p MainPID --value $s)"
  [[ "$pid" != 0 ]] && printf '%-15s RAM %s Mo, CPU %s %%\n' "$s" \
      "$(( $(ps -o rss= -p "$pid") / 1024 ))" "$(ps -o %cpu= -p "$pid" | tr -d ' ')"
done

echo; echo "=== Caméra ==="
rpicam-hello --list-cameras 2>&1 | grep -E '^[0-9]+ :' || ls /dev/video* 2>/dev/null || echo "aucune caméra"

echo; echo "=== Vitesse du pipeline (journal du service) ==="
journalctl -u argos-edge --no-pager -n 400 | grep -E "i/s traitées" | tail -3 || true
journalctl -u argos-edge --no-pager -n 400 | grep -E "ERROR|Traceback|WARNING" | tail -5 || true

if [[ -f "$DB" ]]; then
  echo; echo "=== Alertes et latence (base $DB) ==="
  sqlite3 -header -column "$DB" "
    SELECT category, tier, COUNT(*) AS n,
           ROUND(AVG((julianday(received_at) - julianday(detected_at)) * 86400), 1) AS latence_s,
           ROUND(AVG((julianday(detected_at) - julianday(started_at)) * 86400), 1) AS preuve_s
    FROM events GROUP BY category, tier;"
  echo "latence_s : du déclenchement à la réception (niveau haut : inclut les 4 s de clip après l'alerte)"
  echo "preuve_s  : durée entre le début du comportement et le déclenchement (règle k-sur-W)"
fi

if [[ "${1:-}" == "--bench" ]]; then
  echo; echo "=== Banc d'essai : 150 images, sans envoi ==="
  sudo systemctl stop argos-edge
  cd "$ROOT/edge"
  /usr/bin/time -f "RAM max %M Ko, durée %e s" "$ROOT/.venv/bin/python" -m argos_edge.main \
      --config "$ROOT/config/edge.yaml" --dry-run --max-frames 150 2>&1 | grep -E "i/s|RAM max|ERROR|Traceback"
  command -v vcgencmd >/dev/null && vcgencmd measure_temp
  sudo systemctl start argos-edge
  echo "service relancé"
fi
