# Simpsons TV on Raspberry Pi OS Trixie

Software setup for the 3D-printed Simpsons TV
([withrow.io guide](https://withrow.io/simpsons-tv-build-guide-waveshare)) brought up to date
for current Raspberry Pi OS. Worked out on a Pi Zero W on 2026-09-06; intended to be replayed
on a Pi Zero 2 W for the final build.

Hardware: Raspberry Pi Zero W / Zero 2 W, Waveshare 2.8" DPI LCD (480x640 native, mounted
landscape), PAM8302-style amp on GPIO 19, power knob switch on GPIO 26, panel backlight on GPIO 18.

## Why the original guide no longer works

| Guide step (Buster, 2020) | What changed |
|---|---|
| Legacy DPI lines in config.txt (`dpi_output_format`, `hdmi_timings`, `display_rotate`, `dtoverlay=dpi24`) | Only the firmware honours these, so you see boot text, then the KMS driver takes over without a panel and the screen freezes. |
| `dtoverlay=waveshare-28dpi-*` | Those files are the old touch overlays and are not in the OS. Waveshare now ships a KMS panel overlay, `vc4-kms-dpi-2inch8`. |
| `raspi-gpio set 18 op dl` in `/etc/rc.local` | `raspi-gpio` is gone, replaced by `pinctrl`. `rc.local` is gone too. |
| `RPi.GPIO` in buttons.py | Replaced by the `python3-rpi-lgpio` shim, same API, already installed on Trixie. |
| `omxplayer` | Removed from Debian. Use VLC with the Raspberry Pi `drm_vout` output. Decoding is done in software: VLC's `h264_v4l2m2m` hardware path plays the picture at half speed on the Zero 2 W (see below). |
| `sudo apt-get install usbmount`, udevd `PrivateMounts` edit | Replaced by an fstab mount of a labelled exFAT drive (see below). |
| `/usr/bin/python` in the service files | Python 2 is gone. Use `/usr/bin/python3`. |

## Quick replay (Zero 2 W)

1. Flash **Raspberry Pi OS Lite (32-bit), Trixie** with Raspberry Pi Imager. In the OS
   customisation set hostname, user, WiFi and enable SSH. The 32-bit image is deliberate: the
   `bcm2835-codec` hardware decoder and the `vc4-kms-dpi-2inch8` overlay are 32-bit era
   components and this whole recipe was validated on the armhf build.
2. Boot, `ssh` in, and allow passwordless sudo (optional, only needed for remote-driven setup):
   `echo 'USER ALL=(ALL) NOPASSWD: ALL' | sudo tee /etc/sudoers.d/010_USER-nopasswd`
3. From the Mac, copy the `pi/` folder over and run the installer:

       scp -r pi/ USER@HOST:~/simpsonstv-setup
       ssh USER@HOST 'cd simpsonstv-setup && sudo bash setup.sh'

4. Encode episodes on the Mac (see below), copy them to the `SIMPSONSTV` thumb drive, plug it
   into the Pi, reboot.

`setup.sh` is idempotent and prints what it does. Everything it touches is listed below so it
can also be done by hand.

## What `setup.sh` does

1. **Packages**: `vlc-bin vlc-plugin-base vlc-plugin-video-output python3-vlc python3-rpi-lgpio git exfatprogs`
   (with `--no-install-recommends`; the full `vlc` metapackage drags in Qt and X11).
2. **Overlay**: copies `pi/overlays/vc4-kms-dpi-2inch8.dtbo` (from Waveshare's
   [28DPI-DTBO.zip](https://files.waveshare.com/wiki/2.8inc-DPI-LCD/28DPI-DTBO.zip)) into
   `/boot/firmware/overlays/`. It is `vc4-kms-dpi-generic` with the panel timings baked in:
   480x640, 26.88 MHz pixel clock, RGB666 on GPIO 0-9, 12-17, 20-25, which leaves 18, 19 and 26
   free for backlight, audio and the switch.
3. **`/boot/firmware/config.txt`**, `[all]` section (legacy lines removed):

       dtoverlay=vc4-kms-dpi-2inch8,rotate=90
       dtoverlay=audremap,enable_jack,pins_18_19

   `rotate=90` matches a panel whose native top edge is on the viewer's right. It only affects
   the text console; video is pre-rotated at encode time. `enable_jack` is what makes the
   `bcm2835 Headphones` ALSA card appear on a Zero, which has no jack.
4. **`/boot/firmware/cmdline.txt`**: `console=tty1` becomes `console=tty3`, plus
   `consoleblank=0 logo.nologo quiet loglevel=3 vt.global_cursor_default=0` so the panel shows
   nothing but video. `fsck.repair=yes` is kept (the guide removed it for no stated reason).
5. **`/etc/asound.conf`**: default device is the PWM card, stereo downmixed to mono on both
   channels because the amp only listens to GPIO 19 (PWM channel 1, the right channel).
6. **Scripts** into `~/simpsonstv/`: `player.py`, `buttons.py`, `encode.py`.
7. **Services** `tvplayer.service` and `tvbutton.service`, enabled at boot. The button service
   runs as root because `pinctrl` needs it. The player runs as the normal user (`User=` in the
   unit) because VLC refuses to start as root; the user must be in the `video`, `render` and
   `audio` groups, which the Imager-created user is. Two `XDG_RUNTIME_DIR` warnings in its log
   are VLC probing for a desktop sound server and can be ignored.
8. **No login prompt**: `systemctl disable --now getty@tty1`. tty1 is what the panel shows
   whenever VLC is not holding the display (boot, service restart), and without a getty it is
   plain black. Log in over SSH; there is no keyboard on this build anyway.
9. **WiFi power save off**: `/etc/NetworkManager/conf.d/wifi-powersave-off.conf` sets
   `wifi.powersave=2`. With the driver default (power save on) the Zero 2 W vanished from the
   network for minutes at a time during its first setup. Takes effect on the next reconnect.
10. **USB drive mount**: an fstab line mounts the exFAT drive labelled `SIMPSONSTV` at
   `/mnt/simpsonstv` with `nofail` and a 5 s device timeout, so the Pi boots normally without
   it, plus a udev rule (`/etc/udev/rules.d/99-simpsonstv-usb.rules`, `SYSTEMD_WANTS`) so the
   mount also happens when the drive is plugged in after boot; the fstab line alone did not
   do that. Episodes go in `videos/` on the drive.
11. **Persistent journal**: a journald drop-in (`/etc/systemd/journald.conf.d/simpsonstv.conf`,
   `Storage=persistent`, capped at 64 MB) overrides the Raspberry Pi OS default of a volatile
   journal, so `journalctl -b -1` still works after the power cycle that a WiFi drop forces.

## Episodes on a USB thumb drive

WiFi on a Zero is far too slow for a full run of episodes (a season is ~2.7 GB, the whole show
well over 100 GB), so they live on a thumb drive through a micro-USB OTG adapter. Format it
on the Mac as exFAT with the volume name `SIMPSONSTV` (readable and writable by both macOS
and the Pi), make a `videos` folder at its root, and copy encoded episodes there. If the drive
is a new one, check its real capacity first with `f3` (`brew install f3`, then `f3write` and
`f3read` against the mounted volume) before trusting it with 100 GB of episodes.

    diskutil eraseDisk ExFAT SIMPSONSTV MBR /dev/diskN      # find N with: diskutil list external

The player ignores macOS `._*` resource-fork files and anything modified in the last minute,
so a copy in progress is never queued half-written.

## How playback works

`player.py` shuffles every `.mp4` in `videos/` on the USB drive (and in `~/simpsonstv/videos/`,
kept for test clips) into one libvlc media list player
(`python3-vlc`) with `--vout=drm_vout --codec=avcodec --avcodec-codec=h264`, in loop mode, and
keeps that single VLC instance alive for the life of the service. VLC opens the DRM output once
and reuses it across episodes (confirmed in the verbose log: one `OpenDrmVout`, no close), so the
console never shows through between items. An earlier version spawned a fresh `cvlc` per
batch and the tty1 login prompt flashed on the panel every time a batch ended. New files
copied into either folder are appended to the playlist within 30 s of settling. Every 10 minutes
the journal gets a line with displayed and lost frame counts.

**Why software decoding.** On the Zero 2 W, VLC's hardware decoder path (`h264_v4l2m2m` via
the Raspberry Pi avcodec patches, `/dev/video10`) decodes fine but the picture reaches the
panel at exactly half speed while the audio and VLC's own position clock run at full speed,
so the picture falls a second behind the sound every two seconds. VLC's counters still claim
24 frames per second. This was pinned down on 2026-09-13 with a test clip that shows a digit
and beeps that many times: the digit changed every 20 s through the hardware path (measured
by hashing `/dev/fb0` through the `fb` output) and every 10.0 s with `--avcodec-codec=h264`.
ffmpeg's own `h264_v4l2m2m` decode on the same board is correct, so the fault is in VLC's use
of it, not the driver. Software decoding of a 480x640 24 fps Baseline stream costs about a
quarter to a half of one of the four cores, so it is the right trade. The Zero W was set up
with the hardware path and did not show this; it is not known whether that was the single
core, the `v6` kernel, or package versions. A hardware-decode fix is an open item, not a
requirement.

Measured on the Zero 2 W with software decoding: VLC around 25 percent of one core, board at
about 52 C, 24.1 page flips per second on the panel (counted from the DRM debug state), zero
lost frames.

`buttons.py` polls the switch on GPIO 26 (pulled up). On: GPIO 18 high (backlight) and GPIO 19
to ALT5 (PWM audio). Off: GPIO 18 low and GPIO 19 to input (mute). Video keeps running behind
a dark screen, same as the original design. Flip `INVERT_SWITCH` if the knob is backwards.

## Encoding episodes (on the Mac)

    python3 pi/encode.py /path/to/episodes

Produces `encoded/*.mp4`: scaled to fill 640x480 and cropped (a 16:9 episode loses about
12 percent off each side, a 4:3 episode loses nothing), rotated 90 degrees counter-clockwise
to 480x640 (`transpose=2`), H.264 Baseline yuv420p 24 fps, mono AAC. The Zero W cannot rotate
at playback time without dropping frames, so the rotation is baked in. If picture comes out
upside down, change `transpose=2` to `transpose=1` in `encode.py` and `rotate=90` to
`rotate=270` in `setup.sh`. Copy the results into `videos/` on the SIMPSONSTV drive, or for a
one-off test clip over WiFi:

    scp encoded/*.mp4 USER@HOST:~/simpsonstv/videos/

## Handy commands on the Pi

    pinctrl get 18,19,26                      # pin state
    aplay -l                                  # should list "bcm2835 Headphones"
    speaker-test -D default -c 2 -t sine -l 1 # tone through the TV speaker
    cat /sys/class/drm/card0-DPI-1/status     # "connected"
    sudo journalctl -u tvplayer -u tvbutton -f
    sudo systemctl restart tvplayer

Backups of the original boot files are left at `/boot/firmware/config.txt.bak-*` and
`cmdline.txt.bak-*`.
