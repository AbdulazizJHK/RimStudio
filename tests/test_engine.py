import unittest

import helpers  # noqa: F401  (sandboxes APPDATA before the app imports)
import numpy as np

import rim_engine


def square(n=40, size=64):
    a = np.zeros((size, size), np.float32)
    o = (size - n) // 2
    a[o:o + n, o:o + n] = 1.0
    return a


class RefineAlpha(unittest.TestCase):
    def test_zero_is_identity(self):
        a = square()
        np.testing.assert_array_equal(rim_engine.refine_alpha(a), a)

    def test_shrink_removes_one_pixel_per_unit(self):
        a = square(40)
        self.assertEqual(rim_engine.refine_alpha(a, 1.0).sum(), 38 * 38)
        self.assertEqual(rim_engine.refine_alpha(a, 2.0).sum(), 36 * 36)

    def test_negative_shrink_spreads(self):
        a = square(40)
        # the 4-neighbourhood grows a square into a square with clipped corners
        self.assertGreater(rim_engine.refine_alpha(a, -1.0).sum(), a.sum())

    def test_fraction_lands_between_whole_steps(self):
        a = square(40)
        lo, mid, hi = (rim_engine.refine_alpha(a, v).sum() for v in (1.0, 1.5, 2.0))
        self.assertTrue(hi < mid < lo)

    def test_soften_makes_a_ramp_and_keeps_the_middle(self):
        s = rim_engine.refine_alpha(square(40), 0.0, 3.0)
        edge = s[32, 10:16]
        self.assertTrue(((edge > 0.01) & (edge < 0.99)).any())
        self.assertAlmostEqual(float(s[32, 32]), 1.0, places=4)

    def test_values_scale_with_resolution(self):
        # a 1px choke at full size is half a pixel on a half-size preview
        a = square(40)
        self.assertLess(rim_engine.refine_alpha(a, 1.0, scale=0.5).sum(),
                        a.sum())
        self.assertGreater(rim_engine.refine_alpha(a, 1.0, scale=0.5).sum(),
                           rim_engine.refine_alpha(a, 1.0).sum())


class Relight(unittest.TestCase):
    def setUp(self):
        self.sub, self.plate = helpers.scene()

    def test_shape_and_range(self):
        out = rim_engine.relight(self.sub, self.plate)
        self.assertEqual(out.shape, self.sub.shape)
        self.assertGreaterEqual(out.min(), 0.0)
        self.assertLessEqual(out.max(), 1.0)

    def test_rim_takes_each_side_colour(self):
        out = rim_engine.relight(self.sub, self.plate)
        left, right = out[160, 124], out[160, 276]        # just inside the disc
        self.assertGreater(left[0], left[2])               # warm on the warm side
        self.assertGreater(right[2], right[0])             # blue on the blue side

    def test_shrink_reaches_the_output_matte(self):
        base = rim_engine.relight(self.sub, self.plate)[..., 3].sum()
        choked = rim_engine.relight(self.sub, self.plate, {"edge_shrink": 3})[..., 3].sum()
        self.assertLess(choked, base)

    def test_spread_fills_revealed_pixels_with_subject_colour(self):
        sub = self.sub.copy()
        sub[..., :3][sub[..., 3] == 0] = 0.0       # what cut-out tools leave: black
        out = rim_engine.relight(sub, self.plate, {"edge_shrink": -3,
                                                   "glow": 0})
        # 3px at the 2400px reference is half a pixel on this 400px scene
        ring = (out[..., 3] > 0.2) & (sub[..., 3] == 0)
        self.assertTrue(ring.any())
        # without the fill these would be black; the subject is mid grey
        self.assertGreater(float(out[..., :3][ring].mean()), 0.2)

    def test_parts_for_the_diagnostic_views(self):
        parts = {}
        rim_engine.relight(self.sub, self.plate, parts=parts)
        self.assertEqual(parts["alpha"].shape, self.sub.shape[:2])
        self.assertEqual(parts["light"].shape, self.sub.shape[:2] + (3,))
        self.assertGreater(float(parts["light"].max()), 0.0)

    def test_shadow_follows_the_refined_matte(self):
        p = {"shadow": 0.8}
        a = rim_engine.shadow_for(self.sub, p).sum()
        b = rim_engine.shadow_for(self.sub, dict(p, edge_shrink=3)).sum()
        self.assertLess(b, a)


if __name__ == "__main__":
    unittest.main()
