#!/usr/bin/env python3
"""Encode episodes for the Simpsons TV (run on your Mac, not the Pi).

Usage:  python3 encode.py /path/to/folder/of/episodes
Output: an 'encoded' subfolder of .mp4 files ready to copy to the Pi.

What it does, and why:
  * Scales to 640x480 (the 4:3 panel) with letterboxing if needed.
  * Rotates 90 degrees clockwise so the file is 480x640, the panel's native
    portrait orientation. The Pi Zero W cannot rotate video at playback time
    without dropping frames, so we bake the rotation into the file.
  * H.264 Baseline, yuv420p, 24 fps: what the Pi Zero's hardware decoder likes.
  * AAC mono audio; the TV speaker is mono anyway.

Requires ffmpeg (brew install ffmpeg).
"""
import os
import subprocess
import sys

EXTS = ('.mp4', '.mkv', '.mov', '.avi', '.m4v')

# transpose=1 is 90 degrees clockwise. If video appears upside down on the TV,
# change it to transpose=2 (90 degrees counter-clockwise).
VF = 'scale=640:480:force_original_aspect_ratio=decrease,pad=640:480:(ow-iw)/2:(oh-ih)/2,fps=24,transpose=1'


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.realpath(__file__))
    dst = os.path.join(src, 'encoded')
    os.makedirs(dst, exist_ok=True)

    files = sorted(
        os.path.join(dp, f)
        for dp, _, fns in os.walk(src)
        for f in fns
        if f.lower().endswith(EXTS) and not dp.startswith(dst)
    )
    for path in files:
        out = os.path.join(dst, os.path.splitext(os.path.basename(path))[0] + '.mp4')
        if os.path.isfile(out):
            continue
        print('Encoding', os.path.basename(out), flush=True)
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-stats', '-i', path,
            '-vf', VF,
            '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
            '-preset', 'fast', '-crf', '23', '-pix_fmt', 'yuv420p',
            '-c:a', 'aac', '-ac', '1', '-b:a', '96k',
            '-movflags', '+faststart',
            out,
        ], check=False)


if __name__ == '__main__':
    main()
