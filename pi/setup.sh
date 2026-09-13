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
    vlc-bin vlc-plugin-base vlc-plugin-video-output python3-vlc python3-rpi-lgpio python3-evdev \
    device-tree-compiler evtest i2c-tools git exfatprogs

echo "==> 2. Waveshare KMS panel overlay, and our overlay for its touch controller"
install -m 644 "$HERE/overlays/vc4-kms-dpi-2inch8.dtbo" "$BOOT/overlays/"
# Touch: GT911 on bit-banged I2C (GPIO 10/11, interrupt 27). Compiled from source here so
# the source in git stays the one truth; see the comments in the .dts.
dtc -@ -q -I dts -O dtb -o "$BOOT/overlays/simpsonstv-touch.dtbo" "$HERE/overlays/simpsonstv-touch.dts"

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
                  r'dtoverlay=audremap|dtoverlay=simpsonstv-touch|dtoverlay=waveshare-touch-28dpi|'
                  r'# --- Simpsons TV|# Panel top edge|# PWM audio on GPIO 19|# Touch controller)')
lines = [l for l in s.splitlines() if not drop.match(l.strip())]
s = "\n".join(lines).rstrip("\n") + "\n"
block = """
# --- Simpsons TV: Waveshare 2.8in DPI LCD (480x640 native) under KMS ---
# Panel top edge sits on the viewer right, so rotate 90 for the console.
dtoverlay=vc4-kms-dpi-2inch8,rotate=90
# PWM audio on GPIO 19 (GPIO 18 is the backlight, driven as plain GPIO by buttons.py)
dtoverlay=audremap,enable_jack,pins_18_19
# Touch controller (GT911 on GPIO 10/11/27), read by touch.py
dtoverlay=simpsonstv-touch
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
install -m 755 -o "$TV_USER" -g "$TV_USER" "$HERE/player.py" "$HERE/touch.py" "$HERE/buttons.py" "$HERE/encode.py" "$TV_DIR/"
# player.py reads the touch panel through /dev/input (group input)
usermod -aG input "$TV_USER"

echo "==> 7. systemd services"
for svc in tvplayer tvbutton; do
    sed -e "s#/home/jason/simpsonstv#$TV_DIR#g" -e "s#^User=jason#User=$TV_USER#" "$HERE/$svc.service" > "/etc/systemd/system/$svc.service"
done
systemctl daemon-reload
systemctl enable tvplayer.service tvbutton.service

echo "==> 8. No login prompt on the panel"
# The kernel console is already on tty3 (step 4). tty1 is what the panel shows whenever
# VLC is not holding the display (boot, service restart, crash). Without a getty it is
# plain black. Log in over SSH instead.
systemctl disable --now getty@tty1.service 2>/dev/null || true

echo "==> 9. WiFi power save off"
# The brcmfmac driver enables power save by default and a Zero / Zero 2 W then drops off
# the network for minutes at a time when idle, which makes SSH and file copies unreliable.
# The conf.d default only applies to profiles that do not set powersave themselves, and the
# profile Raspberry Pi Imager creates (via netplan) does set it, so it is also set on every
# existing WiFi profile. NetworkManager reads this on (re)connect, so it takes effect at the
# next reboot. A 2026-09-13 A/B showed no measurable difference either way on a weak link,
# but every time the Pi vanished from the network completely it had power save on.
install -d /etc/NetworkManager/conf.d
printf '[connection]\nwifi.powersave=2\n' > /etc/NetworkManager/conf.d/wifi-powersave-off.conf
nmcli -t -f NAME,TYPE connection show | awk -F: '$2 == "802-11-wireless" {print $1}' |
  while read -r con; do nmcli connection modify "$con" 802-11-wireless.powersave 2; done

echo "==> 10. USB thumb drive mount (episodes live in videos/ on an exFAT drive labelled SIMPSONSTV)"
# nofail + a short device timeout: the Pi boots normally with no drive plugged in, and
# systemd mounts the drive whenever it appears. player.py polls the folder, so plugging
# the drive in after boot just works.
install -d /mnt/simpsonstv
TV_UID="$(id -u "$TV_USER")"; TV_GID="$(id -g "$TV_USER")"
grep -q '^LABEL=SIMPSONSTV ' /etc/fstab || \
    echo "LABEL=SIMPSONSTV /mnt/simpsonstv exfat nofail,x-systemd.device-timeout=5,uid=$TV_UID,gid=$TV_GID,umask=022,noatime 0 0" >> /etc/fstab
systemctl daemon-reload
# The fstab line alone only mounts a drive that is present at boot. This udev rule makes
# systemd start the mount unit whenever a drive with that label appears.
cat > /etc/udev/rules.d/99-simpsonstv-usb.rules <<'EOF'
# Simpsons TV: mount the episode drive whenever it is plugged in (fstab line in setup.sh)
ACTION=="add", SUBSYSTEM=="block", ENV{ID_FS_LABEL}=="SIMPSONSTV", ENV{SYSTEMD_WANTS}+="mnt-simpsonstv.mount"
EOF
udevadm control --reload

echo "==> 11. Persistent journal"
# Raspberry Pi OS ships journald with Storage=volatile (40-rpi-volatile-storage.conf), so
# every log is gone after a reboot. This build has no keyboard: when the WiFi drops the only
# way back in is a power cycle, and the evidence of what happened must survive it.
install -d /etc/systemd/journald.conf.d
printf '[Journal]\nStorage=persistent\nSystemMaxUse=64M\n' > /etc/systemd/journald.conf.d/simpsonstv.conf
systemctl restart systemd-journald

echo
echo "Done. Put encoded episodes in videos/ on the SIMPSONSTV drive (or $TV_DIR/videos) and reboot:  sudo reboot"
