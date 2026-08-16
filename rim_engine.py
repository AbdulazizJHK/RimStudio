"""
rim_engine.py - the pixel engine behind RimStudio's "Colour from the background".

Usable directly from a shell as:
    python rim_engine.py subject.png background.png out.png key=value key=value ...

subject.png   RGBA, the cut-out subject on transparency
background.png RGB, everything that sits behind it, same pixel size
out.png       RGBA written back: the subject relit, alpha preserved

Photoshop's own layer maths cannot do the important step - unpremultiplying the
gathered light - without a pile of fragile channel work, and it is that step
which keeps a colour saturated instead of dragging it toward black. Doing it
here in one pass is both more accurate and faster than the layer pipeline.

Params (all optional):
  seat 0..0.9        darken the subject to the plate's light level
  tint 0..1          pull the subject toward the scene's ambient colour
  reach px           how far light travels to reach the edge (broad spill)
  rim_reach px       gather radius for the rim itself
  core_w px          width of the hot core at the contour
  soft_w px          width of the soft shoulder inside the contour
  wrap  0..3         strength of the broad spill
  rim   0..4         strength of the soft rim
  core  0..4         strength of the hot core
  threshold 0..1     how bright the background must be to count as emitting
  sat   0..3         saturation applied to the gathered light
"""
import sys
import os
import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rim_grade


def _box1d(a, r, axis):
    if r < 1:
        return a
    n = a.shape[axis]
    pad = [(0, 0)] * a.ndim
    pad[axis] = (r, r)
    ap = np.pad(a, pad, mode="edge")
    C = np.cumsum(ap, axis=axis, dtype=np.float64)
    z = list(C.shape)
    z[axis] = 1
    C = np.concatenate([np.zeros(z, dtype=C.dtype), C], axis=axis)
    hi = [slice(None)] * a.ndim
    lo = [slice(None)] * a.ndim
    hi[axis] = slice(2 * r + 1, 2 * r + 1 + n)
    lo[axis] = slice(0, n)
    return ((C[tuple(hi)] - C[tuple(lo)]) / (2 * r + 1)).astype(np.float32)


def blur(a, sigma):
    """Three box passes ~ a Gaussian, and O(n) whatever the radius."""
    r = max(1, int(round(sigma)))
    out = np.asarray(a, dtype=np.float32)
    for _ in range(3):
        out = _box1d(out, r, 0)
        out = _box1d(out, r, 1)
    return out


def srgb_to_lin(x):
    return np.where(x <= 0.04045, x / 12.92, ((x + 0.055) / 1.055) ** 2.4).astype(np.float32)


def lin_to_srgb(x):
    x = np.clip(x, 0.0, None)
    return np.where(x <= 0.0031308, x * 12.92, 1.055 * np.power(x, 1 / 2.4) - 0.055)


def saturate(c, s):
    g = c[..., 0] * 0.2126 + c[..., 1] * 0.7152 + c[..., 2] * 0.0722
    return np.clip(g[..., None] + (c - g[..., None]) * s, 0.0, None)


def _down(a, ds):
    """Area-average downsample. Point sampling would drop thin bright streaks,
    which are exactly the light we are trying to gather."""
    H, W = a.shape[:2]
    Hp = (H + ds - 1) // ds * ds
    Wp = (W + ds - 1) // ds * ds
    pad = [(0, Hp - H), (0, Wp - W)] + [(0, 0)] * (a.ndim - 2)
    ap = np.pad(a, pad, mode="edge")
    shape = (Hp // ds, ds, Wp // ds, ds) + a.shape[2:]
    return ap.reshape(shape).mean(axis=(1, 3)).astype(np.float32)


def _up(a, ds, H, W):
    out = np.repeat(np.repeat(a, ds, axis=0), ds, axis=1)
    return out[:H, :W]


def gathered(bg_lin, alpha, radius, threshold, knee):
    """The gather is a very low-frequency signal - it has just been blurred by
    tens of pixels - so computing it on a reduced grid and scaling back up is
    visually identical and several times faster. That is what makes the live
    preview keep up with a slider."""
    if radius > 14:
        ds = int(min(4, max(2, radius // 14)))
        H, W = alpha.shape[:2]
        c, cov = _gather_core(_down(bg_lin, ds), _down(alpha, ds),
                              radius / ds, threshold, knee)
        return _up(c, ds, H, W), _up(cov, ds, H, W)
    return _gather_core(bg_lin, alpha, radius, threshold, knee)


def _gather_core(bg_lin, alpha, radius, threshold, knee):
    """Colour and reach of light arriving from OUTSIDE the silhouette.

    The division at the end is the unpremultiply. Blurring a colour that was
    masked to zero elsewhere drags it toward black in proportion to how much
    empty space sits in the kernel; dividing by the blurred coverage undoes
    exactly that, so the hue survives instead of turning to grey mud.
    """
    lum = bg_lin[..., 0] * 0.2126 + bg_lin[..., 1] * 0.7152 + bg_lin[..., 2] * 0.0722
    t = np.clip((lum - threshold) / max(knee, 1e-6), 0.0, 1.0)
    w = (t * t * (3.0 - 2.0 * t)).astype(np.float32)     # smoothstep gate
    outside = (1.0 - alpha).astype(np.float32)
    cov = blur(w * outside, radius)
    emi = blur(bg_lin * (w * outside)[..., None], radius)
    return emi / np.maximum(cov, 1e-4)[..., None], cov


def band(alpha, width):
    """Soft band just inside the silhouette, peaking at the contour."""
    b = np.clip(alpha - blur(alpha, width), 0.0, 1.0)
    m = float(b.max())
    return (b / m) if m > 1e-6 else b


def norm(mask, x, pct=99.0):
    """Scale so the strongest edge reaches 1, making strength scene-independent."""
    v = x[mask]
    if v.size == 0:
        return x
    p = float(np.percentile(v, pct))
    return x / p if p > 1e-6 else x


DEFAULTS = dict(seat=0.0, tint=0.0, reach=260.0, rim_reach=70.0,
                core_w=9.0, soft_w=34.0, wrap=0.55, rim=1.35, core=1.90,
                threshold=0.10, knee=0.22, sat=1.45,
                m_exposure=0.0, m_contrast=0.0, m_colour=0.0, m_blacks=0.0,
                m_sat=0.0, hi_protect=0.6, level_pct=70.0,
                defringe=0.0, grain=0.0, focus=0.0,
                glow=1.0, bg_glow=0.0, glow_thr=0.5, glow_colour=1.5,
                shadow=0.0, sh_lean=0.0, sh_squash=0.28, sh_soft=26.0, sh_contact=0.6)


def defringe(obj_lin, alpha, amount, radius=3.0):
    """Replace the colour of semi-transparent edge pixels with colour pulled from
    inside the subject.

    A cut-out carries a thin halo of whatever it was photographed against - a
    dark line if it came off a dark set, a pale one off a white sweep. It survives
    every grade and is the single most common tell that something was pasted.
    Sampling the interior colour outward removes it without touching the matte.
    """
    if amount <= 0:
        return obj_lin
    inner = np.clip((alpha - 0.90) / 0.10, 0.0, 1.0).astype(np.float32)
    num = blur(obj_lin * inner[..., None], radius)
    den = np.maximum(blur(inner, radius), 1e-4)[..., None]
    pulled = num / den
    # only where the matte is soft, and only as far as the user asks
    k = (np.clip(1.0 - (alpha - 0.35) / 0.6, 0.0, 1.0) * amount)[..., None]
    return obj_lin * (1.0 - k) + pulled * k


def grain_deficit(bg_srgb, obj_srgb, alpha):
    """How much grain the subject is MISSING relative to the plate.

    Adding the plate's full sigma is wrong: a subject often already carries as
    much high-frequency detail as the plate, or more, and piling noise on top of
    that just makes it look dirty. Measured on the night comp the subject was
    already at 3.6 levels against the plate's 2.8, so the correct amount to add
    was zero. Only the shortfall gets synthesised.
    """
    hb = bg_srgb - blur(bg_srgb, 2.0)
    ho = obj_srgb - blur(obj_srgb, 2.0)
    out = (1.0 - alpha) > 0.5
    inn = alpha > 0.9
    if not out.any() or not inn.any():
        return 0.0
    plate = min(float(np.std(hb[out])), 0.03)
    subject = float(np.std(ho[inn]))
    # noise adds in quadrature, so that is how much to synthesise
    return float(np.sqrt(max(plate * plate - subject * subject, 0.0)))


def grain_sigma(bg_srgb, alpha):
    """Standard deviation of the plate's high-frequency detail = its grain.

    Measured and applied in DISPLAY space, never linear. Linear light stores
    shadows in a tiny numeric range, so a sigma that looks right there explodes
    once the sRGB curve lifts the darks - which is exactly how a matched-down
    subject ended up buried in noise.
    """
    hi = bg_srgb - blur(bg_srgb, 2.0)
    w = (1.0 - alpha) > 0.5
    if not w.any():
        return 0.0
    # cap it: a compressed or upscaled plate reports far more "grain" than it has
    return float(min(np.std(hi[w]), 0.03))


def add_grain(obj_lin, alpha, sigma, amount, seed=7):
    """Match the plate's noise. A clean studio cutout on a grainy plate reads as
    fake even when the colour is perfect, because the eye picks up the texture
    boundary at the silhouette."""
    if amount <= 0 or sigma <= 0:
        return obj_lin
    rng = np.random.default_rng(seed)
    n = rng.normal(0.0, sigma * amount, size=obj_lin.shape[:2]).astype(np.float32)
    return np.clip(obj_lin + n[..., None] * (alpha > 0.01)[..., None], 0.0, None)


def contact_shadow(alpha, opacity=0.0, lean=0.0, squash=0.28, softness=26.0,
                   contact=0.6, scale=1.0):
    """A ground shadow, returned as a 0-1 darkening map to apply UNDER the subject.

    The subject is lit by the scene but casts nothing into it - that reads as
    floating no matter how good the colour match is. There is no 3D information
    here, so this does what a matte painter does by hand: take the silhouette,
    squash it toward the contact line, lean it away from the light, and blur it.

    Two blurs, not one: a wide soft body plus a tight dark core at the contact
    point. Real shadows harden where the object meets the ground and soften with
    distance, and it is that contact darkening that actually plants the subject.
    """
    if opacity <= 0:
        return None
    H, W = alpha.shape[:2]
    ys = np.where(alpha.max(axis=1) > 0.5)[0]
    if not len(ys):
        return None
    y0 = float(ys.max())                      # the contact line

    sq = float(np.clip(squash, 0.05, 1.0))
    a8 = Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8), "L")
    # PIL maps OUTPUT->INPUT, so these are the inverted coefficients of
    #   X = x + lean*(y0-y)/1 ,  Y = y0 - (y0-y)*sq
    k = float(lean) * scale
    coef = (1.0, k / sq, -k * y0 / sq,
            0.0, 1.0 / sq, y0 - y0 / sq)
    warped = np.asarray(a8.transform((W, H), Image.AFFINE, coef,
                                     resample=Image.BILINEAR), np.float32) / 255.0

    body = blur(warped, max(1.0, softness * scale))
    core = blur(warped, max(1.0, softness * scale * 0.28))
    sh = body + core * float(np.clip(contact, 0.0, 1.5))
    # Renormalise before applying opacity. Squashing makes a thin band and
    # blurring then spreads its energy out, so the raw peak came out at 0.07 for
    # a nominal 60% shadow - the slider has to mean the same thing whatever the
    # squash and softness happen to be.
    peak = float(np.percentile(sh[sh > 1e-4], 99.5)) if (sh > 1e-4).any() else 0.0
    if peak > 1e-4:
        sh = sh / peak
    return np.clip(sh, 0.0, 1.0) * float(opacity)


def sharpness(img, alpha, inside):
    """High-frequency energy, i.e. how much fine detail a region carries."""
    hi = img - blur(img, 2.0)
    w = (alpha > 0.5) if inside else ((1.0 - alpha) > 0.5)
    if not w.any():
        return 0.0
    return float(np.std(hi[w]))


def _sharp_inside(img, alpha, pre_blur=0.0):
    """`sharpness` over the subject, computed on a CROP of the frame.

    Exactly equal to the full-frame result, not an approximation: the box blur
    has finite support, so padding the subject's bounding box by more than that
    support leaves every masked pixel with identical neighbours. Verified at
    delta 0.0 on both test pairs, at 1.3-1.5x the speed - and the focus solver
    calls this up to ten times on a 2400px image, so it is the difference
    between a pull that feels slow and one that does not.
    """
    ys, xs = np.where(alpha > 0.5)
    if not len(ys):
        return 0.0
    pad = int(9 * max(pre_blur, 2.0) + 8)
    H, W = alpha.shape[:2]
    y0, y1 = max(0, ys.min() - pad), min(H, ys.max() + 1 + pad)
    x0, x1 = max(0, xs.min() - pad), min(W, xs.max() + 1 + pad)
    im, al = img[y0:y1, x0:x1], alpha[y0:y1, x0:x1]
    if pre_blur > 0:
        im = blur(im, pre_blur)
    return sharpness(im, al, True)


def sharp_ratio(obj_lin, alpha, bg_lin, s_sub=None, s_bg=None):
    """plate sharpness / subject sharpness, as a scale-free number.

    Must be measured on FULL-resolution data and reused for the preview.
    `sharpness` compares against a fixed 2px blur, so a downscaled preview
    samples a different frequency band entirely - measured on this pair the
    preview reported a ratio of 0.65 against the full image's 0.29, which made
    the preview blur half as much as the final render.
    """
    if s_sub is None:
        s_sub = _sharp_inside(obj_lin, alpha)
    if s_bg is None:
        s_bg = sharpness(bg_lin, alpha, False)
    if s_sub <= 1e-6:
        return 1.0
    return float(np.clip(s_bg / s_sub, 0.0, 1.0))


def solve_focus_px(obj_lin, alpha, bg_lin, max_px=40.0, s_sub=None, s_bg=None):
    """The blur radius at which the subject's detail level actually equals the
    plate's, measured rather than assumed.

    The previous version multiplied by a hand-picked constant. That constant was
    chosen while the focus path was silently disabled, so its effect was never
    once seen - and when the path was fixed it turned faces into blobs. Solving
    for the radius removes the guess: blur until the numbers agree.
    Run ONCE per image pair at full resolution; scale it for previews.
    """
    # both may be handed in: load_pair needs the ratio too, and computing these
    # twice was a full extra pass over a 2400px image for no new information
    if s_bg is None:
        s_bg = sharpness(bg_lin, alpha, False)
    if s_sub is None:
        s_sub = _sharp_inside(obj_lin, alpha)
    if s_sub <= 1e-6 or s_bg >= s_sub:
        return 0.0
    prev_r, prev_s = 0.0, s_sub
    for r in (0.6, 1.0, 1.6, 2.5, 4.0, 6.0, 9.0, 14.0, 20.0, max_px):
        s = _sharp_inside(obj_lin, alpha, r)
        if s <= s_bg:
            # linear interpolation between the two bracketing radii
            span = prev_s - s
            f = (prev_s - s_bg) / span if span > 1e-9 else 0.0
            return float(prev_r + (r - prev_r) * max(0.0, min(1.0, f)))
        prev_r, prev_s = r, s
    return float(max_px)


def focus_radius(obj_lin, alpha, bg_lin, amount, scale=1.0, ratio=None):
    """How much to soften the subject to match the plate's focus.

    MUST be measured on the UNGRADED subject. `sharpness` is the std of
    high-frequency detail in linear light, and darkening the subject scales that
    std down with it - so once the grade had pulled exposure down, the subject
    measured "softer" than the plate and focus matching silently switched itself
    off. Measuring first and applying later keeps the two independent.
    """
    if amount <= 0:
        return 0.0
    if ratio is None:
        ratio = sharp_ratio(obj_lin, alpha, bg_lin)
    if ratio >= 1.0:
        return 0.0                      # plate is equally sharp or sharper
    # 6px was far too timid: a heavily defocused plate needs tens of pixels of
    # softening before the subject stops looking cut out of a different photo
    # scale straight through, no floor on it: clamping scale to 0.25 made the
    # 390px draft blur the equivalent of 28px at full size while the 780px
    # preview blurred 18.5px - the same picture softening by different amounts
    # depending only on which tier drew it.
    return max(0.6, (1.0 - ratio) * 26.0 * amount * scale)


def match_focus(obj_lin, alpha, bg_lin, amount, scale=1.0):
    """Convenience wrapper: measure and apply in one go (used by tests)."""
    r = focus_radius(obj_lin, alpha, bg_lin, amount, scale)
    if r <= 0:
        return obj_lin, alpha
    return blur(obj_lin, r), blur(alpha, r * 0.7)


def auto_focus(pre):
    """Recommended focus-match amount, on its own.

    Kept OUT of auto_params on purpose. Softening is the one correction that
    destroys detail rather than shifting it, and how much of it a shot wants is
    a taste call about the subject as much as a measurement - so it should not
    move every time you re-run the main auto over a grade you have settled.

    Returns (amount, measured) so the UI can say what it based the number on.
    """
    fr = pre.get("focus_ratio")
    if fr is None:
        fr = sharp_ratio(pre["obj_lin"], pre["alpha"], pre["bg_lin"])
    # ease in: a plate only slightly softer than the subject needs nothing, and
    # matching a very soft plate all the way turns the subject to mush
    amt = float(np.clip((1.0 - fr) * 1.1 - 0.1, 0.0, 1.0))
    px = pre.get("focus_px")
    return round(amt, 2), {"sharp_ratio": round(float(fr), 2),
                           "solved_px": None if px is None else round(float(px), 1)}


def _edge_step_L(pre, scale, want, lit):
    """How big a brightness jump there is across the matte, in Lab L units.

    Measured in a ring just OUTSIDE the cut-out, not over the whole plate: what
    matters is what the edge actually abuts, and a plate can be bright overall
    while the few hundred pixels behind the subject are not.
    """
    alpha = pre["alpha"]
    lab_b = pre["bg_lab"]
    ring = (blur((alpha > 0.5).astype(np.float32),
                 max(2.0, 12.0 * scale)) > 0.02) & (alpha <= 0.05)
    Lb_edge = float(np.median(lab_b[..., 0][ring])) if ring.any() else lit
    return abs(want - Lb_edge)


def _light_and_ground(alpha, gate):
    """Where the light is, where the subject stands, and whether there is any
    ground in frame. Shared by the report and by auto_shadow()."""
    H, W = alpha.shape[:2]
    ys, xs = np.mgrid[0:H, 0:W]
    gsum = float(gate.sum())
    if gsum > 1e-3:
        lx = float((gate * xs).sum() / gsum)
        ly = float((gate * ys).sum() / gsum)
    else:
        lx, ly = W * 0.5, 0.0
    sel = alpha > 0.5
    if sel.any():
        sx = float(xs[sel].mean())
        s_bottom = float(np.where(alpha.max(axis=1) > 0.5)[0].max())
    else:
        sx, s_bottom = W * 0.5, H - 1.0
    # No ground in frame means no shadow to cast: a subject cropped at the
    # bottom edge would only get a sliver jammed against the frame.
    return lx, ly, sx, s_bottom, s_bottom >= (H - 3)


def auto_shadow(pre, preview_w):
    """Recommended ground shadow, on its own.

    Kept OUT of auto_params for the same reason as focus and glow, and more so:
    a shadow is the one thing here that adds an object to the picture rather
    than adjusting the one already in it. Whether the subject casts at all is a
    judgement about the scene - what it is standing on, whether that ground is
    even visible - so it should never appear or move because the grade was
    re-run.

    Returns (params, measured).
    """
    alpha = pre["alpha"]
    scale = max(preview_w, 1) / 2400.0
    lum = (pre["bg_lin"][..., 0] * 0.2126 + pre["bg_lin"][..., 1] * 0.7152 +
           pre["bg_lin"][..., 2] * 0.0722)
    out = (1.0 - alpha) > 0.5
    top = float(np.percentile(lum[out], 99.5)) if out.any() else 0.5
    thr = float(np.clip(top * 0.22, 0.02, 0.6))
    gate = ((lum > thr) * (1.0 - alpha)).astype(np.float32)

    H, W = alpha.shape[:2]
    lx, ly, sx, s_bottom, touching = _light_and_ground(alpha, gate)

    # Everything below is computed from SCALE-FREE fractions and expressed in
    # full-resolution pixels, which is what the sliders take.
    #
    # ⚠ The old version measured in preview pixels, clipped to a range that
    # suited a 780px preview, and only then divided by the scale - inflating
    # both numbers by 3x on the way out. Auto handed back sh_lean -400 and
    # sh_soft 120, which are the slider's MIN and MAX: a recommendation pinned
    # to the ends of its own control is not a measurement.
    inv = 1.0 / max(scale, 1e-6)
    lean = float(np.clip(-(lx - sx) / max(W, 1) * 900.0, -400, 400))
    # a high light throws a short shadow, a low one throws a long one
    elev = float(np.clip((s_bottom - ly) / max(H, 1), 0.0, 1.0))
    squash = float(np.clip(0.10 + 0.45 * (1.0 - elev), 0.06, 0.60))
    dist = float(np.hypot(lx - sx, ly - s_bottom)) / max(W, 1)
    soft = float(np.clip(14.0 + dist * 90.0, 8.0, 110.0))

    return ({"shadow": 0.0 if touching else 0.45,
             "sh_lean": round(lean),
             "sh_squash": round(squash, 2),
             "sh_soft": round(soft),
             "sh_contact": 0.7},
            {"light_at": [round(lx * inv), round(ly * inv)],
             "ground": "no - the subject reaches the frame bottom"
                       if touching else "yes"})


def auto_glow(pre, preview_w):
    """Recommended Bloom amount, on its own.

    Kept OUT of auto_params for the same reason as auto_focus: it is the one
    correction that adds light the plate never had. How far to push it is a
    look decision - a heavy setting is atmosphere on one shot and a lamp on the
    next - so it should not jump every time the main auto re-runs over a grade
    you have already settled.

    The measurement behind it is the brightness STEP across the matte. A big
    step means subject and plate share no light at their boundary, which is what
    reads as "stuck on" and which no amount of colour matching hides.

    Returns (amount, measured).
    """
    alpha = pre["alpha"]
    if pre.get("bg_lab") is None:
        pre["bg_lab"] = rim_grade.lin_to_lab(pre["bg_lin"])
    scale = max(preview_w, 1) / 2400.0
    near = blur(alpha, max(1.0, 130.0 * scale))
    bg_w = rim_grade.plate_weight(alpha, near)
    lab_s = rim_grade.lin_to_lab(pre["obj_lin"])
    mLs, _ = rim_grade._weighted_stats(lab_s[..., 0], np.clip(alpha, 0.0, 1.0))
    lit = rim_grade.lit_level(pre["bg_lab"][..., 0], bg_w, 70.0)
    want = min(mLs, lit * 1.25 + 8.0)

    dL = _edge_step_L(pre, scale, want, lit)
    # 4 L units is inside the noise and needs nothing; the cap stops a very
    # high-contrast cut-out turning into a lamp
    amt = float(np.clip((dL - 4.0) / 60.0, 0.0, 0.45))
    return round(amt, 2), {"edge_step_L": round(dL, 1)}


def auto_params(pre, preview_w):
    """Recommend settings by MEASURING the pair, not by guessing.

    Returns slider values. The exposure amount is the interesting one: closing
    the whole gap to a night plate would erase the subject, so it aims to land
    the subject a little above the plate's lit level, which is where a real
    subject standing in that light would sit.
    """
    alpha = pre["alpha"]
    obj_lin = pre["obj_lin"]
    bg_lin = pre["bg_lin"]
    if pre.get("bg_lab") is None:
        pre["bg_lab"] = rim_grade.lin_to_lab(bg_lin)
    lab_b = pre["bg_lab"]

    scale = max(preview_w, 1) / 2400.0
    near = blur(alpha, max(1.0, 130.0 * scale))
    bg_w = rim_grade.plate_weight(alpha, near)
    sub_w = np.clip(alpha, 0.0, 1.0)

    lab_s = rim_grade.lin_to_lab(obj_lin)
    mLs, sLs = rim_grade._weighted_stats(lab_s[..., 0], sub_w)
    _, sLb = rim_grade._weighted_stats(lab_b[..., 0], bg_w)
    lit = rim_grade.lit_level(lab_b[..., 0], bg_w, 70.0)

    # aim slightly above the plate's lit level, then solve for the amount
    want = min(mLs, lit * 1.25 + 8.0)
    gap = mLs - lit
    expo = 0.0 if abs(gap) < 1e-3 else float(np.clip((mLs - want) / gap, 0.0, 1.0))

    # contrast: full match is safe, it is a ratio - but not if it means a big jump
    ratio = sLb / max(sLs, 1e-6)
    contrast = float(np.clip(1.0 - abs(np.log(max(ratio, 1e-3))) * 0.35, 0.35, 1.0))

    # colour: back off when the required shift is extreme, to avoid a colour cast
    mAs, _ = rim_grade._weighted_stats(lab_s[..., 1], sub_w)
    mAb, _ = rim_grade._weighted_stats(lab_b[..., 1], bg_w)
    mBs, _ = rim_grade._weighted_stats(lab_s[..., 2], sub_w)
    mBb, _ = rim_grade._weighted_stats(lab_b[..., 2], bg_w)
    shift = float(np.hypot(mAb - mAs, mBb - mBs))
    # Trust the cast measurement LESS the bigger it is. A small shift means one
    # illuminant and the average is meaningful; a large one usually means the
    # plate mixes two (warm rock under a blue sky) and its average is a colour
    # nothing in the scene actually is. Measured on the Ghar Hira plate: cast
    # shift 12.6 drove colour to 0.74 and turned white thobes pink, while the
    # night plate's shift of 5.6 at 0.75 was correct.
    colour = float(np.clip(0.75 * (6.0 / max(shift, 6.0)), 0.20, 0.80))

    # Threshold relative to the plate's BRIGHT end, not to a percentile of all
    # of it. On a night plate 88% of pixels are near black, so a percentile lands
    # in the shadows and everything counts as a light - which then fools the
    # reach probe into picking the smallest radius.
    lum = (bg_lin[..., 0] * 0.2126 + bg_lin[..., 1] * 0.7152 + bg_lin[..., 2] * 0.0722)
    out = (1.0 - alpha) > 0.5
    top = float(np.percentile(lum[out], 99.5)) if out.any() else 0.5
    thr = float(np.clip(top * 0.22, 0.02, 0.6))

    # reach: the smallest radius that actually collects most of the light there
    # is to collect. Probing beats a fixed number - lights sit anywhere.
    gate = ((lum > thr) * (1.0 - alpha)).astype(np.float32)
    edge = (alpha > 0.5) & (band(alpha, max(2.0, 30.0 * scale)) > 0.2)
    reach_full, best = 260.0, 0.0
    got, covers = [], {}
    for r in (40.0, 80.0, 160.0, 260.0, 400.0):
        c = blur(gate, max(1.0, r * scale))
        v = float(c[edge].mean()) if edge.any() else 0.0
        got.append((r, v))
        covers[r] = c
        best = max(best, v)
    for r, v in got:
        if best > 0 and v >= best * 0.6:
            reach_full = r
            break

    # --- rim strength from how much of the edge actually has light behind it
    cov = covers[reach_full]
    if edge.any():
        ce = cov[edge]
        peak = float(np.percentile(ce, 99)) if ce.size else 0.0
        lit_frac = float((ce > peak * 0.15).mean()) if peak > 1e-6 else 0.0
    else:
        lit_frac = 0.5
    # A silhouette lit nearly all the way round needs LESS gain or it blows out;
    # a single light catching one shoulder needs more to register at all.
    rim_str = float(np.clip(2.1 - lit_frac * 1.3, 0.7, 2.2))
    core_str = float(np.clip(rim_str * 1.4, 0.8, 3.0))
    # Broad spill has to come DOWN when most of the edge is lit, or it stops
    # reading as light and becomes a haze laid over the whole subject - which is
    # exactly what the 0.55 default did on a plate with warm rock right behind.
    wrap_str = float(np.clip(0.55 * (1.0 - lit_frac * 0.6), 0.15, 0.60))

    # --- rim widths from the subject's own size, not a fixed pixel count
    rows = np.where(alpha.max(axis=1) > 0.5)[0]
    cols = np.where(alpha.max(axis=0) > 0.5)[0]
    if rows.size and cols.size:
        objW = float(cols.max() - cols.min())
        objH = float(rows.max() - rows.min())
    else:
        objW = objH = float(min(alpha.shape[:2]))
    sug = max(2.0, round(min(objW, objH) * 0.014))          # preview units
    sug_full = sug / max(scale, 1e-6)
    soft_w = float(np.clip(round(sug_full * 1.5), 4, 120))
    core_w = float(np.clip(round(sug_full * 0.4), 1, 60))

    # focus is deliberately NOT part of the main auto - see auto_focus()
    fr = pre.get("focus_ratio")
    if fr is None:
        fr = sharp_ratio(obj_lin, alpha, bg_lin)

    # glow is deliberately NOT part of the main auto either - see auto_glow()
    dL = _edge_step_L(pre, scale, want, lit)

    # the shadow is deliberately NOT part of the main auto - see auto_shadow().
    # Only the light position is kept, for the report.
    lx, ly, _sx, _sb, touching = _light_and_ground(alpha, gate)

    return {"m_exposure": round(expo, 2), "m_contrast": round(contrast, 2),
            "m_colour": round(colour, 2), "m_blacks": 0.8, "m_sat": 0.35,
            "hi_protect": 0.6, "threshold": round(thr, 3),
            "reach": reach_full, "rim_reach": round(max(20.0, reach_full * 0.28)),
            "defringe": 0.7, "grain": 0.8,
            "soft_w": soft_w, "core_w": core_w,
            "rim": round(rim_str, 2), "core": round(core_str, 2),
            "wrap": round(wrap_str, 2),
            "_measured": {"subject_L": round(mLs, 1), "plate_lit_L": round(lit, 1),
                          "aim_L": round(want, 1),
                          "contrast": [round(sLs, 1), round(sLb, 1)],
                          "cast_shift": round(shift, 1),
                          "edge_lit": round(lit_frac, 2),
                          "edge_step_L": round(dL, 1),
                          "focus_ratio": round(float(fr), 2),
                          "scene_unit": int(sug_full),
                          "light_at": [round(lx / max(scale, 1e-6)),
                                       round(ly / max(scale, 1e-6))],
                          "ground": "no - subject reaches the frame bottom"
                                    if touching else "yes"}}


def prepare(sub_rgba, bg_rgb):
    """Per-image work that does not depend on any slider: the sRGB decode and
    the plate's Lab. Caching these is worth ~100ms per preview frame."""
    return {"alpha": sub_rgba[..., 3].astype(np.float32),
            "obj_lin": srgb_to_lin(sub_rgba[..., :3].astype(np.float32)),
            "bg_lin": srgb_to_lin(bg_rgb.astype(np.float32)),
            "bg_lab": None}


def relight(sub_rgba, bg_rgb, params=None, pre=None):
    """sub_rgba: HxWx4 float 0-1 (sRGB + alpha). bg_rgb: HxWx3 float 0-1 sRGB.
    Returns HxWx4 float 0-1 sRGB with the original alpha."""
    P = dict(DEFAULTS)
    if params:
        for k, v in params.items():
            if k in P:
                try:
                    P[k] = float(v)
                except (TypeError, ValueError):
                    pass

    if pre is None:
        pre = prepare(sub_rgba, bg_rgb)
    alpha = pre["alpha"]
    obj_lin = pre["obj_lin"]
    bg_lin = pre["bg_lin"]
    if pre.get("bg_lab") is None:
        pre["bg_lab"] = rim_grade.lin_to_lab(bg_lin)

    # scale radii with the image so a preview matches the full-size render
    scale = max(sub_rgba.shape[1], 1) / 2400.0
    R = lambda v: max(1.0, v * scale)

    inside = alpha > 0.5
    wrapC, wrapI = gathered(bg_lin, alpha, R(P["reach"]), P["threshold"], P["knee"])
    rimC, rimI = gathered(bg_lin, alpha, R(P["rim_reach"]), P["threshold"], P["knee"])
    wrapC = saturate(wrapC, P["sat"])
    rimC = saturate(rimC, P["sat"])

    bCore = band(alpha, R(P["core_w"]))
    bSoft = band(alpha, R(P["soft_w"]))
    edge = inside & (bSoft > 0.10)
    wrapI = np.clip(norm(edge, wrapI), 0.0, 1.5)
    rimI = np.clip(norm(edge, rimI), 0.0, 1.5)

    # defringe BEFORE grading, so the grade works on clean subject colour and
    # does not amplify a halo it was never meant to see
    obj_lin = defringe(obj_lin, alpha, P["defringe"], R(3.0))

    # measure focus now, on the ungraded subject, and apply it at the end.
    # pre["focus_ratio"] is measured once at FULL resolution by the caller so the
    # preview and the final render soften by the same amount.
    fpx = pre.get("focus_px")
    if fpx is None:
        fpx = solve_focus_px(obj_lin, alpha, bg_lin) / max(scale, 1e-6)
        pre["focus_px"] = fpx
    focus_r = fpx * scale * P["focus"]      # full-res radius, scaled to this tier

    near = blur(alpha, max(R(60.0), R(P["reach"]) * 0.5))
    obj_lin = rim_grade.match(obj_lin, alpha, bg_lin, near=near,
                              exposure=P["m_exposure"], contrast=P["m_contrast"],
                              colour=P["m_colour"], blacks=P["m_blacks"],
                              saturation=P["m_sat"],
                              protect_highlights=P["hi_protect"],
                              level_pct=P["level_pct"], bg_lab=pre["bg_lab"])

    base = obj_lin * (1.0 - P["seat"])
    if P["tint"] > 0:
        amb = bg_lin.reshape(-1, 3).mean(axis=0)
        amb = amb / max(float(amb.max()), 1e-4)
        grey = (base[..., 0] * 0.2126 + base[..., 1] * 0.7152 +
                base[..., 2] * 0.0722)[..., None]
        base = base * (1.0 - P["tint"]) + grey * amb[None, None, :] * P["tint"]

    # Added light is scaled by how bright the subject now is. Without this a
    # matched-down subject gets swamped: the rim is normalised to ~1.0 while the
    # base has dropped several stops, so the same gain that looked right before
    # grading turns into a white veil after it.
    lvl = float(np.clip(np.percentile(obj_lin[inside], 90) if inside.any() else 1.0, 0.02, 1.0))
    k = 0.35 + 0.65 * lvl

    # one master trim over everything the background adds. Rim, core and spill
    # are three views of the SAME light, so the usual note - "a bit more glow" -
    # means all three together; nudging them one at a time changes the character
    # of the light as a side effect of changing its level.
    g = P["glow"]

    out = base
    out = out + wrapC * wrapI[..., None] * P["wrap"] * k * g
    out = out + rimC * (rimI * bSoft)[..., None] * P["rim"] * k * g
    out = out + rimC * (rimI * bCore)[..., None] * P["core"] * k * g

    # apply the focus match after the light is added, so the rim softens with
    # everything else - a sharp rim on a soft subject is its own kind of tell
    if focus_r > 0:
        out = blur(out, focus_r)
        alpha = blur(alpha, focus_r * 0.7)

    rgb = np.clip(lin_to_srgb(np.clip(out, 0.0, None)), 0.0, 1.0)

    # grain LAST and in display space, and only the SHORTFALL against the plate
    if P["grain"] > 0:
        need = grain_deficit(bg_rgb, rgb, alpha)
        if need > 0:
            rgb = np.clip(add_grain(rgb, alpha, need, P["grain"]), 0.0, 1.0)
    return np.dstack([rgb, np.clip(alpha, 0.0, 1.0)]).astype(np.float32)


def halo_layer(bg_rgb, params=None, alpha=None):
    """The glow a frame throws off its own highlights, as a separate pass.

    Fed the FINISHED composite, not the plate. Two reasons, and the second is
    the important one:

    1. If only the cut-out glows, light is behaving differently on the two
       halves of one frame, and the eye reads that long before it can say why.
       A torch throwing a rim onto a shoulder is also throwing a halo onto the
       rock beside it.
    2. A halo computed on the plate alone stops dead at the matte, which draws
       the exact line the tool exists to hide. Measured over the whole frame it
       crosses the boundary in both directions - background light spills onto
       the subject, the subject's highlights spill back out - and that bleed is
       most of what dissolves a cut-out edge.

    Returned on its own rather than pre-mixed so the same pixels can go to
    Photoshop as a Screen layer on top of the stack.
    Returns None when the amount is 0 - then this costs nothing.
    """
    P = dict(DEFAULTS)
    if params:
        for k, v in params.items():
            if k in P:
                try:
                    P[k] = float(v)
                except (TypeError, ValueError):
                    pass
    amt = P["bg_glow"] * P["glow"]
    if amt <= 0.002:
        return None

    lin = srgb_to_lin(bg_rgb.astype(np.float32))
    lum = lin[..., 0] * 0.2126 + lin[..., 1] * 0.7152 + lin[..., 2] * 0.0722

    # Radius comes from the FRAME, not from the subject's reach. Reach is how
    # far light travels to an edge and runs to hundreds of pixels; borrowing it
    # here gave a 186px blur on a 780px preview, which is a fog bank rather than
    # a halo. Halation is a lens property, so ~2% of frame width.
    W = max(bg_rgb.shape[1], 1)
    r = max(2.0, W * 0.022)

    # Emitters are picked relative to the frame's own top end. A fixed threshold
    # cannot work: on this comp the two white thobes ARE the brightest thing in
    # frame, brighter than the torch, so any absolute cut takes the clothing.
    # The slider walks the cut from "only the very brightest specks" to "most of
    # the lit half of the frame".
    hi = float(np.percentile(lum, 100.0))
    thr = P["glow_thr"]
    pct = float(np.clip(40.0 + thr * 59.5, 20.0, 99.9))
    t = float(np.percentile(lum, pct))
    bright = np.clip((lum - t) / max(hi - t, 1e-3), 0.0, 1.0) ** 2

    # Brightness alone is not enough at the tight end: a flare comes from
    # something brighter than ITS OWN SURROUNDINGS, and without this term a
    # large white garment hazes the whole frame while a torch barely registers.
    #
    # But at the loose end it is exactly wrong. Turned down, the slider is
    # asking for the plate's AMBIENT colour - a blue sky, a warm rock face -
    # which is large and even by nature and which this term would reject. So
    # the gate fades in with the threshold: low means broad colour bleed off the
    # background, high means tight speculars only.
    local = np.clip((lum - blur(lum, r * 1.5)) / max(hi * 0.12, 1e-3), 0.0, 1.0)
    local = (1.0 - thr) + thr * local
    w = (bright * local).astype(np.float32)

    halo = _gather_colour(lin, w, r, P["glow_colour"])

    # Normalise to the halo's own peak so the slider means the same thing on
    # every image. Without this the number depends on how much of the frame
    # happened to be bright, and a setting carried over in a preset lands
    # somewhere different on the next shot.
    peak = float(np.percentile(halo.max(axis=2), 99.9))
    halo = halo / max(peak, 1e-6) * amt

    if alpha is not None:
        halo = halo + _spill_out(lin, alpha, r, amt,
                                 P["glow_colour"], P["glow_thr"])

    # blurred in LINEAR light, where falloff is physical, but handed back
    # display-encoded so Photoshop's Screen blend reproduces the preview exactly
    return np.clip(lin_to_srgb(np.clip(halo, 0.0, 1.0)), 0.0, 1.0).astype(np.float32)


def _gather_colour(lin, w, r, colour):
    """Spread the emitters over radius r while KEEPING the colour they emit.

    The naive version - blur(lin * w) - is what made the glow come out grey.
    The blur mixes the emitters with every non-emitter around them, and those
    contribute zero to the numerator, so the result carries the right hue only
    where an emitter is dense and drifts to neutral everywhere else. Measured on
    this comp it kept 54% of the source's colour: a strongly blue sky emitted a
    glow of 0.156/0.146/0.167, which is grey.

    Dividing by the same blur of the WEIGHT alone - unpremultiplying - recovers
    the emitters' true colour independently of how many of them there were. That
    colour then carries the blurred intensity for its falloff. This is the same
    correction the rim gather already makes, and it is the whole reason the rim
    stopped being one flat colour.

    `colour` is a trim on how far to go: 0 leaves a white glow, 1 is the true
    emitted colour, above 1 pushes past it.
    """
    two = lambda x: blur(x, r) * 0.62 + blur(x, max(1.5, r * 0.30)) * 0.38
    num = two(lin * w[..., None])
    den = two(w)[..., None]
    pure = num / np.maximum(den, 1e-4)          # emitter colour, undiluted

    # Unpremultiplying alone does NOT restore the colour, which is worth saying
    # because it is the obvious guess: measured on this comp it gave a hue
    # identical to the plain blur to three decimals. The washout is not dilution
    # by non-emitters, it is that the brightest pixels dominate any average, and
    # in most scenes the brightest version of a colour is its palest one - pale
    # horizon sky, not the deep blue above it.
    #
    # So the saturation is restored explicitly, against the emitters' OWN
    # chroma. That is what makes `colour = 1.0` mean "the glow is as saturated
    # as the thing emitting it" rather than an arbitrary multiplier.
    lift = 1.0
    sel = den[..., 0] > 1e-3
    if sel.any():
        def _chroma(x):
            mx = x.max(axis=2)
            return (mx - x.min(axis=2)) / np.maximum(mx, 1e-6)
        c_src = float(np.average(_chroma(lin)[sel], weights=w[sel] + 1e-6))
        c_got = float(_chroma(pure)[sel].mean())
        lift = float(np.clip(c_src / max(c_got, 1e-4), 1.0, 4.0))

    grey = (pure[..., 0] * 0.2126 + pure[..., 1] * 0.7152 +
            pure[..., 2] * 0.0722)[..., None]
    tinted = np.clip(grey + (pure - grey) * (colour * lift), 0.0, None)

    # `num` still carries the falloff, so rescale the pure colour to match its
    # brightness rather than using it raw - otherwise the halo has no edge
    nl = num[..., 0] * 0.2126 + num[..., 1] * 0.7152 + num[..., 2] * 0.0722
    tl = tinted[..., 0] * 0.2126 + tinted[..., 1] * 0.7152 + tinted[..., 2] * 0.0722
    return tinted * (nl / np.maximum(tl, 1e-6))[..., None]


def _spill_out(flat_lin, alpha, r, amt, colour=1.0, thr=0.5):
    """Light thrown from the SUBJECT out onto the plate.

    This is the half that was missing, and it is the one that matters for a hard
    edge. Every other tool here moves light one way - the plate lights the
    subject - so a bright subject on a dark plate keeps a razor boundary with no
    shared light across it at all. Measured on this comp it is worth an 85%
    drop in the luminance step across the matte, where the frame bloom managed
    3%, and it costs nothing away from the edge because it is masked to the
    plate side.

    Deliberately NOT symmetric with the frame bloom: bloom screens light onto
    whatever is already brightest, which on a bright subject widens the very gap
    it was meant to close.
    """
    # The subject emits through the SAME threshold as the rest of the glow, so
    # one slider governs what counts as a light source on both sides of the
    # matte. Weighting by alpha alone made every pixel of the subject emit
    # equally - a shadowed sleeve throwing as much light as a lit shoulder -
    # which is a lamp-shaped subject, not a lit one.
    #
    # Measured on the subject's OWN top end, not the frame's: a subject can be
    # the darker half of the picture and still have highlights that should
    # spill, and cutting it against the frame's brightest pixels would silence
    # it entirely.
    lum = (flat_lin[..., 0] * 0.2126 + flat_lin[..., 1] * 0.7152 +
           flat_lin[..., 2] * 0.0722)
    ins = alpha > 0.5
    if ins.any():
        hi = float(np.percentile(lum[ins], 99.5))
        t = float(np.percentile(lum[ins], np.clip(20.0 + thr * 70.0, 5.0, 99.0)))
        bright = np.clip((lum - t) / max(hi - t, 1e-3), 0.0, 1.0)
    else:
        bright = np.ones_like(lum)

    # A floor under the gate, because two different things are being asked of
    # one slider. Emission is threshold-governed - bright things glow, dark
    # things do not. Atmospheric bleed is not: haze sits between the camera and
    # the whole subject regardless of how bright any part of it is, and it is
    # the bleed that keeps the cut-out edge from re-hardening.
    #
    # Measured: with no floor the threshold at maximum takes the edge fix from
    # -42% back to -24%, i.e. turning the threshold up re-opens the very gap
    # this exists to close. 0.2 keeps the emission clearly linked - the share of
    # the subject that emits still runs 80% down to 10% across the slider -
    # while holding the bleed roughly steady.
    w = (alpha * (0.2 + 0.8 * bright)).astype(np.float32)

    # unpremultiplied for the same reason as the frame glow: without it the
    # spill takes its colour from an average that includes the plate it is
    # landing on, and comes out grey exactly where it matters
    h = _gather_colour(flat_lin, w, r, colour)
    h = h * (1.0 - alpha)[..., None]                  # lands on the plate side only
    peak = float(np.percentile(h.max(axis=2), 99.5))
    # Raised from 0.55 when the threshold gate went in: gating the subject's
    # emitters cuts the total light thrown, and normalising by the peak only
    # partly makes that back, so the edge fix had dropped from -55% to -26% at
    # the default threshold. 0.85 restores it without the halo starting to read
    # as its own object rather than as air between subject and plate - clipping
    # stays at 0.06% of frame.
    return h / max(peak, 1e-6) * amt * 0.85


def bloom(bg_rgb, params=None, alpha=None):
    """The frame with its own glow screened back on. Screen, not add, so an
    already-bright highlight cannot be pushed past white into a flat disc."""
    halo = halo_layer(bg_rgb, params, alpha=alpha)
    if halo is None:
        return bg_rgb
    return np.clip(1.0 - (1.0 - np.clip(bg_rgb, 0.0, 1.0)) * (1.0 - halo), 0.0, 1.0)


def shadow_for(sub_rgba, params=None):
    """The ground shadow for this subject at this size, or None if switched off."""
    P = dict(DEFAULTS)
    if params:
        for k, v in params.items():
            if k in P:
                try:
                    P[k] = float(v)
                except (TypeError, ValueError):
                    pass
    if P["shadow"] <= 0:
        return None
    scale = max(sub_rgba.shape[1], 1) / 2400.0
    return contact_shadow(sub_rgba[..., 3].astype(np.float32), P["shadow"],
                          P["sh_lean"], P["sh_squash"], P["sh_soft"],
                          P["sh_contact"], scale)


def stats(sub_rgba, bg_rgb):
    alpha = sub_rgba[..., 3].astype(np.float32)
    obj = srgb_to_lin(sub_rgba[..., :3].astype(np.float32))
    bg = srgb_to_lin(bg_rgb.astype(np.float32))
    scale = max(sub_rgba.shape[1], 1) / 2400.0
    return rim_grade.report(obj, alpha, bg, blur(alpha, max(1.0, 130.0 * scale)))


def main():
    sub_path, bg_path, out_path = sys.argv[1], sys.argv[2], sys.argv[3]
    params = {}
    for a in sys.argv[4:]:
        if "=" in a:
            k, v = a.split("=", 1)
            params[k] = v

    sub = Image.open(sub_path).convert("RGBA")
    W, H = sub.size
    bg = Image.open(bg_path).convert("RGB")
    if bg.size != (W, H):
        bg = bg.resize((W, H), Image.LANCZOS)

    s = np.asarray(sub, dtype=np.float32) / 255.0
    b = np.asarray(bg, dtype=np.float32) / 255.0
    out = relight(s, b, params)
    Image.fromarray((out * 255).astype(np.uint8), mode="RGBA").save(out_path)
    print("OK %dx%d" % (W, H))


# guarded so the module can be imported by the standalone app without running
if __name__ == "__main__":
    main()
