#!/usr/bin/env python3
# scripts/tests/test_composite_overlay.py
# The identity-SAFE fallback: composite the overlay onto the REAL approved photo
# with code (no generative pass) so the player/kit can never mutate. Output must be
# an exact-4:5 JPEG. Run: python3 scripts/tests/test_composite_overlay.py

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from mandem.stylize import composite_overlay  # noqa: E402

W45, H45 = 1080, 1350


def _src(w=620, h=930):
    d = Path(tempfile.mkdtemp())
    p = d / "src.jpg"
    Image.new("RGB", (w, h), (70, 90, 120)).save(p)
    return p


def _src_jpeg(w, h, *, orientation=None):
    """Source JPEG with an asymmetric marker so an orientation flip visibly changes the
    cover-fit crop. Optionally stamps an EXIF orientation tag."""
    d = Path(tempfile.mkdtemp())
    p = d / "src.jpg"
    im = Image.new("RGB", (w, h), (70, 90, 120))
    for y in range(min(h, 240)):
        for x in range(min(w, 240)):
            im.putpixel((x, y), (250, 250, 250))
    if orientation is not None:
        ex = Image.Exif()
        ex[0x0112] = orientation
        im.save(p, "JPEG", exif=ex.tobytes(), quality=95)
    else:
        im.save(p, "JPEG", quality=95)
    return p


def test_output_is_exact_4x5_jpeg():
    out = composite_overlay(_src(), "TIMELESS")
    im = Image.open(out)
    assert im.size == (W45, H45), im.size
    assert im.format == "JPEG", im.format


def test_landscape_source_also_4x5():
    out = composite_overlay(_src(1600, 900), "SCENES")
    assert Image.open(out).size == (W45, H45)


def test_handles_empty_word():
    out = composite_overlay(_src(), "")
    assert Image.open(out).size == (W45, H45)


def test_exif_orientation_is_applied():
    """The identity-safe fallback must honour EXIF orientation so it can't ship a
    sideways photo. Same landscape pixels, one tagged orientation=6 → different output."""
    out_plain = Image.open(composite_overlay(_src_jpeg(1500, 1000), "X")).convert("RGB")
    out_tagged = Image.open(composite_overlay(_src_jpeg(1500, 1000, orientation=6), "X")).convert("RGB")
    assert out_plain.size == (W45, H45) and out_tagged.size == (W45, H45)
    assert out_plain.tobytes() != out_tagged.tobytes(), \
        "EXIF orientation ignored in composite_overlay"


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
