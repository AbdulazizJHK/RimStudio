# RimStudio

Composite a cut-out into a plate, and make it look like it was photographed there.

A Photoshop companion: it pulls your cut-out layer and your background out of the
open document, matches them, and sends the result back as layers. The pixel work
happens in numpy, not in Photoshop, because the one thing that matters here —
gathering the colour a background actually emits onto a subject's edge — cannot be
done with layer maths.

![RimStudio](RimStudio.png)

## What it does

**Light from the background.** The rim, core and spill are sampled from the real
plate, unpremultiplied so the colour is the emitter's own rather than an average
diluted by everything dark around it. A subject standing between a warm lamp and a
blue window gets warm on one side and blue on the other, with no direction to set.

**Grade matching.** Exposure, contrast, colour cast, black point and saturation are
measured against the plate in **Lab**, so they stay independent — in RGB, moving
contrast drags hue with it. Exposure aims the subject a little above the plate's lit
level rather than at its mean; a night plate's mean would erase the subject.

**Glow, both ways.** Halation measured on the finished frame, plus the half that is
usually missing: light thrown *from* the subject back onto the plate. Without it, a
bright subject on a dark plate keeps a razor edge with no shared light across it,
which is most of what reads as "pasted on".

**Focus, grain, defringe, contact shadow.** Each measured rather than guessed — the
focus match solves for the blur radius at which the subject's detail level equals the
plate's, instead of multiplying by a constant.

**Three separate Auto buttons.** Auto match sets the grade. Auto focus and Auto glow
are deliberately separate: both are look decisions, and neither should jump every time
you re-run the main auto over a grade you have settled.

## Install

Windows, Photoshop CC 2019 or newer. Open PowerShell and paste:

```powershell
irm https://raw.githubusercontent.com/AbdulazizJHK/RimStudio/main/install.ps1 | iex
```

Already downloaded the ZIP? Unzip it and **double-click `Install RimStudio.bat`**.

Then restart Photoshop. The tool is at **File > Scripts > RimStudio**.

The installer finds a Python 3.9+ (and offers to install one if there is none),
puts `numpy` and `Pillow` in a private environment of its own so it cannot
disturb any Python you already use, and copies the menu entry into every
Photoshop it finds. That last step is the only one that asks for admin, because
Photoshop's `Presets\Scripts` lives in Program Files.

Undo all of it with `install.ps1 -Uninstall`. Useful switches: `-NoMenu` (skip
Photoshop and the admin prompt), `-NoVenv` (install into your own Python).

<details>
<summary>By hand, or on a machine where you cannot run the installer</summary>

1. Put this folder anywhere — `Documents\Photoshop Scripts` is the usual home.
2. `pip install numpy Pillow` into a Python 3.9+ that has `tkinter`
   (python.org builds do; some rebuilds and most conda ones do not).
3. In Photoshop: `File > Scripts > Browse...` and pick `RimStudio Panel.jsx`.
   For a permanent menu entry, copy `RimStudio.jsx` into
   `C:\Program Files\Adobe\Adobe Photoshop <version>\Presets\Scripts\`
   (elevated copy, then restart Photoshop). It is a stub that runs
   `RimStudio Panel.jsx` from wherever the tool lives, so editing the tool never
   needs another elevated copy.

</details>

<details>
<summary>If it does not start</summary>

- **Nothing happens when you pick it from the menu.** The panel is launched by
  `pythonw.exe`, which has no console to print to. Missing packages now raise a
  message box saying which one — if you get no box at all, run
  `install.ps1` again and read what it says.
- **"Could not find Python".** Run the installer; it records the interpreter it
  built in `%APPDATA%\RimStudio\config.txt`, and the menu entry reads that file.
- **The menu entry is missing after installing.** Photoshop only reads
  `Presets\Scripts` at startup — restart it.
- **A pull takes tens of seconds.** Suspect the document, not the tool: a
  healthy pull on a 2400px two-layer file is about 3.5 seconds.

</details>

## Use

Select your cut-out layer in Photoshop and press **Pull from Photoshop**. The active
layer becomes the subject and everything else visible becomes the plate. No cut-out
yet? **Cut out subject** runs Photoshop's Select Subject and hides the original rather
than deleting it.

Then **Auto match**, adjust, and **Send to Photoshop**. It comes back as up to three
layers — the relit subject, a ground shadow underneath it, and the glow on top as a
Screen layer. Your background is never modified.

`Hold to compare` and the split view sit under the preview. Ctrl+wheel zooms,
Ctrl+Z undoes.

## Standalone

Runs without Photoshop too:

```
python rimstudio_gui.py subject.png background.png     # native window
python rimstudio_app.py subject.png background.png     # localhost web UI
```

The subject needs real transparency. The server binds to localhost only; `--lan`
opts in to the local network.

## Files

| | |
|---|---|
| `rim_engine.py` | the light gather, glow, grain, focus and shadow |
| `rim_grade.py` | plate matching in Lab |
| `rimstudio_app.py` | shared core, Photoshop bridge, web UI |
| `rimstudio_gui.py` | the native window |
| `rimstudio_ui.py` | Canvas-drawn widgets |
| `make_icon.py` | renders the icon by running the engine on a synthetic scene |
| `install.ps1` | the installer: Python, dependencies, Photoshop menu entry |
