#!/usr/bin/env python3
"""Encode episodes for the Simpsons TV (run on your Mac, not the Pi).

Usage:  python3 encode.py /path/to/folder/of/episodes
Output: an 'encoded' subfolder of .mp4 files ready to copy to the Pi.

        python3 encode.py --fuzzy /path/to/folder/of/episodes
Output: the same, plus encoded/fuzzy/<same name>.mp4 for every episode: the
        "vintage" look (soft picture, colour fringing, grain, vignette,
        scanlines) that the TV's menu switches to under LOOK. Both files come
        out of ONE pass over the source (decoded once, encoded twice), which is
        what makes a 790-episode batch bearable. Copy the whole encoded/ folder
        into videos/ on the drive; the player finds videos/fuzzy/<name>.mp4 by
        itself. Files are written as .part and renamed when complete, and
        finished outputs are skipped on the next run, so a batch can be
        stopped and resumed over as many days as it takes. Running --fuzzy
        over a folder that already has clean encodes only adds the fuzzy set.

        python3 encode.py --sample 20 [--fuzzy] /path/to/folder
Output: encoded/sample/: 20 s of each episode starting a minute in, for
        checking the look before committing to a long batch.

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
FIT = 'scale=640:480:force_original_aspect_ratio=increase,crop=640:480,fps=24'
ROTATE = 'transpose=2'
VF = FIT + ',' + ROTATE

ENCODE = [
    '-c:v', 'libx264', '-profile:v', 'baseline', '-level', '3.0',
    '-preset', 'fast', '-pix_fmt', 'yuv420p',
    '-c:a', 'aac', '-ac', '1', '-b:a', '96k',
    '-movflags', '+faststart',
]
CRF_CLEAN = '23'
CRF_FUZZY = '24'         # grain costs bits; a notch softer keeps the files near the clean size

# The vintage look, applied to the landscape 640x480 frame before the rotation, in the
# order a real signal picked it up: a soft picture (poor bandwidth), colour fringing
# (chroma out of step with luma), grain (weak reception), then the tube itself: a
# vignette and scanlines (a translucent stripe every SCANLINE_PERIOD rows).
FUZZY = ','.join([
    'gblur=sigma=0.9',
    'chromashift=cbh=2:crh=-2',
    'eq=saturation=0.9:contrast=0.94',
    'noise=c0s=20:c0f=t+u',
    'vignette=angle=PI/6',
])
SCANLINE_PERIOD = 3
SCANLINE_ALPHA = 90      # 0-255, how dark the stripe is
SCANLINES_PNG = '.scanlines.png'

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
        vf.append(ROTATE)
        subprocess.run([
            'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
            '-f', 'lavfi', '-i', 'nullsrc=s=%s:r=24:d=%s' % (STATIC_GRAIN, STATIC_SECONDS),
            '-f', 'lavfi', '-i', 'anoisesrc=d=%s:c=white:r=48000:a=0.25' % STATIC_SECONDS,
            '-vf', ','.join(vf), '-shortest', *ENCODE, '-crf', '34',
            out,
        ], check=True)


def make_scanlines(path):
    """A transparent 640x480 PNG with a dark stripe every SCANLINE_PERIOD rows."""
    if os.path.isfile(path):
        return
    subprocess.run([
        'ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-f', 'lavfi', '-i', 'color=c=black:s=640x480:r=1,format=rgba',
        '-vf', "geq=r=0:g=0:b=0:a='if(eq(mod(Y,%d),%d),%d,0)'" % (
            SCANLINE_PERIOD, SCANLINE_PERIOD - 1, SCANLINE_ALPHA),
        '-frames:v', '1', path,
    ], check=True)


def encode(src, clean, fuzzy, scanlines, sample=None):
    """One ffmpeg run writing whichever of `clean` and `fuzzy` is not None."""
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-stats', '-y']
    if sample:
        cmd += ['-ss', '60', '-t', str(sample)]
    cmd += ['-i', src]
    outputs = []
    if clean and fuzzy:
        cmd += ['-loop', '1', '-i', scanlines]
        graph = ('[0:v]%s,split=2[c][f];[c]%s[clean];'
                 '[f]%s[f1];[f1][1:v]overlay=shortest=1,format=yuv420p,%s[fuzzy]'
                 % (FIT, ROTATE, FUZZY, ROTATE))
        outputs = [('[clean]', CRF_CLEAN, clean), ('[fuzzy]', CRF_FUZZY, fuzzy)]
    elif fuzzy:
        cmd += ['-loop', '1', '-i', scanlines]
        graph = ('[0:v]%s,%s[f1];[f1][1:v]overlay=shortest=1,format=yuv420p,%s[fuzzy]'
                 % (FIT, FUZZY, ROTATE))
        outputs = [('[fuzzy]', CRF_FUZZY, fuzzy)]
    else:
        graph = '[0:v]%s[clean]' % VF
        outputs = [('[clean]', CRF_CLEAN, clean)]
    cmd += ['-filter_complex', graph]
    parts = []
    for label, crf, out in outputs:
        part = out + '.part'
        parts.append((part, out))
        cmd += ['-map', label, '-map', '0:a:0?', *ENCODE, '-crf', crf, '-f', 'mp4', part]
    try:
        result = subprocess.run(cmd)
    except KeyboardInterrupt:
        result = None
    if result is None or result.returncode != 0:
        for part, _ in parts:
            if os.path.isfile(part):
                os.remove(part)
        if result is None:
            raise KeyboardInterrupt
        print('ffmpeg failed on', os.path.basename(src), file=sys.stderr, flush=True)
        return False
    for part, out in parts:
        os.replace(part, out)
    return True


def main():
    here = os.path.dirname(os.path.realpath(__file__))
    args = sys.argv[1:]
    if args and args[0] == '--static':
        make_static(int(args[1]) if len(args) > 1 else 9, os.path.join(here, 'static'))
        return
    fuzzy = False
    sample = None
    while args and args[0].startswith('--'):
        flag = args.pop(0)
        if flag == '--fuzzy':
            fuzzy = True
        elif flag == '--sample':
            sample = int(args.pop(0))
        else:
            sys.exit('unknown option ' + flag)
    src = args[0] if args else here
    dst = os.path.join(src, 'encoded')
    if sample:
        dst = os.path.join(dst, 'sample')
    fuzzy_dir = os.path.join(dst, 'fuzzy')
    os.makedirs(fuzzy_dir if fuzzy else dst, exist_ok=True)
    scanlines = os.path.join(fuzzy_dir, SCANLINES_PNG)
    if fuzzy:
        make_scanlines(scanlines)

    files = sorted(
        os.path.join(dp, f)
        for dp, _, fns in os.walk(src)
        for f in fns
        if f.lower().endswith(EXTS) and not dp.startswith(os.path.join(src, 'encoded'))
    )
    for path in files:
        name = os.path.splitext(os.path.basename(path))[0] + '.mp4'
        clean = os.path.join(dst, name)
        want_clean = None if os.path.isfile(clean) else clean
        want_fuzzy = None
        if fuzzy:
            f = os.path.join(fuzzy_dir, name)
            want_fuzzy = None if os.path.isfile(f) else f
        if not want_clean and not want_fuzzy:
            continue
        print('Encoding %s (%s)' % (name, ' + '.join(
            w for w, x in (('clean', want_clean), ('fuzzy', want_fuzzy)) if x)), flush=True)
        encode(path, want_clean, want_fuzzy, scanlines, sample)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped; run again to resume.', file=sys.stderr)
        sys.exit(130)
