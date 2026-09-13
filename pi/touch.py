#!/usr/bin/env python3
"""Simpsons TV touch input: turns the panel's touch controller into two gestures.

  tap        finger down and up again quickly, without moving: "change channel"
             (or, while the menu is open, "press what is under the finger")
  long press finger held still for a while: "open the menu" (fires while still held)

Anything else (a swipe, a second finger, a very slow tap) is ignored. Gestures
are decided from the timing of BTN_TOUCH alone, so channel changes work however
the touch axes are oriented; the position is carried along for the menu.

The controller is a Goodix GT911 on the Waveshare 2.8in DPI LCD, brought up by
overlays/simpsonstv-touch.dts (bit-banged I2C on GPIO 10/11, interrupt on 27)
and read here through the kernel's evdev interface (python3-evdev). The reader
runs in a background thread and hands finished gestures to a queue; the player
never blocks on touch and keeps working if the panel has no touch at all.

Coordinates. The overlay carries Waveshare's axis flags, so the controller
reports X 0-639 and Y 0-479: already the landscape frame the viewer sees, with
the origin top left, IF the controller's own origin sits at the panel's native
top-left corner. SWAP_XY / FLIP_X / FLIP_Y below correct it if it does not;
check with `python3 menu.py` (draws the menu and a dot where each tap lands).

Run this file directly to print gestures for tuning the thresholds:
    python3 touch.py          # gestures with screen coordinates
    python3 touch.py --raw    # the controller's own coordinates
"""
import glob
import os
import queue
import select
import sys
import threading
import time

try:
    import evdev
except ImportError:      # keep the player usable on a box without python3-evdev
    evdev = None

DEVICE_NAME = 'Goodix Capacitive TouchScreen'

TAP_MAX_S = 0.40         # finger up within this long of going down = tap
LONG_PRESS_S = 0.80      # finger still down after this long = long press
MOVE_PX = 30             # more movement than this (in raw touch units) cancels either
RETRY_S = 5.0            # how often to look for the device if it is missing

# Raw controller axes -> screen (640x480 landscape as mounted, origin top left).
# Set after checking with `python3 menu.py`; see the module docstring.
SWAP_XY = False
FLIP_X = False
FLIP_Y = False
SCREEN_W, SCREEN_H = 640, 480

TAP = 'tap'
LONG_PRESS = 'long_press'


def to_screen(x, y, max_x, max_y):
    """Map a raw controller position to screen pixels."""
    if SWAP_XY:
        x, y, max_x, max_y = y, x, max_y, max_x
    if FLIP_X:
        x = max_x - x
    if FLIP_Y:
        y = max_y - y
    sx = round(x * (SCREEN_W - 1) / max_x) if max_x else x
    sy = round(y * (SCREEN_H - 1) / max_y) if max_y else y
    return (min(max(sx, 0), SCREEN_W - 1), min(max(sy, 0), SCREEN_H - 1))


def find_device():
    if evdev is None:
        return None
    for path in sorted(glob.glob('/dev/input/event*')):
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if dev.name == DEVICE_NAME:
            return dev
        dev.close()
    return None


class TouchInput:
    """Background reader. Gestures arrive on .events as (TAP or LONG_PRESS, x, y)
    tuples, x and y in screen pixels (raw controller units with raw=True)."""

    def __init__(self, log=print, events=None, raw=False):
        self.events = events if events is not None else queue.Queue()
        self.log = log
        self.raw = raw
        self._thread = threading.Thread(target=self._run, name='touch', daemon=True)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        warned = False
        while True:
            dev = find_device()
            if dev is None:
                if not warned:
                    self.log('Touch: no "%s" input device%s, will keep looking' % (
                        DEVICE_NAME, '' if evdev else ' (python3-evdev not installed)'))
                    warned = True
                time.sleep(RETRY_S)
                continue
            warned = False
            self.log('Touch: reading %s (%s)' % (dev.path, dev.name))
            try:
                self._read(dev)
            except OSError as e:          # device unplugged / driver reloaded
                self.log('Touch: device went away (%s), will keep looking' % e)
            finally:
                try:
                    dev.close()
                except Exception:
                    pass
            time.sleep(RETRY_S)

    def _emit(self, kind, pos, max_x, max_y):
        if self.raw:
            self.events.put((kind, pos[0], pos[1]))
        else:
            sx, sy = to_screen(pos[0], pos[1], max_x, max_y)
            self.events.put((kind, sx, sy))

    def _read(self, dev):
        try:
            max_x = dev.absinfo(evdev.ecodes.ABS_X).max
            max_y = dev.absinfo(evdev.ecodes.ABS_Y).max
        except (KeyError, OSError):
            max_x, max_y = SCREEN_W - 1, SCREEN_H - 1
        touching = False
        down_at = 0.0
        down_pos = [0, 0]
        pos = [0, 0]
        just_down = False        # down seen, position of this frame not yet complete
        released = False
        moved = False
        fired_long = False
        # Recognise a long press after the timeout even if the controller sends no
        # further events while the finger is held still, hence select() with a timeout.
        while True:
            timeout = None
            if touching and not moved and not fired_long:
                timeout = max(0.0, down_at + LONG_PRESS_S - time.monotonic())
            ready, _, _ = select.select([dev.fd], [], [], timeout)
            now = time.monotonic()
            if not ready:
                if touching and not moved and not fired_long:
                    fired_long = True
                    self._emit(LONG_PRESS, down_pos, max_x, max_y)
                continue
            try:
                events = list(dev.read())
            except BlockingIOError:
                continue
            # Events come in frames closed by SYN_REPORT. Within a frame the kernel
            # sends BTN_TOUCH before ABS_X/ABS_Y, so decisions that need the position
            # are taken at the end of the frame, not on the BTN_TOUCH event itself.
            for ev in events:
                if ev.type == evdev.ecodes.EV_ABS:
                    if ev.code == evdev.ecodes.ABS_X:
                        pos[0] = ev.value
                    elif ev.code == evdev.ecodes.ABS_Y:
                        pos[1] = ev.value
                elif ev.type == evdev.ecodes.EV_KEY and ev.code == evdev.ecodes.BTN_TOUCH:
                    if ev.value == 1 and not touching:
                        touching = True
                        just_down = True
                        down_at = now
                        moved = False
                        fired_long = False
                    elif ev.value == 0 and touching:
                        released = True
                elif ev.type == evdev.ecodes.EV_SYN and ev.code == evdev.ecodes.SYN_REPORT:
                    if just_down:
                        down_pos = list(pos)
                        just_down = False
                    elif touching and not moved and (abs(pos[0] - down_pos[0]) > MOVE_PX or
                                                     abs(pos[1] - down_pos[1]) > MOVE_PX):
                        moved = True
                    if released:
                        released = False
                        touching = False
                        if not moved and not fired_long and now - down_at <= TAP_MAX_S:
                            self._emit(TAP, down_pos, max_x, max_y)


if __name__ == '__main__':
    raw = '--raw' in sys.argv[1:]
    t = TouchInput(raw=raw).start()
    print('Tap or hold the screen (Ctrl-C to stop); positions are %s' % (
        'raw controller units' if raw else 'screen pixels'))
    while True:
        kind, x, y = t.events.get()
        print(time.strftime('%H:%M:%S'), kind, x, y)
