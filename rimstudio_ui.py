"""
rimstudio_ui.py - custom Tk widgets for RimStudio.

Tk's stock Scale and Button look like Windows 95 and cannot be restyled far
enough to fix. Everything here is drawn on a Canvas instead, which buys rounded
corners, hover states, a filled slider track and a proper handle - none of which
the built-in widgets can do.

Kept separate from the app so the window file stays about behaviour.
"""
import tkinter as tk

# palette
BG      = "#0f0f13"
PANEL   = "#16161c"
CARD    = "#1c1c24"
LINE    = "#2a2a34"
INK     = "#e8e8ef"
DIM     = "#8b8b99"
FAINT   = "#5a5a68"
ACC     = "#8b5cf6"
ACC_DIM = "#6d43d1"
OK      = "#4ade80"
WARN    = "#fbbf24"

F_TITLE = ("Segoe UI Semibold", 14)
F_H2    = ("Segoe UI Semibold", 9)
F_BODY  = ("Segoe UI", 9)
F_SMALL = ("Segoe UI", 8)
F_MONO  = ("Consolas", 8)


def round_rect(cv, x1, y1, x2, y2, r, **kw):
    """Rounded rectangle as a smoothed polygon - Canvas has no native one."""
    pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2,
           x2 - r, y2, x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
    return cv.create_polygon(pts, smooth=True, **kw)


class Tip(object):
    """Minimal hover tooltip. Icon-only buttons need one or nobody can tell what
    they do; Tk has no built-in, so this is a bare Toplevel."""

    def __init__(self, widget, text, delay=450):
        self.w, self.text, self.delay = widget, text, delay
        self.tip = None
        self.job = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress-1>", self._hide, add="+")

    def _schedule(self, _e=None):
        self._cancel()
        self.job = self.w.after(self.delay, self._show)

    def _cancel(self):
        if self.job:
            try:
                self.w.after_cancel(self.job)
            except Exception:
                pass
            self.job = None

    def _show(self):
        if self.tip or not self.text:
            return
        try:
            x = self.w.winfo_rootx() + self.w.winfo_width() // 2
            y = self.w.winfo_rooty() + self.w.winfo_height() + 6
            self.tip = tk.Toplevel(self.w)
            self.tip.wm_overrideredirect(True)
            self.tip.configure(bg=LINE)
            tk.Label(self.tip, text=self.text, bg=CARD, fg=INK, font=F_SMALL,
                     padx=8, pady=4, bd=0).pack(padx=1, pady=1)
            self.tip.update_idletasks()
            self.tip.wm_geometry("+%d+%d" % (x - self.tip.winfo_width() // 2, y))
        except Exception:
            self.tip = None

    def _hide(self, _e=None):
        self._cancel()
        if self.tip:
            try:
                self.tip.destroy()
            except Exception:
                pass
            self.tip = None


class Button(tk.Canvas):
    def __init__(self, parent, text, command=None, primary=False, width=300,
                 height=34, icon=None, tip=None):
        super().__init__(parent, width=width, height=height, bg=PANEL,
                         highlightthickness=0, bd=0, cursor="hand2")
        self.command = command
        self.primary = primary
        self.w, self.h = width, height
        self.text = text
        self.icon = icon          # "undo" / "redo" - drawn, never a font glyph
        self._enabled = True
        if tip:
            Tip(self, tip)
        self._hover = False
        self._draw(False)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self.bind("<ButtonPress-1>", self._press)
        self.bind("<ButtonRelease-1>", self._release)
        # Without this the button keeps painting at its CONSTRUCTION width even
        # after pack/grid stretches the canvas, so a stretched button draws a
        # short pill inside a wide widget and the column looks ragged.
        self.bind("<Configure>", self._resize)

    def _resize(self, e):
        self.w, self.h = e.width, e.height
        self._draw(self._hover)

    def _enter(self, _e):
        self._hover = True
        self._draw(True)

    def _leave(self, _e):
        self._hover = False
        self._draw(False)

    def _colors(self, hover):
        if not self._enabled:
            return CARD, FAINT
        if self.primary:
            return (ACC if hover else ACC_DIM), "#ffffff"
        return ("#2b2b36" if hover else CARD), INK

    def _draw(self, hover, pressed=False):
        self.delete("all")
        fill, fg = self._colors(hover)
        if pressed:
            fill = ACC_DIM if self.primary else "#232330"
        round_rect(self, 1, 1, self.w - 1, self.h - 1, 8, fill=fill, outline="")
        if self.icon:
            self._draw_icon(fg)
        else:
            self.create_text(self.w / 2, self.h / 2, text=self.text, fill=fg, font=F_BODY)

    def _draw_icon(self, fg):
        """Icons are drawn, never typed.

        Segoe UI has no glyph for the undo/redo arrows - the same gap that made
        the section chevron render as a bare dot - so these are built from
        primitives.
        """
        cx, cy = self.w / 2.0, self.h / 2.0
        if self.icon in ("compare", "split"):
            # a rectangle divided down the middle, one half filled: the picture
            # of before-and-after, at a size where a glyph would be unreadable
            hw, hh = 7.0, 5.5
            self.create_rectangle(cx - hw, cy - hh, cx + hw, cy + hh,
                                  outline=fg, width=1)
            if self.icon == "compare":
                self.create_rectangle(cx - hw + 1, cy - hh + 1, cx - 0.5,
                                      cy + hh - 1, fill=fg, outline="")
            else:
                # split shows the divider itself, with a grab handle on it
                self.create_line(cx, cy - hh - 2, cx, cy + hh + 2, fill=fg)
                self.create_rectangle(cx - 1.5, cy - 2.5, cx + 1.5, cy + 2.5,
                                      fill=fg, outline="")
            return

        cy, r = cy + 1.5, 5.5
        self.create_arc(cx - r, cy - r, cx + r, cy + r,
                        start=10, extent=160, style="arc", outline=fg, width=2)
        if self.icon == "undo":
            tipx = cx - r                       # head at the left end, pointing down
            self.create_polygon(tipx - 3.2, cy - 1.5, tipx + 3.2, cy - 1.5,
                                tipx, cy + 3.8, fill=fg, outline="")
        else:
            tipx = cx + r                       # mirrored for redo
            self.create_polygon(tipx - 3.2, cy - 1.5, tipx + 3.2, cy - 1.5,
                                tipx, cy + 3.8, fill=fg, outline="")

    def _press(self, _e):
        if self._enabled:
            self._draw(True, True)

    def _release(self, _e):
        if self._enabled:
            self._draw(True)
            if self.command:
                self.command()

    def set_active(self, on):
        """Light the button up while its mode is on - a toggle that looks
        identical whether or not it is engaged is not a toggle."""
        on = bool(on)
        if on != self.primary:
            self.primary = on
            self._draw(False)

    def set_enabled(self, on):
        self._enabled = bool(on)
        self.configure(cursor="hand2" if on else "arrow")
        self._draw(False)


class Slider(tk.Canvas):
    """Label and value on one line, full-width track underneath.

    Narrow panel plus 23 controls means a label/track/value all on one row leaves
    the track too short to aim at. Stacking gives the handle real estate.
    """
    H = 42

    def __init__(self, parent, key, label, lo, hi, default, step,
                 on_change=None, width=300):
        super().__init__(parent, width=width, height=self.H, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.key, self.label = key, label
        self.lo, self.hi, self.step = float(lo), float(hi), float(step)
        self.default = float(default)
        self.value = float(default)
        self.on_change = on_change
        self.w = width
        self.pad = 4
        self.ty = 32
        self._hover = False
        self._drag = False
        self.bind("<Configure>", self._resize)
        self.bind("<Button-1>", self._click)
        self.bind("<B1-Motion>", self._click)
        self.bind("<ButtonRelease-1>", self._done)
        self.bind("<Double-Button-1>", self._reset)
        self.bind("<Enter>", self._enter)
        self.bind("<Leave>", self._leave)
        self._draw()

    # ---- geometry
    def _x_of(self, v):
        f = (v - self.lo) / (self.hi - self.lo) if self.hi > self.lo else 0
        return self.pad + f * (self.w - 2 * self.pad)

    def _v_of(self, x):
        f = (x - self.pad) / max(self.w - 2 * self.pad, 1)
        v = self.lo + max(0.0, min(1.0, f)) * (self.hi - self.lo)
        v = round(v / self.step) * self.step
        return max(self.lo, min(self.hi, v))

    def _resize(self, e):
        self.w = e.width
        self._draw()

    # ---- paint
    def _fmt(self):
        v = self.value
        if self.step >= 1:
            return "%d" % round(v)
        return ("%.2f" % v).rstrip("0").rstrip(".") or "0"

    def _draw(self):
        self.delete("all")
        moved = abs(self.value - self.default) > 1e-9
        self.create_text(2, 11, text=self.label, anchor="w",
                         fill=INK if (self._hover or moved) else DIM, font=F_SMALL)
        self.create_text(self.w - 2, 11, text=self._fmt(), anchor="e",
                         fill=ACC if moved else DIM, font=F_MONO)
        x = self._x_of(self.value)
        round_rect(self, self.pad, self.ty - 2, self.w - self.pad, self.ty + 2, 2,
                   fill="#23232c", outline="")
        if x > self.pad + 2:
            round_rect(self, self.pad, self.ty - 2, x, self.ty + 2, 2,
                       fill=ACC if (self._hover or self._drag) else ACC_DIM, outline="")
        r = 7 if (self._hover or self._drag) else 5
        self.create_oval(x - r, self.ty - r, x + r, self.ty + r,
                         fill="#ffffff" if self._drag else INK, outline="")

    # ---- events
    def _enter(self, _e):
        self._hover = True
        self.configure(cursor="hand2")
        self._draw()

    def _leave(self, _e):
        self._hover = False
        self._draw()

    def _click(self, e):
        self._drag = True
        v = self._v_of(e.x)
        if v != self.value:
            self.value = v
            self._draw()
            if self.on_change:
                self.on_change(self.key, v, False)
        else:
            self._draw()

    def _done(self, _e):
        self._drag = False
        self._draw()
        if self.on_change:
            self.on_change(self.key, self.value, True)

    def _reset(self, _e):
        self.set(self.default)
        if self.on_change:
            self.on_change(self.key, self.value, True)

    # ---- api
    def get(self):
        return self.value

    def set(self, v):
        self.value = max(self.lo, min(self.hi, float(v)))
        self._draw()


class Section(tk.Frame):
    """Collapsible group. 23 sliders in one column is a wall; this hides the
    two-thirds you are not currently touching."""

    def __init__(self, parent, title, open_=True, width=300):
        super().__init__(parent, bg=PANEL)
        self.open = open_
        self.head = tk.Canvas(self, height=26, bg=PANEL, highlightthickness=0,
                              bd=0, cursor="hand2")
        self.head.pack(fill="x")
        self.body = tk.Frame(self, bg=PANEL)
        if open_:
            self.body.pack(fill="x")
        self.title = title
        self.head.bind("<Button-1>", self.toggle)
        self.head.bind("<Configure>", lambda e: self._draw())
        self._draw()

    def _draw(self):
        self.head.delete("all")
        w = max(self.head.winfo_width(), 10)
        # Draw the chevron rather than typing one: Segoe UI has no glyph for the
        # small triangles, so the character rendered as a bare dot.
        x, y = 6, 13
        if self.open:
            pts = [x, y - 2, x + 8, y - 2, x + 4, y + 3]
        else:
            pts = [x + 1, y - 4, x + 6, y, x + 1, y + 4]
        self.head.create_polygon(pts, fill=FAINT, outline="")
        self.head.create_text(22, y, text=self.title.upper(), anchor="w",
                              fill=DIM, font=F_H2)
        self.head.create_line(0, 25, w, 25, fill=LINE)

    def toggle(self, _e=None):
        self.open = not self.open
        if self.open:
            self.body.pack(fill="x")
        else:
            self.body.pack_forget()
        self._draw()


class Progress(tk.Canvas):
    """Stage progress for the long operations.

    The work has known stages (render, write, hand to Photoshop) so the bar
    reports real progress rather than a fake percentage. A pulse travels along
    the filled portion while a stage is running, because the Photoshop step can
    sit for tens of seconds and a frozen bar reads as a hang.
    """
    H = 40

    def __init__(self, parent, width=300):
        super().__init__(parent, width=width, height=1, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.w = width
        self.active = False
        self.frac = 0.0          # last CONFIRMED milestone
        self._shown = 0.0        # what is drawn: creeps between milestones
        self._ceil = 0.0         # never creep past the next milestone
        self.text = ""
        self.t0 = None
        self._phase = 0.0
        self._job = None
        self.bind("<Configure>", self._resize)

    def _resize(self, e):
        self.w = e.width
        if self.active:
            self._draw()

    def start(self, text=""):
        import time
        self.active = True
        self.frac = self._shown = 0.0
        self._ceil = 0.30
        self.text = text
        self.t0 = time.time()
        self.configure(height=self.H)
        self._tick()

    def step(self, text=None, frac=None, ceiling=None):
        """Record a real milestone. `ceiling` is how far the bar may creep on
        its own before the NEXT milestone lands - it must stay short of it, or
        the bar claims progress that has not happened."""
        if text is not None:
            self.text = text
        if frac is not None:
            self.frac = max(0.0, min(1.0, frac))
            self._shown = max(self._shown, self.frac)
            self._ceil = max(0.0, min(0.97, self.frac + 0.28
                                      if ceiling is None else ceiling))
        if self.active:
            self._draw()

    def stop(self):
        self.active = False
        if self._job:
            try:
                self.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        self.configure(height=1)
        self.delete("all")

    def _tick(self):
        if not self.active:
            return
        self._phase = (self._phase + 0.035) % 1.0
        # Creep between milestones, decelerating. The slow part of a pull is one
        # blocking call into Photoshop that reports nothing, so without this the
        # bar holds at whatever the last milestone was - the highlight slides
        # along a fill that never grows, which reads as "moving but not
        # actually moving". Easing toward a ceiling short of the next milestone
        # keeps it honest: it never arrives on its own.
        if self._ceil > self._shown:          # forward only, never rewind
            self._shown += (self._ceil - self._shown) * 0.012
        self._draw()
        self._job = self.after(55, self._tick)

    def _draw(self):
        import time
        self.delete("all")
        y = 26
        secs = (time.time() - self.t0) if self.t0 else 0
        self.create_text(2, 9, text=self.text or "working...", anchor="w",
                         fill=INK, font=F_SMALL)
        self.create_text(self.w - 2, 9, text="%ds" % int(secs), anchor="e",
                         fill=FAINT, font=F_MONO)
        round_rect(self, 0, y - 3, self.w, y + 3, 3, fill="#23232c", outline="")
        end = max(6.0, self._shown * self.w)
        round_rect(self, 0, y - 3, end, y + 3, 3, fill=ACC_DIM, outline="")
        # travelling highlight so a long stage still looks alive
        pw = max(28.0, end * 0.28)
        px = (self._phase * (end + pw)) - pw
        x1 = max(0.0, px)
        x2 = min(end, px + pw)
        if x2 > x1 + 1:
            round_rect(self, x1, y - 3, x2, y + 3, 3, fill=ACC, outline="")


class Toast(tk.Canvas):
    """Status line that colours itself by outcome instead of a wall of grey."""

    def __init__(self, parent, width=300, height=52):
        super().__init__(parent, width=width, height=height, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.w, self.h = width, height
        self.bind("<Configure>", self._resize)
        self._text, self._kind = "", "info"
        self.configure(height=1)      # start collapsed until there is something to say

    def _resize(self, e):
        self.w, self.h = e.width, e.height
        self._draw()

    def show(self, text, kind="info"):
        self._text, self._kind = str(text or ""), kind
        if not self._text.strip():
            # collapse instead of leaving an empty card taking up space
            self.configure(height=1)
            self.delete("all")
            return
        lines = self._text.count("\n") + 1
        self.configure(height=max(32, 14 + 14 * min(lines, 6)))
        self._draw()

    def _draw(self):
        self.delete("all")
        if not self._text.strip():
            return
        col = {"info": DIM, "ok": OK, "warn": WARN, "err": "#f87171"}.get(self._kind, DIM)
        round_rect(self, 1, 1, self.w - 1, self.h - 1, 6, fill=CARD, outline="")
        self.create_rectangle(1, 1, 3, self.h - 1, fill=col, outline="")
        self.create_text(12, 8, text=self._text, anchor="nw", fill=col,
                         font=F_MONO, width=self.w - 20)


class TitleBar(tk.Canvas):
    """Our own caption strip, for a borderless window.

    Windows' caption cannot be restyled - it is drawn by the shell, in the
    shell's colours - so a dark tool with a light title bar always looks like
    two programs stacked. Removing it means re-implementing the four things it
    did: drag to move, double-click to maximise, the three buttons, and showing
    which window has focus.
    """

    H = 34
    G = 5                                # resize hit zone along its own edges

    def __init__(self, parent, title, on_min, on_max, on_close, icon=None,
                 on_resize_start=None, on_resize_drag=None):
        super().__init__(parent, height=self.H, bg=PANEL,
                         highlightthickness=0, bd=0)
        self.title_text = title
        self.icon = icon                 # a PhotoImage, kept alive by the caller
        self._cbs = (on_min, on_max, on_close)
        # The title bar resizes its OWN edges rather than having grip frames
        # laid over it. A Tk frame cannot be transparent, so an overlaid grip
        # has to be painted some colour - and whatever colour it is, it shows
        # the moment anything underneath changes. Painted PANEL it was
        # invisible at rest and then appeared as a dark square over the close
        # button's red hover fill.
        self._rs, self._rd = on_resize_start, on_resize_drag
        self._resizing = False
        self._hot = None                 # which button the pointer is over
        self._active = True
        self._drag = None

        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Motion>", self._motion)
        self.bind("<Leave>", lambda e: (setattr(self, "_hot", None), self._draw()))
        self.bind("<Button-1>", self._press)
        self.bind("<B1-Motion>", self._move)
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Double-Button-1>", self._double)

    # ---------------------------------------------------------------- paint
    def _boxes(self):
        """Right-to-left: close, maximise, minimise. 46px each, the Windows 11
        metric - muscle memory for where the close button is, is worth more than
        a bespoke size."""
        w = max(self.winfo_width(), 1)
        return [("close", w - 46, w), ("max", w - 92, w - 46),
                ("min", w - 138, w - 92)]

    def _draw(self):
        self.delete("all")
        w = max(self.winfo_width(), 1)
        self.create_rectangle(0, 0, w, self.H, fill=PANEL, outline="")
        self.create_line(0, self.H - 1, w, self.H - 1, fill=LINE)

        x = 11
        if self.icon is not None:
            self.create_image(x, self.H // 2, image=self.icon, anchor="w")
            x += 24
        self.create_text(x, self.H // 2 + 1, text=self.title_text, anchor="w",
                         fill=INK if self._active else FAINT, font=F_H2)

        for name, x0, x1 in self._boxes():
            if self._hot == name:
                self.create_rectangle(x0, 0, x1, self.H - 1,
                                      fill="#e81123" if name == "close" else CARD,
                                      outline="")
            ink = "#ffffff" if (self._hot == "close" and name == "close") \
                else (INK if self._active else FAINT)
            cx, cy = (x0 + x1) // 2, self.H // 2
            if name == "close":
                self.create_line(cx - 5, cy - 5, cx + 5, cy + 5, fill=ink)
                self.create_line(cx - 5, cy + 5, cx + 5, cy - 5, fill=ink)
            elif name == "max":
                self.create_rectangle(cx - 5, cy - 5, cx + 5, cy + 5,
                                      outline=ink)
            else:
                self.create_line(cx - 5, cy, cx + 5, cy, fill=ink)

    def set_active(self, on):
        if on != self._active:
            self._active = on
            self._draw()

    # ---------------------------------------------------------------- input
    def _at(self, x):
        for name, x0, x1 in self._boxes():
            if x0 <= x < x1:
                return name
        return None

    def _zone(self, x, y):
        """Which window edge, if any, the pointer is on. Corners win over edges,
        and edges win over the buttons - the same precedence Windows uses, so
        the very corner of the window is always a resize and never a close."""
        w = max(self.winfo_width(), 1)
        left, right, top = x < self.G, x >= w - self.G, y < self.G
        if top and left:
            return "nw"
        if top and right:
            return "ne"
        if top:
            return "n"
        if left:
            return "w"
        if right:
            return "e"
        return None

    CURSORS = {"n": "sb_v_double_arrow", "w": "sb_h_double_arrow",
               "e": "sb_h_double_arrow", "nw": "size_nw_se", "ne": "size_ne_sw"}

    def _motion(self, e):
        zone = self._zone(e.x, e.y)
        self.configure(cursor=self.CURSORS.get(zone, ""))
        hot = None if zone else self._at(e.x)
        if hot != self._hot:
            self._hot = hot
            self._draw()

    def _press(self, e):
        zone = self._zone(e.x, e.y)
        if zone and self._rs:
            self._resizing = True
            self._rs(e, zone)
            return
        if self._at(e.x):
            return                        # a button press is not a drag
        self._drag = (e.x_root, e.y_root)

    def _move(self, e):
        if self._resizing:
            if self._rd:
                self._rd(e)
            return
        if not self._drag:
            return
        win = self.winfo_toplevel()
        dx, dy = e.x_root - self._drag[0], e.y_root - self._drag[1]
        self._drag = (e.x_root, e.y_root)
        win.geometry("+%d+%d" % (win.winfo_x() + dx, win.winfo_y() + dy))

    def _release(self, e):
        was_resize, self._resizing = self._resizing, False
        was_drag, self._drag = self._drag, None
        if was_resize:
            return
        name = self._at(e.x)
        if name and not was_drag and not self._zone(e.x, e.y):
            {"min": self._cbs[0], "max": self._cbs[1],
             "close": self._cbs[2]}[name]()

    def _double(self, e):
        if not self._at(e.x) and not self._zone(e.x, e.y):
            self._cbs[1]()
