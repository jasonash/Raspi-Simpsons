#!/usr/bin/env python3
"""Simpsons TV power knob.

GPIO 26: power switch input (pulled up; switch closes to ground).
GPIO 18: LCD backlight enable (high = on).
GPIO 19: PWM audio to the amplifier. Set to ALT5 (PWM1) for sound, or to a
         plain input to mute when the TV is "off".

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


def pinctrl(*args):
    subprocess.run(['pinctrl', 'set', *args], check=False)


def turn_on_screen():
    pinctrl(str(PIN_AUDIO), 'a5')          # PWM1 -> audio out
    GPIO.output(PIN_BACKLIGHT, GPIO.HIGH)


def turn_off_screen():
    pinctrl(str(PIN_AUDIO), 'ip')          # mute
    GPIO.output(PIN_BACKLIGHT, GPIO.LOW)


def main():
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(PIN_SWITCH, GPIO.IN, pull_up_down=GPIO.PUD_UP)
    GPIO.setup(PIN_BACKLIGHT, GPIO.OUT)

    screen_on = None
    while True:
        wanted = bool(GPIO.input(PIN_SWITCH))
        if INVERT_SWITCH:
            wanted = not wanted
        if wanted != screen_on:
            screen_on = wanted
            if screen_on:
                turn_on_screen()
            else:
                turn_off_screen()
        time.sleep(0.3)


if __name__ == '__main__':
    main()
