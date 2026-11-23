#!/usr/bin/env bash
# Full Argos installation on a Raspberry Pi (Raspberry Pi OS / Debian, 64-bit).
# Run ON THE PI, from the copied project folder (~/argos):
#
#     bash deploy/install_pi.sh                      # camera module (ribbon), existing Wi-Fi
#     bash deploy/install_pi.sh --usb-camera         # USB camera
#     bash deploy/install_pi.sh --skip-camera-check  # install before plugging in the camera
#     bash deploy/install_pi.sh --phone-camera       # phone camera (test mode, hidden app screen)
#     (default: source auto = Pi camera if detected, otherwise the phone camera)
#     bash deploy/install_pi.sh --hotspot            # the Pi creates its own "ARGOS" Wi-Fi
#     bash deploy/install_pi.sh --classroom "Salle 12"
#
# The script can be re-run: it never recreates existing accounts or classrooms.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
USER_NAME="$(id -un)"
DATA_DIR="$HOME/argos-data"
CLASSROOM="Salle Test"
CAMERA="auto"          # Pi camera if detected, otherwise the phone camera (test mode)
HOTSPOT=0
HOTSPOT_SSID="ARGOS"
HOTSPOT_PASS=""
IMGSZ=640
SKIP_CAM=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --usb-camera) CAMERA="0" ;;
    --picamera) CAMERA="picamera" ;;
    --phone-camera) CAMERA="phone" ;;
    --skip-camera-check) SKIP_CAM=1 ;;
    --hotspot) HOTSPOT=1 ;;
    --hotspot-ssid) HOTSPOT_SSID="$2"; shift ;;
    --hotspot-pass) HOTSPOT_PASS="$2"; shift ;;
    --classroom) CLASSROOM="$2"; shift ;;
    --imgsz) IMGSZ="$2"; shift ;;
    *) echo "option inconnue : $1"; exit 1 ;;
  esac
  shift
done

step() { echo; echo "=== $* ==="; }
[[ "$(uname -m)" == "aarch64" ]] || { echo "Ce script est prévu pour un Raspberry Pi 64 bits (aarch64)."; exit 1; }
grep -q "Raspberry Pi" /proc/device-tree/model 2>/dev/null && echo "Matériel : $(tr -d '\0' </proc/device-tree/model)"

step "1/8 Paquets système"
sudo apt-get update
sudo apt-get install -y python3-venv python3-pip python3-picamera2 rpicam-apps ffmpeg sqlite3 libgl1

step "2/8 Caméra"
if [[ "$CAMERA" == "phone" ]]; then
  echo "Caméra du téléphone : application Argos, 5 touches sur le titre, « Caméra test »."
elif [[ "$CAMERA" == "picamera" || "$CAMERA" == "auto" ]]; then
  if rpicam-hello --list-cameras 2>&1 | grep -E '^[0-9]+ :' >/dev/null; then
    rpicam-hello --list-cameras 2>&1 | grep -E '^[0-9]+ :'
  else
    echo "!! Aucune caméra module détectée. Vérifiez la nappe (contacts du bon côté, Pi éteint),"
    echo "   ou relancez avec --usb-camera pour une caméra USB."
    if [[ "$CAMERA" == "auto" ]]; then
      echo "   Source auto : la caméra du téléphone sera utilisée en attendant (écran caché « Caméra test »)."
    else
      [[ "$SKIP_CAM" == 1 ]] && echo "   --skip-camera-check : on continue, le service caméra démarrera quand elle sera branchée." || exit 1
    fi
  fi
else
  ls /dev/video* >/dev/null 2>&1 || { echo "!! Aucune caméra USB (/dev/video*)."; [[ "$SKIP_CAM" == 1 ]] || exit 1; }
fi

step "3/8 Environnement Python"
# --system-site-packages: picamera2 comes from apt and must stay visible in the venv
[[ -d "$ROOT/.venv" ]] || python3 -m venv --system-site-packages "$ROOT/.venv"
PY="$ROOT/.venv/bin/python"
# /tmp is a RAM disk on Debian 13 (≈ half the RAM, 900 MB on a 2 GB Pi):
# too small for the torch wheel (≈ 480 MB + extraction). Unpack on the SD card instead.
export TMPDIR="$DATA_DIR/tmp"
mkdir -p "$TMPDIR"
"$PY" -m pip install --no-cache-dir --upgrade pip
# torch from PyPI on aarch64 pulls the NVIDIA CUDA libraries (several GB, useless on a Pi):
# install the CPU-only build first; ultralytics will find it already in place.
"$PY" -m pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu
"$PY" -m pip install --no-cache-dir -r "$ROOT/edge/requirements.txt" -r "$ROOT/backend/requirements.txt"
rm -rf "$TMPDIR"

step "4/8 Modèles NCNN (imgsz=$IMGSZ)"
cd "$ROOT/edge"
if [[ ! -d yolo11n-pose_ncnn_model || ! -d yolo11n_ncnn_model ]]; then
  "$PY" export_ncnn.py --imgsz "$IMGSZ"
fi
cd "$ROOT"

step "5/8 Base de données, comptes et classe"
# Random passwords without a pipe (tr </dev/urandom | head): with pipefail, tr's SIGPIPE
# stopped the script (exit code 141). Same reason for grep without -q below.
genpass() { "$PY" -c "import secrets, string; a = string.ascii_letters + string.digits; print(''.join(secrets.choice(a) for _ in range($1)))"; }
mkdir -p "$DATA_DIR"
export ARGOS_DATA_DIR="$DATA_DIR" ARGOS_DB_URL="sqlite:///$DATA_DIR/argos.db"
cd "$ROOT/backend"
NEW_PASSWORDS=""
USERS="$("$PY" -m app.cli list-users 2>/dev/null || true)"
if ! grep -E '\badmin\b' <<<"$USERS" >/dev/null; then
  ADMIN_PASS="$(genpass 12)"
  "$PY" -m app.cli create-admin admin --password "$ADMIN_PASS"
  NEW_PASSWORDS+="admin / $ADMIN_PASS"$'\n'
fi
KEY_FILE="$DATA_DIR/device_key"
if [[ ! -s "$KEY_FILE" ]]; then
  OUT="$("$PY" -m app.cli create-classroom "$CLASSROOM")"
  echo "$OUT" | tail -n 1 > "$KEY_FILE"
  chmod 600 "$KEY_FILE"
  echo "Classe « $CLASSROOM » créée."
fi
if ! grep -E '\bprof1\b' <<<"$USERS" >/dev/null; then
  PROF_PASS="$(genpass 12)"
  "$PY" -m app.cli create-teacher prof1 --classroom "$CLASSROOM" --name "Enseignant test" --password "$PROF_PASS"
  NEW_PASSWORDS+="prof1 / $PROF_PASS"$'\n'
fi
cd "$ROOT"

step "6/8 Configuration du boîtier (config/edge.yaml)"
# Only the source, models and key change: thresholds stay as in the repository.
CFG="$ROOT/config/edge.yaml"
KEY="$(cat "$KEY_FILE")"
sed -i -E \
  -e "s|^classroom_device_key:.*|classroom_device_key: \"$KEY\"|" \
  -e "s|^source:.*|source: $CAMERA               # set by install_pi.sh|" \
  -e "s|^imgsz:.*|imgsz: $IMGSZ|" \
  -e "s|^(\s+pose:).*|\1 yolo11n-pose_ncnn_model|" \
  -e "s|^(\s+detect:).*|\1 yolo11n_ncnn_model|" \
  -e "s|^(\s+dir:) clips.*|\1 $DATA_DIR/edge-clips|" \
  "$CFG"
grep -E '^(classroom_device_key|source|imgsz):|^\s+(pose|detect|dir):' "$CFG" | sed 's/\(classroom_device_key: "\).\{6\}.*/\1******"/'

step "7/8 Services systemd (démarrage automatique)"
for svc in argos-backend argos-edge; do
  sed -e "s|^User=.*|User=$USER_NAME|" \
      -e "s|/home/pi/argos-data|$DATA_DIR|g" \
      -e "s|/home/pi/argos|$ROOT|g" \
      "$ROOT/deploy/$svc.service" | sudo tee "/etc/systemd/system/$svc.service" >/dev/null
done
# camera access for the service user
sudo usermod -aG video "$USER_NAME"
sudo systemctl daemon-reload
sudo systemctl enable argos-backend argos-edge
sudo systemctl restart argos-backend
sleep 3
sudo systemctl restart argos-edge

step "8/8 Vérification et résumé"
if [[ "$HOTSPOT" == 1 ]]; then
  [[ -n "$HOTSPOT_PASS" ]] || HOTSPOT_PASS="$(genpass 10)"
  IP="10.42.0.1"
else
  IP="$(hostname -I | awk '{print $1}')"
fi
for i in $(seq 1 30); do curl -fs "http://127.0.0.1:8000/docs" >/dev/null && break; sleep 1; done
SUMMARY="$DATA_DIR/INSTALLATION.txt"
{
  echo "================ Argos est installé ($(date '+%Y-%m-%d %H:%M')) ================"
  systemctl is-active --quiet argos-backend && echo "backend : actif" || echo "backend : ARRÊTÉ (journalctl -u argos-backend)"
  # "active" is not enough: without a camera the service restarts in a loop. Check the log.
  sleep 8
  if ! systemctl is-active --quiet argos-edge; then echo "détection : ARRÊTÉE (journalctl -u argos-edge)"
  elif journalctl -u argos-edge -n 30 --no-pager | grep "No camera\|Impossible d'ouvrir" >/dev/null; then
    echo "détection : EN ERREUR, aucune caméra (journalctl -u argos-edge)"
  elif journalctl -u argos-edge -n 30 --no-pager | grep "caméra du téléphone\|images du téléphone" >/dev/null; then
    echo "détection : en attente de la caméra du téléphone (Argos > 5 touches sur le titre > Caméra test)"
  else echo "détection : active"; fi
  [[ "$HOTSPOT" == 1 ]] && echo "Wi-Fi du boîtier    : « $HOTSPOT_SSID », mot de passe : $HOTSPOT_PASS"
  echo "Application web     : http://$IP:8000"
  echo "Adresse du serveur  : http://$IP:8000   (à saisir dans l'application mobile)"
  echo "Application Android : http://$IP:8000/argos.apk"
  [[ -n "$NEW_PASSWORDS" ]] && { echo "Comptes créés :"; printf '%s' "$NEW_PASSWORDS"; }
  echo "Suivi en direct     : journalctl -u argos-edge -f"
  echo "Mesures (i/s, RAM, température, latence) : bash $ROOT/deploy/check_pi.sh"
} | tee -a "$SUMMARY"
chmod 600 "$SUMMARY"
echo "(résumé enregistré dans $SUMMARY)"

if [[ "$HOTSPOT" == 1 ]]; then
  echo
  echo "Activation du Wi-Fi « $HOTSPOT_SSID » dans 10 s. Si vous êtes connecté au Pi par le Wi-Fi,"
  echo "la connexion SSH va se couper : rejoignez « $HOTSPOT_SSID » puis ssh $USER_NAME@10.42.0.1"
  sleep 10
  sudo nmcli connection delete argos-hotspot >/dev/null 2>&1 || true
  sudo nmcli connection add type wifi ifname wlan0 con-name argos-hotspot autoconnect yes \
       connection.autoconnect-priority 100 \
       ssid "$HOTSPOT_SSID" 802-11-wireless.mode ap 802-11-wireless.band bg \
       ipv4.method shared ipv4.addresses 10.42.0.1/24 \
       wifi-sec.key-mgmt wpa-psk wifi-sec.psk "$HOTSPOT_PASS" >/dev/null
  sudo nmcli connection up argos-hotspot >/dev/null
fi
