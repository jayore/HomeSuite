#!/usr/bin/env python3
import subprocess
import time

print("Testing microphone and speaker...")
print("Recording 5 seconds of audio...")
subprocess.run(["arecord", "-f", "S16_LE", "-c", "1", "-r", "44100", "-d", "5", "test.wav"])

print("Playing back the recorded audio...")
subprocess.run(["aplay", "test.wav"])

print("Done.")
