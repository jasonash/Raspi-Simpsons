#!/bin/bash
# Simpsons TV: one-shot software setup for Raspberry Pi OS Lite (Trixie, 32-bit)
# on a Pi Zero W or Zero 2 W with the Waveshare 2.8" DPI LCD.
#
# Run ON the Pi, from the directory containing this script and its siblings:
#     sudo bash setup.sh
# Safe to re-run; every step is idempotent. Reboot when it finishes.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TV_USER="${SUDO_USER:-$(id -un)}"
TV_HOME="$(getent passwd "$TV_USER" | cut -d: -f6)"
TV_DIR="$TV_HOME/simpsonstv"
BOOT=/boot/firmware
[ -d "$BOOT" ] || BOOT=/boot

[ "$(id -u)" -eq 0 ] || { echo "run with sudo"; exit 1; }

echo "==> 1. Packages (VLC with the Raspberry Pi DRM output, no desktop bits)"
apt-get update -q
DEBIAN_FRONTEND=noninteractive apt-get install -y -q --no-install-recommends \
    vlc-bin vlc-plugin-base vlc-plugin-video-output python3-rpi-lgpio git

echo "==> 2. Waveshare KMS panel overlay"
install -m 644 "$HERE/overlays/vc4-kms-dpi-2inch8.dtbo" "$BOOT/overlays/"

echo "==> 3. config.txt: replace legacy Buster display lines with the KMS overlay + PWM audio"
cp -n "$BOOT/config.txt" "$BOOT/config.txt.bak-simpsonstv" || true
python3 - "$BOOT/config.txt" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
# Strip anything the old withrow.io guide or a previous run of this script added.
drop = re.compile(r'^(gpio=\d+-\d+=a2|dtoverlay=dpi24|enable_dpi_lcd=|display_default_lcd=|'
                  r'extra_transpose_buffer=|dpi_group=|dpi_mode=|dpi_output_format=|hdmi_timings=|'
                  r'dtoverlay=waveshare-28dpi|dtoverlay=vc4-kms-dpi-2inch8|display_rotate=|'
                  r'dtoverlay=audremap|# --- Simpsons TV|# Panel top edge|# PWM audio on GPIO 19)')
lines = [l for l in s.splitlines() if not drop.match(l.strip())]
s = "\n".join(lines).rstrip("\n") + "\n"
block = """
# --- Simpsons TV: Waveshare 2.8in DPI LCD (480x640 native) under KMS ---
# Panel top edge sits on the viewer left, so rotate 270 for the console.
dtoverlay=vc4-kms-dpi-2inch8,rotate=270
# PWM audio on GPIO 19 (GPIO 18 is the backlight, driven as plain GPIO by buttons.py)
dtoverlay=audremap,enable_jack,pins_18_19
"""
if "\n[all]\n" in s:
    head, _, tail = s.rpartition("\n[all]\n")
    s = head + "\n[all]\n" + tail.rstrip("\n") + "\n" + block
else:
    s = s + "\n[all]" + block
open(p, "w").write(s)
PY
grep -q '^dtoverlay=vc4-kms-v3d' "$BOOT/config.txt" || echo "WARNING: vc4-kms-v3d overlay missing from config.txt"

echo "==> 4. cmdline.txt: quiet boot, no cursor, console on tty3 so the panel stays clean"
cp -n "$BOOT/cmdline.txt" "$BOOT/cmdline.txt.bak-simpsonstv" || true
python3 - "$BOOT/cmdline.txt" <<'PY'
import sys
p = sys.argv[1]
args = open(p).read().split()
args = [a for a in args if a not in ("console=tty1", "console=tty3", "consoleblank=0", "logo.nologo",
                                     "quiet", "splash", "vt.global_cursor_default=0", "loglevel=3")]
args += ["console=tty3", "consoleblank=0", "logo.nologo", "quiet", "loglevel=3", "vt.global_cursor_default=0"]
open(p, "w").write(" ".join(args) + "\n")
PY

echo "==> 5. ALSA: PWM 'Headphones' card as default, stereo downmixed to mono"
install -m 644 "$HERE/asound.conf" /etc/asound.conf

echo "==> 6. Scripts in $TV_DIR"
install -d -o "$TV_USER" -g "$TV_USER" "$TV_DIR" "$TV_DIR/videos"
install -m 755 -o "$TV_USER" -g "$TV_USER" "$HERE/player.py" "$HERE/buttons.py" "$HERE/encode.py" "$TV_DIR/"

echo "==> 7. systemd services"
for svc in tvplayer tvbutton; do
    sed -e "s#/home/jason/simpsonstv#$TV_DIR#g" -e "s#^User=jason#User=$TV_USER#" "$HERE/$svc.service" > "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
systemctl enable tvplayer.service tvbutton.service

echo
echo "Done. Copy encoded episodes into $TV_DIR/videos and reboot:  sudo reboot"
