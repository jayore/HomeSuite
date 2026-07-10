#!/usr/bin/env python3
"""Compatibility launcher for the legacy gpio_ptt.service on piphone1."""
from pathlib import Path
import os
import runpy
import sys

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)
sys.path.insert(0, str(ROOT))
runpy.run_path(str(ROOT / "main.py"), run_name="__main__")
