#!/usr/bin/env python3
"""Simpsons TV video player.

A small TV with virtual channels. Every .mp4 on the USB drive (videos/ on the
drive mounted at /mnt/simpsonstv, see setup.sh) plus any in the videos/ folder
next to this script is the library. channels.py splits the library into
channels, each a fixed shuffled playlist with its own clock running since boot,
so tuning away and back later lands further on in the schedule like a real
broadcast. A tap on the screen (touch.py) goes to the next channel through a
second of TV static; a long press is reserved for the menu.

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
import os
import queue
import time
from urllib.parse import unquote

import vlc

import channels
import touch

HERE = os.path.dirname(os.path.realpath(__file__))
DRIVE = '/mnt/simpsonstv'                        # USB thumb drive (fstab mount, absent is fine)
VIDEO_DIRS = [os.path.join(DRIVE, 'videos'), os.path.join(HERE, 'videos')]
STATIC_DIRS = [os.path.join(DRIVE, 'static'), os.path.join(HERE, 'static')]
CONFIG_PATHS = [os.path.join(DRIVE, 'channels.json'), os.path.join(HERE, 'channels.json')]

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


def log(msg):
    print(msg, flush=True)


def hms(seconds):
    seconds = int(seconds)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def static_clip(channel_name):
    """Path of the static clip to show when tuning to this channel, or None."""
    for d in STATIC_DIRS:
        for name in ('ch%s.mp4' % channel_name, 'static.mp4'):
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
    return None


class TV:
    """One VLC media player showing whichever channel is selected."""

    STATIC, EPISODE, IDLE = 'static', 'episode', 'idle'

    def __init__(self, instance, chans, events):
        self.instance = instance
        self.channels = chans
        self.events = events             # shared queue: gestures and player events
        self.player = instance.media_player_new()
        self.state = self.IDLE
        self.current = 0                 # channel being shown (or last shown)
        self.target = 0                  # channel to show once the static clip ends
        self.playing_index = None        # index into the channel's playlist
        self.started_at = 0.0            # monotonic time of the last play()
        # Callbacks run on VLC's thread: never call libvlc from them, just post a note.
        em = self.player.event_manager()
        em.event_attach(vlc.EventType.MediaPlayerEndReached, lambda e: self.events.put('ended'))
        em.event_attach(vlc.EventType.MediaPlayerEncounteredError, lambda e: self.events.put('ended'))

    def _play(self, path, offset=0.0):
        media = self.instance.media_new_path(path)
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

    def on_ended(self):
        if self.state == self.STATIC:
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
        if self.state == self.IDLE:
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
    tv = TV(instance, chans, events)

    def on_media_changed(event):
        media = tv.player.get_media()
        if media is not None:
            log('Now playing: ' + unquote(os.path.basename(media.get_mrl())))
    tv.player.event_manager().event_attach(vlc.EventType.MediaPlayerMediaChanged, on_media_changed)

    touch.TouchInput(log=log, events=events).start()
    tv.show(0)

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
        elif event == touch.TAP:
            if now - last_tap >= TAP_HOLDOFF:
                last_tap = now
                log('Tap: next channel')
                tv.next_channel()
        elif event == touch.LONG_PRESS:
            log('Long press: menu (not built yet)')

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
