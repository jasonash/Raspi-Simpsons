#!/usr/bin/env python3
"""Encode episodes for the Simpsons TV (run on your Mac, not the Pi).

Usage:  python3 encode.py /path/to/folder/of/episodes
Output: an 'encoded' subfolder of .mp4 files ready to copy to the Pi.

        python3 encode.py --static [N]
Output: static/static.mp4 (one second of TV snow with hiss) and static/ch1.mp4 ..
        chN.mp4, the same with a green channel number in the corner, next to this
        script. Copy the static/ folder to the root of the SIMPSONSTV drive;
        player.py plays the numbered clip for the channel being tuned to, or the
        plain one, between channels. Not in git: a second of noise is 0.6 MB
        however hard x264 tries. The number is drawn with drawbox from a 5x7
        block font because Homebrew's ffmpeg has no drawtext, and it looks like
        a 1980s on-screen display anyway.

What it does, and why:
  * Scales to fill 640x480 (the 4:3 panel) and crops the excess. A 16:9
    episode loses about 12 percent off each side; 4:3 episodes lose nothing.
    On a 2.8 inch screen a full picture beats black bars.
  * Rotates 90 degrees counter-clockwise so the file is 480x640, the panel's native
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

# transpose=2 is 90 degrees counter-clockwise, which matches the panel as mounted
# in the enclosure (native top edge on the viewer's right). If video appears
# upside down on the TV, change it to transpose=1 (90 degrees clockwise).
VF = 'scale=640:480:force_original_aspect_ratio=increase,crop=640:480,fps=24,transpose=2'

ENCODE = [
    '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
    '-preset', 'fast', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-ac', '1', '-b:a', '96k',
    '-movflags', '+faststart',
]

STATIC_SECONDS = 1.0
STATIC_GRAIN = '320x240'     # noise is generated at this size and doubled: chunkier snow, smaller file

FONT = {   # 5x7 block digits
    '0': ['01110', '10001', '10011', '10101', '11001', '10001', '01110'],
    '1': ['00100', '01100', '00100', '00100', '00100', '00100', '01110'],
    '2': ['01110', '10001', '00001', '00010', '00100', '01000', '11111'],
    '3': ['11111', '00010', '00100', '00010', '00001', '10001', '01110'],
    '4': ['00010', '00110', '01010', '10010', '11111', '00010', '00010'],
    '5': ['11111', '10000', '11110', '00001', '00001', '10001', '01110'],
    '6': ['00110', '01000', '10000', '11110', '10001', '10001', '01110'],
    '7': ['11111', '00001', '00010', '00100', '01000', '01000', '01000'],
    '8': ['01110', '10001', '10001', '01110', '10001', '10001', '01110'],
    '9': ['01110', '10001', '10001', '01111', '00001', '00010', '01100'],
}


def number_boxes(label, block=16, gap=2, margin=36, width=640):
    """drawbox filters spelling `label` top right of a landscape frame, one box per lit pixel."""
    boxes = []
    x0 = width - margin - len(label) * 6 * block
    for ci, ch in enumerate(label):
        for r, row in enumerate(FONT[ch]):
            for c, bit in enumerate(row):
                if bit == '1':
                    boxes.append('drawbox=x=%d:y=%d:w=%d:h=%d:c=0x40FF40:t=fill' % (
                        x0 + (ci * 6 + c) * block, margin + r * block, block - gap, block - gap))
    return boxes


def make_static(count, dst):
    """TV snow: random luma per pixel, white noise audio, same format as an episode."""
    os.makedirs(dst, exist_ok=True)
    jobs = [('static.mp4', None)] + [('ch%d.mp4' % n, str(n)) for n in range(1, count + 1)]
    for name, label in jobs:
        out = os.path.join(dst, name)
        print('Generating', name, flush=True)
        vf = ["geq=lum='random(1)*255':cb=128:cr=128", 'scale=640:480:flags=neighbor']
        if label:
            # Drawn on the landscape frame, before the rotation, so it ends up the right
            # way round on the panel like the episodes do.
            vf += number_boxes(label)
        vf.append('transpose=2')
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'nullsrc=s=%s:r=24:d=%s' % (STATIC_GRAIN, STATIC_SECONDS),
            '-f', 'lavfi', '-i', 'anoisesrc=d=%s:c=white:r=48000:a=0.25' % STATIC_SECONDS,
            '-vf', ','.join(vf), '-shortest', *ENCODE, '-crf', '34',
            out,
        ], check=True)


def main():
    here = os.path.dirname(os.path.realpath(__file__))
    if len(sys.argv) > 1 and sys.argv[1] == '--static':
        make_static(int(sys.argv[2]) if len(sys.argv) > 2 else 9, os.path.join(here, 'static'))
        return
    src = sys.argv[1] if len(sys.argv) > 1 else here
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
            '-vf', VF, *ENCODE, '-crf', '23',
            out,
        ], check=False)


if __name__ == '__main__':
    main()
