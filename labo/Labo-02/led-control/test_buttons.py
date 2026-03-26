#!/usr/bin/env python3
from gpiozero import Button
import time

btn_left = Button(16, pull_up=False, bounce_time=0.1)
btn_right = Button(19, pull_up=False, bounce_time=0.1)

print("GPIO 16 (Pin 36) et GPIO 19 (Pin 39)")
print("pull_up=False - externe seulement")
print("Appuie sur les boutons (Ctrl+C pour quitter)")
print()

try:
    while True:
        l = btn_left.is_pressed
        r = btn_right.is_pressed
        l_txt = "APPUYÉ" if l else "relâché"
        r_txt = "APPUYÉ" if r else "relâché"
        print(f"\rPin 36: {int(l)} ({l_txt:10s})  Pin 39: {int(r)} ({r_txt:10s})", end="", flush=True)
        time.sleep(0.05)
except KeyboardInterrupt:
    print("\nTerminé")
