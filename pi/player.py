#!/usr/bin/env python3
"""Simpsons TV video player.

Plays every .mp4 in the videos/ folder next to this script in a random
order, forever. Uses VLC (cvlc) with the Raspberry Pi DRM video output and
hardware H.264 decoding. One cvlc process plays a whole shuffled batch so
there is no startup gap between episodes; a new shuffle happens when the
batch finishes.

Videos should already be encoded for the panel (see encode.py): 480x640,
pre-rotated so no rotation is needed at playback time.
"""
import os
import random
import subprocess
import time

HERE = os.path.dirname(os.path.realpath(__file__))
VIDEO_DIR = os.path.join(HERE, 'videos')

VLC_ARGS = [
    'cvlc',
    '--quiet',
    '--no-osd',
    '--no-video-title-show',
    '--play-and-exit',
    '--no-loop',
    '--vout', 'drm_vout',     # Raspberry Pi KMS/DRM output (zero-copy from the HW decoder)
    '--aout', 'alsa',
    '--codec', 'v4l2m2m,avcodec',  # prefer the BCM2835 hardware decoder
]


def get_videos():
    try:
        names = os.listdir(VIDEO_DIR)
    except FileNotFoundError:
        return []
    return sorted(os.path.join(VIDEO_DIR, n) for n in names if n.lower().endswith('.mp4'))


def main():
    while True:
        videos = get_videos()
        if not videos:
            print('No videos in %s, waiting...' % VIDEO_DIR, flush=True)
            time.sleep(10)
            continue
        random.shuffle(videos)
        print('Playing batch of %d videos' % len(videos), flush=True)
        proc = subprocess.Popen(VLC_ARGS + videos)
        proc.wait()
        time.sleep(1)


if __name__ == '__main__':
    main()
