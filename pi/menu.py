#!/usr/bin/env python3
"""Simpsons TV on-screen menu, drawn straight into the Linux framebuffer.

A long press on the panel opens it (player.py). The player stops VLC, which
releases the display, and the kernel hands the panel back to the console
framebuffer (/dev/fb0, 480x640 RGB565, always black because tty1 has no login
prompt). The menu is drawn there with Pillow. Taps press whatever is under the
finger; a long press, DONE or 20 s without a touch closes it and VLC takes the
panel back on the channel's current programme.

Rows, top to bottom (each one finger high on the 2.8 inch panel):
    CHANNEL   < 3 >      pick a channel, applied when the menu closes
    LOOK      CLEAN      tap to switch between the clean and vintage encodes
    VOLUME    < 70 >     10 percent steps, applied at once
    SHUT DOWN            tap twice; the Pi powers off cleanly
    DONE                 close the menu

Coordinates are the viewer's: 640x480 landscape, origin top left. The image is
rotated to the panel's native portrait orientation on the way to the
framebuffer, the same 90 degrees counter-clockwise encode.py bakes into the
episodes, so anything that looks right as a video looks right here too.

Run this file directly (with the tvplayer service stopped) to try the menu
without the player: taps work, and every tap prints the controller's raw
coordinates next to the screen pixel they mapped to, with a dot drawn there.
That is how the SWAP_XY / FLIP_X / FLIP_Y flags in touch.py were checked.
"""
import os
import sys
import time

from PIL import Image, ImageChops, ImageDraw, ImageFont

FB_DEVICE = '/dev/fb0'
W, H = 640, 480                  # the viewer's landscape frame
ROWS = 5
ROW_H = H // ROWS

BLACK = (0, 0, 0)
GREEN = (64, 255, 64)            # same green as the channel number in the static clips
DIM = (0, 96, 0)
RED = (255, 72, 48)
FONT_PATH = '/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf'

CHANNEL, LOOK, VOLUME, SHUTDOWN, DONE = 'channel', 'look', 'volume', 'shutdown', 'done'
LOOKS = ['clean', 'vintage']
VOLUME_STEP = 10

# Where the < and > buttons sit inside a row with a value: x ranges in pixels.
MINUS_X = (300, 430)
PLUS_X = (510, 640)


def font(size):
    try:
        return ImageFont.truetype(FONT_PATH, size)
    except OSError:
        return ImageFont.load_default(size)


class Framebuffer:
    """The console framebuffer as a place to put a landscape image."""

    def __init__(self, device=FB_DEVICE):
        self.device = device
        sysfs = '/sys/class/graphics/' + os.path.basename(device)
        with open(sysfs + '/virtual_size') as f:
            self.width, self.height = (int(v) for v in f.read().split(','))
        with open(sysfs + '/bits_per_pixel') as f:
            self.bpp = int(f.read())
        with open(sysfs + '/stride') as f:
            self.stride = int(f.read())
        if self.bpp != 16:
            raise RuntimeError('%s is %d bpp, only RGB565 is supported' % (device, self.bpp))
        # Fail now, not at the first long press, if the user cannot write to it.
        with open(device, 'r+b'):
            pass

    def show(self, img):
        """Put a 640x480 landscape image on the panel."""
        if (self.width, self.height) == (H, W):
            img = img.rotate(90, expand=True)         # counter-clockwise, like encode.py
        elif (self.width, self.height) != (W, H):
            img = img.resize((self.width, self.height))
        data = rgb565(img)
        row = self.width * 2
        if self.stride != row:
            pad = b'\0' * (self.stride - row)
            data = b''.join(data[i:i + row] + pad for i in range(0, len(data), row))
        with open(self.device, 'r+b') as f:
            f.write(data)


def rgb565(img):
    """Little-endian RGB565 bytes of an RGB image."""
    img = img.convert('RGB')
    try:
        return img.tobytes('raw', 'BGR;16')
    except ValueError:
        # Same thing built from the channels: high byte = RRRRRGGG, low byte = GGGBBBBB.
        r, g, b = img.split()
        hi = ImageChops.add(r.point(lambda v: v & 0xF8), g.point(lambda v: v >> 5))
        lo = ImageChops.add(g.point(lambda v: (v & 0x1C) << 3), b.point(lambda v: v >> 3))
        return Image.merge('LA', (lo, hi)).tobytes()


class Menu:
    """The menu's state, its picture, and what a tap at (x, y) does to it."""

    def __init__(self, channel_names, channel, look, volume):
        self.channel_names = list(channel_names)
        self.channel = channel               # index into channel_names
        self.look = look if look in LOOKS else LOOKS[0]
        self.volume = max(0, min(100, int(volume)))
        self.confirm_shutdown = False
        self.label_font = font(34)
        self.value_font = font(40)
        self.arrow_font = font(48)
        self.marks = []                      # (x, y) dots for the standalone test

    def tap(self, x, y):
        """Apply a tap; return which item changed (CHANNEL, LOOK, VOLUME, SHUTDOWN, DONE) or None."""
        row = y // ROW_H
        if row != 3:
            self.confirm_shutdown = False
        if row == 0:
            if MINUS_X[0] <= x < MINUS_X[1]:
                self.channel = (self.channel - 1) % len(self.channel_names)
            elif x >= MINUS_X[1]:
                self.channel = (self.channel + 1) % len(self.channel_names)
            else:
                return None
            return CHANNEL
        if row == 1:
            self.look = LOOKS[(LOOKS.index(self.look) + 1) % len(LOOKS)]
            return LOOK
        if row == 2:
            if MINUS_X[0] <= x < MINUS_X[1]:
                self.volume = max(0, self.volume - VOLUME_STEP)
            elif x >= MINUS_X[1]:
                self.volume = min(100, self.volume + VOLUME_STEP)
            else:
                return None
            return VOLUME
        if row == 3:
            if self.confirm_shutdown:
                return SHUTDOWN
            self.confirm_shutdown = True
            return None
        if row == 4:
            return DONE
        return None

    def render(self):
        img = Image.new('RGB', (W, H), BLACK)
        d = ImageDraw.Draw(img)
        for r in range(1, ROWS):
            d.line([(16, r * ROW_H), (W - 16, r * ROW_H)], fill=DIM, width=2)
        self._value_row(d, 0, 'CHANNEL', self.channel_names[self.channel])
        self._row(d, 1, 'LOOK', self.look.upper())
        self._value_row(d, 2, 'VOLUME', str(self.volume))
        if self.confirm_shutdown:
            self._centered(d, 3, 'TAP AGAIN TO SHUT DOWN', RED, self.label_font)
        else:
            self._row(d, 3, 'SHUT DOWN', '')
        self._centered(d, 4, 'DONE', GREEN, self.value_font)
        for x, y in self.marks:
            d.ellipse([x - 8, y - 8, x + 8, y + 8], outline=RED, width=3)
        return img

    def _row(self, d, row, label, value):
        cy = row * ROW_H + ROW_H // 2
        d.text((28, cy), label, fill=GREEN, font=self.label_font, anchor='lm')
        if value:
            d.text((W - 28, cy), value, fill=GREEN, font=self.value_font, anchor='rm')

    def _value_row(self, d, row, label, value):
        cy = row * ROW_H + ROW_H // 2
        d.text((28, cy), label, fill=GREEN, font=self.label_font, anchor='lm')
        d.text((sum(MINUS_X) // 2, cy), '<', fill=GREEN, font=self.arrow_font, anchor='mm')
        d.text(((MINUS_X[1] + PLUS_X[0]) // 2, cy), value, fill=GREEN, font=self.value_font, anchor='mm')
        d.text((sum(PLUS_X) // 2, cy), '>', fill=GREEN, font=self.arrow_font, anchor='mm')

    def _centered(self, d, row, text, color, fnt):
        d.text((W // 2, row * ROW_H + ROW_H // 2), text, fill=color, font=fnt, anchor='mm')


def message(text, color=GREEN):
    """A full-screen one-liner, e.g. while shutting down."""
    img = Image.new('RGB', (W, H), BLACK)
    ImageDraw.Draw(img).text((W // 2, H // 2), text, fill=color, font=font(40), anchor='mm')
    return img


if __name__ == '__main__':
    import touch
    fb = Framebuffer()
    print('Framebuffer %dx%d, %d bpp, stride %d' % (fb.width, fb.height, fb.bpp, fb.stride))
    print('Stop the tvplayer service first or VLC keeps the panel. Ctrl-C to stop.')
    m = Menu(['1', '2', '3'], 0, 'clean', 70)
    fb.show(m.render())
    t = touch.TouchInput(raw=True).start()
    while True:
        kind, rx, ry = t.events.get()
        sx, sy = touch.to_screen(rx, ry, 639, 479)
        result = m.tap(sx, sy) if kind == touch.TAP else None
        print('%s %-10s raw (%3d,%3d) -> screen (%3d,%3d) row %d: %s' % (
            time.strftime('%H:%M:%S'), kind, rx, ry, sx, sy, sy // ROW_H, result))
        m.marks = [(sx, sy)]
        fb.show(m.render())
        if result == SHUTDOWN:
            print('(shutdown requested; ignored in the test)')
            m.confirm_shutdown = False
