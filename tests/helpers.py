"""Shared set-up for the tests.

Imported FIRST by every test module: rimstudio_app fixes its settings and log
paths from %APPDATA% at import time, so APPDATA has to point at a throwaway
folder before that import happens - otherwise the tests would read and
overwrite the real user's last session.
"""
import os
import sys
import tempfile

import numpy as np
from PIL import Image

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SANDBOX = tempfile.mkdtemp(prefix="rimstudio-test-")
os.environ["APPDATA"] = SANDBOX
if REPO not in sys.path:
    sys.path.insert(0, REPO)

W, H = 400, 300


def scene():
    """A plate lit warm from the left and blue from the right, and a grey disc
    standing in it - small enough to run in a second, rich enough that the
    rim has two colours to find."""
    x = np.linspace(0, 1, W)[None, :, None]
    plate = np.concatenate([0.9 - 0.6 * x, 0.4 + 0 * x, 0.2 + 0.7 * x], 2) * np.ones((H, 1, 1))
    yy, xx = np.mgrid[:H, :W]
    a = ((xx - 200) ** 2 + (yy - 160) ** 2 < 80 ** 2).astype(np.float32)
    sub = np.dstack([np.full((H, W, 3), 0.35), a]).astype(np.float32)
    return sub, plate.astype(np.float32)


def scene_files(folder=None):
    """The scene written as subject.png / plate.png; returns both paths."""
    folder = folder or tempfile.mkdtemp(dir=SANDBOX)
    sub, plate = scene()
    sp = os.path.join(folder, "sub.png")
    bp = os.path.join(folder, "bg.png")
    Image.fromarray((sub * 255).astype(np.uint8), "RGBA").save(sp)
    Image.fromarray((plate * 255).astype(np.uint8)).save(bp)
    return sp, bp
