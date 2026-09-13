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

1. **Packages**: `vlc-bin vlc-plugin-base vlc-plugin-video-output python3-vlc python3-rpi-lgpio
   python3-evdev python3-pil fonts-dejavu-core device-tree-compiler evtest i2c-tools git exfatprogs`
   (with `--no-install-recommends`; the full `vlc` metapackage drags in Qt and X11).
2. **Overlay**: copies `pi/overlays/vc4-kms-dpi-2inch8.dtbo` (from Waveshare's
   [28DPI-DTBO.zip](https://files.waveshare.com/wiki/2.8inc-DPI-LCD/28DPI-DTBO.zip)) into
   `/boot/firmware/overlays/`. It is `vc4-kms-dpi-generic` with the panel timings baked in:
   480x640, 26.88 MHz pixel clock, RGB666 on GPIO 0-9, 12-17, 20-25, which leaves 18, 19 and 26
   free for backlight, audio and the switch. Also compiles `pi/overlays/simpsonstv-touch.dts`
   into `/boot/firmware/overlays/` for the touch controller (see "Touch" below).
3. **`/boot/firmware/config.txt`**, `[all]` section (legacy lines removed):

       dtoverlay=vc4-kms-dpi-2inch8,rotate=90
       dtoverlay=audremap,enable_jack,pins_18_19
       dtoverlay=simpsonstv-touch

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
   `wifi.powersave=2`, and the same is set with `nmcli` on every existing WiFi profile because
   the profile Imager creates (a netplan file under `/etc/netplan/`) carries its own powersave
   value that overrides the conf.d default. With the driver default (power save on) the Zero 2 W
   vanished from the network for minutes at a time during its first setup, and once for good
   until power-cycled. Takes effect on the next reconnect.
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

`player.py` treats every `.mp4` in `videos/` on the USB drive (and in `~/simpsonstv/videos/`,
kept for test clips) as the library and plays it through one libvlc media player
(`python3-vlc`) with `--vout=drm_vout --codec=avcodec --avcodec-codec=h264`. The same player
object is reused for every file for the life of the service, so VLC opens the DRM output once
and keeps it (confirmed in the verbose log: one `OpenDrmVout` across episodes and static clips,
no close), and the console never shows through between items. An earlier version spawned a
fresh `cvlc` per batch and the tty1 login prompt flashed on the panel every time a batch ended.
New files copied into either folder join the channels within 30 s of settling. Every 10 minutes
the journal gets a line with displayed and lost frame counts.

### Channels

`channels.py` turns the library into virtual channels. Each channel is a fixed shuffled
playlist (seeded from the channel name, so the order survives reboots) with a clock that
started at a random point at boot. The channel "is" always somewhere in its schedule; when you
tune to it the player computes where from the file durations and starts the episode there
(VLC's `start-time` option, accurate to within a second), so tuning away and back later lands
further on, like a broadcast. Nothing is decoded for channels nobody is watching. When an
episode ends the player moves to whatever the clock says is next. Durations come straight
from each file's MP4 header (`mvhd`), which is instant even for hundreds of files on the
thumb drive; they match `ffprobe` to within 50 ms on the encoded episodes.

A **tap** goes to the next channel: a second of TV static plays first, then the channel's
current programme. Taps during the static restart it for the next channel, so you can flip
through. By default there are three channels, each carrying every episode in a different
order. `channels.json` at the root of the drive (or in `~/simpsonstv/`) overrides that:

    {"channels": [
        {"name": "3", "match": ["S01", "S02", "S03"]},
        {"name": "5", "match": ["S23"]},
        {"name": "7"}
    ]}

`match` is a list of case-insensitive substrings of the file name; leave it out for a channel
that carries everything. The player logs `Channel 5: The Simpsons S23E04.mp4 at 0:12:31` on
every change.

### Static clips

`python3 pi/encode.py --static 9` on the Mac writes `pi/static/static.mp4` (plain snow with
hiss, 480x640, 24 fps, one second) and `ch1.mp4` to `ch9.mp4`, the same with a big green
block-font channel number top right, drawn with ffmpeg's `drawbox` because Homebrew's ffmpeg
has no `drawtext`. Copy the `static/` folder to the root of the SIMPSONSTV drive (the player
also looks in `~/simpsonstv/static/`). The clips are not in git: a second of noise is close to
a megabyte no matter how it is encoded. Without them a channel change is instant, no static.

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

## Touch

The panel's capacitive touch is a Goodix GT911 on the display's own I2C lines: header pins
19, 23 and 13, which are GPIO 10 (SDA), 11 (SCL) and 27 (interrupt). The hardware I2C pins
are part of the DPI bus, so the bus is bit-banged with the kernel's `i2c-gpio` driver.
Waveshare's `waveshare-touch-28dpi.dtbo` (in the same zip as the panel overlay) does exactly
that, but it also declares GPIO 18 as a kernel `gpio-backlight`, which would take the pin
away from `buttons.py`. `pi/overlays/simpsonstv-touch.dts` is the same thing without the
backlight node, one GT911 node at address 0x5d (the address the controller actually answers
on, checked with `i2cdetect`), and the mainline `goodix` driver. It compiles with `dtc` in
`setup.sh`. The controller shows up as `/dev/input/eventN`, name "Goodix Capacitive
TouchScreen", and reports `BTN_TOUCH` plus X 0-639, Y 0-479 (Waveshare's axis swap; only the
future menu cares about coordinates).

`touch.py` reads it with `python3-evdev` in a thread inside the player and turns it into two
gestures: a **tap** (down and up within 0.4 s, less than 30 units of movement) and a **long
press** (held still for 0.8 s, fires while the finger is still down). Swipes, slow taps and
extra fingers do nothing. A tap is "change channel" (taps within 0.4 s of the last are
ignored) and a long press is "open the menu". Each gesture carries the finger position in
screen pixels (640x480 landscape, origin top left). `python3 touch.py` prints gestures for
tuning the thresholds (`--raw` for the controller's own coordinates); `evtest` shows the raw
events. The service user must be in the `input` and `video` groups (the installer does this).

### Menu

`menu.py` is the long-press menu. The player stops VLC, which releases the display, and the
kernel puts the console framebuffer back on the panel (`/dev/fb0`, `vc4drmfb`, 480x640
RGB565, black because tty1 has no login prompt). The menu is drawn there with Pillow
(`python3-pil`, DejaVu Sans Bold) as a 640x480 landscape picture rotated 90 degrees
counter-clockwise on the way in, the same rotation `encode.py` bakes into the episodes.
Measured on the Zero 2 W: VLC lets go in 0.07 s, the menu draws in 0.14 s, and VLC is
playing again 0.1 s after the menu closes.

Five rows, each one finger high:

| Row | Tap | Effect |
|---|---|---|
| CHANNEL `<` n `>` | the arrows | picks a channel; applied (through static) when the menu closes |
| LOOK | anywhere | CLEAN or VINTAGE: with VINTAGE the player plays `videos/fuzzy/<same name>.mp4` when it exists, else the clean file (the fuzzy encodes are roadmap item 4) |
| VOLUME `<` n `>` | the arrows | VLC's software gain in steps of 10, applied when video resumes |
| SHUT DOWN | twice | the row turns red and asks for a second tap, then `sudo systemctl poweroff` with SHUTTING DOWN on the panel |
| DONE | anywhere | closes the menu |

A long press or 20 s without a touch also closes it. Look and volume are saved in
`~/simpsonstv/settings.json` (defaults: volume 100, clean) and restored at boot. If Pillow is
missing or `/dev/fb0` cannot be opened, the player logs it once and a long press does
nothing.

Touch coordinates: with the overlay's axis flags the GT911 reports X 0-639 and Y 0-479,
which is the viewer's landscape frame when the controller's origin sits at the panel's native
top-left corner. `SWAP_XY`, `FLIP_X` and `FLIP_Y` in `touch.py` correct it if it does not.
To check: `sudo systemctl stop tvplayer`, then `python3 ~/simpsonstv/menu.py` draws the menu
and, for every tap, prints the raw and screen coordinates and draws a red ring where the tap
landed. Touch a corner: a ring at the opposite end of an axis means that axis is flipped.

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

    pinctrl get 10,11,18,19,26,27             # pin state (10/11/27 are the touch bus)
    sudo dmesg | grep Goodix                  # "Goodix-TS 11-005d" = bus 11, address 0x5d
    sudo i2cdetect -y 11                      # the GT911 should show at 5d
    sudo evtest                               # pick the Goodix device, then poke the screen
    aplay -l                                  # should list "bcm2835 Headphones"
    speaker-test -D default -c 2 -t sine -l 1 # tone through the TV speaker
    cat /sys/class/drm/card0-DPI-1/status     # "connected"
    sudo journalctl -u tvplayer -u tvbutton -f
    sudo systemctl restart tvplayer
    sudo systemctl stop tvplayer && python3 ~/simpsonstv/menu.py   # menu without the player

Backups of the original boot files are left at `/boot/firmware/config.txt.bak-*` and
`cmdline.txt.bak-*`.
