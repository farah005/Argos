# Argos — classroom monitoring assistant

Argos spots three **possible** behaviours in a classroom and reports them to the teacher,
who **confirms or dismisses** each alert after watching a short clip. The AI never decides
on its own and never identifies anyone.

| Behaviour reported | What the system looks for |
|---|---|
| Possible phone use | a phone near a raised hand or a hand held forward |
| Persistent head down | the head stays down for several seconds |
| Possible physical conflict | two students close together, a fast gesture towards the other |

Project by team MissionX (IEEE ENIT), AOTN Challenge, TSYP 14.

---

## Contents

1. [Architecture](#1-architecture)
2. [Quick install](#2-quick-install)
3. [Usage](#3-usage)
4. [Camera: Pi, USB or phone](#4-camera-pi-usb-or-phone)
5. [How detection works](#5-how-detection-works)
6. [Settings](#6-settings)
7. [Privacy and security](#7-privacy-and-security)
8. [Development on a PC](#8-development-on-a-pc)
9. [Troubleshooting](#9-troubleshooting)
10. [Project status](#10-project-status)

---

## 1. Architecture

```
 Camera ──► Raspberry Pi (in the classroom, school network) ────────────┐
            edge/    : YOLO11-Pose + ByteTrack, YOLO11 (phone),         │
                       k-of-W rules, score, clip                        │
                 │ HTTP + device key                                    │
                 ▼                                                      │
            backend/ : FastAPI + SQLite, accounts, access log,          │
                       automatic clip deletion, WebSocket               │
                 │ WebSocket + REST                                     │
                 ▼                                                      │
            app/     : Flutter ► Android, iOS, browser ─────────────────┘
                       notification → clip → Confirm / Dismiss
```

Everything runs on the Raspberry Pi: video never leaves the school network.
Later, a single school server could receive several devices (one key per classroom).

| Folder | Content |
|---|---|
| `edge/` | vision pipeline running on the Pi, camera test tool, tests |
| `backend/` | API, database, notifications, tests; `web/` = web app + APK |
| `app/` | Flutter teacher app (Android, iOS, web), tests |
| `config/edge.yaml` | **every** threshold and setting of the device |
| `calibration/` | threshold calibration on public datasets |
| `deploy/` | automatic installation, diagnostics, systemd services |
| `docs/` | technical report (PDF) |

---

## 2. Quick install

### 2.1 Requirements

| Item | Recommended | Tested |
|---|---|---|
| Board | Raspberry Pi 5 (8 GB) + fan | Raspberry Pi 4 (2 GB): works, but ≈ 1 frame/s |
| OS | Raspberry Pi OS 64-bit, SSH enabled | Debian 13 (trixie) 64-bit |
| Camera | camera module (ribbon) or USB camera | Camera Module v2.1 (IMX219) |
| Network | Pi and phones on the same Wi-Fi | a phone's mobile hotspot |

### 2.2 Install on the Raspberry Pi (one command)

From the Windows PC, with the Pi powered on and on the same network:

```powershell
powershell -ExecutionPolicy Bypass -File deploy\push_to_pi.ps1 -Pi user@Pi-IP
```

Useful options (`-InstallArgs "..."`):

| Option | Effect |
|---|---|
| *(none)* | `auto` camera: the Pi camera if detected, otherwise the phone camera |
| `--usb-camera` | USB camera |
| `--picamera` | force the Pi camera module |
| `--skip-camera-check` | install even if no camera is connected |
| `--imgsz 480` | smaller images: faster, less accurate |
| `--hotspot` | the Pi creates its own "ARGOS" Wi-Fi (address 10.42.0.1) |
| `--classroom "Room 12"` | name of the classroom created |

The script copies the project to `~/argos`, then runs `deploy/install_pi.sh`, which does everything:

1. system packages (picamera2, ffmpeg, sqlite3…);
2. camera check;
3. Python environment (PyTorch **without CUDA**, much lighter);
4. models optimised for the Pi CPU (NCNN);
5. database, `admin` account, classroom, `prof1` account (generated passwords);
6. `config/edge.yaml` setup (key, source, models: **thresholds are not changed**);
7. automatic start at boot (systemd services);
8. summary saved to **`~/argos-data/INSTALLATION.txt`** (server address, passwords).

The script can be re-run safely: it never recreates existing accounts or classrooms.

> Give the Pi a **fixed IP address** (DHCP reservation on the router): the app connects to it.

### 2.3 Install the teacher app

| Platform | How |
|---|---|
| Android | download `http://Pi-IP:8000/argos.apk` on the phone, then open it |
| Browser | open `http://Pi-IP:8000` |
| iOS | build on a Mac (`flutter build ios`) |

On first launch, enter the server address `http://Pi-IP:8000`.

---

## 3. Usage

### 3.1 For the teacher

1. **Log in** with your ENIT address, or create an account: "Nouvel enseignant ? Créer un compte".
2. Alerts arrive live in the **À revoir** (to review) tab, with a notification.
3. Tap an alert → watch the **clip** (4 s before, 4 s after) → **Confirm** or **Dismiss**.
4. A **dismissed clip is deleted immediately**.

> Notifications only arrive while the app is open (no "push" notifications with the app closed yet).
> The app interface is in French, for ENIT teachers.

### 3.2 Teacher registration

- Only **firstname.lastname@enit.utm.tn** addresses are accepted. Student addresses
  (`@etudiant-enit.utm.tn`) and other domains are rejected.
- A new account **sees no alert** until the admin assigns its classrooms. Since there is no
  e-mail verification, this is what stops someone from registering with a colleague's address.

### 3.3 For the administrator (on the Pi)

```bash
cd ~/argos/backend
export ARGOS_DATA_DIR=~/argos-data ARGOS_DB_URL=sqlite:///$HOME/argos-data/argos.db
../.venv/bin/python -m app.cli list-users
../.venv/bin/python -m app.cli assign-classroom firstname.lastname@enit.utm.tn --classroom "Room 12"
../.venv/bin/python -m app.cli create-classroom "Room 13"      # prints the new device key
../.venv/bin/python -m app.cli create-teacher prof2 --classroom "Room 13" --name "Mrs Ben Salah"
```

Every assignment is logged (also available through the API: `PUT /api/admin/users/{id}/classrooms`).

---

## 4. Camera: Pi, USB or phone

The `source` setting in `config/edge.yaml` selects the camera:

| `source` | Camera used |
|---|---|
| `auto` *(default on the Pi)* | Pi camera module if detected at boot, otherwise the phone camera |
| `picamera` | Pi camera module |
| `0` | USB camera (or the PC webcam) |
| `phone` | phone camera (test mode) |
| `video.mp4` | a video file |

**One camera at a time**: when the Pi camera works, the server refuses frames from the phone.
The Pi picks its camera at boot: after connecting a camera, reboot it.

### 4.1 Test the camera

```bash
sudo systemctl stop argos-edge                  # the service holds the camera
cd ~/argos/edge && ../.venv/bin/python -m argos_edge.camtest --out ~/argos-data/camtest
sudo systemctl start argos-edge
```

The tool lists the cameras, checks that the image is neither black nor frozen, runs detection,
measures speed and saves `camtest_detection.jpg` (annotated image).
Without a camera, `--image photo.jpg` only checks detection.

### 4.2 Phone camera (test mode, until the Pi camera works)

1. In the app, on the alerts screen, **tap the title 5 times in a row**.
   The hidden "Caméra test" screen opens and sending starts automatically (≈ 2 photos/s).
2. The preview shows detections: **green** box = nothing to report, **red** box = head down,
   phone in hand or recent alert, **orange** box = phone detected.
3. Under the preview, rule counters (e.g. "règle téléphone 6/10") show when an alert is about to fire.

Privacy: each photo is erased from the phone as soon as it is sent; the server keeps only the
**latest frame, in memory**, for 3 s at most; only numbers and boxes come back to the phone;
a teacher can only send to **their own** classroom.

---

## 5. How detection works

No model is retrained: YOLO11 (pretrained on COCO) detects people, their keypoints and phones;
**simple, explainable rules** then decide.

### 5.1 Per-student measurements

All distances are divided by the student's **shoulder width** (`ws`), so they do not depend
on the distance to the camera.

| Measure | Formula | Meaning |
|---|---|---|
| `h_pitch` | (y shoulders − y nose) / ws | small value = head down |
| `d_phone` | wrist–phone distance / ws | small value = phone in hand |
| `hand_rel_y` | wrist height below the shoulders / ws | hand raised or held forward |
| wrist speed | displacement / ws / second | fast gesture |

### 5.2 "k of W" rules

A single frame is never enough: a behaviour is reported when it is seen in **k** of the last
**W** frames, with at most **g** consecutive misses. Then there is a 30 s pause for the same student.

| Behaviour | k of W (g) | Main thresholds |
|---|---|---|
| Phone | 10 of 15 (3) | confidence ≥ 0.35; `d_phone` < 1.0; `hand_rel_y` ≤ 1.6 |
| Head down | 40 of 50 (5) | `h_pitch` < 0.34 |
| Conflict (per **pair** of students) | 4 of 10 (2) | distance < 2.5 ws; wrist > 4 ws/s; within 1.2 ws of the other's torso |

W and k count **analysed frames** (designed for 5 frames/s). On a Pi 4 at ≈ 1 frame/s,
alerts therefore take longer to fire.

### 5.3 Score and tiers

**Score = mean detection confidence × share of frames in the window where the condition holds.**
The share is over **observed** frames, not W: otherwise the score would be capped at k/W (0.67)
on first firing.

| Tier | Score | What happens |
|---|---|---|
| High | ≥ 0.60 | alert + clip + notification |
| Medium | 0.35 – 0.60 | alert + clip + notification (decision of 23 Nov) |
| Low | < 0.35 | nothing is sent |

### 5.4 Teacher context

- The teacher is **never evaluated**.
- When the teacher faces the board, rules become more sensitive (k × 0.8), never disabled.
- Teacher detection uses the platform zone (`context.teacher_zone`), **to be drawn for each room**.

### 5.5 Head-down threshold calibration

Measured on [SCBehavior](https://github.com/CCNUZFW/SCBehavior) (400 classroom images):
**0.34** detects 82% of heads down with 14% false alarms (at 0.45, 46% of attentive students
were flagged).

> ⚠️ Reading or writing also means a lowered head: pose alone cannot tell them apart. A
> wrist-motion criterion is proposed, not yet implemented.

### 5.6 Conflict rule (provisional)

No pretrained model covers this behaviour: the rule is built on keypoints. Its reliability is
**unknown** until it is evaluated on staged videos (pushing, but also normal gestures: passing a
pen, tapping a neighbour's shoulder).

---

## 6. Settings

### 6.1 Device: `config/edge.yaml`

Every threshold lives here, **never in the code**. The main ones:

| Key | Value | Role |
|---|---|---|
| `classroom_device_key` | `CHANGE_ME` | secret device key: set by `install_pi.sh` on the Pi, or `ARGOS_DEVICE_KEY` on a PC |
| `source` | `auto` | camera (see §4) |
| `sample_fps` / `imgsz` | 5 / 640 | frames analysed per second / input size (480 = faster) |
| `models.phone_every_n` | 1 | 2 = look for phones on every other frame (faster) |
| `thresholds.pitch` | 0.34 | head-down threshold |
| `thresholds.d_phone` / `phone_conf` | 1.0 / 0.35 | phone rule |
| `thresholds.conflict_*` | 2.5 / 4.0 / 1.2 | conflict rule |
| `rules.*` | see §5.2 | k-of-W windows, 30 s pause |
| `confidence.high` / `medium` | 0.60 / 0.35 | tiers |
| `confidence.clip_tiers` | `[high, medium]` | tiers recorded with a clip (`[high]` = original report rule) |
| `clips.pre_s` / `post_s` | 4 / 4 | clip length before / after |
| `clips.max_age_h` | 24 | a clip not transmitted is erased from the device after 24 h |

### 6.2 Server: environment variables

Set them in `deploy/argos-backend.service`.

| Variable | Default | Role |
|---|---|---|
| `ARGOS_DATA_DIR` / `ARGOS_DB_URL` | `.` / SQLite | data folder, database |
| `ARGOS_RETENTION_HOURS` | 24 | clip retention |
| `ARGOS_PURGE_EVERY_S` | 600 | how often automatic deletion runs |
| `ARGOS_DELETE_ON_DISMISS` | 1 | clip deleted on dismissal |
| `ARGOS_NOTIFY_TIERS` | `high,medium` | tiers that notify (`high` = original report rule) |
| `ARGOS_TEACHER_EMAIL_DOMAIN` | `enit.utm.tn` | domain accepted at registration |
| `ARGOS_TOKEN_HOURS` | 12 | session length |
| `ARGOS_SECRET` | generated | session signing key |
| `ARGOS_CORS` | `*` | allowed web origins |

---

## 7. Privacy and security

| Measure | Implementation |
|---|---|
| Clips kept 24 h at most | automatic deletion every 10 min; the device also erases its clips older than 24 h |
| Dismissed = deleted | the clip disappears as soon as the teacher dismisses the alert |
| Data stays in the school | the clip leaves the Pi only towards the local server, then is erased from the device |
| No identification | no face recognition, no gaze estimation; student numbers (#1, #2…) valid for the session only |
| Neutral wording | "possible", never "cheating" |
| Role-based access | a teacher sees only their classrooms; keys and passwords stored hashed |
| Accountability | every view, review, deletion and assignment is logged |

- **Known weak point**: the web video player receives the session token in the clip URL (`?t=`),
  so it can appear in logs. Planned: a short-lived token valid for one clip only.
- **Licence**: Ultralytics is AGPL-3.0. A commercial version needs the enterprise licence,
  or a switch to RTMDet/RTMPose.

---

## 8. Development on a PC

### 8.1 Run the tests

```bash
cd edge        && python -m pytest -q      # 30 tests: rules, clips, camera, live summary
cd backend     && python -m pytest -q      # 11 tests: end-to-end API
cd calibration && python -m pytest -q      #  4 tests
cd app         && flutter analyze && flutter test   # 15 tests
```

Run them after **every** change to `edge/` or `backend/`.

### 8.2 Try the pipeline without a Pi

```bash
cd edge
python -m argos_edge.main --source 0 --dry-run --show                                # webcam
python -m argos_edge.main --source my_video.mp4 --dry-run --save-annotated annotated.mp4
```

To send events to a local backend, give the device key through the environment
(`config/edge.yaml` only holds the placeholder `CHANGE_ME`, so no secret is committed):
`set ARGOS_DEVICE_KEY=<key printed by app.cli create-classroom>` (Windows) or `export ARGOS_DEVICE_KEY=...`.

`--dry-run` prints events without a server. The annotated image shows each student's number
and `p=` value (h_pitch), with "BAS" below the threshold.

### 8.3 Build the app

```bash
cd app
flutter run                                         # phone connected (USB debugging)
flutter build apk --release                         # APK for teachers
flutter build web --release --no-web-resources-cdn  # works without Internet
cp -r build/web ../backend/web && cp build/app/outputs/flutter-apk/app-release.apk ../backend/web/argos.apk
```

Android (permissions, local HTTP traffic, desugaring for notifications) and iOS (local network,
camera) configuration is already done in `app/android` and `app/ios`.

### 8.4 Calibrate the thresholds

```bash
cd calibration
python calibrate_scb.py --root datasets/SCBehavior/SCBehavior_YOLO \
    --names "write,read,lookup,turn_head,raise_hand,stand,discuss" \
    --head-neg "read,write" --out resultats_scbehavior
```

Output: `features.csv` and `rapport.md` (proposed thresholds). Copy the values into `config/edge.yaml`.

- **SCBehavior**: Git LFS files with a broken `.gitattributes` (`git lfs pull` fetches nothing:
  download through `media.githubusercontent.com`). The class order is **not** the one in its README
  (verified: 0 write, 1 read, 2 lookup, 3 turn_head, 4 raise_hand, 5 stand, 6 discuss).
- **SCB-Dataset3** ([link](https://github.com/Whiffe/SCB-dataset)): "bow head" and "using phone"
  classes, to use with `--head-pos` and `--phone-pos`.
- **SLBDetection-Net** is not used: it is a model to train, with no published weights.

k-of-W windows are tuned on staged **videos**, not on images.

---

## 9. Troubleshooting

### 9.1 Useful commands on the Pi

```bash
bash ~/argos/deploy/check_pi.sh          # temperature, memory, services, frames/s, latency
bash ~/argos/deploy/check_pi.sh --bench  # measurement over 150 frames
journalctl -u argos-edge -f              # live detection log
journalctl -u argos-backend -f           # live server log
sudo systemctl restart argos-edge argos-backend
```

### 9.2 Known problems

| Symptom | Cause | Fix |
|---|---|---|
| "No cameras available" | ribbon in the DISPLAY connector, upside down or not fully inserted | Pi **off and unplugged**: ribbon in **CAMERA** (between audio jack and HDMI), contacts facing HDMI, then reboot |
| `imx219 … error -121` / `-110` | the sensor does not answer | wiring, ribbon or camera problem, not software |
| "No space left on device" during install | `/tmp` is a RAM disk on Debian 13 | already fixed in `install_pi.sh` |
| Huge NVIDIA CUDA downloads | default PyTorch on ARM | already fixed: CPU PyTorch installed first |
| App: "Serveur injoignable" (server unreachable) | different network, or Wi-Fi isolating devices | same Wi-Fi as the Pi; try a mobile hotspot |
| Blank web page | resources loaded from the Internet | build with `--no-web-resources-cdn` |
| Clips do not play in the browser | ffmpeg missing | `sudo apt install ffmpeg` |
| Very slow detection | Pi 4, or `.pt` models | NCNN models, `imgsz: 480`, `phone_every_n: 2`, or a Pi 5 |

---

## 10. Project status

**Verified**
- 60 automated tests pass (device 30, server 11, app 15, calibration 4).
- Full installation on a Raspberry Pi 4: server, detection, auto-start.
- End-to-end chain in real conditions: detection → alert → app.
- Speed measured on a Pi 4: 914 ms per frame at 640 px (≈ 1.1 frames/s), ≈ 720 MB of memory.

**Limitations**
- Pi camera module not detected yet (hardware problem).
- Reading and writing look like "head down".
- Small phones, or phones hidden by the hand, are poorly detected.
- Precision, recall and F1 not yet measured on real classroom videos.

**Next steps**
1. Get the Pi camera working, then run `check_pi.sh --bench`.
2. Choose: Raspberry Pi 5, or `imgsz: 320` on the Pi 4.
3. Record staged videos, annotate them, measure precision, recall and F1.
4. Compare teacher context against fixed thresholds.
5. Wrist-motion criterion to recognise writing.
6. Short-lived clip tokens, HTTPS, push notifications, admin screens, iOS build.

**Deviations from the technical report submitted to the jury**: Pi 4 instead of Pi 5;
≈ 1 frame/s instead of 5; head-down threshold 0.34 (calibrated); tiers 0.60 / 0.35 instead of
0.85 / 0.60; clip and notification for the medium tier too; clips kept 24 h; conflict rule added.
Details in [`docs/Argos_Technical_Report.pdf`](docs/Argos_Technical_Report.pdf).
