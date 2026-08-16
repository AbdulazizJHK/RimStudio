"""Build the RimStudio icon by RUNNING RimStudio.

The icon is a real render from rim_engine, not a drawing of one: a subject cut
out against a plate lit warm from the left and violet from the right, so the rim
picks up two different colours off the background. That two-tone rim is the one
thing this tool does that a stock rim-light filter cannot, so it is what the
icon should show.

Close the RimStudio panel before re-running this: Tk holds RimStudio.ico open
while a window is using it, and the write fails with PermissionError.
"""
import sys, os
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import numpy as np
from PIL import Image
import rim_engine as E

S = 1024
y, x = np.mgrid[0:S, 0:S].astype(np.float32)


def blob(cx, cy, r, soft):
    d = np.hypot(x - cx, y - cy)
    return np.clip(1.0 - (d - r) / soft, 0.0, 1.0) ** 2


# ---- the plate: two coloured sources behind the subject -------------------
WARM = np.array([1.00, 0.62, 0.24], np.float32)     # low sun / firelight
COOL = np.array([0.52, 0.36, 1.00], np.float32)     # the panel's violet
bg = np.zeros((S, S, 3), np.float32)
# Sources sit directly BEHIND the silhouette's two edges, which is where a
# backlight physically is. Parked out in the corners they read as two lamps and
# the rim they cast is too weak to survive a downsample - the rim is the subject
# of this icon, so the lights exist to make it, not to be looked at.
bg += WARM * blob(S * 0.275, S * 0.44, S * 0.042, S * 0.150)[..., None] * 2.8
bg += COOL * blob(S * 0.725, S * 0.46, S * 0.042, S * 0.158)[..., None] * 2.5
bg += np.array([0.022, 0.022, 0.038], np.float32)   # night, not black
bg *= (1.0 - 0.30 * np.clip((y - S * 0.62) / (S * 0.38), 0, 1))[..., None]
bg = np.clip(bg, 0, 1)

# ---- the subject: one centred form, dark, with a soft matte --------------
# A bust was tried first. Its shoulders vanish against a near-black plate at
# every size that matters, so it read as a sphere anyway - but an off-centre one
# with the bottom third of the icon empty. A centred form says the same thing
# and balances.
alpha = E.blur(blob(S * 0.5, S * 0.50, S * 0.265, 3.0), 2.0)

sub = np.zeros((S, S, 4), np.float32)
# near-black: the rim is the whole point, and its contrast is set by how dark
# the thing it wraps is
sub[..., :3] = np.array([0.030, 0.028, 0.036], np.float32)
# a little internal modelling so it is not a flat cut-out
sub[..., :3] *= (0.75 + 0.55 * blob(S * 0.44, S * 0.42, S * 0.06, S * 0.32))[..., None]
sub[..., 3] = alpha

# ---- run the actual engine ----------------------------------------------
P = dict(E.DEFAULTS)
P.update(reach=260.0, rim_reach=110.0, soft_w=22.0, core_w=7.0,
         rim=4.0, core=4.0, wrap=0.30, sat=1.9, threshold=0.05,
         m_exposure=0.0, m_contrast=0.0, m_colour=0.0,
         glow=1.0, bg_glow=0.22, glow_thr=0.72, glow_colour=1.9,
         shadow=0.0, grain=0.0, focus=0.0)

pre = E.prepare(sub, bg)
lit = E.relight(sub, bg, P, pre=pre)
a = lit[..., 3:4]
flat = np.clip(bg * (1 - a) + lit[..., :3] * a, 0, 1)
flat = E.bloom(flat, P, alpha=lit[..., 3])

# ---- rounded-square mask ------------------------------------------------
def rounded(size, radius, pad):
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)
    lo, hi = pad, size - 1 - pad
    dx = np.maximum(np.maximum(lo + radius - xx, xx - (hi - radius)), 0)
    dy = np.maximum(np.maximum(lo + radius - yy, yy - (hi - radius)), 0)
    d = np.hypot(dx, dy)
    inside = (xx >= lo) & (xx <= hi) & (yy >= lo) & (yy <= hi)
    return np.clip(radius + 1.0 - d, 0, 1) * inside


mask = rounded(S, S * 0.225, S * 0.02)

# a hairline inner edge lifts it off a dark taskbar
edge = np.clip(mask - rounded(S, S * 0.225 - S * 0.012, S * 0.02 + S * 0.012), 0, 1)
flat = np.clip(flat + edge[..., None] * 0.16, 0, 1)

rgba = np.dstack([flat, mask])
master = Image.fromarray((rgba * 255).astype(np.uint8), "RGBA")

DEST = HERE
master.save(os.path.join(DEST, "RimStudio.png"))

# Small sizes get their own pass rather than being left to the .ico writer:
# a straight LANCZOS to 16px thins the rim until it disappears, so the tiny
# ones are sharpened to hold the one feature the icon is made of.
from PIL import ImageEnhance
frames = []
for s in (256, 128, 64, 48, 40, 32, 24, 20, 16):
    im = master.resize((s, s), Image.LANCZOS)
    if s <= 48:
        im = ImageEnhance.Sharpness(im).enhance(1.0 + (48 - s) * 0.05)
        im = ImageEnhance.Contrast(im).enhance(1.0 + (48 - s) * 0.012)
    frames.append(im)
frames[0].save(os.path.join(DEST, "RimStudio.ico"), format="ICO",
               sizes=[(f.width, f.height) for f in frames],
               append_images=frames[1:])
print("written to", DEST)
for f in ("RimStudio.png", "RimStudio.ico"):
    p = os.path.join(DEST, f)
    print("  %-16s %6.1f KB" % (f, os.path.getsize(p) / 1024))
