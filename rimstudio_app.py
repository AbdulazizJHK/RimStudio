"""
RimStudio - standalone compositing tool with a live preview.

    python rimstudio_app.py [subject.png] [background.png]

Opens a local page at http://127.0.0.1:8765. Drop in a cut-out subject and a
background, drag the sliders and watch it update, then Apply to write a
full-resolution PNG that Photoshop can place.

Preview renders at a reduced size and the engine scales every radius by image
width, so what you see is what the full-size render gives - just faster. That is
the whole point: the rim strength and the grade match interact, and no sensible
defaults survive contact with a new plate. You need to see it move.

Nothing leaves the machine; the server binds to localhost only.
"""
import io
import json
import os
import sys
import base64
import threading
import secrets
import socket
import subprocess
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rim_engine

PORT = 8765
PREVIEW_W = 780
# Set by main(). Binding to the LAN is opt-in because /load reads any path on
# this PC and /apply writes files - fine on your own Wi-Fi, not on a cafe's.
HOST = "127.0.0.1"
TOKEN = ""


def lan_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"

STATE = {
    "sub_full": None,   # HxWx4 float 0-1
    "bg_full": None,    # HxWx3 float 0-1
    "sub_prev": None,
    "bg_prev": None,
    "sub_path": None,
    "bg_path": None,
    "out_path": None,
    "pre_prev": None,
    "pre_full": None,
    "sub_draft": None,
    "bg_draft": None,
    "pre_draft": None,
}


def _to_float(img, mode):
    return np.asarray(img.convert(mode), dtype=np.float32) / 255.0


def load_pair(sub_path, bg_path):
    sub = Image.open(sub_path).convert("RGBA")
    W, H = sub.size
    bg = Image.open(bg_path).convert("RGB")
    if bg.size != (W, H):
        # cover-fit rather than squash - a squashed plate ruins the colour match
        s = max(W / bg.width, H / bg.height)
        bg = bg.resize((max(1, int(bg.width * s)), max(1, int(bg.height * s))), Image.LANCZOS)
        x = (bg.width - W) // 2
        y = (bg.height - H) // 2
        bg = bg.crop((x, y, x + W, y + H))

    pw = min(PREVIEW_W, W)
    ph = max(1, int(H * pw / W))
    STATE["sub_full"] = _to_float(sub, "RGBA")
    STATE["bg_full"] = _to_float(bg, "RGB")
    STATE["sub_prev"] = _to_float(sub.resize((pw, ph), Image.LANCZOS), "RGBA")
    STATE["bg_prev"] = _to_float(bg.resize((pw, ph), Image.LANCZOS), "RGB")
    dw = max(200, pw // 2)
    dh = max(1, int(H * dw / W))
    STATE["sub_draft"] = _to_float(sub.resize((dw, dh), Image.LANCZOS), "RGBA")
    STATE["bg_draft"] = _to_float(bg.resize((dw, dh), Image.LANCZOS), "RGB")
    STATE["pre_draft"] = rim_engine.prepare(STATE["sub_draft"], STATE["bg_draft"])
    STATE["pre_prev"] = rim_engine.prepare(STATE["sub_prev"], STATE["bg_prev"])
    # Measure the focus ratio ONCE on the full-size pair and share it with every
    # preview tier, so what the sliders show is what the final render does.
    try:
        full_pre = rim_engine.prepare(STATE["sub_full"], STATE["bg_full"])
        # measure the two base sharpness figures ONCE - the ratio and the
        # solver both need them, and each is a full pass over a 2400px image
        s_sub = rim_engine._sharp_inside(full_pre["obj_lin"], full_pre["alpha"])
        s_bg = rim_engine.sharpness(full_pre["bg_lin"], full_pre["alpha"], False)
        ratio = rim_engine.sharp_ratio(full_pre["obj_lin"], full_pre["alpha"],
                                       full_pre["bg_lin"], s_sub, s_bg)
        fpx = rim_engine.solve_focus_px(full_pre["obj_lin"], full_pre["alpha"],
                                        full_pre["bg_lin"], s_sub=s_sub, s_bg=s_bg)
        full_pre["focus_ratio"] = ratio
        full_pre["focus_px"] = fpx
        for tier in ("pre_draft", "pre_prev"):
            STATE[tier]["focus_ratio"] = ratio
            STATE[tier]["focus_px"] = fpx
        STATE["pre_full"] = full_pre
    except Exception:
        STATE["pre_full"] = None
    STATE["sub_path"] = sub_path
    STATE["bg_path"] = bg_path
    return {"w": W, "h": H, "preview_w": pw, "preview_h": ph,
            "subject": os.path.basename(sub_path), "background": os.path.basename(bg_path),
            "subject_path": sub_path, "bg_path": bg_path,
            "stats": rim_engine.stats(STATE["sub_prev"], STATE["bg_prev"])}


def save_upload(data_url, name_hint):
    """Phones cannot type a Windows path, so the browser sends the picked photo
    as a data URL and it lands in a temp folder on this PC."""
    head, _, b64 = data_url.partition(",")
    ext = ".png" if "png" in head else (".jpg" if ("jpeg" in head or "jpg" in head) else ".png")
    folder = os.path.join(os.path.expanduser("~"), "RimStudio uploads")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    safe = "".join(c for c in (name_hint or "upload") if c.isalnum() or c in "._- ")[:60] or "upload"
    path = os.path.join(folder, safe if safe.lower().endswith((".png", ".jpg", ".jpeg")) else safe + ext)
    with open(path, "wb") as f:
        f.write(base64.b64decode(b64))
    return path


def composite(sub_rgba, bg_rgb, params, graded=True, pre=None):
    if graded:
        lit = rim_engine.relight(sub_rgba, bg_rgb, params, pre=pre)
    else:
        lit = sub_rgba
    plate = bg_rgb
    if graded:
        sh = rim_engine.shadow_for(sub_rgba, params)
        if sh is not None:
            plate = plate * (1.0 - sh)[..., None]   # shadow goes UNDER the subject
    a = lit[..., 3:4]
    flat = np.clip(plate * (1 - a) + lit[..., :3] * a, 0, 1)
    if graded:
        # Glow goes on LAST, over the finished frame. Blooming the plate alone
        # left the halo stopping dead at the cut-out edge, which draws the very
        # line the tool exists to hide. A lens does not know where the matte is:
        # light from the background spills onto the subject and the subject's
        # own highlights spill back out, and that two-way bleed across the
        # boundary is most of what makes a comp sit in its plate.
        flat = rim_engine.bloom(flat, params, alpha=lit[..., 3])
    return flat


SETTINGS_FILE = os.path.join(os.path.expanduser("~"), "RimStudio_settings.txt")
PRESET_FILE = os.path.join(os.path.expanduser("~"), "RimStudio presets.json")

# Starting points, not house style - each is a setting that was actually
# validated on a real plate during development, so they are somewhere sane to
# begin rather than invented numbers.
BUILTIN_PRESETS = {
    "Night plate": {
        "m_exposure": 0.87, "m_contrast": 0.99, "m_colour": 0.75, "m_blacks": 0.8,
        "m_sat": 0.35, "hi_protect": 0.6, "defringe": 0.7, "grain": 0.8, "focus": 0.0,
        "reach": 80, "rim_reach": 22, "soft_w": 34, "core_w": 9, "rim": 1.35,
        "core": 1.9, "wrap": 0.55, "sat": 1.45, "threshold": 0.213,
        "shadow": 0.0, "sh_lean": 0, "sh_squash": 0.28, "sh_soft": 26, "sh_contact": 0.6},
    "Subtle integrate": {
        "m_exposure": 0.4, "m_contrast": 0.5, "m_colour": 0.5, "m_blacks": 0.6,
        "m_sat": 0.25, "hi_protect": 0.6, "defringe": 0.6, "grain": 0.8, "focus": 0.3,
        "reach": 200, "rim_reach": 60, "soft_w": 30, "core_w": 8, "rim": 0.8,
        "core": 1.0, "wrap": 0.4, "sat": 1.25, "threshold": 0.15,
        "shadow": 0.0, "sh_lean": 0, "sh_squash": 0.28, "sh_soft": 26, "sh_contact": 0.6},
    "Rim only (no grade)": {
        "m_exposure": 0.0, "m_contrast": 0.0, "m_colour": 0.0, "m_blacks": 0.0,
        "m_sat": 0.0, "hi_protect": 0.6, "defringe": 0.5, "grain": 0.0, "focus": 0.0,
        "reach": 260, "rim_reach": 70, "soft_w": 34, "core_w": 9, "rim": 1.35,
        "core": 1.9, "wrap": 0.55, "sat": 1.45, "threshold": 0.1,
        "shadow": 0.0, "sh_lean": 0, "sh_squash": 0.28, "sh_soft": 26, "sh_contact": 0.6},
}


def load_presets():
    """User presets merged over the built-ins; a user preset of the same name wins."""
    out = dict((k, dict(v)) for k, v in BUILTIN_PRESETS.items())
    try:
        if os.path.exists(PRESET_FILE):
            with open(PRESET_FILE, "r") as f:
                user = json.load(f)
            if isinstance(user, dict):
                for k, v in user.items():
                    if isinstance(v, dict):
                        out[str(k)] = dict(v)
    except Exception:
        pass          # a corrupt preset file must never stop the tool opening
    return out


def _user_presets():
    try:
        if os.path.exists(PRESET_FILE):
            with open(PRESET_FILE, "r") as f:
                d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        pass
    return {}


def save_preset(name, params):
    name = str(name).strip()
    if not name:
        return {"error": "give it a name"}
    d = _user_presets()
    d[name] = dict((k, float(v)) for k, v in params.items() if not str(k).startswith("_"))
    try:
        with open(PRESET_FILE, "w") as f:
            json.dump(d, f, indent=2, sort_keys=True)
    except Exception as e:
        return {"error": "could not write presets: %s" % e}
    return {"msg": 'saved preset "%s"' % name, "name": name}


def delete_preset(name):
    d = _user_presets()
    if name not in d:
        # built-ins have no file entry to remove
        if name in BUILTIN_PRESETS:
            return {"error": '"%s" is a built-in starting point, not removable' % name}
        return {"error": '"%s" not found' % name}
    d.pop(name)
    try:
        with open(PRESET_FILE, "w") as f:
            json.dump(d, f, indent=2, sort_keys=True)
    except Exception as e:
        return {"error": "could not write presets: %s" % e}
    return {"msg": 'deleted "%s"' % name}


def save_settings(params):
    """Write what the app is using so RimStudio.jsx can apply the same look.

    Plain key=value rather than JSON because ExtendScript has no JSON parser in
    older Photoshop builds and splitting on ';' always works.
    """
    try:
        pairs = []
        for k, v in sorted(params.items()):
            if not str(k).startswith("_"):
                pairs.append("%s=%s" % (k, v))
        with open(SETTINGS_FILE, "w") as f:
            f.write(";".join(pairs))
    except Exception:
        pass


def render_full(params):
    save_settings(params)
    """Full-resolution render. Returns the files written: the relit subject and,
    if switched on, the ground shadow as its own layer (it belongs UNDER the
    subject, so it cannot be baked into the same image)."""
    if STATE["pre_full"] is None:
        STATE["pre_full"] = rim_engine.prepare(STATE["sub_full"], STATE["bg_full"])
    lit = rim_engine.relight(STATE["sub_full"], STATE["bg_full"], params,
                             pre=STATE["pre_full"])
    stem = os.path.splitext(STATE["sub_path"])[0]
    out = stem + " - relit.png"
    Image.fromarray((lit * 255).astype(np.uint8), mode="RGBA").save(out)
    STATE["out_path"] = out

    shadow_path = None
    sh = rim_engine.shadow_for(STATE["sub_full"], params)
    if sh is not None:
        rgba = np.zeros(sh.shape + (4,), np.uint8)      # black, alpha = shadow
        rgba[..., 3] = (np.clip(sh, 0, 1) * 255).astype(np.uint8)
        shadow_path = stem + " - shadow.png"
        Image.fromarray(rgba, mode="RGBA").save(shadow_path)

    # The glow is measured off the FINISHED frame - subject already in place -
    # so it bleeds across the cut-out edge in both directions instead of
    # stopping at it. It is never baked in: it goes over as its own Screen
    # layer, which is also how it can be dialled back later without re-running
    # the tool.
    glow_path = None
    flat, lit_a = _flatten(params)
    halo = rim_engine.halo_layer(flat, params, alpha=lit_a)
    if halo is not None:
        glow_path = stem + " - glow.png"
        Image.fromarray((halo * 255).astype(np.uint8), mode="RGB").save(glow_path)
    return {"relit": out, "shadow": shadow_path, "glow": glow_path}


def _flatten(params):
    """The full-res composite as Photoshop will show it once the relit subject
    and shadow are placed - what the glow pass has to measure. Returns it with
    the relit matte, which the outward spill needs to know which side is which."""
    lit = rim_engine.relight(STATE["sub_full"], STATE["bg_full"], params,
                             pre=STATE["pre_full"])
    plate = STATE["bg_full"]
    sh = rim_engine.shadow_for(STATE["sub_full"], params)
    if sh is not None:
        plate = plate * (1.0 - sh)[..., None]
    a = lit[..., 3:4]
    return np.clip(plate * (1 - a) + lit[..., :3] * a, 0, 1), lit[..., 3]


def _no_console():
    """Keyword args that stop Windows flashing a console for each PowerShell call.

    Every Photoshop round trip shells out, so without this a black window pops up
    and vanishes on every pull, send and cut-out - and it steals focus from the
    panel as it goes, which is what makes the UI look like it is flickering.
    CREATE_NO_WINDOW alone is not always enough when a console host is inherited,
    so the STARTUPINFO hide flag goes on as well.
    """
    kw = {}
    if os.name != "nt":
        return kw
    kw["creationflags"] = 0x08000000            # CREATE_NO_WINDOW
    try:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0                      # SW_HIDE
        kw["startupinfo"] = si
    except Exception:
        pass
    return kw


def run_jsx(script, timeout=300):
    """Run ExtendScript in the running Photoshop and return its last expression."""
    ps = ("$ps = New-Object -ComObject Photoshop.Application; "
          "$ps.DoJavaScript(@'\n" + script + "\n'@)")
    r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                       capture_output=True, text=True, timeout=timeout,
                       **_no_console())
    return ((r.stdout or "") + (r.stderr or "")).strip()


def cutout_in_photoshop():
    """Run Photoshop's Select Subject on the active layer, then pull the result.

    Everything else here assumes a cut-out with real transparency, which was the
    one prerequisite the tool could not satisfy itself - yet Photoshop does it in
    one call. The original layer is HIDDEN rather than deleted: it is the user's
    artwork, and hiding also keeps it out of the plate, which would otherwise
    contain a second copy of the subject.
    """
    script = """
if (!app.documents.length) { "NO DOC" } else {
  var doc = app.activeDocument;
  // Clear any cut-out from a previous run first. Without this every press
  // leaves another layer with the SAME name behind, and after three goes the
  // document has three "RimStudio Subject" layers and the plate export has to
  // guess which one is live.
  for (var q = doc.artLayers.length - 1; q >= 0; q--) {
    var old = doc.artLayers[q];
    if (old.name === "RimStudio Subject" && old !== doc.activeLayer) {
      try { if (old.allLocked) old.allLocked = false; } catch (eL) {}
      try { if (old.grouped) old.grouped = false; } catch (eG) {}
      try { old.remove(); } catch (eR) {}
    }
  }
  var src = doc.activeLayer;
  if (src.typename === "LayerSet") { "IS GROUP" } else {
    if (src.isBackgroundLayer) src.isBackgroundLayer = false;
    doc.activeLayer = src;
    var d = new ActionDescriptor();
    d.putBoolean(stringIDToTypeID("sampleAllLayers"), false);
    executeAction(stringIDToTypeID("autoCutout"), d, DialogModes.NO);
    var b = doc.selection.bounds;   // throws if Select Subject found nothing
    executeAction(stringIDToTypeID("copyToLayer"), undefined, DialogModes.NO);
    var cut = doc.activeLayer;
    cut.name = "RimStudio Subject";
    src.visible = false;
    doc.activeLayer = cut;
    "OK|" + src.name;
  }
}
"""
    try:
        out = run_jsx(script)
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}
    if "NO DOC" in out:
        return {"error": "Photoshop has no document open."}
    if "IS GROUP" in out:
        return {"error": "Select a pixel layer, not a group."}
    if not out.startswith("OK|"):
        return {"error": "Select Subject failed: " + (out or "(no response - busy?)")}
    res = pull_from_photoshop()
    if not res.get("error"):
        res["msg"] = 'cut "%s" out and pulled it' % out.split("|", 1)[1].strip()
    return res


def pull_from_photoshop():
    """Export the active document's layers so the app can work on them.

    The ACTIVE layer is taken as the subject and everything else visible becomes
    the plate, which matches how you already have the file set up in Photoshop -
    no exporting two PNGs by hand.
    """
    folder = os.path.join(os.path.expanduser("~"), "RimStudio pulled")
    if not os.path.isdir(folder):
        os.makedirs(folder)
    sub = os.path.join(folder, "subject.png").replace("\\", "/")
    bg = os.path.join(folder, "plate.png").replace("\\", "/")
    generated = ["Relit Subject", "RimStudio Relit", "RimStudio Shadow",
                 "RimStudio Glow", "RimStudio Plate Glow",
                 "Rim Colour", "Rim Glow", "Rim Core", "Rim Light",
                 "Light Wrap", "Seat", "Photo (master)"]
    script = """
var doc = app.activeDocument;
var subjName = doc.activeLayer.name;
var GEN = [%s];
function isGen(n){ for (var i=0;i<GEN.length;i++) if (n===GEN[i]) return true; return false; }
function dump(keepSubject, path, flatten) {
    var d = doc.duplicate(doc.name + " _rs", false);
    app.activeDocument = d;
    var guard = 0, skip = {};
    while (guard++ < 80) {
        var v = null;
        for (var i = 0; i < d.artLayers.length; i++) {
            var nm = d.artLayers[i].name;
            var keep = skip[nm] ? true : (keepSubject ? (nm === subjName)
                                                      : (nm !== subjName && !isGen(nm)));
            if (!keep) { v = d.artLayers[i]; break; }
        }
        if (!v) { for (var k = 0; k < d.layerSets.length; k++)
                    if (!skip["S:" + d.layerSets[k].name]) { v = d.layerSets[k]; break; } }
        if (!v) break;
        try { if (v.allLocked) v.allLocked = false; } catch (e) {}
        // ONLY when it is actually clipped. Setting grouped=false on a layer
        // that is not clipped fires "Release Clipping Mask", which Photoshop
        // then reports as unavailable in a MODAL dialog - and a modal blocks
        // every further scripted call until someone clicks OK.
        try { if (v.grouped) v.grouped = false; } catch (e) {}
        try { v.remove(); } catch (e2) { skip[(v.typename==="LayerSet"?"S:":"") + v.name] = 1;
                                         try { v.visible = false; } catch (e3) {} }
    }
    for (var j = 0; j < d.artLayers.length; j++) {
        var L = d.artLayers[j];
        try { if (L.grouped) L.grouped = false; } catch (e) {}
        try { if (L.allLocked) L.allLocked = false; } catch (e) {}
        L.visible = true; L.opacity = 100; L.blendMode = BlendMode.NORMAL;
    }
    if (flatten) d.flatten();
    // The engine's maths - sRGB<->linear, Lab, the whole grade - assumes sRGB.
    // An Adobe RGB or ProPhoto document would hand over numbers that mean
    // something else entirely and the colour match would be quietly wrong, so
    // convert rather than merely warn. Done on the DUPLICATE, never the user's
    // document.
    try { d.convertProfile("sRGB IEC61966-2.1", Intent.RELATIVECOLORIMETRIC, true, false); }
    catch (eP) {}
    var po = new PNGSaveOptions();
    d.saveAs(new File(path), po, true, Extension.LOWERCASE);
    d.close(SaveOptions.DONOTSAVECHANGES);
    app.activeDocument = doc;
}
if (!app.documents.length) { "NO DOC" } else {
  var prof = "";
  try { prof = doc.colorProfileName || ""; } catch (eProf) { prof = ""; }
  dump(true,  "%s", false);
  dump(false, "%s", true);
  "OK|" + subjName + "|" + prof;
}
""" % (",".join('"%s"' % g for g in generated), sub, bg)
    out = run_jsx(script)
    if "NO DOC" in out:
        return {"error": "Photoshop has no document open."}
    if not out.startswith("OK|"):
        return {"error": "Photoshop said: " + (out or "(nothing - is it busy?)")}
    parts = out.split("|")
    name = parts[1].strip() if len(parts) > 1 else "?"
    profile = parts[2].strip() if len(parts) > 2 else ""
    if not (os.path.exists(sub) and os.path.exists(bg)):
        return {"error": "Photoshop reported success but the files are missing."}
    info = load_pair(sub, bg)
    info["msg"] = 'pulled "%s" as the subject' % name
    info["profile"] = profile
    if profile and "srgb" not in profile.lower():
        info["msg"] += "\nconverted from %s to sRGB" % profile
    return info


def send_to_photoshop(paths):
    """Place the finished layers into the open Photoshop document.

    Driven through PowerShell + COM rather than pywin32 so there is no extra
    dependency to install - the same bridge already proven for RimStudio.jsx.
    """
    jsx = []
    jsx.append('if (!app.documents.length) { "NO DOC" } else {')
    jsx.append('var doc = app.activeDocument; var added = [];')
    # placed bottom-up: each new layer goes to the TOP, so the first one written
    # ends up underneath. Shadow under the subject, and the glow over BOTH -
    # it is a lens effect on the finished frame, so it must not be trapped
    # beneath the cut-out or its halo stops at the edge.
    for key, name, blend in (("shadow", "RimStudio Shadow", None),
                             ("relit", "RimStudio Relit", None),
                             ("glow", "RimStudio Glow", "SCREEN")):
        p = paths.get(key)
        if not p:
            continue
        jsx.append('try {')
        jsx.append('  var f = new File("%s");' % p.replace("\\", "/"))
        jsx.append('  var d = app.open(f); app.activeDocument = d;')
        # The engine writes sRGB. Say so explicitly, then Photoshop CONVERTS the
        # pixels when the layer lands in a document with another working space
        # instead of reinterpreting the numbers and shifting every colour.
        jsx.append('  try { d.colorProfileType = ColorProfileType.CUSTOM;')
        jsx.append('        d.colorProfileName = "sRGB IEC61966-2.1"; } catch (eA) {}')
        jsx.append('  d.artLayers[0].duplicate(doc, ElementPlacement.PLACEATBEGINNING);')
        jsx.append('  d.close(SaveOptions.DONOTSAVECHANGES); app.activeDocument = doc;')
        jsx.append('  doc.artLayers[0].name = "%s"; added.push("%s");' % (name, name))
        if blend:
            jsx.append('  doc.artLayers[0].blendMode = BlendMode.%s;' % blend)
        jsx.append('} catch (e) { added.push("FAILED %s: " + e); }' % name)
    jsx.append('added.join(", "); }')
    script = "\n".join(jsx)

    ps = ("$ps = New-Object -ComObject Photoshop.Application; "
          "$ps.DoJavaScript(@'\n" + script + "\n'@)")
    try:
        r = subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                           capture_output=True, text=True, timeout=180,
                           **_no_console())
        out = (r.stdout or "").strip() or (r.stderr or "").strip()
        if "NO DOC" in out:
            return {"error": "Photoshop has no document open - open one first."}
        if not out:
            return {"error": "Photoshop did not respond. Is it running and idle?"}
        return {"msg": "placed in Photoshop: " + out}
    except subprocess.TimeoutExpired:
        return {"error": "Photoshop was busy for 3 minutes - it may be mid-operation."}
    except Exception as e:
        return {"error": "%s: %s" % (type(e).__name__, e)}


def png_bytes(arr, quality=None):
    im = Image.fromarray((arr * 255).astype(np.uint8))
    buf = io.BytesIO()
    if quality:
        im.save(buf, format="JPEG", quality=quality)
    else:
        im.save(buf, format="PNG")
    return buf.getvalue()


PAGE = r"""<!doctype html><html><head><meta charset="utf-8"><title>RimStudio</title>
<style>
:root{--bg:#141418;--panel:#1d1d23;--line:#2e2e37;--ink:#e9e9ef;--dim:#9a9aa8;--acc:#8a5cf0}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 system-ui,Segoe UI,sans-serif;display:flex;height:100vh}
#side{width:330px;flex:none;background:var(--panel);border-right:1px solid var(--line);overflow-y:auto;padding:14px}
#main{flex:1;display:flex;align-items:center;justify-content:center;position:relative;padding:16px}
/* width:100% not max-width. The draft frame is half the pixels of the settled
   one, and with max-width each rendered at its own intrinsic size, so the
   preview visibly shrank and grew again on every slider move. Both share the
   same aspect ratio, so pinning the width makes them occupy an identical box. */
#view{width:100%;height:auto;max-height:100%;object-fit:contain;
      box-shadow:0 8px 40px #0009;border-radius:4px}
h1{font-size:14px;margin:0 0 12px;letter-spacing:.4px}
h2{font-size:11px;text-transform:uppercase;letter-spacing:.9px;color:var(--dim);margin:16px 0 8px;border-bottom:1px solid var(--line);padding-bottom:5px}
.row{display:flex;align-items:center;gap:8px;margin:7px 0}
.row label{flex:1;color:var(--dim)}
.row input[type=range]{flex:1.5;accent-color:var(--acc)}
.row .val{width:42px;text-align:right;font-variant-numeric:tabular-nums;color:var(--ink)}
button{background:var(--acc);color:#fff;border:0;border-radius:5px;padding:9px 12px;font:inherit;cursor:pointer;width:100%;margin-top:8px}
button.sec{background:#33333d}
input[type=text]{width:100%;background:#111;border:1px solid var(--line);color:var(--ink);border-radius:4px;padding:6px;font:12px monospace}
#msg{color:var(--dim);margin-top:10px;white-space:pre-wrap;font-size:12px}
#stats{font-size:11px;color:var(--dim);white-space:pre-wrap;font-family:monospace;margin-top:6px}
#badge{position:absolute;top:20px;left:20px;background:#000a;padding:4px 9px;border-radius:4px;font-size:11px;color:#fff}
.pick{display:block;margin:6px 0;padding:11px;background:#26262f;border:1px dashed var(--line);border-radius:6px;text-align:center;color:var(--dim)}
.pick input{display:none}
/* Phone layout: preview pinned to the top, controls scroll underneath, and
   touch-sized sliders. Below 900px the desktop sidebar is unusable. */
@media (max-width:900px){
  body{flex-direction:column-reverse;height:auto;min-height:100vh}
  #side{width:100%;border-right:0;border-top:1px solid var(--line);padding:12px 12px 40px}
  #main{padding:8px;min-height:44vh;position:sticky;top:0;background:var(--bg);z-index:5}
  .row input[type=range]{height:32px}
  .row label{font-size:14px}
  button{padding:14px;font-size:15px}
  input[type=text]{font-size:16px;padding:10px}
}
</style></head><body>
<div id="side">
  <h1>RimStudio</h1>
  <h2>Images</h2>
  <div class="row"><label>Subject</label></div>
  <input type="text" id="p_sub" placeholder="C:\path\subject.png (RGBA cut-out)">
  <div class="row"><label>Background</label></div>
  <input type="text" id="p_bg" placeholder="C:\path\background.jpg">
  <label class="pick">Pick subject from this device<br><small>PNG with transparency</small>
    <input type="file" id="f_sub" accept="image/*" onchange="upload(this,'sub')"></label>
  <label class="pick">Pick background / take a photo
    <input type="file" id="f_bg" accept="image/*" onchange="upload(this,'bg')"></label>
  <button class="sec" onclick="loadPair()">Load</button>
  <button onclick="pull()">Pull from Photoshop</button>
  <div id="stats"></div>

  <h2>Match to the plate</h2>
  <button onclick="auto()">Auto match</button>
  <div id="auto_note"></div>
  <div id="g_match"></div>

  <h2>Light from the background</h2>
  <div id="g_light"></div>

  <h2>Ground shadow</h2>
  <div id="g_shadow"></div>

  <button onclick="apply()">Apply - write full-resolution PNG</button>
  <button onclick="toPS()">Send straight to Photoshop</button>
  <button class="sec" onmousedown="showOrig(1)" onmouseup="showOrig(0)" onmouseleave="showOrig(0)">Hold to compare</button>
  <div id="msg"></div>
</div>
<div id="main"><img id="view"><div id="badge" style="display:none">ORIGINAL</div></div>
<script>
const M=[["m_exposure","Exposure",0,1,.85,.01],["m_contrast","Contrast",0,1,.6,.01],
         ["m_colour","Colour balance",0,1,.6,.01],["m_blacks","Black point",0,1,.7,.01],
         ["m_sat","Saturation",0,1,.3,.01],["hi_protect","Protect highlights",0,1,.6,.01],
         ["defringe","Defringe edge",0,1,.7,.01],["grain","Match grain",0,1,.8,.01],
         ["focus","Match focus",0,1,0,.01]];
const S=[["shadow","Shadow",0,1,0,.01],["sh_lean","Lean",-400,400,0,5],
         ["sh_squash","Squash",.05,1,.28,.01],["sh_soft","Softness",2,120,26,1],
         ["sh_contact","Contact",0,1.5,.6,.05]];
const L=[["glow","Glow",0,2.5,1,.05],["bg_glow","Bloom (whole frame)",0,1,0,.02],
         ["glow_thr","Glow threshold",0,1,.5,.02],["glow_colour","Glow colour",0,3,1.5,.05],
         ["reach","Reach",20,500,260,5],["rim_reach","Rim reach",10,300,70,5],
         ["soft_w","Rim width",4,120,34,1],["core_w","Core width",1,60,9,1],
         ["rim","Rim strength",0,4,1.35,.05],["core","Core strength",0,4,1.9,.05],
         ["wrap","Spill",0,3,.55,.05],["sat","Light saturation",0,3,1.45,.05],
         ["threshold","Emit threshold",0,.6,.1,.01]];
function mk(host,defs){defs.forEach(([k,n,a,b,d,st])=>{
  const r=document.createElement('div');r.className='row';
  r.innerHTML=`<label>${n}</label><input type=range id=s_${k} min=${a} max=${b} step=${st} value=${d}><span class=val id=v_${k}>${d}</span>`;
  host.appendChild(r);
  const s=r.querySelector('input');s.addEventListener('input',()=>{document.getElementById('v_'+k).textContent=s.value;schedule();});});}
mk(document.getElementById('g_match'),M);mk(document.getElementById('g_light'),L);
mk(document.getElementById('g_shadow'),S);
const KEY=new URLSearchParams(location.search).get('k')||'';
function post(path,obj){return fetch(path+(KEY?('?k='+encodeURIComponent(KEY)):''),
  {method:'POST',body:JSON.stringify(obj)});}
function upload(inp,which){const f=inp.files[0]; if(!f)return;
  msg('sending '+f.name+' ...');
  const rd=new FileReader();
  rd.onload=async()=>{
    const r=await post('/upload',{data:rd.result,name:f.name});
    const j=await r.json(); if(j.error){msg(j.error);return;}
    document.getElementById(which==='sub'?'p_sub':'p_bg').value=j.path;
    msg('saved on the PC:\n'+j.path);
    if(document.getElementById('p_sub').value&&document.getElementById('p_bg').value)loadPair();};
  rd.readAsDataURL(f);}
function params(){const p={};M.concat(L).concat(S).forEach(([k])=>p[k]=parseFloat(document.getElementById('s_'+k).value));return p;}
function showStats(j){document.getElementById('stats').textContent=
  'subject L '+j.stats.subject_L+'   plate L '+j.stats.plate_L+
  '\ncontrast  '+j.stats.subject_contrast+' vs '+j.stats.plate_contrast+
  '\ncast      ['+j.stats.subject_ab+'] vs ['+j.stats.plate_ab+']';
  document.getElementById('view').style.aspectRatio=j.preview_w+'/'+j.preview_h;}
async function pull(){msg('asking Photoshop for the active layer...');
  const r=await post('/pull',{}); const j=await r.json();
  if(j.error){msg(j.error);return;}
  document.getElementById('p_sub').value=j.subject_path||'';
  document.getElementById('p_bg').value=j.bg_path||'';
  showStats(j); msg(j.msg+'   '+j.w+'x'+j.h); render(false);}
async function toPS(){msg('rendering and sending to Photoshop...');
  const r=await post('/photoshop',params()); const j=await r.json();
  msg(j.error?j.error:j.msg);}
let busy=false,again=false,settle=null;
function schedule(){clearTimeout(settle);settle=setTimeout(()=>render(false),260);
  if(busy){again=true;return;}render(true);}
async function render(fast){busy=true;
  try{const q=params(); if(fast)q.__fast=1;
      const r=await post('/render',q);
      const j=await r.json(); if(j.img) document.getElementById('view').src='data:image/jpeg;base64,'+j.img;}
  catch(e){msg('preview failed: '+e);}
  busy=false; if(again){again=false;render(true);}}
function msg(t){document.getElementById('msg').textContent=t;}
function setSlider(k,v){const s=document.getElementById('s_'+k); if(!s)return;
  s.value=v; document.getElementById('v_'+k).textContent=s.value;}
async function auto(){msg('measuring both images...');
  const r=await post('/auto',{}); const j=await r.json();
  if(j.error){msg(j.error);return;}
  Object.keys(j).forEach(k=>{if(!k.startsWith('_'))setSlider(k,j[k]);});
  const m=j._measured||{};
  document.getElementById('auto_note').innerHTML=
    '<div id=stats>subject L '+m.subject_L+' -> aiming '+m.aim_L+
    '  (plate lit '+m.plate_lit_L+')\ncontrast '+(m.contrast||[]).join(' vs ')+
    '\ncast shift '+m.cast_shift+'</div>';
  msg('auto match applied - adjust from here'); render(false);}
async function loadPair(){msg('loading...');
  const r=await post('/load',{sub:document.getElementById('p_sub').value.trim(),bg:document.getElementById('p_bg').value.trim()});
  const j=await r.json(); if(j.error){msg(j.error);return;}
  document.getElementById('stats').textContent=
    'subject L '+j.stats.subject_L+'   plate L '+j.stats.plate_L+
    '\ncontrast  '+j.stats.subject_contrast+' vs '+j.stats.plate_contrast+
    '\ncast      ['+j.stats.subject_ab+'] vs ['+j.stats.plate_ab+']';
  // pin the box to the image's aspect so the draft and settled frames - which
  // differ by a rounded pixel - cannot nudge the layout between them
  document.getElementById('view').style.aspectRatio=j.preview_w+'/'+j.preview_h;
  msg(j.subject+'  +  '+j.background+'   '+j.w+'x'+j.h); render(false);}
async function showOrig(on){const b=document.getElementById('badge');
  if(on){b.style.display='block';const r=await post('/render',Object.assign(params(),{__plain:1}));
    const j=await r.json(); document.getElementById('view').src='data:image/jpeg;base64,'+j.img;}
  else{b.style.display='none';render(false);}}
async function apply(){msg('rendering at full resolution...');
  const r=await post('/apply',params());
  const j=await r.json();
  if(j.error){msg(j.error);return;}
  msg('written on the PC:\n'+j.out+'\n\nIn Photoshop: File > Place Embedded.');
  const a=document.createElement('a');
  a.href='/result'+(KEY?('?k='+encodeURIComponent(KEY)):'');
  a.download='relit.png'; a.textContent='Save the result to this device';
  a.style.cssText='display:block;margin-top:10px;color:#b69cff';
  document.getElementById('msg').appendChild(a);}
window.addEventListener('load',()=>{if(document.getElementById('p_sub').value)loadPair();else render(false);});
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        """Only enforced when serving on the LAN. A shared key in the URL keeps
        a curious device on the same Wi-Fi from reading files off this PC."""
        if not TOKEN:
            return True
        q = self.path.split("?", 1)[1] if "?" in self.path else ""
        for part in q.split("&"):
            if part.startswith("k="):
                from urllib.parse import unquote
                return unquote(part[2:]) == TOKEN
        return False

    @property
    def route(self):
        return self.path.split("?", 1)[0]

    def do_GET(self):
        if not self._authed():
            self._send(403, b"forbidden - open the link printed in the console",
                       "text/plain")
            return
        if self.route == "/result":
            p = STATE.get("out_path")
            if not p or not os.path.exists(p):
                self._send(404, b"nothing applied yet", "text/plain")
                return
            with open(p, "rb") as f:
                body = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Disposition", 'attachment; filename="relit.png"')
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.route in ("/", "/index.html"):
            b = PAGE.encode("utf-8")
            if STATE["sub_path"]:
                b = PAGE.replace('id="p_sub" placeholder',
                                 'id="p_sub" value="%s" placeholder' % STATE["sub_path"]) \
                        .replace('id="p_bg" placeholder',
                                 'id="p_bg" value="%s" placeholder' % STATE["bg_path"]).encode("utf-8")
            self._send(200, b, "text/html; charset=utf-8")
        else:
            self._send(404, b"{}")

    def do_POST(self):
        if not self._authed():
            self._send(403, json.dumps({"error": "forbidden"}).encode())
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            data = json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            data = {}
        try:
            if self.route == "/upload":
                p = save_upload(data.get("data", ""), data.get("name", ""))
                self._send(200, json.dumps({"path": p}).encode())
            elif self.route == "/pull":
                self._send(200, json.dumps(pull_from_photoshop()).encode())
            elif self.route == "/photoshop":
                if STATE["sub_full"] is None:
                    self._send(200, json.dumps({"error": "load images first"}).encode())
                    return
                paths = render_full(data)
                self._send(200, json.dumps(send_to_photoshop(paths)).encode())
            elif self.route == "/auto":
                if STATE["pre_prev"] is None:
                    self._send(200, json.dumps({"error": "load images first"}).encode())
                    return
                rec = rim_engine.auto_params(STATE["pre_prev"],
                                             STATE["sub_prev"].shape[1])
                self._send(200, json.dumps(rec).encode())
            elif self.route == "/load":
                info = load_pair(data["sub"], data["bg"])
                self._send(200, json.dumps(info).encode())
            elif self.route == "/render":
                if STATE["sub_prev"] is None:
                    self._send(200, json.dumps({"error": "load images first"}).encode())
                    return
                plain = bool(data.pop("__plain", 0))
                # a smaller grid while a slider is moving, full preview on release
                fast = bool(data.pop("__fast", 0))
                sub = STATE["sub_draft"] if fast else STATE["sub_prev"]
                bg = STATE["bg_draft"] if fast else STATE["bg_prev"]
                pre = STATE["pre_draft"] if fast else STATE["pre_prev"]
                arr = composite(sub, bg, data, graded=not plain, pre=pre)
                self._send(200, json.dumps(
                    {"img": base64.b64encode(png_bytes(arr, quality=88)).decode()}).encode())
            elif self.route == "/apply":
                if STATE["sub_full"] is None:
                    self._send(200, json.dumps({"error": "load images first"}).encode())
                    return
                paths = render_full(data)
                out = paths["relit"] + (("\n" + paths["shadow"]) if paths["shadow"] else "")
                self._send(200, json.dumps({"out": out}).encode())
            else:
                self._send(404, b"{}")
        except Exception as e:
            self._send(200, json.dumps({"error": "%s: %s" % (type(e).__name__, e)}).encode())


def main():
    global HOST, TOKEN
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    flags = [a for a in sys.argv[1:] if a.startswith("--")]

    if "--lan" in flags or "--phone" in flags:
        HOST = "0.0.0.0"
        TOKEN = secrets.token_urlsafe(9)

    if len(args) >= 2:
        try:
            load_pair(args[0], args[1])
            print("loaded %s + %s" % (args[0], args[1]))
        except Exception as e:
            print("could not load: %s" % e)
    srv = HTTPServer((HOST, PORT), H)
    local = "http://127.0.0.1:%d/" % PORT
    print("RimStudio running   (Ctrl+C to stop)")
    print("  on this PC : %s" % local)
    if TOKEN:
        phone = "http://%s:%d/?k=%s" % (lan_ip(), PORT, TOKEN)
        print("  on a phone : %s" % phone)
        print("               same Wi-Fi. Windows Firewall may ask to allow")
        print("               Python on private networks the first time - say yes.")
        print("               The ?k= key is required; a new one is made each run.")
    threading.Timer(0.6, lambda: webbrowser.open(local + (("?k=" + TOKEN) if TOKEN else ""))).start()
    srv.serve_forever()


if __name__ == "__main__":
    main()
