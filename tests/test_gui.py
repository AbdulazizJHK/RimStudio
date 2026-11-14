"""The native window, driven directly. Skipped where Tk cannot open a window."""
import os
import time
import unittest

import helpers
import tkinter as tk

import rimstudio_app as core
import rimstudio_gui as gui


def pump(app, secs):
    end = time.time() + secs
    while time.time() < end:
        app.root.update()
        time.sleep(0.02)


def wait_idle(app, limit=60):
    end = time.time() + limit
    while time.time() < end:
        pump(app, 0.1)
        if not app.busy and app.b_pull._enabled:
            return
    raise AssertionError("timed out waiting for the panel")


def quiet(root):
    """Cancel the panel's pending timers, or Tk prints an "invalid command
    name" for each one once the window is gone."""
    for job in root.tk.call("after", "info"):
        root.after_cancel(job)


def ev(**kw):
    return type("E", (), kw)()


class Panel(unittest.TestCase):
    def setUp(self):
        if os.path.exists(core.SESSION_FILE):
            os.remove(core.SESSION_FILE)
        try:
            self.root = tk.Tk()
        except tk.TclError as e:
            self.skipTest("no display: %s" % e)
        self.app = gui.App(self.root)
        pump(self.app, 0.5)
        core.load_pair(*helpers.scene_files())
        self.app.render(fast=False)
        wait_idle(self.app)

    def tearDown(self):
        try:
            quiet(self.root)
            self.root.destroy()
        except Exception:
            pass

    def test_slider_grab_fine_drag_nudge_and_typed_value(self):
        s = self.app.sliders["rim"]
        for sec in [w for w in self.app.col.winfo_children() if isinstance(w, gui.Section)]:
            if not sec.open:
                sec.toggle()
        pump(self.app, 0.3)
        hx, before = int(s._x_of(s.value)), s.value
        s._press(ev(x=hx + 3, y=s.ty, state=0))
        self.assertAlmostEqual(s.value, before)                # grabbing does not jump
        s._motion(ev(x=hx + 33, y=s.ty, state=0x100))
        coarse = s.value - before
        s._done(ev(x=hx + 33, y=s.ty, state=0))
        s.set(before)
        s._press(ev(x=hx, y=s.ty, state=0))
        s._motion(ev(x=hx + 30, y=s.ty, state=0x101))           # Shift = fine
        fine = s.value - before
        s._done(ev(x=hx + 30, y=s.ty, state=0))
        self.assertTrue(0 < fine < coarse)
        s.set(1.0)
        s._nudge(1, ev(state=0))
        self.assertAlmostEqual(s.value, 1.05)
        s._type_value()
        pump(self.app, 0.1)
        s._entry.delete(0, "end")
        s._entry.insert(0, "2,5")
        s._entry.event_generate("<Return>")
        pump(self.app, 0.1)
        self.assertAlmostEqual(s.value, 2.5)

    def test_edge_sliders_exist_and_reach_the_render(self):
        self.assertIn("edge_shrink", self.app.sliders)
        self.assertIn("edge_soft", self.app.sliders)
        self.app.sliders["edge_shrink"].set(2.0)
        self.assertEqual(self.app.params()["edge_shrink"], 2.0)

    def test_views_switch_and_hide_the_split(self):
        self.app.toggle_split()
        for mode in ("matte", "light", "shadow", "result"):
            self.app.set_view(mode)
            wait_idle(self.app)
            self.assertEqual(self.app.view_mode, mode)
            self.assertEqual(self.app._last_arr.shape[2], 3)
            if mode != "result":
                self.assertIsNone(self.app._split_x())
        self.assertIsNotNone(self.app._split_x())
        matte = None
        self.app.set_view("matte")
        wait_idle(self.app)
        matte = self.app._last_arr
        self.assertAlmostEqual(float(matte[0, 0, 0]), 0.0)

    def test_preset_resets_what_it_does_not_mention(self):
        self.app.sliders["glow"].set(2.2)
        core.save_preset("test look", {"rim": 0.5})
        try:
            self.app.apply_preset("test look")
            self.assertEqual(self.app.sliders["glow"].get(), 1.0)
            self.assertEqual(self.app.sliders["rim"].get(), 0.5)
        finally:
            core.delete_preset("test look")

    def test_autos_do_not_block_the_window(self):
        for fn in (self.app.auto_focus, self.app.auto_glow, self.app.auto_shadow):
            t = time.time()
            fn()
            self.assertLess(time.time() - t, 0.5, fn.__name__)
            wait_idle(self.app)

    def test_session_is_restored(self):
        self.app.sliders["rim"].set(3.3)
        quiet(self.root)
        self.app.close()
        self.root = tk.Tk()
        app2 = gui.App(self.root)
        pump(app2, 0.3)
        self.assertAlmostEqual(app2.sliders["rim"].get(), 3.3)
        self.assertEqual(app2.hist[0]["rim"], 3.3)


if __name__ == "__main__":
    unittest.main()
