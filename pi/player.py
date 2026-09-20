#!/usr/bin/env python3
"""Simpsons TV video player.

A small TV with virtual channels. Every .mp4 on the USB drive (videos/ on the
drive mounted at /mnt/simpsonstv, see setup.sh) plus any in the videos/ folder
next to this script is the library. channels.py splits the library into
channels, each a fixed shuffled playlist with its own clock running since boot,
so tuning away and back later lands further on in the schedule like a real
broadcast. A tap on the screen (touch.py) goes to the next channel through a
second of TV static; a long press opens the menu (menu.py): VLC is stopped,
which hands the panel to the console framebuffer, the menu is drawn there, and
VLC takes the panel back when the menu closes. Channel, look (clean or the
vintage encode from encode.py --fuzzy), volume and a clean shutdown live there;
look and volume persist in settings.json next to this script.

The power knob (buttons.py) only darkens the panel and mutes the amp, but it
also tells this player: while the TV is "off" VLC is stopped (nothing is
decoded, and the panel shows the black console behind the dark backlight), and
when it comes back "on", and at boot, static/poweron.mp4 plays first if there
is one, then the channel at wherever its clock has got to.

Everything plays inside ONE long-lived VLC media player driven through libvlc
(python3-vlc). The process never exits and the player object is reused for
every file, so VLC keeps hold of the display and the text console never gets
a chance to show through between items.

Uses the Raspberry Pi DRM video output and VLC's software H.264 decoder
(the hardware decoder path runs at half speed on the Zero 2 W, see VLC_ARGS).
Videos should already be encoded for the panel (see encode.py): 480x640,
pre-rotated so no rotation is needed at playback time.

New files dropped into either folder are picked up on the next rescan and
appended to every channel that wants them without disturbing playback. A
file is ignored until it has stopped changing for a minute so a copy in
progress is never queued half-written.
"""
import json
import os
import queue
import subprocess
import threading
import time
from urllib.parse import unquote

import vlc

import channels
import touch

try:
    import menu
except ImportError:      # no python3-pil: the TV still works, a long press just logs
    menu = None

HERE = os.path.dirname(os.path.realpath(__file__))
DRIVE = '/mnt/simpsonstv'                        # USB thumb drive (fstab mount, absent is fine)
VIDEO_DIRS = [os.path.join(DRIVE, 'videos'), os.path.join(HERE, 'videos')]
STATIC_DIRS = [os.path.join(DRIVE, 'static'), os.path.join(HERE, 'static')]
CONFIG_PATHS = [os.path.join(DRIVE, 'channels.json'), os.path.join(HERE, 'channels.json')]
SETTINGS_PATH = os.path.join(HERE, 'settings.json')
LOOKS = ('clean', 'vintage')
DEFAULT_SETTINGS = {'volume': 100, 'look': LOOKS[0]}
VINTAGE_DIR = 'fuzzy'     # videos/fuzzy/<name>.mp4 is the vintage version of videos/<name>.mp4
POWER_PATH = '/run/simpsonstv/power'             # "on" or "off", written by buttons.py
POWER_CLIP = 'poweron.mp4'                       # in static/, played at boot and at every switch-on

VLC_ARGS = [
    '--quiet',
    '--no-osd',
    '--no-video-title-show',
    '--vout=drm_vout',           # Raspberry Pi KMS/DRM output, holds the panel between items
    '--aout=alsa',
    '--codec=avcodec',
    '--avcodec-codec=h264',      # software H.264. VLC's h264_v4l2m2m hardware path plays the
                                 # picture at exactly half speed on the Zero 2 W (audio and
                                 # demux clock run at 1x, picture falls behind), measured
                                 # 2026-09-13. Software decode of 480x640 24 fps costs about
                                 # half of one of the four cores.
]

RESCAN_SECONDS = 30      # how often to look for new files
STATS_SECONDS = 600      # how often to log decoder/display counters
TAP_HOLDOFF = 0.4        # ignore taps this soon after the last one (double taps, bounces)
WATCHDOG_SECONDS = 5     # VLC sitting in Ended/Error this long without telling us = restart
MENU_TIMEOUT = 20        # close the menu after this long without a touch
POWER_POLL = 0.2         # how often to look at the power knob's state file


def log(msg):
    print(msg, flush=True)


def hms(seconds):
    seconds = int(seconds)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update({k: v for k, v in json.load(f).items() if k in s})
    except FileNotFoundError:
        pass
    except (OSError, ValueError, AttributeError) as e:
        log('Bad %s, using defaults: %s' % (SETTINGS_PATH, e))
    s['volume'] = max(0, min(100, int(s['volume'])))
    if s['look'] not in LOOKS:
        s['look'] = LOOKS[0]
    return s


def save_settings(s):
    try:
        with open(SETTINGS_PATH + '.tmp', 'w') as f:
            json.dump(s, f)
        os.replace(SETTINGS_PATH + '.tmp', SETTINGS_PATH)
    except OSError as e:
        log('Cannot save %s: %s' % (SETTINGS_PATH, e))


def find_clip(*names):
    """Path of the first of `names` found in a static/ folder, or None."""
    for d in STATIC_DIRS:
        for name in names:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


def static_clip(channel_name):
    """Path of the static clip to show when tuning to this channel, or None."""
    return find_clip('ch%s.mp4' % channel_name, 'static.mp4')


def power_is_on():
    """What the power knob says. No state file (no buttons.py, no knob) means on."""
    try:
        with open(POWER_PATH) as f:
            return f.read().strip() != 'off'
    except OSError:
        return True


def watch_power(events, state):
    """Thread: post 'power_on' / 'power_off' whenever the knob's state file changes."""
    while True:
        time.sleep(POWER_POLL)
        on = power_is_on()
        if on != state:
            state = on
            events.put('power_on' if on else 'power_off')


class TV:
    """One VLC media player showing whichever channel is selected."""

    STATIC, EPISODE, IDLE, MENU, INTRO, OFF = 'static', 'episode', 'idle', 'menu', 'intro', 'off'

    def __init__(self, instance, chans, events, look='clean'):
        self.instance = instance
        self.channels = chans
        self.events = events             # shared queue: gestures and player events
        self.player = instance.media_player_new()
        self.state = self.IDLE
        self.look = look                 # 'clean' or 'vintage', see variant()
        self.volume = None               # 0-100 software gain, see set_volume()
        self.volume_pending = False
        self.current = 0                 # channel being shown (or last shown)
        self.target = 0                  # channel to show once the static clip ends
        self.playing_index = None        # index into the channel's playlist
        self.started_at = 0.0            # monotonic time of the last play()
        # Callbacks run on VLC's thread: never call libvlc from them, just post a note.
        em = self.player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: self.events.put('ended'))
        em.event_attach(vlc.EventType.MediaPlayerEncounteredError, lambda e: self.events.put('ended'))
        em.event_attach(vlc.EventType.MediaPlayerPlaying, lambda e: self.events.put('playing'))

    def variant(self, path):
        """The file to actually play for `path` given the current look."""
        if self.look == 'vintage':
            alt = os.path.join(os.path.dirname(path), VINTAGE_DIR, os.path.basename(path))
            if os.path.isfile(alt):
                return alt
        return path

    def set_volume(self, volume):
        """Software gain in VLC, 0-100. libvlc can only set it while an audio output
        exists, which is not the case in the menu (player stopped) or in the first moments
        of an item, so apply_volume() is retried from the main loop until it takes. Once set
        the value survives stop and restart (VLC copies it to the media player object)."""
        self.volume = int(volume)
        self.volume_pending = True
        self.apply_volume()

    def apply_volume(self):
        if self.volume_pending and self.player.audio_set_volume(self.volume) == 0:
            self.volume_pending = False
            log('Volume %d' % self.volume)

    def _play(self, path, offset=0.0):
        media = self.instance.media_new_path(self.variant(path))
        if offset > 0:
            media.add_option(':start-time=%.3f' % offset)
        self.player.set_media(media)
        self.player.play()
        self.started_at = time.monotonic()

    def tune(self, index):
        """Change channel: a burst of static, then the channel's current programme."""
        self.target = index % len(self.channels)
        clip = static_clip(self.channels[self.target].name)
        if clip:
            self.state = self.STATIC
            self._play(clip)
        else:
            self.show(self.target)

    def show(self, index, pos=None):
        """Show what channel `index` is broadcasting right now (or the given position)."""
        self.current = self.target = index
        ch = self.channels[index]
        pos = pos or ch.position()
        if pos is None:
            self.state = self.IDLE
            self.playing_index = None
            self.player.stop()
            log('Channel %s: nothing to play' % ch.name)
            return
        i, path, offset = pos
        self.state = self.EPISODE
        self.playing_index = i
        log('Channel %s: %s at %s' % (ch.name, os.path.basename(path), hms(offset)))
        self._play(path, offset)

    def next_channel(self):
        self.tune(self.target + 1)

    def open_menu(self):
        """Stop VLC so the panel falls back to the console framebuffer for menu.py."""
        self.state = self.MENU
        self.player.stop()

    def close_menu(self, channel):
        """Back to television: through static if the channel changed, else straight on."""
        self.state = self.IDLE
        if channel != self.current:
            self.tune(channel)
        else:
            self.show(self.current)

    def power_off(self):
        """The knob went to off: stop decoding. The channel clocks carry on by themselves."""
        self.state = self.OFF
        self.player.stop()

    def power_on(self):
        """Boot or the knob going to on: the power-on clip if there is one, then television."""
        clip = find_clip(POWER_CLIP)
        if clip:
            self.state = self.INTRO
            self._play(clip)
        else:
            self.show(self.target)

    def on_ended(self):
        if self.state in (self.MENU, self.OFF):
            return
        if self.state in (self.STATIC, self.INTRO):
            self.show(self.target)
        elif self.state == self.EPISODE:
            ch = self.channels[self.current]
            pos = ch.position()
            if pos is None:
                self.show(self.current)
            elif pos[0] == self.playing_index:
                # The clock says this item is still on (VLC finished a hair before the
                # header duration). Move to the next one rather than replay the end.
                self.show(self.current, ch.after(self.playing_index))
            else:
                self.show(self.current, pos)

    def check(self):
        """Watchdog for a lost end-of-item event or a stuck player."""
        if self.state in (self.IDLE, self.MENU, self.OFF):
            return
        if time.monotonic() - self.started_at < WATCHDOG_SECONDS:
            return
        if self.player.get_state() in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped):
            log('Player is %s without telling us, moving on' % self.player.get_state())
            self.on_ended()


def load_library(known):
    """(new entries, config) where entries are (path, duration) for settled files not in `known`."""
    entries = []
    for path in channels.scan_videos(VIDEO_DIRS):
        if path in known:
            continue
        dur = channels.mp4_duration(path)
        if not dur:
            log('Skipping %s: cannot read its duration' % os.path.basename(path))
            known.add(path)          # do not retry every rescan
            continue
        entries.append((path, dur))
        known.add(path)
    return entries


def main():
    settings = load_settings()
    log('Settings: volume %d, look %s' % (settings['volume'], settings['look']))
    screen = None
    if menu is None:
        log('Menu: python3-pil not installed, long press will do nothing')
    else:
        try:
            screen = menu.Framebuffer()
        except (OSError, RuntimeError) as e:
            log('Menu: no usable framebuffer (%s), long press will do nothing' % e)

    try:
        config, config_path = channels.load_config(CONFIG_PATHS)
    except ValueError as e:
        log('Bad channels.json, using defaults: %s' % e)
        config, config_path = None, None
    chans = channels.build_channels(config)
    log('%d channels (%s): %s' % (len(chans), config_path or 'defaults',
                                  ', '.join(c.name for c in chans)))

    known = set()
    entries = load_library(known)
    while not entries:
        log('No videos in %s, waiting...' % ' or '.join(VIDEO_DIRS))
        time.sleep(10)
        entries = load_library(known)
    for c in chans:
        c.add(entries)
    for c in chans:
        log('Channel %s: %d videos, %s of programming' % (c.name, len(c), hms(c.total)))

    events = queue.Queue()
    instance = vlc.Instance(VLC_ARGS)
    tv = TV(instance, chans, events, look=settings['look'])

    def on_media_changed(event):
        media = tv.player.get_media()
        if media is not None:
            log('Now playing: ' + unquote(os.path.basename(media.get_mrl())))
    tv.player.event_manager().event_attach(vlc.EventType.MediaPlayerMediaChanged, on_media_changed)

    touch.TouchInput(log=log, events=events).start()
    tv.set_volume(settings['volume'])
    powered = power_is_on()
    threading.Thread(target=watch_power, args=(events, powered), daemon=True).start()
    if powered:
        tv.power_on()
    else:
        log('Power: off, waiting for the knob')
        tv.power_off()

    ui = None                # the open menu.Menu, or None while watching TV
    menu_touched = 0.0       # monotonic time of the last touch while the menu was open

    def open_menu():
        nonlocal ui, menu_touched
        tv.open_menu()
        ui = menu.Menu([c.name for c in chans], tv.current, tv.look, settings['volume'])
        menu_touched = time.monotonic()
        screen.show(ui.render())
        log('Menu: open')

    def close_menu(why):
        nonlocal ui
        log('Menu: closed (%s)' % why)
        chosen, ui = ui.channel, None
        tv.close_menu(chosen)

    last_rescan = last_stats = time.monotonic()
    last_tap = 0.0
    while True:
        # Wake up for a gesture or a player event, or once a second for housekeeping.
        try:
            event = events.get(timeout=1.0)
        except queue.Empty:
            event = None
        now = time.monotonic()

        if event == 'ended':
            tv.on_ended()
        elif event == 'power_off':
            log('Power: off')
            ui = None                # an open menu just goes away
            tv.power_off()
        elif event == 'power_on':
            log('Power: on')
            tv.power_on()
        elif isinstance(event, tuple) and tv.state == tv.OFF:
            pass                     # the dark screen ignores fingers
        elif isinstance(event, tuple):
            kind, x, y = event
            if ui is not None:
                menu_touched = now
                if kind == touch.LONG_PRESS:
                    close_menu('long press')
                elif kind == touch.TAP:
                    changed = ui.tap(x, y)
                    if changed == menu.DONE:
                        close_menu('done')
                    elif changed == menu.SHUTDOWN:
                        log('Menu: shutting down')
                        screen.show(menu.message('SHUTTING DOWN'))
                        save_settings(settings)
                        subprocess.run(['sudo', 'systemctl', 'poweroff'], check=False)
                    else:
                        if changed == menu.VOLUME:
                            settings['volume'] = ui.volume
                            tv.set_volume(ui.volume)
                            save_settings(settings)
                        elif changed == menu.LOOK:
                            settings['look'] = tv.look = ui.look
                            save_settings(settings)
                        if changed == menu.CHANNEL:
                            log('Menu: channel -> %s' % chans[ui.channel].name)
                        elif changed:
                            log('Menu: %s -> %s' % (changed, getattr(ui, changed)))
                        screen.show(ui.render())
            elif kind == touch.TAP:
                if now - last_tap >= TAP_HOLDOFF:
                    last_tap = now
                    log('Tap: next channel')
                    tv.next_channel()
            elif kind == touch.LONG_PRESS:
                if screen is None:
                    log('Long press: no menu available')
                else:
                    open_menu()

        if ui is not None and now - menu_touched >= MENU_TIMEOUT:
            close_menu('timeout')

        tv.apply_volume()
        tv.check()

        if now - last_rescan >= RESCAN_SECONDS:
            last_rescan = now
            new = load_library(known)
            if new:
                for c in chans:
                    c.add(new)
                log('Added %d new video(s)' % len(new))
                if tv.state == tv.IDLE:
                    tv.show(tv.current)

        if now - last_stats >= STATS_SECONDS:
            last_stats = now
            media = tv.player.get_media()
            if media is not None:
                st = vlc.MediaStats()
                if vlc.libvlc_media_get_stats(media, st):
                    log('Stats: displayed %d, lost %d, audio lost %d, decoded %d' % (
                        st.displayed_pictures, st.lost_pictures, st.lost_abuffers, st.decoded_video))


if __name__ == '__main__':
    main()
