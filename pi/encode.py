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

        python3 encode.py --out /Volumes/8TB/SimpsonsTV --fuzzy -j 3 /Volumes/8TB/NG/complete
Output: the whole library, laid out exactly as the USB drive wants it:
        SimpsonsTV/videos/*.mp4, SimpsonsTV/videos/fuzzy/*.mp4, the static/
        clips and a draft channels.json. Every season folder under the source
        is walked; episodes already sitting in a season's old encoded/ folder
        are copied in rather than re-encoded. -j runs that many episodes at
        once (three is the sweet spot on an M4 Pro, with the hardware HEVC
        decoder doing the source decode). Progress, per-episode times and an
        ETA go to the terminal and to SimpsonsTV/encode.log. The Mac is kept
        awake (caffeinate) while it runs. Ctrl-C stops it cleanly (partial
        files removed) and running the same command again resumes; Ctrl-Z
        and fg pause and continue it. When it is done, copy the contents of
        SimpsonsTV/ to the root of the SIMPSONSTV drive.

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
import concurrent.futures
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time

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

# Draft channel line-up written next to a --out library (the drive's root channels.json).
# "match" strings are substrings of the file name, so season codes must be two digits.
CHANNELS = {"channels": [
    {"name": "1"},
    {"name": "2", "match": ['S%02d' % n for n in range(1, 10)]},
    {"name": "3", "match": ['S%02d' % n for n in range(10, 20)]},
    {"name": "4", "match": ['S%02d' % n for n in range(20, 40)]},
]}

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


class Runner:
    """Bookkeeping shared by the worker threads: which ffmpegs are running, and a stop flag."""

    def __init__(self):
        self.lock = threading.Lock()
        self.procs = set()
        self.stop = False

    def interrupt(self):
        """Ctrl-C: let no new job start, ask every running ffmpeg to quit."""
        self.stop = True
        with self.lock:
            for p in self.procs:
                try:
                    p.send_signal(signal.SIGINT)
                except OSError:
                    pass


def encode(src, clean, fuzzy, scanlines, sample=None, runner=None, hwaccel=False, stats=True):
    """One ffmpeg run writing whichever of `clean` and `fuzzy` is not None."""
    cmd = ['ffmpeg', '-hide_banner', '-loglevel', 'error', '-stats' if stats else '-nostats', '-y']
    if hwaccel:
        cmd += ['-hwaccel', 'videotoolbox']
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
    runner = runner or Runner()
    proc = subprocess.Popen(cmd)
    with runner.lock:
        runner.procs.add(proc)
    try:
        code = proc.wait()
    except KeyboardInterrupt:
        # Single-job mode: the terminal also sent SIGINT to ffmpeg; give it a moment.
        runner.interrupt()
        code = proc.wait()
    finally:
        with runner.lock:
            runner.procs.discard(proc)
    if code != 0:
        for part, _ in parts:
            if os.path.isfile(part):
                os.remove(part)
        if runner.stop:
            raise KeyboardInterrupt
        print('ffmpeg failed on', os.path.basename(src), file=sys.stderr, flush=True)
        return False
    for part, out in parts:
        os.replace(part, out)
    return True


def find_sources(src, out_root):
    """Every episode under `src`, in name order, skipping encoded/ folders and the output."""
    found = []
    for dp, dirs, fns in os.walk(src):
        dirs[:] = sorted(d for d in dirs
                         if d != 'encoded' and os.path.join(dp, d) != out_root)
        found += [os.path.join(dp, f) for f in fns if f.lower().endswith(EXTS)]
    return sorted(found, key=lambda p: os.path.basename(p).lower())


def adopt(path, name, target):
    """Copy an encode of `path` from a season folder's old encoded/ tree into `target` if
    one exists (encoded/<name> for clean, encoded/fuzzy/<name> for fuzzy). True if adopted."""
    sub = os.path.basename(os.path.dirname(target)) == 'fuzzy'
    for base in (os.path.dirname(path), os.path.dirname(os.path.dirname(path))):
        old = os.path.join(base, 'encoded', 'fuzzy', name) if sub else os.path.join(base, 'encoded', name)
        if os.path.isfile(old) and os.path.realpath(old) != os.path.realpath(target):
            shutil.copy2(old, target + '.part')
            os.replace(target + '.part', target)
            return True
    return False


def hms(seconds):
    seconds = int(seconds)
    return '%d:%02d:%02d' % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def main():
    here = os.path.dirname(os.path.realpath(__file__))
    args = sys.argv[1:]
    if args and args[0] == '--static':
        make_static(int(args[1]) if len(args) > 1 else 9, os.path.join(here, 'static'))
        return
    fuzzy = False
    sample = None
    out = None
    jobs = 1
    while args and args[0].startswith('-'):
        flag = args.pop(0)
        if flag == '--fuzzy':
            fuzzy = True
        elif flag == '--sample':
            sample = int(args.pop(0))
        elif flag == '--out':
            out = os.path.abspath(args.pop(0))
        elif flag == '-j':
            jobs = max(1, int(args.pop(0)))
        else:
            sys.exit('unknown option ' + flag)
    src = os.path.abspath(args[0] if args else here)

    if out:
        dst = os.path.join(out, 'sample' if sample else 'videos')
        log_path = os.path.join(out, 'encode.log')
    else:
        dst = os.path.join(src, 'encoded', 'sample') if sample else os.path.join(src, 'encoded')
        log_path = os.path.join(dst, 'encode.log')
    fuzzy_dir = os.path.join(dst, 'fuzzy')
    os.makedirs(fuzzy_dir if fuzzy else dst, exist_ok=True)
    scanlines = os.path.join(fuzzy_dir, SCANLINES_PNG)
    if fuzzy:
        make_scanlines(scanlines)
    if out and not sample:
        # The rest of what the drive's root wants: static clips and a channel line-up.
        static_src = os.path.join(here, 'static')
        if os.path.isdir(static_src):
            os.makedirs(os.path.join(out, 'static'), exist_ok=True)
            for n in os.listdir(static_src):
                if n.endswith('.mp4') and not os.path.isfile(os.path.join(out, 'static', n)):
                    shutil.copy2(os.path.join(static_src, n), os.path.join(out, 'static', n))
        if not os.path.isfile(os.path.join(out, 'channels.json')):
            with open(os.path.join(out, 'channels.json'), 'w') as f:
                json.dump(CHANNELS, f, indent=2)

    logf = open(log_path, 'a')

    def log(msg):
        line = '%s %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
        print(line, flush=True)
        logf.write(line + '\n')
        logf.flush()

    # Plan: (source, clean target or None, fuzzy target or None) for everything not done.
    todo = []
    adopted = 0
    for path in find_sources(src, out):
        name = os.path.splitext(os.path.basename(path))[0] + '.mp4'
        want = []
        for target in [os.path.join(dst, name)] + ([os.path.join(fuzzy_dir, name)] if fuzzy else []):
            if os.path.isfile(target):
                want.append(None)
            elif out and not sample and adopt(path, name, target):
                adopted += 1
                want.append(None)
            else:
                want.append(target)
        want += [None] * (2 - len(want))
        if want[0] or want[1]:
            todo.append((path, want[0], want[1]))
    total = len(todo)
    if adopted:
        log('Adopted %d finished encode(s) from season encoded/ folders' % adopted)
    if not todo:
        log('Nothing to do: every output exists')
        return
    log('%d episode(s) to encode%s, %d at a time, into %s' % (
        total, ' (clean + fuzzy)' if fuzzy else '', jobs, dst))

    if sys.platform == 'darwin' and shutil.which('caffeinate'):
        subprocess.Popen(['caffeinate', '-i', '-w', str(os.getpid())])

    runner = Runner()
    done = 0
    failed = []
    started = time.monotonic()
    lock = threading.Lock()

    def work(item):
        path, clean, fz = item
        if runner.stop:
            return None
        t0 = time.monotonic()
        ok = encode(path, clean, fz, scanlines, sample, runner,
                    hwaccel=(jobs > 1 and sys.platform == 'darwin'), stats=(jobs == 1))
        return (path, ok, time.monotonic() - t0)

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=jobs)
    futures = [pool.submit(work, item) for item in todo]
    try:
        for fut in concurrent.futures.as_completed(futures):
            result = fut.result()
            if result is None:
                continue
            path, ok, took = result
            name = os.path.basename(path)
            with lock:
                done += 1
                if not ok:
                    failed.append(name)
                elapsed = time.monotonic() - started
                rate = elapsed / done          # wall time per finished episode, all jobs together
                log('[%d/%d] %s %s in %d s, elapsed %s, ETA %s (%.0f s/episode)' % (
                    done, total, name, 'done' if ok else 'FAILED', took, hms(elapsed),
                    hms(rate * (total - done)), rate))
    except KeyboardInterrupt:
        runner.interrupt()
        pool.shutdown(wait=True, cancel_futures=True)
        log('Stopped after %d of %d; run the same command again to resume' % (done, total))
        sys.exit(130)
    pool.shutdown(wait=True)
    if failed:
        log('FAILED (%d): %s' % (len(failed), ', '.join(failed)))
    log('Finished %d of %d in %s' % (done - len(failed), total, hms(time.monotonic() - started)))
    if out and not sample:
        log('Copy the contents of %s to the root of the SIMPSONSTV drive' % out)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nStopped; run again to resume.', file=sys.stderr)
        sys.exit(130)
