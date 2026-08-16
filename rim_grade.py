"""
rim_grade.py - make a cut-out subject sit in a plate for real.

A rim alone never sells a composite. What gives a cutout away is that its
exposure, its contrast, its black point and its colour cast all belong to the
photograph it came from, not the one it has been dropped into. This module
measures both images and moves the subject toward the plate, with a separate
amount for each property so none of it has to be all-or-nothing.

Matching is done in CIE Lab: L carries lightness and contrast, a and b carry the
colour cast, so the three controls stay independent. Doing the same thing in RGB
would cross-couple them - raising contrast would also shift hue.

Everything here is float32 and vectorised; a 2400x1790 plate grades in well
under a second, which is what makes a real-time preview possible.
"""
import numpy as np

# ---------------------------------------------------------------- colour space
_M_RGB2XYZ = np.array([[0.4124564, 0.3575761, 0.1804375],
                       [0.2126729, 0.7151522, 0.0721750],
                       [0.0193339, 0.1191920, 0.9503041]], dtype=np.float32)
_M_XYZ2RGB = np.linalg.inv(_M_RGB2XYZ).astype(np.float32)
_WHITE = np.array([0.95047, 1.00000, 1.08883], dtype=np.float32)


def _f(t):
    d = 6.0 / 29.0
    return np.where(t > d ** 3, np.cbrt(np.maximum(t, 1e-8)), t / (3 * d * d) + 4.0 / 29.0)


def _finv(t):
    d = 6.0 / 29.0
    return np.where(t > d, t ** 3, 3 * d * d * (t - 4.0 / 29.0))


def lin_to_lab(rgb):
    """Linear-light RGB -> Lab. Input may be any shape (...,3)."""
    xyz = rgb @ _M_RGB2XYZ.T
    xyz = xyz / _WHITE
    fx, fy, fz = _f(xyz[..., 0]), _f(xyz[..., 1]), _f(xyz[..., 2])
    L = 116.0 * fy - 16.0
    a = 500.0 * (fx - fy)
    b = 200.0 * (fy - fz)
    return np.stack([L, a, b], axis=-1).astype(np.float32)


def lab_to_lin(lab):
    L, a, b = lab[..., 0], lab[..., 1], lab[..., 2]
    fy = (L + 16.0) / 116.0
    fx = fy + a / 500.0
    fz = fy - b / 200.0
    xyz = np.stack([_finv(fx), _finv(fy), _finv(fz)], axis=-1) * _WHITE
    return (xyz @ _M_XYZ2RGB.T).astype(np.float32)


# ---------------------------------------------------------------- statistics
def _weighted_stats(vals, w):
    """Mean and standard deviation under a weight map, ignoring empty weight."""
    tot = float(w.sum())
    if tot < 1e-6:
        return 0.0, 1.0
    m = float((vals * w).sum() / tot)
    var = float(((vals - m) ** 2 * w).sum() / tot)
    return m, float(np.sqrt(max(var, 1e-12)))


def plate_weight(alpha, near=None):
    """Where to measure the plate.

    Measuring the WHOLE plate is wrong when the subject only occupies part of the
    frame - a lamp in one corner would drag the whole grade. Weighting by
    proximity to the subject measures the light the subject is actually standing
    in. `near` is a blurred version of the subject's alpha supplied by the caller.
    """
    out = (1.0 - alpha)
    if near is None:
        return out
    return out * np.clip(near, 0.0, 1.0)


def lit_level(L, w, pct=70.0):
    """The level of the LIT part of the plate, not its average.

    A night plate is mostly black, so its mean lightness is near zero and driving
    the subject to that makes it disappear. What a subject standing in the scene
    actually matches is the brightness of the regions that are lit, so the target
    is a high percentile of the weighted plate instead of its mean.
    """
    sel = w > 0.05
    if not sel.any():
        return 0.0
    return float(np.percentile(L[sel], pct))


def match(obj_lin, alpha, bg_lin, near=None,
          exposure=0.0, contrast=0.0, colour=0.0, blacks=0.0,
          saturation=0.0, protect_highlights=0.6, level_pct=70.0, bg_lab=None):
    """Move the subject toward the plate. Each amount is 0..1.

    exposure   - lightness level
    contrast   - spread of lightness
    colour     - the a/b cast, i.e. white balance
    blacks     - lift the subject's darkest tone to the plate's
    saturation - chroma strength
    protect_highlights keeps speculars from being crushed when exposure is pulled
    down hard, which is what stops a graded subject looking flat and plasticky.
    """
    sub_w = np.clip(alpha, 0.0, 1.0)
    bg_w = plate_weight(alpha, near)
    if sub_w.sum() < 1e-6 or bg_w.sum() < 1e-6:
        return obj_lin

    lab_s = lin_to_lab(obj_lin)
    # the plate's Lab never changes while sliders move, so let the caller cache it
    lab_b = bg_lab if bg_lab is not None else lin_to_lab(bg_lin)

    Ls, La, Lb = lab_s[..., 0], lab_s[..., 1], lab_s[..., 2]
    mLs, sLs = _weighted_stats(Ls, sub_w)
    mLb, sLb = _weighted_stats(lab_b[..., 0], bg_w)
    mAs, _ = _weighted_stats(La, sub_w)
    mAb, _ = _weighted_stats(lab_b[..., 1], bg_w)
    mBs, _ = _weighted_stats(Lb, sub_w)
    mBb, _ = _weighted_stats(lab_b[..., 2], bg_w)

    # --- lightness: contrast first (about the subject's own mean), then level
    gain = 1.0 + contrast * ((sLb / max(sLs, 1e-6)) - 1.0)
    gain = float(np.clip(gain, 0.25, 4.0))
    Lnew = (Ls - mLs) * gain + mLs
    target = lit_level(lab_b[..., 0], bg_w, level_pct)
    Lnew = Lnew + exposure * (target - mLs)

    # Roll the top off instead of holding highlights at their original value.
    # Pinning them flattened the faces: midtones dropped while speculars stayed
    # put, so all the modelling in between was squashed out.
    if protect_highlights > 0.0:
        k = float(np.clip(protect_highlights, 0.0, 1.0))
        knee = 100.0 - 35.0 * k
        over = np.maximum(Lnew - knee, 0.0)
        Lnew = np.minimum(Lnew, knee) + over / (1.0 + over / max(100.0 - knee, 1e-3))

    # --- black point: align the floor, which is what makes a cutout "sit"
    if blacks > 0.0:
        pLs = float(np.percentile(Ls[sub_w > 0.5], 1.0)) if (sub_w > 0.5).any() else 0.0
        sel = bg_w > 0.05
        pLb = float(np.percentile(lab_b[..., 0][sel], 1.0)) if sel.any() else 0.0
        Lnew = Lnew + blacks * (pLb - pLs) * np.clip(1.0 - (Ls - pLs) / 45.0, 0.0, 1.0)

    Anew = La + colour * (mAb - mAs)
    Bnew = Lb + colour * (mBb - mBs)

    if saturation != 0.0:
        sSub = float(np.sqrt(_weighted_stats(La, sub_w)[1] ** 2 +
                             _weighted_stats(Lb, sub_w)[1] ** 2))
        sBg = float(np.sqrt(_weighted_stats(lab_b[..., 1], bg_w)[1] ** 2 +
                            _weighted_stats(lab_b[..., 2], bg_w)[1] ** 2))
        k = 1.0 + saturation * ((sBg / max(sSub, 1e-6)) - 1.0)
        k = float(np.clip(k, 0.2, 3.0))
        Anew = Anew * k
        Bnew = Bnew * k

    out = lab_to_lin(np.stack([np.clip(Lnew, 0.0, 100.0), Anew, Bnew], axis=-1))
    return np.clip(out, 0.0, None)


def report(obj_lin, alpha, bg_lin, near=None, bg_lab=None):
    """Numbers for the UI: how far apart the two images currently are."""
    sub_w = np.clip(alpha, 0.0, 1.0)
    bg_w = plate_weight(alpha, near)
    ls = lin_to_lab(obj_lin)
    lb = bg_lab if bg_lab is not None else lin_to_lab(bg_lin)
    mLs, sLs = _weighted_stats(ls[..., 0], sub_w)
    mLb, sLb = _weighted_stats(lb[..., 0], bg_w)
    mAs, _ = _weighted_stats(ls[..., 1], sub_w)
    mAb, _ = _weighted_stats(lb[..., 1], bg_w)
    mBs, _ = _weighted_stats(ls[..., 2], sub_w)
    mBb, _ = _weighted_stats(lb[..., 2], bg_w)
    return {"subject_L": round(mLs, 1), "plate_L": round(mLb, 1),
            "subject_contrast": round(sLs, 1), "plate_contrast": round(sLb, 1),
            "subject_ab": [round(mAs, 1), round(mBs, 1)],
            "plate_ab": [round(mAb, 1), round(mBb, 1)]}
