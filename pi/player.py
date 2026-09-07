#!/usr/bin/env python3
"""Simpsons TV video player.

Plays every .mp4 in the videos/ folder next to this script in a random
order, forever, inside ONE long-lived VLC instance driven through libvlc
(python3-vlc). The process never exits between episodes, so VLC keeps hold
of the display and the text console never gets a chance to show through.
(The earlier version spawned a fresh cvlc per shuffled batch; every time a
batch ended the tty1 login prompt flashed on the panel for a second.)

Uses the Raspberry Pi DRM video output and the bcm2835 hardware H.264
decoder. Videos should already be encoded for the panel (see encode.py):
480x640, pre-rotated so no rotation is needed at playback time.

New files dropped into videos/ are picked up on the next rescan and appended
to the running playlist without interrupting playback.
"""
import os
import random
import time
from urllib.parse import unquote

import vlc

HERE = os.path.dirname(os.path.realpath(__file__))
VIDEO_DIR = os.path.join(HERE, 'videos')

VLC_ARGS = [
    '--quiet',
    '--no-osd',
    '--no-video-title-show',
    '--vout=drm_vout',           # Raspberry Pi KMS/DRM output (zero-copy from the HW decoder)
    '--aout=alsa',
    '--codec=v4l2m2m,avcodec',   # prefer the BCM2835 hardware decoder
]

RESCAN_SECONDS = 30      # how often to look for new files in videos/
STATS_SECONDS = 600      # how often to log decoder/display counters


def log(msg):
    print(msg, flush=True)


def get_videos():
    try:
        names = os.listdir(VIDEO_DIR)
    except FileNotFoundError:
        return []
    return sorted(os.path.join(VIDEO_DIR, n) for n in names if n.lower().endswith('.mp4'))


def main():
    videos = get_videos()
    while not videos:
        log('No videos in %s, waiting...' % VIDEO_DIR)
        time.sleep(10)
        videos = get_videos()
    random.shuffle(videos)

    instance = vlc.Instance(VLC_ARGS)
    media_list = instance.media_list_new()
    for path in videos:
        media_list.add_media(instance.media_new_path(path))
    known = set(videos)

    list_player = instance.media_list_player_new()
    list_player.set_media_list(media_list)
    list_player.set_playback_mode(vlc.PlaybackMode.loop)   # wrap around, never stop
    player = list_player.get_media_player()

    # Log episode changes. Callbacks run on VLC's thread: keep them trivial and
    # never call back into libvlc from here (it can deadlock).
    def on_media_changed(event):
        media = player.get_media()
        if media is not None:
            log('Now playing: ' + unquote(os.path.basename(media.get_mrl())))
    player.event_manager().event_attach(vlc.EventType.MediaPlayerMediaChanged, on_media_changed)

    log('Starting playlist of %d videos' % len(videos))
    list_player.play()

    last_rescan = last_stats = time.monotonic()
    while True:
        time.sleep(5)
        now = time.monotonic()

        if now - last_rescan >= RESCAN_SECONDS:
            last_rescan = now
            new = [p for p in get_videos() if p not in known]
            if new:
                random.shuffle(new)
                media_list.lock()
                try:
                    for path in new:
                        media_list.add_media(instance.media_new_path(path))
                finally:
                    media_list.unlock()
                known.update(new)
                log('Added %d new video(s) to the playlist' % len(new))

        if now - last_stats >= STATS_SECONDS:
            last_stats = now
            media = player.get_media()
            if media is not None:
                st = vlc.MediaStats()
                if vlc.libvlc_media_get_stats(media, st):
                    log('Stats: displayed %d, lost %d, audio lost %d, decoded %d' % (
                        st.displayed_pictures, st.lost_pictures, st.lost_abuffers, st.decoded_video))

        # If VLC somehow stops (bad file, decoder hiccup), kick it again. Loop
        # mode means this should only happen when the playlist is exhausted by
        # errors.
        if not list_player.is_playing() and player.get_state() in (vlc.State.Ended, vlc.State.Error, vlc.State.Stopped):
            log('Player stopped (%s), restarting playlist' % player.get_state())
            list_player.play()


if __name__ == '__main__':
    main()
