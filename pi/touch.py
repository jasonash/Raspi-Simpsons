#!/usr/bin/env python3
"""Simpsons TV touch input: turns the panel's touch controller into two gestures.

  tap        finger down and up again quickly, without moving: "change channel"
  long press finger held still for a while: "open the menu" (fires while still held)

Anything else (a swipe, a second finger, a very slow tap) is ignored. Only the
timing of BTN_TOUCH is used, so this works regardless of how the touch axes are
oriented relative to the mounted panel; only the menu needs real coordinates.

The controller is a Goodix GT911 on the Waveshare 2.8in DPI LCD, brought up by
overlays/simpsonstv-touch.dts (bit-banged I2C on GPIO 10/11, interrupt on 27)
and read here through the kernel's evdev interface (python3-evdev). The reader
runs in a background thread and hands finished gestures to a queue; the player
never blocks on touch and keeps working if the panel has no touch at all.

Run this file directly to print gestures for tuning the thresholds:
    python3 touch.py
"""
import glob
import os
import queue
import select
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

TAP = 'tap'
LONG_PRESS = 'long_press'


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
    """Background reader. Gestures arrive on .events as TAP or LONG_PRESS strings."""

    def __init__(self, log=print, events=None):
        self.events = events if events is not None else queue.Queue()
        self.log = log
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

    def _read(self, dev):
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
                    self.events.put(LONG_PRESS)
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
                            self.events.put(TAP)

if __name__ == '__main__':
    t = TouchInput().start()
    print('Tap or hold the screen (Ctrl-C to stop)')
    while True:
        print(time.strftime('%H:%M:%S'), t.events.get())
