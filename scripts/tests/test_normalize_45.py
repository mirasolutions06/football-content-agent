#!/usr/bin/env python3
# scripts/tests/test_normalize_45.py
# Plain-python tests for the deterministic 4:5 normalizer (repo has no pytest config).
# Run:  python3 scripts/tests/test_normalize_45.py

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from mandem.image import normalize_to_45  # noqa: E402

W45, H45 = 1080, 1350  # IG-native 4:5


def _mk(w, h, color=(20, 20, 20)):
    d = Path(tempfile.mkdtemp())
    p = d / "in.png"
    Image.new("RGB", (w, h), color).save(p)
    return p


def _mk_jpeg(w, h, *, orientation=None):
    """A JPEG with an asymmetric marker (white block, top-left) so an orientation flip
    visibly changes the crop. Optionally stamps an EXIF orientation tag."""
    d = Path(tempfile.mkdtemp())
    p = d / "in.jpg"
    im = Image.new("RGB", (w, h), (20, 20, 20))
    for y in range(min(h, 300)):
        for x in range(min(w, 300)):
            im.putpixel((x, y), (255, 255, 255))
    if orientation is not None:
        ex = Image.Exif()
        ex[0x0112] = orientation
        im.save(p, "JPEG", exif=ex.tobytes(), quality=95)
    else:
        im.save(p, "JPEG", quality=95)
    return p


def test_tall_input_becomes_exactly_4x5():
    p = _mk(1024, 1536)  # 2:3, the gpt-image portrait size
    out = normalize_to_45(p)
    assert Image.open(out).size == (W45, H45), Image.open(out).size


def test_square_input_becomes_exactly_4x5():
    p = _mk(1024, 1024)
    out = normalize_to_45(p)
    assert Image.open(out).size == (W45, H45), Image.open(out).size


def test_wide_input_becomes_exactly_4x5():
    p = _mk(1536, 1024)
    out = normalize_to_45(p)
    assert Image.open(out).size == (W45, H45), Image.open(out).size


def test_already_4x5_is_idempotent():
    p = _mk(W45, H45)
    out = normalize_to_45(p)
    assert Image.open(out).size == (W45, H45), Image.open(out).size


def test_output_is_jpeg_even_from_png():
    """IG's image_url publish wants a standard format; we standardise on JPEG."""
    p = _mk(1024, 1536)  # _mk writes a .png
    out = normalize_to_45(p)
    assert Path(out).suffix == ".jpg", out
    assert Image.open(out).format == "JPEG", Image.open(out).format
    assert Image.open(out).size == (W45, H45)


def test_webp_input_becomes_jpeg_and_removes_original():
    """gemini can return .webp, which IG rejects — normalize must coerce to .jpg."""
    d = Path(tempfile.mkdtemp())
    p = d / "in.webp"
    Image.new("RGB", (1024, 1536), (30, 40, 50)).save(p, "WEBP")
    out = normalize_to_45(p)
    assert Path(out).suffix == ".jpg", out
    assert Image.open(out).format == "JPEG"
    assert Image.open(out).size == (W45, H45)
    assert not p.exists(), "stale .webp original should be removed"


def test_tall_input_keeps_top_trims_bottom():
    """The Seedream headline lives in the TOP third, and a raw portrait's face is in
    the upper region — so excess height is trimmed off the BOTTOM. Paint a red band at
    the extreme top (must survive) and a white band at the extreme bottom (should be
    trimmed) of a 1024x1536 frame; after normalize the top must read red and the bottom
    must NOT read white."""
    im = Image.new("RGB", (1024, 1536), (20, 20, 20))
    for y in range(0, 90):          # red band at the extreme top (must survive)
        for x in range(1024):
            im.putpixel((x, y), (220, 0, 0))
    for y in range(1456, 1536):     # white band at the extreme bottom (should be trimmed)
        for x in range(1024):
            im.putpixel((x, y), (255, 255, 255))
    d = Path(tempfile.mkdtemp())
    p = d / "banded.png"
    im.save(p)

    out = Image.open(normalize_to_45(p)).convert("RGB")
    assert out.size == (W45, H45)
    # top-centre pixel should be (near) red — the headline/top band kept
    tr, tg, tb = out.getpixel((W45 // 2, 8))
    assert tr > 180 and tg < 80 and tb < 80, f"top not red (not kept): {(tr, tg, tb)}"
    # bottom-centre pixel should NOT be white — the bottom band trimmed away
    br, bg, bb = out.getpixel((W45 // 2, H45 - 8))
    assert not (br > 200 and bg > 200 and bb > 200), f"bottom still white (not trimmed): {(br, bg, bb)}"


def test_exif_orientation_is_applied():
    """A source carrying an EXIF orientation flag must be transposed before crop — not
    read as its stored (sideways) pixels. The same landscape pixels with orientation=6
    (display = portrait) must normalize differently from the untagged landscape."""
    out_plain = Image.open(normalize_to_45(_mk_jpeg(1500, 1000))).convert("RGB")
    out_tagged = Image.open(normalize_to_45(_mk_jpeg(1500, 1000, orientation=6))).convert("RGB")
    assert out_plain.size == (W45, H45) and out_tagged.size == (W45, H45)
    assert out_plain.tobytes() != out_tagged.tobytes(), \
        "EXIF orientation ignored — tagged output identical to untagged"


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
