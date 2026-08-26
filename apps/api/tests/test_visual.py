"""Visual regression: tolerance, localisation, and what outranks what.

The load-bearing claim is that structural comparison leads and pixels support
it. These tests exist because the opposite ordering fails silently: removing a
required field from a form changes well under 1% of a screenshot.
"""

from __future__ import annotations

import pytest

from galeqea.intelligence.pixels import compare_images, describe
from galeqea.intelligence.visual import parse_aria, structural_diff


@pytest.fixture()
def canvas(tmp_path):
    """A baseline image plus helpers to mutate it."""
    from PIL import Image, ImageDraw

    def make(name, mutate=None, size=(400, 300)):
        image = Image.new("RGB", size, (255, 255, 255))
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, size[0], 48), fill=(37, 99, 235))     # header
        draw.rectangle((20, 80, 380, 200), fill=(240, 240, 240))    # body
        draw.rectangle((20, 230, 140, 270), fill=(37, 99, 235))     # button
        if mutate:
            mutate(draw, image)
        path = tmp_path / f"{name}.png"
        image.save(path)
        return str(path)

    return make


# --------------------------------------------------------------------------- #
# Pixel comparison
# --------------------------------------------------------------------------- #
def test_anti_aliasing_noise_is_not_a_change(canvas):
    """An exact compare flags font hinting, which teaches people to ignore it."""
    baseline = canvas("base")
    noisy = canvas("noisy", lambda draw, _: [
        draw.point((x, 150), fill=(252, 252, 252)) for x in range(0, 400, 7)
    ])
    diff = compare_images(baseline, noisy)
    assert diff.regions == []
    assert "identical" in describe(diff)


def test_a_removed_control_is_localised_to_a_region(canvas):
    baseline = canvas("base")
    gone = canvas("gone", lambda draw, _: draw.rectangle((20, 230, 140, 270), fill=(255, 255, 255)))
    diff = compare_images(baseline, gone)
    assert len(diff.regions) == 1
    region = diff.regions[0]
    # The button occupied (20,230)-(140,270); the 16px grid rounds outward.
    assert region["x"] <= 20 and region["y"] <= 230
    assert region["x"] + region["w"] >= 140
    assert region["y"] + region["h"] >= 270


def test_separate_changes_stay_separate_regions(canvas):
    baseline = canvas("base")
    two = canvas("two", lambda draw, _: (
        draw.rectangle((0, 0, 400, 48), fill=(220, 38, 38)),
        draw.rectangle((300, 240, 380, 265), fill=(16, 185, 129)),
    ))
    diff = compare_images(baseline, two)
    assert len(diff.regions) == 2


def test_a_size_change_is_reported_as_such(canvas):
    baseline = canvas("base")
    taller = canvas("taller", size=(400, 420))
    diff = compare_images(baseline, taller)
    assert diff.dimensions_changed
    assert "changed size" in describe(diff)


def test_a_diff_image_is_written_when_something_changed(canvas, tmp_path):
    from pathlib import Path

    baseline = canvas("base")
    gone = canvas("gone", lambda draw, _: draw.rectangle((20, 230, 140, 270), fill=(255, 255, 255)))
    out = tmp_path / "diff.png"
    diff = compare_images(baseline, gone, str(out))
    assert diff.diff_path == str(out)
    assert Path(out).exists()


# --------------------------------------------------------------------------- #
# Structural comparison
# --------------------------------------------------------------------------- #
SNAPSHOT = """- heading "Acme Checkout" [level=1]
- paragraph: A tiny application under test.
- text: Card number
- textbox "Card number":
  - /placeholder: 4242 4242 4242 4242
- checkbox "Remember me" [checked]
- button "Confirm payment"
"""


def test_both_aria_shapes_are_parsed():
    """Named controls AND unnamed text content.

    Reading only the quoted form meant a paragraph changing from "Your order is
    confirmed" to "Your order failed" registered as no change at all.
    """
    pairs = dict(parse_aria(SNAPSHOT))
    assert pairs["textbox"] == "Card number"
    assert pairs["heading"] == "Acme Checkout"          # [level=1] must not break it
    assert pairs["paragraph"] == "A tiny application under test."
    assert pairs["checkbox"] == "Remember me"           # [checked] must not break it


def test_attribute_lines_are_not_mistaken_for_nodes():
    assert not any(role.startswith("/") for role, _ in parse_aria(SNAPSHOT))


def test_a_lost_control_is_separated_from_ordinary_text_change():
    gone = "\n".join(
        line for line in SNAPSHOT.split("\n")
        if "Card number" not in line and "4242" not in line
    )
    diff = structural_diff(SNAPSHOT, gone)
    assert diff["lost_controls"] == ["textbox: Card number"]


def test_a_copy_change_is_detected_but_is_not_a_lost_control():
    reworded = SNAPSHOT.replace("A tiny application under test.", "Your order failed.")
    diff = structural_diff(SNAPSHOT, reworded)
    assert diff["lost_controls"] == []
    assert any("Your order failed." in item for item in diff["added"])
