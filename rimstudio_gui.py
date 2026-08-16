"""
RimStudio - native desktop window (no browser).

Launch from Photoshop with "RimStudio Panel.jsx", or run:
    pythonw rimstudio_gui.py [subject.png background.png]

Same engine as the web version; only the shell differs. Widgets are drawn on
Canvas (see rimstudio_ui.py) because Tk's stock Scale and Button cannot be
restyled far enough to look like anything but Windows 95.

Renders run on a worker thread. Tk only redraws safely from the main thread, so
the worker hands finished frames back through a queue that the UI drains on a
timer - touching Tk widgets from the worker crashes intermittently.
"""
import os
import queue
import subprocess
import sys
import threading
import traceback

import numpy as np
from PIL import Image, ImageTk
import tkinter as tk
from tkinter import filedialog

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rim_engine
import rimstudio_app as core          # reuse load/pull/send/render, __main__-guarded
from rimstudio_ui import (Button, Slider, Section, Toast, Progress, TitleBar,
                          round_rect,
                          BG, PANEL, CARD, LINE, INK, DIM, FAINT, ACC,
                          F_TITLE, F_BODY, F_SMALL, F_MONO)

SIDE_W = 344

MATCH = [("m_exposure", "Exposure", 0, 1, .85, .01),
         ("m_contrast", "Contrast", 0, 1, .6, .01),
         ("m_colour", "Colour balance", 0, 1, .6, .01),
         ("m_blacks", "Black point", 0, 1, .7, .01),
         ("m_sat", "Saturation", 0, 1, .3, .01),
         ("hi_protect", "Protect highlights", 0, 1, .6, .01),
         ("defringe", "Defringe edge", 0, 1, .7, .01),
         ("grain", "Match grain", 0, 1, .8, .01),
         ("focus", "Match focus", 0, 1, 0, .01)]
LIGHT = [("glow", "Glow", 0, 2.5, 1.0, .05),
         ("bg_glow", "Bloom (whole frame)", 0, 1, 0, .02),
         ("glow_thr", "Glow threshold", 0, 1, .5, .02),
         ("glow_colour", "Glow colour", 0, 3, 1.5, .05),
         ("reach", "Reach", 20, 500, 260, 5),
         ("rim_reach", "Rim reach", 10, 300, 70, 5),
         ("soft_w", "Rim width", 4, 120, 34, 1),
         ("core_w", "Core width", 1, 60, 9, 1),
         ("rim", "Rim strength", 0, 4, 1.35, .05),
         ("core", "Core strength", 0, 4, 1.9, .05),
         ("wrap", "Spill", 0, 3, .55, .05),
         ("sat", "Light saturation", 0, 3, 1.45, .05),
         ("threshold", "Emit threshold", 0, .6, .1, .01)]
SHADOW = [("shadow", "Shadow", 0, 1, 0, .01),
          ("sh_lean", "Lean", -400, 400, 0, 5),
          ("sh_squash", "Squash", .05, 1, .28, .01),
          ("sh_soft", "Softness", 2, 120, 26, 1),
          ("sh_contact", "Contact", 0, 1.5, .6, .05)]


class App:
    def __init__(self, root):
        self.root = root
        root.title("RimStudio")
        root.configure(bg=BG)
        root.minsize(980, 620)
        self._restore = None
        self._gr = None
        self._icon(root)
        self._centre(1240, 800)
        self._borderless(root)
        root.bind("<Map>", self._remap)
        root.bind("<FocusIn>", lambda e: self._focus(True))
        root.bind("<FocusOut>", lambda e: self._focus(False))

        self.sliders = {}
        self.results = queue.Queue()
        self.busy = False
        self.pending_fast = None
        self.settle_id = None
        self.photo = None
        self.comparing = False
        self.split = None            # None = off, else 0..1 divider position
        self._dragging_split = False
        self._plain_arr = None
        self._img_rect = None

        self._build()
        self._grips(root)          # place() last, so the grips sit on top
        # Re-assert the sidebar width once the layout has actually settled.
        # Bindings alone are not enough: main() builds the whole UI before the
        # window has ever been laid out, so the canvas reports width 1 while the
        # sidebar is filled and the pin lands on nothing. Same shape of bug as
        # the taskbar style - a value applied before the thing it applies to
        # exists - and the same remedy.
        root.after_idle(self._pin_sidebar)
        for _ms in (120, 350, 800):
            root.after(_ms, self._pin_sidebar)
        # After the withdraw/deiconify that the taskbar fix performs, or the
        # window comes back up behind whatever was in front.
        for _ms in (250, 700, 1200):
            root.after(_ms, lambda: self._raise(root))
        self._hist_init()
        self.root.after(50, self._drain)
        self.root.bind("<Configure>", self._maybe_refit)
        self._last_arr = None
        self._last_size = (0, 0)

    # ------------------------------------------------------------- chrome
    def _borderless(self, root):
        """Drop Windows' caption and draw our own.

        `overrideredirect` also drops the window from the TASKBAR, because
        Windows treats a caption-less window as a tool window. Putting
        WS_EX_APPWINDOW back (and clearing WS_EX_TOOLWINDOW) returns it, along
        with Alt-Tab and the icon. It has to be re-applied after the window is
        mapped, hence the `after`.
        """
        root.overrideredirect(True)
        root.after(40, lambda: self._appwindow(root))

    @staticmethod
    def _hwnd(root):
        """The window the SHELL sees, which is NOT `GetParent(winfo_id())`.

        Tk re-parents into a fresh wrapper when overrideredirect is set, so a
        handle taken before that settles points at a wrapper Tk then discards -
        writes to it appear to succeed and change nothing on screen. Walk to the
        true root every time instead of caching one.
        """
        import ctypes
        from ctypes import wintypes
        u = ctypes.windll.user32
        u.GetParent.argtypes = [wintypes.HWND]
        u.GetParent.restype = wintypes.HWND
        h = root.winfo_id()
        for _ in range(8):
            p = u.GetParent(h)
            if not p:
                return h
            h = p
        return h

    def _raise(self, root):
        """Bring the panel to the front when it opens.

        Photoshop launches this, so Photoshop owns the foreground - and Windows
        refuses to let an arbitrary process steal it, which is why the panel
        opened behind. `lift()` and `focus_force()` are not enough on their own;
        they raise the window within Tk's idea of the stack while the shell
        keeps Photoshop in front.

        The way through is to attach this thread's input queue to the current
        foreground thread for the moment of the call. Windows then treats the
        two as one input context and allows the change. Detached again straight
        after, so nothing is left hooked to Photoshop.

        A brief topmost flash backs it up, immediately released - a panel that
        stayed topmost would sit over Photoshop for the whole session, which is
        worse than opening behind it.
        """
        try:
            root.deiconify()
            root.lift()
            root.attributes("-topmost", True)
            root.after(120, lambda: root.attributes("-topmost", False))
        except Exception:
            pass
        try:
            import ctypes
            from ctypes import wintypes
            u = ctypes.windll.user32
            k = ctypes.windll.kernel32
            for fn, args, res in (
                    ("GetForegroundWindow", [], wintypes.HWND),
                    ("SetForegroundWindow", [wintypes.HWND], wintypes.BOOL),
                    ("BringWindowToTop", [wintypes.HWND], wintypes.BOOL),
                    ("SetActiveWindow", [wintypes.HWND], wintypes.HWND)):
                f = getattr(u, fn)
                f.argtypes, f.restype = args, res
            u.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.c_void_p]
            u.GetWindowThreadProcessId.restype = wintypes.DWORD
            u.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                            wintypes.BOOL]

            hwnd = self._hwnd(root)
            fg = u.GetForegroundWindow()
            me = k.GetCurrentThreadId()
            other = u.GetWindowThreadProcessId(fg, None) if fg else 0
            attached = bool(other and other != me
                            and u.AttachThreadInput(other, me, True))
            try:
                u.BringWindowToTop(hwnd)
                u.SetForegroundWindow(hwnd)
                u.SetActiveWindow(hwnd)
            finally:
                if attached:
                    u.AttachThreadInput(other, me, False)
        except Exception:
            pass
        try:
            root.focus_force()
        except Exception:
            pass

    def _round_corners(self, root, radius=10):
        """Rounded corners on the borderless window.

        Windows 11 will do it properly through DWM - antialiased, and the drop
        shadow follows the curve. That is worth trying first and falling back
        only if it fails, because the old way (clipping the window to a region)
        gives hard stair-stepped edges and has to be redone on every resize.
        """
        try:
            import ctypes
            from ctypes import wintypes
            DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND = 33, 2
            pref = ctypes.c_int(DWMWCP_ROUND)
            hr = ctypes.windll.dwmapi.DwmSetWindowAttribute(
                wintypes.HWND(self._hwnd(root)),
                ctypes.c_uint(DWMWA_WINDOW_CORNER_PREFERENCE),
                ctypes.byref(pref), ctypes.sizeof(pref))
            if hr == 0:
                self._corner_mode = "dwm"
                return
        except Exception:
            pass
        self._corner_mode = "region"
        self._region(root, radius)
        root.bind("<Configure>", lambda e: self._region(root, radius), add="+")

    def _region(self, root, radius):
        """Fallback for anything older than Windows 11. The region is in window
        coordinates, so it is wrong the moment the window resizes - hence the
        <Configure> binding that calls this again."""
        try:
            import ctypes
            from ctypes import wintypes
            g, u = ctypes.windll.gdi32, ctypes.windll.user32
            g.CreateRoundRectRgn.restype = wintypes.HANDLE
            u.SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HANDLE,
                                       wintypes.BOOL]
            w, h = root.winfo_width(), root.winfo_height()
            if w < 2 or h < 2:
                return
            rgn = g.CreateRoundRectRgn(0, 0, w + 1, h + 1, radius * 2, radius * 2)
            u.SetWindowRgn(wintypes.HWND(self._hwnd(root)), rgn, True)
        except Exception:
            pass

    def _appwindow(self, root):
        """Put the window back in the taskbar and Alt-Tab.

        ⚠ The argtypes/restype below are not decoration. Left to guess, ctypes
        treats a HWND as a C int, which TRUNCATES the handle on 64-bit Python -
        the call then quietly targets nothing and the styles never change. That
        is exactly what happened here: WS_EX_APPWINDOW read back as NOT set and
        the window had no taskbar button, with no error anywhere.
        """
        try:
            import ctypes
            from ctypes import wintypes
            GWL_EXSTYLE, WS_EX_APPWINDOW, WS_EX_TOOLWINDOW = -20, 0x40000, 0x80
            u = ctypes.windll.user32
            u.GetParent.argtypes = [wintypes.HWND]
            u.GetParent.restype = wintypes.HWND
            name = ("GetWindowLongPtrW" if hasattr(u, "GetWindowLongPtrW")
                    else "GetWindowLongW")
            get, put = getattr(u, name), getattr(u, name.replace("Get", "Set"))
            get.argtypes = [wintypes.HWND, ctypes.c_int]
            get.restype = ctypes.c_ssize_t
            put.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
            put.restype = ctypes.c_ssize_t

            def apply():
                h = self._hwnd(root)
                s = get(h, GWL_EXSTYLE)
                if not (s & WS_EX_APPWINDOW) or (s & WS_EX_TOOLWINDOW):
                    put(h, GWL_EXSTYLE,
                        (s & ~WS_EX_TOOLWINDOW) | WS_EX_APPWINDOW)
                    # the shell only re-reads these on a hide/show cycle
                    root.withdraw()
                    root.after(10, root.deiconify)

            apply()
            # re-parenting and deiconify can each undo it, and neither has a
            # reliable event to hang off, so just re-assert a few times. The
            # corner shape rides along for the same reason - it is set on the
            # same wrapper handle and is lost with it.
            for delay in (150, 400, 900):
                root.after(delay, apply)
                root.after(delay + 10, lambda: self._round_corners(root))
        except Exception:
            pass

    def minimise(self):
        """Tk refuses to iconify an overrideredirect window, so hand the caption
        back for the moment it takes to minimise and take it away again on the
        way back."""
        try:
            self.root.overrideredirect(False)
            self.root.iconify()
        except Exception:
            pass

    def _remap(self, _e=None):
        if self.root.state() == "normal" and not self.root.overrideredirect():
            self.root.overrideredirect(True)
            self._appwindow(self.root)

    def maximise(self):
        """Maximise by hand. `state('zoomed')` on an overrideredirect window
        covers the taskbar, because there is no caption for the shell to
        negotiate the work area with."""
        if self._restore:
            self.root.geometry(self._restore)
            self._restore = None
            return
        self._restore = "%dx%d+%d+%d" % (
            self.root.winfo_width(), self.root.winfo_height(),
            self.root.winfo_x(), self.root.winfo_y())
        try:
            import ctypes, ctypes.wintypes
            r = ctypes.wintypes.RECT()
            ctypes.windll.user32.SystemParametersInfoW(0x0030, 0,
                                                       ctypes.byref(r), 0)
            self.root.geometry("%dx%d+%d+%d" % (r.right - r.left,
                                                r.bottom - r.top, r.left, r.top))
        except Exception:
            self.root.geometry("%dx%d+0+0" % (self.root.winfo_screenwidth(),
                                              self.root.winfo_screenheight()))

    def _grips(self, root):
        """Invisible 5px strips for resizing, since there is no frame left to
        grab. Thinner than this and the window is genuinely hard to resize;
        Windows' own hit area is about the same.

        ⚠ Each strip MUST be painted the colour of whatever it lies on top of.
        A Tk frame cannot be transparent, so a single shared colour shows up as
        a stripe down one side and a square in the corner - which is exactly
        what it did: BG (#0f0f13) grips over the PANEL (#16161c) title bar.
        Hence the split: the title bar spans the full width, the sidebar is
        PANEL, the stage is BG, and the bottom edge crosses both.
        """
        G, T = 5, TitleBar.H

        def grip(side, cur, bg, **place):
            f = tk.Frame(root, bg=bg, cursor=cur)
            f.place(**place)
            f.bind("<Button-1>", lambda e, s=side: self._grip_start(e, s))
            f.bind("<B1-Motion>", self._grip_drag)

        VER, HOR = "sb_v_double_arrow", "sb_h_double_arrow"
        # NOTHING is laid over the title bar - it resizes its own top edge and
        # top corners itself. An overlaid frame cannot be transparent, so it
        # shows the instant anything under it changes colour, which is how a
        # PANEL-coloured corner grip ended up as a dark square on the close
        # button's red hover fill.
        # sides: start BELOW the title bar, so each meets only one colour
        grip("w", HOR, PANEL, x=0, y=T, width=G, relheight=1.0, height=-T)
        grip("e", HOR, BG, relx=1.0, y=T, anchor="ne", width=G,
             relheight=1.0, height=-T)
        # bottom edge crosses the sidebar and the stage, so it takes two
        grip("s", VER, PANEL, x=0, rely=1.0, anchor="sw", width=SIDE_W, height=G)
        grip("s", VER, BG, x=SIDE_W, rely=1.0, anchor="sw", relwidth=1.0,
             width=-SIDE_W, height=G)

        for corner, cur, bg, pl in (
                ("sw", "size_ne_sw", PANEL, dict(x=0, rely=1.0, anchor="sw")),
                ("se", "size_nw_se", BG, dict(relx=1.0, rely=1.0, anchor="se"))):
            grip(corner, cur, bg, width=G * 2, height=G * 2, **pl)

    def _grip_start(self, e, side):
        self._gr = (side, e.x_root, e.y_root, self.root.winfo_width(),
                    self.root.winfo_height(), self.root.winfo_x(),
                    self.root.winfo_y())

    def _grip_drag(self, e):
        if not getattr(self, "_gr", None):
            return
        side, sx, sy, w, h, wx, wy = self._gr
        dx, dy = e.x_root - sx, e.y_root - sy
        mw, mh = 980, 620
        nw, nh, nx, ny = w, h, wx, wy
        if "e" in side:
            nw = max(mw, w + dx)
        if "s" in side:
            nh = max(mh, h + dy)
        if "w" in side:
            nw = max(mw, w - dx); nx = wx + (w - nw)
        if "n" in side:
            nh = max(mh, h - dy); ny = wy + (h - nh)
        self._restore = None            # a manual resize cancels "maximised"
        self.root.geometry("%dx%d+%d+%d" % (nw, nh, nx, ny))

    def _icon(self, root):
        """Title bar / taskbar icon, if the .ico is sitting next to this file.

        Wrapped because it is pure decoration: a missing or unreadable icon must
        never be the reason the panel does not open. Windows also groups the
        taskbar button by AppUserModelID, and without setting one the window
        inherits pythonw.exe's - so it shows the generic Python icon however
        good this one is.
        """
        try:
            here = os.path.dirname(os.path.abspath(__file__))
            ico = os.path.join(here, "RimStudio.ico")
            if os.path.isfile(ico):
                root.iconbitmap(default=ico)
            # a small copy for our own caption strip, since a borderless window
            # has no system title bar left to show the .ico in
            png = os.path.join(here, "RimStudio.png")
            if os.path.isfile(png):
                self.icon_img = ImageTk.PhotoImage(
                    Image.open(png).resize((18, 18), Image.LANCZOS))
        except Exception:
            pass
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "Abdulaziz.RimStudio.Panel.1")
        except Exception:
            pass

    def _centre(self, w, h):
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        self.root.geometry("%dx%d+%d+%d" % (w, h, max(0, (sw - w) // 2),
                                            max(0, (sh - h) // 3)))

    def _pin_sidebar(self):
        """Hold the scrolling column to the width of its viewport.

        Cheap and idempotent: once the widths agree this does nothing, so the
        <Configure> that it triggers does not loop.
        """
        try:
            cw = self._sidecanvas.winfo_width()
            if cw > 1 and self.col.winfo_width() != cw:
                self._sidecanvas.itemconfig(self._colwin, width=cw)
        except Exception:
            pass

    def _prog_start(self, label):
        """Start the bar AND make sure it is on screen. The sidebar scrolls, so
        a bar that is merely packed can still be somewhere the user cannot see
        while a 10-second pull runs."""
        try:
            self._sidecanvas.yview_moveto(0.0)
        except Exception:
            pass
        self.prog.start(label)

    def _focus(self, on):
        if getattr(self, "titlebar", None) is not None:
            self.titlebar.set_active(on)

    # ---------------------------------------------------------------- layout
    def _build(self):
        # our own caption, then a hairline so the borderless window still has a
        # defined edge against a dark desktop
        self.titlebar = TitleBar(self.root, "RimStudio", self.minimise,
                                 self.maximise, self.root.destroy,
                                 icon=getattr(self, "icon_img", None),
                                 on_resize_start=self._grip_start,
                                 on_resize_drag=self._grip_drag)
        self.titlebar.pack(side="top", fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(side="top", fill="both", expand=True)

        side = tk.Frame(body, bg=PANEL, width=SIDE_W)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Frame(body, bg=LINE, width=1).pack(side="left", fill="y")

        stage = tk.Frame(body, bg=BG)
        stage.pack(side="right", fill="both", expand=True)

        # --- preview stage. A Canvas, not a Label, so the image can be panned
        # and drawn at an offset once it is zoomed past the window.
        self.stage = stage
        self.view = tk.Canvas(stage, bg=BG, highlightthickness=0, bd=0)
        self.view.pack(fill="both", expand=True, padx=18, pady=(18, 4))

        # Zoom controls sit under the image they act on, not up in the sidebar
        # among the settings - they change the view, not the picture.
        bar = tk.Frame(stage, bg=BG)
        bar.pack(fill="x", padx=18, pady=(0, 12))
        zbox = tk.Frame(bar, bg=BG)
        zbox.pack(side="left")
        Button(zbox, "-", lambda: self.set_zoom(self.zoom / 1.25),
               width=32, height=26).pack(side="left")
        self.zoom_lbl = tk.Label(zbox, text="Fit", bg=BG, fg=DIM, font=F_MONO, width=6)
        self.zoom_lbl.pack(side="left", padx=2)
        Button(zbox, "+", lambda: self.set_zoom(self.zoom * 1.25),
               width=32, height=26).pack(side="left")
        Button(zbox, "Reset zoom", lambda: self.set_zoom(1.0, fit=True),
               width=92, height=26).pack(side="left", padx=(8, 0))

        # Comparison lives under the picture it compares, not in the sidebar
        # among the settings - it changes what you are looking at, not the file.
        cmp_btn = Button(zbox, "", None, width=34, height=26, icon="compare",
                         tip="Hold to see the original")
        cmp_btn.pack(side="left", padx=(14, 0))
        cmp_btn.bind("<ButtonPress-1>", lambda e: self.compare(True), add="+")
        cmp_btn.bind("<ButtonRelease-1>", lambda e: self.compare(False), add="+")
        self.b_split = Button(zbox, "", self.toggle_split, width=34, height=26,
                              icon="split", tip="Split view - drag the divider")
        self.b_split.pack(side="left", padx=(4, 0))
        self.caption = tk.Label(bar, text="Nothing loaded", bg=BG, fg=FAINT,
                                font=F_SMALL)
        self.caption.pack(side="right")

        self.zoom = 1.0          # 1.0 = fit to window
        self.pan = [0.5, 0.5]    # centre of the view, in image fractions
        self._drag_from = None
        self.view.bind("<Control-MouseWheel>", self._wheel_zoom)
        self.view.bind("<ButtonPress-1>", self._pan_start)
        self.view.bind("<B1-Motion>", self._pan_move)
        self.view.bind("<ButtonRelease-1>",
                       lambda e: (setattr(self, "_drag_from", None),
                                  self._split_release(e)))
        self.view.bind("<Double-Button-1>", lambda e: self.set_zoom(1.0, fit=True))
        for seq in ("<Control-plus>", "<Control-equal>", "<Control-KP_Add>"):
            self.root.bind_all(seq, lambda e: self.set_zoom(self.zoom * 1.25))
        for seq in ("<Control-minus>", "<Control-KP_Subtract>"):
            self.root.bind_all(seq, lambda e: self.set_zoom(self.zoom / 1.25))
        self.root.bind_all("<Control-Key-0>", lambda e: self.set_zoom(1.0, fit=True))
        self.root.bind_all("<Control-z>", self.undo)
        self.root.bind_all("<Control-y>", self.redo)
        # capital Z means Shift is down, which is the other common redo
        self.root.bind_all("<Control-Z>", self.redo)

        # --- scrolling sidebar
        canvas = self._sidecanvas = tk.Canvas(side, bg=PANEL,
                                              highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(side, orient="vertical", command=canvas.yview,
                            width=10, troughcolor=PANEL, bg=CARD,
                            activebackground=ACC, relief="flat", bd=0)
        self.col = tk.Frame(canvas, bg=PANEL)
        win = self._colwin = canvas.create_window((0, 0), window=self.col, anchor="nw")
        self.col.bind("<Configure>",
                      lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        # add="+" - a bare bind() REPLACES, and quietly losing the scrollregion
        # update above would leave the sidebar unable to scroll
        canvas.bind("<Configure>", lambda e: self._pin_sidebar())
        # ...and again whenever the COLUMN changes size, which the canvas's own
        # <Configure> cannot catch. The canvas is sized once, before any of the
        # sidebar's children exist; Tk applies the item width to the empty frame
        # and then re-lays it at its own requested width the moment children
        # arrive, silently dropping the pin. The result was a 717px column
        # inside a 344px viewport with every button and slider running off the
        # edge. Collapsing or expanding a section changes the requested width
        # again, so this has to keep watching, not fire once.
        self.col.bind("<Configure>", lambda e: self._pin_sidebar(), add="+")
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")
        def wheel(e):
            # Ctrl+wheel belongs to the preview zoom. bind_all means this fires
            # for the whole window, so it has to stand aside when Ctrl is held
            # or the panel scrolls away underneath you while you zoom.
            if e.state & 0x0004:
                return
            canvas.yview_scroll(int(-e.delta / 120), "units")
        canvas.bind_all("<MouseWheel>", wheel)

        pad = dict(padx=14)

        head = tk.Frame(self.col, bg=PANEL)
        head.pack(fill="x", **pad)
        tk.Label(head, text="RimStudio", bg=PANEL, fg=INK,
                 font=F_TITLE).pack(anchor="w", pady=(14, 0))
        tk.Label(head, text="composite a cut-out into a plate", bg=PANEL,
                 fg=FAINT, font=F_SMALL).pack(anchor="w", pady=(0, 10))

        self.b_pull = Button(self.col, "Pull from Photoshop", self.pull, primary=True)
        self.b_pull.pack(fill="x", pady=(2, 4), **pad)
        self.b_cut, _ = self._pair(self.col, ("Cut out subject", self.cutout),
                                   ("Open from disk", self.open_files))
        # The progress bar belongs HERE, immediately under the three buttons
        # that start the slow work. It used to sit at the bottom of this column,
        # which scrolls - so during a pull it was reporting perfectly well from
        # somewhere below the fold, and the tool looked hung instead.
        self.prog = Progress(self.col)
        self.prog.pack(fill="x", pady=(6, 0), **pad)
        self.stats = Toast(self.col)
        self.stats.pack(fill="x", pady=(8, 2), **pad)
        # Presets / Save / Undo / Redo share ONE row. Undo and redo are narrow
        # steppers pinned to the right - they are used constantly but are not
        # worth a whole row of the panel's height.
        prow = tk.Frame(self.col, bg=PANEL)
        prow.pack(fill="x", pady=2, padx=14)
        prow.columnconfigure(0, weight=1, uniform="p")
        prow.columnconfigure(1, weight=1, uniform="p")
        self.b_preset = Button(prow, "Presets", self.preset_menu)
        self.b_preset.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        Button(prow, "Save preset", self.save_preset).grid(
            row=0, column=1, sticky="ew", padx=(3, 6))
        self.b_undo = Button(prow, "Undo", self.undo, width=34, icon="undo",
                             tip="Undo    Ctrl+Z")
        self.b_undo.grid(row=0, column=2, padx=(0, 3))
        self.b_redo = Button(prow, "Redo", self.redo, width=34, icon="redo",
                             tip="Redo    Ctrl+Y")
        self.b_redo.grid(row=0, column=3)
        self.preset_name = tk.Label(self.col, text="", bg=PANEL, fg=FAINT,
                                    font=F_SMALL, anchor="w")
        self.preset_name.pack(fill="x", padx=16)

        sec = Section(self.col, "Match to the plate", True)
        sec.pack(fill="x", pady=(10, 0), **pad)
        Button(sec.body, "Auto match", self.auto, primary=True).pack(
            fill="x", pady=(8, 4))
        self.auto_note = Toast(sec.body)
        self.auto_note.pack(fill="x", pady=(0, 4))
        self._add(sec.body, MATCH)
        # Focus gets its own button, right under its own slider. Softening is
        # the one correction that destroys detail rather than moving it, so it
        # must not be reset every time the main auto re-runs.
        frow = tk.Frame(sec.body, bg=PANEL)
        frow.pack(fill="x", pady=(0, 4))
        Button(frow, "Auto focus", self.auto_focus, width=104,
               height=26, tip="Set Match focus only - the main Auto leaves it alone").pack(
            side="right")

        sec2 = Section(self.col, "Light from the background", False)
        sec2.pack(fill="x", pady=(8, 0), **pad)
        # the two glow controls first, with their own auto directly under them,
        # then the rim shaping. Bloom is measured, but how far to push it is a
        # look call, so it stays off the main Auto - same as Match focus.
        self._add(sec2.body, LIGHT[:4])
        grow = tk.Frame(sec2.body, bg=PANEL)
        grow.pack(fill="x", pady=(0, 4))
        Button(grow, "Auto glow", self.auto_glow, width=104, height=26,
               tip="Set Bloom only - the main Auto leaves it alone").pack(side="right")
        self._add(sec2.body, LIGHT[4:])

        sec3 = Section(self.col, "Ground shadow", False)
        sec3.pack(fill="x", pady=(8, 0), **pad)
        self._add(sec3.body, SHADOW)

        out = Section(self.col, "Output", True)
        out.pack(fill="x", pady=(8, 0), **pad)
        self.b_send = Button(out.body, "Send to Photoshop", self.send, primary=True)
        self.b_send.pack(fill="x", pady=(8, 4))
        orow = tk.Frame(out.body, bg=PANEL)
        orow.pack(fill="x", pady=2)
        orow.columnconfigure(0, weight=1, uniform="out")
        orow.columnconfigure(1, weight=1, uniform="out")
        self.b_save_png = Button(orow, "Save PNG", self.apply_only)
        self.b_save_png.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        Button(orow, "Open folder", self.open_output_folder).grid(
            row=0, column=1, sticky="ew", padx=(3, 0))

        self.msg = Toast(self.col)
        self.msg.pack(fill="x", pady=(6, 18), **pad)
        self.msg.show("Select your cut-out layer in Photoshop,\nthen press Pull.", "info")

    def _pair(self, parent, left, right, primary_left=False, primary_right=False):
        """Two buttons sharing one row, each exactly half of it.

        grid with uniform columns rather than pack(side=...) with fixed widths -
        fixed widths never add up to the column and leave one button overhanging
        the full-width buttons above it.
        """
        row = tk.Frame(parent, bg=PANEL)
        row.pack(fill="x", pady=2, padx=14)
        row.columnconfigure(0, weight=1, uniform="pair")
        row.columnconfigure(1, weight=1, uniform="pair")
        a = Button(row, left[0], left[1], primary=primary_left)
        b = Button(row, right[0], right[1], primary=primary_right)
        a.grid(row=0, column=0, sticky="ew", padx=(0, 3))
        b.grid(row=0, column=1, sticky="ew", padx=(3, 0))
        return a, b

    def _add(self, parent, defs):
        for key, label, lo, hi, dflt, step in defs:
            s = Slider(parent, key, label, lo, hi, dflt, step, self._slider_moved)
            s.pack(fill="x", pady=1)
            self.sliders[key] = s

    # ---------------------------------------------------------------- history
    def _hist_init(self):
        """One snapshot of every slider per settled change.

        Snapshots are pushed on slider RELEASE, not while dragging - otherwise a
        single drag across a slider would bury the previous state under fifty
        near-identical entries and Ctrl+Z would appear to do nothing.
        """
        self.hist = [self.params()]
        self.hist_i = 0
        self._restoring = False
        self._sync_hist_buttons()

    def _hist_push(self):
        if self._restoring:
            return
        cur = self.params()
        if self.hist and cur == self.hist[self.hist_i]:
            return
        del self.hist[self.hist_i + 1:]         # a new edit discards the redo tail
        self.hist.append(cur)
        if len(self.hist) > 60:
            self.hist.pop(0)
        self.hist_i = len(self.hist) - 1
        self._sync_hist_buttons()

    def _hist_apply(self, snap):
        self._restoring = True
        try:
            for k, v in snap.items():
                if k in self.sliders:
                    self.sliders[k].set(v)
        finally:
            self._restoring = False
        self._sync_hist_buttons()
        self.render(fast=False)

    def _sync_hist_buttons(self):
        try:
            self.b_undo.set_enabled(self.hist_i > 0)
            self.b_redo.set_enabled(self.hist_i < len(self.hist) - 1)
        except Exception:
            pass

    def undo(self, _e=None):
        if self.hist_i <= 0:
            self.msg.show("nothing left to undo", "info")
            return "break"
        self.hist_i -= 1
        self._hist_apply(self.hist[self.hist_i])
        self.msg.show("undo  (%d more)" % self.hist_i, "ok")
        return "break"

    def redo(self, _e=None):
        if self.hist_i >= len(self.hist) - 1:
            self.msg.show("nothing to redo", "info")
            return "break"
        self.hist_i += 1
        self._hist_apply(self.hist[self.hist_i])
        self.msg.show("redo  (%d more)" % (len(self.hist) - 1 - self.hist_i), "ok")
        return "break"

    # ---------------------------------------------------------------- render
    def params(self):
        return {k: s.get() for k, s in self.sliders.items()}

    def _slider_moved(self, _key, _val, released):
        if self.settle_id:
            self.root.after_cancel(self.settle_id)
        self.settle_id = self.root.after(180, lambda: self.render(fast=False))
        if not released:
            self.render(fast=True)
        else:
            self._hist_push()

    def render(self, fast=True, plain=False):
        if core.STATE["sub_prev"] is None:
            return
        if self.busy:
            self.pending_fast = fast
            return
        self.busy = True
        p = self.params()

        def work():
            try:
                if plain:
                    sub, bg = core.STATE["sub_prev"], core.STATE["bg_prev"]
                    a = sub[..., 3:4]
                    arr = np.clip(bg * (1 - a) + sub[..., :3] * a, 0, 1)
                else:
                    k = "draft" if fast else "prev"
                    arr = core.composite(core.STATE["sub_" + k], core.STATE["bg_" + k],
                                         p, pre=core.STATE["pre_" + k])
                self.results.put(("img", arr))
            except Exception:
                self.results.put(("err", traceback.format_exc(limit=3)))

        threading.Thread(target=work, daemon=True).start()

    def _drain(self):
        try:
            while True:
                kind, payload = self.results.get_nowait()
                if kind == "img":
                    self._last_arr = payload
                    self._show(payload)
                    self.busy = False
                    if self.pending_fast is not None:
                        f = self.pending_fast
                        self.pending_fast = None
                        self.render(fast=f)
                elif kind == "prog":
                    self.prog.step(payload[0], payload[1])
                elif kind == "done":
                    self.prog.step("finished", 1.0)
                    self.prog.stop()
                    self._busy_ui(False)
                    self.msg.show(payload[0], payload[1])
                elif kind == "fail":
                    self.prog.stop()
                    self._busy_ui(False)
                    self.msg.show(payload[-320:], "err")
                elif kind == "err":
                    self.busy = False
                    self.msg.show(payload[-320:], "err")
                elif kind == "msg":
                    self.msg.show(payload[0], payload[1])
                elif kind == "stats":
                    self.stats.show(payload, "info")
                elif kind == "caption":
                    self.caption.config(text=payload)
                elif kind == "auto":
                    self._apply_auto(payload)
        except queue.Empty:
            pass
        except Exception:
            # An exception escaping here would skip the reschedule below and the
            # drain loop would never run again - the whole UI silently stops
            # updating while the app still looks alive. Never let that happen.
            self.busy = False
            try:
                self.msg.show(traceback.format_exc(limit=3)[-320:], "err")
            except Exception:
                pass
        self.root.after(40, self._drain)

    # ---------------------------------------------------------------- zoom
    def set_zoom(self, z, fit=False):
        self.zoom = 1.0 if fit else float(np.clip(z, 1.0, 12.0))
        if fit:
            self.pan = [0.5, 0.5]
        try:
            self.zoom_lbl.config(text="Fit" if self.zoom <= 1.0001
                                 else "%d%%" % round(self.zoom * 100))
        except Exception:
            pass
        if self._last_arr is not None:
            self._show(self._last_arr)

    def _wheel_zoom(self, e):
        vw = max(self.view.winfo_width(), 1)
        vh = max(self.view.winfo_height(), 1)
        # keep whatever is under the cursor under the cursor
        before = self._view_rect()
        if before:
            x0, y0, w, h = before
            self.pan = [x0 + (e.x / vw) * w, y0 + (e.y / vh) * h]
        self.set_zoom(self.zoom * (1.25 if e.delta > 0 else 1 / 1.25))
        return "break"

    def _pan_start(self, e):
        # grabbing the divider wins over panning, but only within 16px of it -
        # everywhere else the picture still pans as before
        if self._split_press(e):
            return "break"
        self._drag_from = (e.x, e.y, list(self.pan))

    def _pan_move(self, e):
        if self._dragging_split:
            return self._split_drag(e)
        if not self._drag_from or self.zoom <= 1.0:
            return
        x, y, p0 = self._drag_from
        r = self._view_rect()
        if not r:
            return
        vw = max(self.view.winfo_width(), 1)
        vh = max(self.view.winfo_height(), 1)
        self.pan = [p0[0] - (e.x - x) / vw * r[2],
                    p0[1] - (e.y - y) / vh * r[3]]
        self._show(self._last_arr)

    def _view_rect(self):
        """Visible region of the image as fractions: (x0, y0, w, h)."""
        if self._last_arr is None:
            return None
        w = h = 1.0 / max(self.zoom, 1e-6)
        x0 = float(np.clip(self.pan[0] - w / 2, 0.0, max(0.0, 1.0 - w)))
        y0 = float(np.clip(self.pan[1] - h / 2, 0.0, max(0.0, 1.0 - h)))
        return (x0, y0, w, h)

    def _show(self, arr):
        if arr is None:
            return
        # Split is mixed in IMAGE space, before the crop, so the divider stays
        # on the same feature when you zoom rather than sliding across it.
        if self.split is not None:
            plain = self._plain()
            if plain is not None and plain.shape == arr.shape:
                cut = int(round(self.split * arr.shape[1]))
                arr = arr.copy()
                arr[:, :cut] = plain[:, :cut]
        im = Image.fromarray((arr * 255).astype(np.uint8))
        vw = max(self.view.winfo_width(), 200)
        vh = max(self.view.winfo_height(), 150)
        if self.zoom > 1.0:
            # crop first, then scale only what is visible - resizing the whole
            # image at 12x would allocate a picture the size of a wall
            x0, y0, fw, fh = self._view_rect()
            box = (int(x0 * im.width), int(y0 * im.height),
                   int(min(im.width, (x0 + fw) * im.width)),
                   int(min(im.height, (y0 + fh) * im.height)))
            im = im.crop(box)
        sc = min(vw / im.width, vh / im.height)
        im = im.resize((max(1, int(im.width * sc)), max(1, int(im.height * sc))),
                       Image.LANCZOS if self.zoom > 1.0 else Image.BILINEAR)
        self.photo = ImageTk.PhotoImage(im)   # keep a ref or Tk drops the image
        self.view.delete("all")
        self.view.create_image(vw // 2, vh // 2, image=self.photo, anchor="center")
        # where the picture actually landed, which the divider needs
        self._img_rect = ((vw - im.width) / 2.0, (vh - im.height) / 2.0,
                          im.width, im.height)

        sx = self._split_x()
        if sx is not None:
            top, hgt = self._img_rect[1], self._img_rect[3]
            self.view.create_line(sx, top, sx, top + hgt, fill="#ffffff", width=1)
            cy = top + hgt / 2
            round_rect(self.view, sx - 11, cy - 16, sx + 11, cy + 16, 11,
                       fill="#f2f2f7", outline="")
            for dx in (-3, 3):
                self.view.create_line(sx + dx, cy - 6, sx + dx, cy + 6,
                                      fill="#3a3a46", width=2)
            self.view.create_text(self._img_rect[0] + 8, top + 8, anchor="nw",
                                  fill="#ffffff", font=F_MONO, text="BEFORE")
            self.view.create_text(self._img_rect[0] + self._img_rect[2] - 8,
                                  top + 8, anchor="ne", fill="#ffffff",
                                  font=F_MONO, text="AFTER")
        if self.zoom > 1.0:
            self.view.create_text(10, 10, anchor="nw", fill=INK, font=F_MONO,
                                  text="%d%%  -  drag to pan, double-click to fit"
                                       % round(self.zoom * 100))
        self.view.configure(cursor="fleur" if self.zoom > 1.0 else "")
        self._last_size = (self.stage.winfo_width(), self.stage.winfo_height())

    def _maybe_refit(self, _e):
        """Re-fit on resize without re-rendering the whole frame."""
        if self._last_arr is None:
            return
        now = (self.stage.winfo_width(), self.stage.winfo_height())
        if abs(now[0] - self._last_size[0]) > 8 or abs(now[1] - self._last_size[1]) > 8:
            self._show(self._last_arr)

    # ---------------------------------------------------------------- actions
    def _bg(self, fn):
        threading.Thread(target=fn, daemon=True).start()

    def _stats_text(self, s):
        return ("subject L %-6s plate L %s\ncontrast  %-6s vs %s\ncast      %s vs %s"
                % (s["subject_L"], s["plate_L"], s["subject_contrast"],
                   s["plate_contrast"], s["subject_ab"], s["plate_ab"]))

    # ---------------------------------------------------------------- presets
    def preset_menu(self):
        """Native tk.Menu rather than a hand-drawn popup: it posts at the cursor,
        closes on click-away and handles the screen edge, none of which is worth
        reimplementing for a list of names."""
        presets = core.load_presets()
        m = tk.Menu(self.root, tearoff=0, bg=CARD, fg=INK,
                    activebackground=ACC, activeforeground="#ffffff",
                    bd=0, relief="flat", font=F_BODY)
        builtin = [n for n in presets if n in core.BUILTIN_PRESETS]
        mine = sorted(n for n in presets if n not in core.BUILTIN_PRESETS)
        for n in mine:
            m.add_command(label=n, command=lambda k=n: self.apply_preset(k))
        if mine:
            m.add_separator()
        for n in builtin:
            m.add_command(label="  " + n, command=lambda k=n: self.apply_preset(k))
        m.add_separator()
        if mine:
            sub = tk.Menu(m, tearoff=0, bg=CARD, fg=INK, activebackground=ACC,
                          activeforeground="#ffffff", bd=0, font=F_BODY)
            for n in mine:
                sub.add_command(label=n, command=lambda k=n: self.remove_preset(k))
            m.add_cascade(label="Delete", menu=sub)
        m.add_command(label="Save current as...", command=self.save_preset)
        try:
            x = self.b_preset.winfo_rootx()
            y = self.b_preset.winfo_rooty() + self.b_preset.winfo_height()
            m.tk_popup(x, y)
        finally:
            m.grab_release()

    def apply_preset(self, name):
        p = core.load_presets().get(name)
        if not p:
            self.msg.show('preset "%s" not found' % name, "warn")
            return
        applied = 0
        for k, v in p.items():
            if k in self.sliders:
                self.sliders[k].set(v)
                applied += 1
        self.preset_name.config(text="preset: " + name)
        self._hist_push()
        self.msg.show('applied "%s"  (%d settings)' % (name, applied), "ok")
        self.render(fast=False)

    def save_preset(self):
        from tkinter import simpledialog
        name = simpledialog.askstring("Save preset", "Name this look:",
                                      parent=self.root,
                                      initialvalue=self.preset_name.cget("text")
                                      .replace("preset: ", ""))
        if not name:
            return
        r = core.save_preset(name, self.params())
        if r.get("error"):
            self.msg.show(r["error"], "err")
            return
        self.preset_name.config(text="preset: " + r["name"])
        self.msg.show(r["msg"], "ok")

    def remove_preset(self, name):
        r = core.delete_preset(name)
        self.msg.show(r.get("error") or r.get("msg"),
                      "err" if r.get("error") else "ok")
        if not r.get("error") and self.preset_name.cget("text").endswith(name):
            self.preset_name.config(text="")

    def cutout(self):
        self._pull_via(core.cutout_in_photoshop, "running Select Subject")

    def pull(self):
        self._pull_via(core.pull_from_photoshop, "asking Photoshop for the layer")

    def _pull_via(self, fn, label):
        self.msg.show("", "info")
        self._busy_ui(True)
        self._prog_start(label)

        def work():
            try:
                self.results.put(("prog", ("exporting layers from Photoshop", 0.35)))
                j = fn()
                if j.get("error"):
                    self.results.put(("fail", j["error"]))
                    return
                self.results.put(("prog", ("measuring both images", 0.85)))
                self._plain_arr = None
                self.results.put(("stats", self._stats_text(j["stats"])))
                self.results.put(("caption", "%s   %d x %d" % (j["msg"], j["w"], j["h"])))
                self.results.put(("done", ("%s\n%d x %d" % (j["msg"], j["w"], j["h"]), "ok")))
                self.render(fast=False)
            except Exception:
                self.results.put(("fail", traceback.format_exc(limit=3)))
        self._bg(work)

    def _caption(self, t):
        """MAIN THREAD ONLY. Calling this from a worker blocks that thread
        outright - Tcl is not thread-safe and does not raise, it just stops,
        so everything after it in the worker silently never runs."""
        try:
            self.caption.config(text=t)
        except Exception:
            pass

    def open_files(self):
        sub = filedialog.askopenfilename(title="Subject - PNG with transparency",
                                         filetypes=[("Images", "*.png *.tif *.tiff")])
        if not sub:
            return
        bg = filedialog.askopenfilename(
            title="Background", filetypes=[("Images", "*.png *.jpg *.jpeg *.avif *.tif")])
        if not bg:
            return

        # Loading is not instant - it measures the focus match on the full-size
        # pair, which is ~10s on a 2400px image. Without a bar that reads as a
        # hung window.
        self.msg.show("", "info")
        self._busy_ui(True)
        self._prog_start("reading images")

        def work():
            try:
                self.results.put(("prog", ("analysing the pair at full size", 0.3)))
                j = core.load_pair(sub, bg)
                self.results.put(("prog", ("rendering the preview", 0.85)))
                self._plain_arr = None
                self.results.put(("stats", self._stats_text(j["stats"])))
                self.results.put(("caption", "%s + %s   %d x %d"
                                  % (j["subject"], j["background"], j["w"], j["h"])))
                self.results.put(("done", ("%s + %s" % (j["subject"], j["background"]), "ok")))
                self.render(fast=False)
            except Exception:
                self.results.put(("fail", traceback.format_exc(limit=3)))
        self._bg(work)

    def auto(self):
        if core.STATE["pre_prev"] is None:
            self.msg.show("pull or open images first", "warn")
            return
        self.msg.show("measuring both images...", "info")

        def work():
            try:
                rec = rim_engine.auto_params(core.STATE["pre_prev"],
                                             core.STATE["sub_prev"].shape[1])
                self.results.put(("auto", rec))
            except Exception:
                self.results.put(("err", traceback.format_exc(limit=3)))
        self._bg(work)

    def auto_glow(self):
        if core.STATE["pre_prev"] is None:
            self.msg.show("pull or open images first", "warn")
            return
        amt, m = rim_engine.auto_glow(core.STATE["pre_prev"],
                                      core.STATE["sub_prev"].shape[1])
        self.sliders["bg_glow"].set(amt)
        self._hist_push()
        self.msg.show("bloom %.2f   (brightness step across the edge is %.1f L)"
                      % (amt, m["edge_step_L"]), "ok")
        self.render(fast=False)

    def auto_focus(self):
        if core.STATE["pre_prev"] is None:
            self.msg.show("pull or open images first", "warn")
            return
        amt, m = rim_engine.auto_focus(core.STATE["pre_prev"])
        self.sliders["focus"].set(amt)
        self._hist_push()
        px = m["solved_px"]
        self.msg.show("focus %.2f   (plate is %.0f%% as sharp as the subject%s)"
                      % (amt, m["sharp_ratio"] * 100,
                         ", %.1fpx blur" % px if px else ""), "ok")
        self.render(fast=False)

    def _apply_auto(self, rec):
        m = rec.pop("_measured", {})
        for k, v in rec.items():
            if k in self.sliders:
                self.sliders[k].set(v)
        self.auto_note.show(
            "subject L %s -> aiming %s\nplate lit %s   contrast %s"
            % (m.get("subject_L"), m.get("aim_L"), m.get("plate_lit_L"),
               m.get("contrast")), "ok")
        self._hist_push()
        self.msg.show("auto match applied - adjust from here", "ok")
        self.render(fast=False)

    def compare(self, on):
        if self.comparing == on:
            return
        self.comparing = on
        self._caption("ORIGINAL" if on else "")
        self.render(fast=False, plain=on)

    # ------------------------------------------------------------ split view
    def _plain(self):
        """The untouched paste-up, cached.

        It does not depend on a single slider, so it is computed once per image
        pair and then the split costs nothing but a column copy - which is what
        makes dragging the divider smooth instead of re-rendering per frame.
        """
        if self._plain_arr is None and core.STATE["sub_prev"] is not None:
            sub, bg = core.STATE["sub_prev"], core.STATE["bg_prev"]
            a = sub[..., 3:4]
            self._plain_arr = np.clip(bg * (1 - a) + sub[..., :3] * a, 0, 1)
        return self._plain_arr

    def toggle_split(self):
        if self.split is None:
            if core.STATE["sub_prev"] is None:
                self.msg.show("pull or open images first", "warn")
                return
            self.split = 0.5
            self._plain()
            self.msg.show("split view - drag the divider; left is the original",
                          "info")
        else:
            self.split = None
            self.msg.show("", "info")
        self.b_split.set_active(self.split is not None)
        if self._last_arr is not None:
            self._show(self._last_arr)

    def _split_x(self):
        """Screen x of the divider, or None when it is scrolled out of view."""
        if self.split is None or not self._img_rect:
            return None
        left, top, w, h = self._img_rect
        r = self._view_rect()
        if r is None:
            return None
        x0, _, fw, _ = r
        f = (self.split - x0) / max(fw, 1e-6)
        if f < -0.02 or f > 1.02:
            return None
        return left + f * w

    def _split_press(self, e):
        sx = self._split_x()
        # near the divider grabs it; anywhere else keeps the normal pan
        if sx is not None and abs(e.x - sx) <= 16:
            self._dragging_split = True
            self._split_drag(e)
            return "break"
        return None

    def _split_drag(self, e):
        if not self._dragging_split or not self._img_rect:
            return
        left, top, w, h = self._img_rect
        x0, _, fw, _ = self._view_rect()
        self.split = float(np.clip(x0 + (e.x - left) / max(w, 1) * fw, 0.0, 1.0))
        self._show(self._last_arr)
        return "break"

    def _split_release(self, _e):
        self._dragging_split = False

    def open_output_folder(self):
        """Show the written files. Save PNG puts them next to the subject, which
        is not somewhere the user picked, so give them a way to get there."""
        p = core.STATE.get("out_path") or core.STATE.get("sub_path")
        if not p:
            self.msg.show("nothing saved yet", "warn")
            return
        try:
            subprocess.Popen(["explorer", "/select,", os.path.normpath(p)],
                             **core._no_console())
        except Exception as ex:
            self.msg.show("%s: %s" % (type(ex).__name__, ex), "err")

    def apply_only(self):
        self._output(send=False)

    def send(self):
        self._output(send=True)

    def _output(self, send):
        if core.STATE["sub_full"] is None:
            self.msg.show("nothing loaded", "warn")
            return
        self.msg.show("", "info")
        self._busy_ui(True)
        self._prog_start("preparing...")
        p = self.params()
        wants_shadow = p.get("shadow", 0) > 0

        def work():
            try:
                self.results.put(("prog", ("rendering at full resolution", 0.12)))
                paths = core.render_full(p)
                n = 2 if (wants_shadow and paths.get("shadow")) else 1
                if not send:
                    self.results.put(("done", ("written:\n" + paths["relit"], "ok")))
                    return
                # one stage, not two: a step posted immediately before another
                # drains in the same pass and never gets painted
                self.results.put(("prog", ("sending %d layer%s to Photoshop"
                                           % (n, "" if n == 1 else "s"), 0.6)))
                r = core.send_to_photoshop(paths)
                self.results.put(("prog", ("placing layers", 0.95)))
                self.results.put(("done", (r.get("error") or r.get("msg"),
                                           "err" if r.get("error") else "ok")))
            except Exception:
                self.results.put(("fail", traceback.format_exc(limit=3)))
        self._bg(work)

    def _busy_ui(self, on):
        """Stop a second run being fired while one is in flight - Photoshop
        cannot service two of these at once and the second just times out."""
        for b in (self.b_pull, self.b_cut, self.b_send, self.b_save_png):
            try:
                b.set_enabled(not on)
            except Exception:
                pass


def _dump_layout(app, path):
    """Write the real widget geometry to a file. Set RIMSTUDIO_DEBUG=1 to get it.

    The panel can only be looked at where it runs, and it lays out differently
    depending on how it was STARTED - so a bug that only appears when Photoshop
    launches it cannot be diagnosed by reading the code.
    """
    try:
        w = []
        w.append("window      %s" % app.root.geometry())
        w.append("side frame  %d" % app._sidecanvas.master.winfo_width())
        w.append("scrollcanvas%d" % app._sidecanvas.winfo_width())
        w.append("inner col   %d" % app.col.winfo_width())
        w.append("col reqwidth %d" % app.col.winfo_reqwidth())
        w.append("item width  %s" % app._sidecanvas.itemcget(app._colwin, "width"))
        w.append("b_pull      %d" % app.b_pull.winfo_width())
        w.append("b_pull self.w %d" % app.b_pull.w)
        s = app.sliders.get("m_exposure")
        if s is not None:
            w.append("slider      %d  self.w %d" % (s.winfo_width(), s.w))
        open(path, "w").write("\n".join(w))
    except Exception as e:
        try:
            open(path, "w").write("dump failed: %r" % (e,))
        except Exception:
            pass


def main():
    root = tk.Tk()
    app = App(root)
    if os.environ.get("RIMSTUDIO_DEBUG"):
        root.after(3000, lambda: _dump_layout(
            app, os.path.join(os.environ.get("TEMP", "."), "rimstudio_layout.txt")))
    if len(sys.argv) >= 3:
        try:
            j = core.load_pair(sys.argv[1], sys.argv[2])
            app.stats.show(app._stats_text(j["stats"]), "info")
            app._caption("%s + %s   %d x %d"
                         % (j["subject"], j["background"], j["w"], j["h"]))
            app.render(fast=False)
        except Exception as e:
            app.msg.show("could not load: %s" % e, "err")
    root.mainloop()


if __name__ == "__main__":
    main()
