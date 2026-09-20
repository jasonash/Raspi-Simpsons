#!/usr/bin/env python3
"""Simpsons TV power knob.

GPIO 26: power switch input (pulled up; switch closes to ground).
GPIO 18: LCD backlight enable (high = on).
GPIO 19: PWM audio to the amplifier (ALT5 = PWM1). Set once at start and then
         left alone: switching the pin between PWM and a plain input to mute, as
         the original design did, steps the DC level at the amplifier's input
         and the speaker pops loudly, both ways.

The Pi keeps running either way: "off" is a dark, silent panel. The silence
comes from player.py: the state is written to /run/simpsonstv/power ("on" or
"off"), and the player fades out and stops while the TV is off, then plays the
power-on clip when it comes back.

Raspberry Pi OS Bookworm/Trixie replaced raspi-gpio with pinctrl and
RPi.GPIO with the rpi-lgpio compatibility layer; this script uses both.
"""
import os
import subprocess
import time

import RPi.GPIO as GPIO

PIN_SWITCH = 26
PIN_BACKLIGHT = 18
PIN_AUDIO = 19

# If the knob works backwards (TV on when it should be off), flip this.
INVERT_SWITCH = False

POWER_PATH = '/run/simpsonstv/power'     # read by player.py
POLL_SECONDS = 0.1


def pinctrl(*args):
    subprocess.run(['pinctrl', 'set', *args], check=False)


def publish(screen_on):
    """Tell player.py. Written to a temporary name and renamed so it never reads half a word."""
    try:
        os.makedirs(os.path.dirname(POWER_PATH), exist_ok=True)
        with open(POWER_PATH + '.tmp', 'w') as f:
            f.write('on\n' if screen_on else 'off\n')
        os.chmod(POWER_PATH + '.tmp', 0o644)
        os.replace(POWER_PATH + '.tmp', POWER_PATH)
    except OSError as e:
        print('Cannot write %s: %s' % (POWER_PATH, e), flush=True)


def turn_on_screen():
    GPIO.output(PIN_BACKLIGHT, GPIO.HIGH)


def turn_off_screen():
    GPIO.output(PIN_BACKLIGHT, GPIO.LOW)


def main():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_SWITCH, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(PIN_BACKLIGHT, GPIO.OUT)
    pinctrl(str(PIN_AUDIO), 'a5')          # PWM1 -> audio out (the audremap overlay's setting)

    screen_on = None
    last = None
    while True:
        wanted = bool(GPIO.input(PIN_SWITCH))
        if INVERT_SWITCH:
            wanted = not wanted
        # Two readings in a row must agree, so contact bounce does not flash the panel.
        if wanted == last and wanted != screen_on:
            screen_on = wanted
            print('TV on' if screen_on else 'TV off', flush=True)
            if screen_on:
                publish(True)        # player first: the clip should start as the panel lights
                turn_on_screen()
            else:
                turn_off_screen()
                publish(False)
        last = wanted
        time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    main()
