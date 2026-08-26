"""Pixel comparison for visual regression.

Two decisions define this module, and both exist to stop it crying wolf:

**Tolerance.** An exact per-pixel compare flags font hinting, sub-pixel
anti-aliasing and a one-unit shift in a gradient — noise that differs between
two runs on the same machine. A channel must differ by more than
``CHANNEL_TOLERANCE`` before the pixel counts as changed at all.

**Regions, not confetti.** A list of 40,000 changed pixels tells a reviewer
nothing. The image is divided into a coarse grid; a cell counts as changed only
when enough of it changed, and adjacent changed cells are merged into boxes. The
output is "three regions changed, the largest is the header" — something a
person can actually act on.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

#: Per-channel difference below which two pixels are considered the same.
#: Chosen to absorb anti-aliasing without hiding a colour change.
CHANNEL_TOLERANCE = 32

#: Grid cell size in pixels. Small enough to localise a changed button, large
#: enough that a reviewer gets regions rather than a mosaic.
CELL = 16

#: Fraction of a cell that must change before the cell is marked.
CELL_THRESHOLD = 0.06


@dataclass(slots=True)
class PixelDiff:
    available: bool = True
    changed_pct: float = 0.0
    regions: list[dict] = field(default_factory=list)
    dimensions_changed: bool = False
    baseline_size: tuple[int, int] = (0, 0)
    candidate_size: tuple[int, int] = (0, 0)
    diff_path: str = ""
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "available": self.available,
            "changed_pct": round(self.changed_pct, 3),
            "regions": self.regions,
            "dimensions_changed": self.dimensions_changed,
            "baseline_size": list(self.baseline_size),
            "candidate_size": list(self.candidate_size),
            "diff_path": self.diff_path,
            "reason": self.reason,
        }


def compare_images(baseline_path: str, candidate_path: str, diff_path: str = "") -> PixelDiff:
    """Compare two screenshots and localise what changed."""
    try:
        from PIL import Image, ImageChops, ImageDraw
    except ImportError:
        return PixelDiff(
            available=False,
            reason="Pillow is not installed, so only structural comparison ran",
        )

    if not (Path(baseline_path).exists() and Path(candidate_path).exists()):
        return PixelDiff(available=False, reason="one of the images is missing")

    with Image.open(baseline_path) as raw_baseline, Image.open(candidate_path) as raw_candidate:
        baseline = raw_baseline.convert("RGB")
        candidate = raw_candidate.convert("RGB")

        result = PixelDiff(
            baseline_size=baseline.size,
            candidate_size=candidate.size,
            dimensions_changed=baseline.size != candidate.size,
        )

        # A size change is itself the finding. Compare the overlapping area so
        # the reviewer still sees *what* moved, not just that something did.
        if result.dimensions_changed:
            width = min(baseline.width, candidate.width)
            height = min(baseline.height, candidate.height)
            if width < 2 or height < 2:
                result.changed_pct = 100.0
                result.reason = "the screenshots have no overlapping area to compare"
                return result
            baseline = baseline.crop((0, 0, width, height))
            candidate = candidate.crop((0, 0, width, height))

        delta = ImageChops.difference(baseline, candidate)
        # Collapse RGB into a single per-pixel magnitude, then threshold it.
        mask = delta.convert("L").point(lambda value: 255 if value > CHANNEL_TOLERANCE else 0)

        width, height = mask.size
        pixels = mask.load()
        cols = max(1, (width + CELL - 1) // CELL)
        rows = max(1, (height + CELL - 1) // CELL)

        changed_cells: set[tuple[int, int]] = set()
        changed_pixels = 0
        for row in range(rows):
            for col in range(cols):
                x0, y0 = col * CELL, row * CELL
                x1, y1 = min(x0 + CELL, width), min(y0 + CELL, height)
                hits = 0
                # Sample every second pixel: at CELL=16 that is 64 samples per
                # cell, plenty to decide a 6% threshold and four times faster.
                for y in range(y0, y1, 2):
                    for x in range(x0, x1, 2):
                        if pixels[x, y]:
                            hits += 1
                sampled = max(1, ((x1 - x0 + 1) // 2) * ((y1 - y0 + 1) // 2))
                if hits / sampled >= CELL_THRESHOLD:
                    changed_cells.add((col, row))
                    changed_pixels += hits * 4

        result.changed_pct = 100.0 * changed_pixels / max(1, width * height)
        result.regions = _merge_cells(changed_cells, width, height)

        if diff_path and result.regions:
            annotated = candidate.copy()
            draw = ImageDraw.Draw(annotated, "RGBA")
            for region in result.regions:
                box = (region["x"], region["y"],
                       region["x"] + region["w"], region["y"] + region["h"])
                draw.rectangle(box, fill=(248, 81, 73, 48), outline=(248, 81, 73, 255), width=2)
            Path(diff_path).parent.mkdir(parents=True, exist_ok=True)
            annotated.save(diff_path)
            result.diff_path = diff_path

    return result


def _merge_cells(cells: set[tuple[int, int]], width: int, height: int) -> list[dict]:
    """Flood-fill adjacent changed cells into rectangles, largest first."""
    if not cells:
        return []

    remaining = set(cells)
    regions: list[dict] = []

    while remaining:
        seed = remaining.pop()
        cluster = {seed}
        frontier = [seed]
        while frontier:
            col, row = frontier.pop()
            for dcol, drow in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
                neighbour = (col + dcol, row + drow)
                if neighbour in remaining:
                    remaining.discard(neighbour)
                    cluster.add(neighbour)
                    frontier.append(neighbour)

        cols = [c for c, _ in cluster]
        rows = [r for _, r in cluster]
        x = min(cols) * CELL
        y = min(rows) * CELL
        w = min(width, (max(cols) + 1) * CELL) - x
        h = min(height, (max(rows) + 1) * CELL) - y
        regions.append({"x": x, "y": y, "w": w, "h": h, "cells": len(cluster)})

    regions.sort(key=lambda r: r["w"] * r["h"], reverse=True)
    # A reviewer will look at three boxes; forty is a heatmap they will ignore.
    return regions[:12]


def perceptual_hash(image_path: str, size: int = 9) -> str:
    """Difference hash: robust to scale and mild compression, not to content."""
    try:
        from PIL import Image
    except ImportError:
        import hashlib

        data = Path(image_path).read_bytes() if Path(image_path).exists() else b""
        return hashlib.blake2b(data, digest_size=8).hexdigest()

    with Image.open(image_path) as raw:
        grey = raw.convert("L").resize((size, size - 1))
        pixels = list(grey.getdata())

    bits = "".join(
        "1" if pixels[row * size + col] > pixels[row * size + col + 1] else "0"
        for row in range(size - 1)
        for col in range(size - 1)
    )
    return f"{int(bits, 2):016x}"


def describe(diff: PixelDiff) -> str:
    """One sentence a reviewer can read without opening the images."""
    if not diff.available:
        return diff.reason
    if diff.dimensions_changed:
        bw, bh = diff.baseline_size
        cw, ch = diff.candidate_size
        return f"the screen changed size, {bw}×{bh} → {cw}×{ch}"
    if not diff.regions:
        return "pixel-identical within anti-aliasing tolerance"
    largest = diff.regions[0]
    return (
        f"{len(diff.regions)} region(s) changed, {diff.changed_pct:.1f}% of the image; "
        f"the largest is {largest['w']}×{largest['h']} at ({largest['x']}, {largest['y']})"
    )
