import os
import unittest

import helpers
import numpy as np
from PIL import Image

import rimstudio_app as core
import rimstudio_gui as gui

SLIDERS = gui.MATCH + gui.EDGE + gui.GLOW + gui.RIM + gui.RIM_SHAPE + gui.SHADOW
DEFAULTS = dict((d[0], d[4]) for d in SLIDERS)


class Core(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sp, cls.bp = helpers.scene_files()
        cls.info = core.load_pair(cls.sp, cls.bp)

    def test_settings_live_in_the_sandbox(self):
        self.assertTrue(core.SESSION_FILE.startswith(helpers.SANDBOX))

    def test_every_slider_is_an_engine_parameter(self):
        for key in DEFAULTS:
            self.assertIn(key, core.rim_engine.DEFAULTS, key)

    def test_presets_only_name_real_sliders(self):
        for name, p in core.BUILTIN_PRESETS.items():
            self.assertFalse(set(p) - set(DEFAULTS), name)

    def test_views(self):
        k = "prev"
        args = (core.STATE["sub_" + k], core.STATE["bg_" + k], DEFAULTS)
        kw = {"pre": core.STATE["pre_" + k]}
        result = core.composite(*args, **kw)
        for view in core.VIEWS:
            arr = core.composite(*args, view=view, **kw)
            self.assertEqual(arr.shape, result.shape, view)
            self.assertTrue(np.isfinite(arr).all(), view)
        matte = core.composite(*args, view="matte", **kw)
        self.assertAlmostEqual(float(matte[0, 0, 0]), 0.0)          # corner: plate
        self.assertAlmostEqual(float(matte[160, 200, 0]), 1.0, 3)   # disc centre
        light = core.composite(*args, view="light", **kw)
        self.assertEqual(float(light[0, 0].max()), 0.0)             # nothing outside
        shadow = core.composite(*args, view="shadow", **kw)
        np.testing.assert_allclose(shadow, np.clip(core.STATE["bg_prev"], 0, 1))

    def test_render_full_overwrites_unless_unique(self):
        p = dict(DEFAULTS, shadow=0.6)
        a = core.render_full(p)
        b = core.render_full(p)
        self.assertEqual(a, b)
        u1 = core.render_full(p, unique=True)
        u2 = core.render_full(p, unique=True)
        self.assertNotEqual(u1["relit"], u2["relit"])
        for v in list(u1.values()) + list(u2.values()):
            self.assertTrue(v is None or os.path.exists(v), v)
        self.assertTrue(u2["shadow"].endswith(" - shadow 3.png"), u2["shadow"])

    def test_composite_save_is_never_overwritten(self):
        c1 = core.render_composite(DEFAULTS)
        c2 = core.render_composite(DEFAULTS)
        self.assertNotEqual(c1, c2)
        self.assertEqual(np.asarray(Image.open(c1)).shape, (helpers.H, helpers.W, 3))

    def test_settings_round_trip_and_corrupt_file(self):
        core.save_settings(dict(DEFAULTS, _private=1))
        self.assertEqual(core.load_settings(),
                         dict((k, float(v)) for k, v in DEFAULTS.items()))
        with open(core.SESSION_FILE, "w") as f:
            f.write("{corrupt")
        self.assertEqual(core.load_settings(), {})

    def test_send_script_escapes_names(self):
        s = core.build_send_jsx({"relit": r"C:\a\b - relit.png", "shadow": None,
                                 "glow": r"C:\a\b - glow.png"}, 'Model "final"\\v2')
        self.assertIn('"C:/a/b - relit.png"', s)
        self.assertIn('Model \\"final\\"\\\\v2', s)
        self.assertIn("var SUBJ = null;", core.build_send_jsx({"relit": "x.png"}, None))


class Friendly(unittest.TestCase):
    def test_traceback_becomes_one_line_and_is_logged(self):
        tb = "Traceback (most recent call last):\n  File x\nValueError: bad thing"
        out = gui.App._friendly(tb)
        self.assertTrue(out.startswith("ValueError: bad thing"))
        with open(core.LOG_FILE, encoding="utf-8") as f:
            self.assertIn("bad thing", f.read())

    def test_own_messages_pass_through(self):
        msg = "Photoshop has no document open."
        self.assertEqual(gui.App._friendly(msg), msg)


if __name__ == "__main__":
    unittest.main()
