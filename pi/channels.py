#!/usr/bin/env python3
"""Virtual TV channels for the Simpsons TV.

A channel is a fixed, shuffled playlist of episodes plus a clock that started
running at boot. At any moment the channel "is" at some point in its playlist,
whether anyone is watching or not, so tuning back to a channel later lands
mid-episode further on, like a real broadcast. Nothing is decoded for channels
nobody is watching; the position is pure arithmetic on file durations.

Channels come from channels.json (on the USB drive's root, else next to this
file), for example:

    {"channels": [
        {"name": "3", "match": ["S01", "S02", "S03"]},
        {"name": "5", "match": ["S23"]},
        {"name": "7"}
    ]}

"match" is a list of case-insensitive substrings of the file name; a channel
with no "match" carries every episode. Without a file, DEFAULT_CHANNELS
channels each carry every episode in a different order.

Playlist order is seeded from the channel name, so it is the same after every
boot; only the clock's starting point is random per boot (the Pi has no RTC).

Durations are read straight from the MP4 header (mvhd atom), which is much
cheaper than asking VLC to parse hundreds of files over a USB thumb drive.
"""
import json
import os
import random
import struct
import time

DEFAULT_CHANNELS = 3
SETTLE_SECONDS = 60      # ignore files modified more recently than this (copy in progress)


def mp4_duration(path):
    """Seconds of playback in an MP4/MOV file, or None if it cannot be read."""
    try:
        with open(path, 'rb') as f:
            size = os.fstat(f.fileno()).st_size
            moov = _find_atom(f, 0, size, b'moov')
            if moov is None:
                return None
            mvhd = _find_atom(f, moov[0], moov[1], b'mvhd')
            if mvhd is None:
                return None
            f.seek(mvhd[0])
            version = f.read(1)[0]
            if version == 1:
                f.seek(mvhd[0] + 20)
                timescale, duration = struct.unpack('>IQ', f.read(12))
            else:
                f.seek(mvhd[0] + 12)
                timescale, duration = struct.unpack('>II', f.read(8))
            return duration / timescale if timescale else None
    except (OSError, struct.error, IndexError):
        return None


def _find_atom(f, start, end, name):
    """Walk the boxes in [start, end); return (payload_start, payload_end) of the first `name`."""
    pos = start
    while pos + 8 <= end:
        f.seek(pos)
        head = f.read(8)
        if len(head) < 8:
            return None
        size, kind = struct.unpack('>I4s', head)
        header = 8
        if size == 1:                       # 64-bit size follows
            size = struct.unpack('>Q', f.read(8))[0]
            header = 16
        elif size == 0:                     # box runs to end of file
            size = end - pos
        if size < header:
            return None
        if kind == name:
            return (pos + header, pos + size)
        pos += size
    return None


def scan_videos(dirs, settle=SETTLE_SECONDS):
    """Every settled .mp4 in the given folders, sorted."""
    cutoff = time.time() - settle
    found = []
    for d in dirs:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        for n in names:
            # Skip macOS "._foo.mp4" resource forks that Finder leaves on exFAT drives.
            if n.startswith('.') or not n.lower().endswith('.mp4'):
                continue
            path = os.path.join(d, n)
            try:
                if os.path.getmtime(path) > cutoff:
                    continue
            except OSError:
                continue
            found.append(path)
    return sorted(found)


class Channel:
    def __init__(self, name, match=None):
        self.name = str(name)
        self.match = [m.lower() for m in (match or [])]
        self.items = []          # [(path, duration_s)] in play order
        self.total = 0.0
        self.rng = random.Random(self.name)      # same order every boot
        self.epoch = None        # monotonic time at which the channel was at position 0

    def accepts(self, path):
        if not self.match:
            return True
        base = os.path.basename(path).lower()
        return any(m in base for m in self.match)

    def add(self, entries):
        """Append (path, duration) pairs in a shuffled order. Appending never moves the
        current position, so a rescan that finds new files does not disturb a viewer."""
        entries = [e for e in entries if self.accepts(e[0])]
        self.rng.shuffle(entries)
        self.items.extend(entries)
        self.total = sum(d for _, d in self.items)
        if self.epoch is None and self.total > 0:
            # Start the clock somewhere random so every boot is not the same episode.
            self.epoch = time.monotonic() - random.uniform(0, self.total)

    def position(self, now=None):
        """(index, path, offset_s) of what the channel is showing right now, or None if empty."""
        if not self.items or self.total <= 0:
            return None
        now = time.monotonic() if now is None else now
        t = (now - self.epoch) % self.total
        for i, (path, dur) in enumerate(self.items):
            if t < dur:
                return (i, path, t)
            t -= dur
        return (0, self.items[0][0], 0.0)       # rounding at the very end of the loop

    def after(self, index):
        """(index, path, 0.0) of the item following the given one, wrapping around."""
        i = (index + 1) % len(self.items)
        return (i, self.items[i][0], 0.0)

    def __len__(self):
        return len(self.items)


def load_config(paths):
    for p in paths:
        try:
            with open(p) as f:
                return json.load(f), p
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            raise ValueError('%s: %s' % (p, e))
    return None, None


def build_channels(config):
    """Empty channels from a parsed channels.json, or the defaults."""
    if config and config.get('channels'):
        return [Channel(c.get('name', i + 1), c.get('match')) for i, c in enumerate(config['channels'])]
    return [Channel(i + 1) for i in range(DEFAULT_CHANNELS)]


if __name__ == '__main__':
    import sys
    for p in sys.argv[1:]:
        print('%10.3f  %s' % (mp4_duration(p) or -1, os.path.basename(p)))
