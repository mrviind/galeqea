"""Build docs/media/galeqea-ui-tour.gif from the light/dark screenshot set.

One-off build script (not part of the app), run manually whenever the
screenshot set changes. Produces a captioned, crossfaded slideshow: a full
dark-mode tour of the four core screens, then the same four in light mode,
looping seamlessly back to the start.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

MEDIA = Path(__file__).resolve().parents[1] / "docs" / "media"
OUT = MEDIA / "galeqea-ui-tour.gif"

WIDTH = 760
CAPTION_H = 40
HOLD_MS = 1400
FADE_MS = 300
FADE_STEPS = 6

DARK_BG, DARK_FG = (17, 19, 26), (230, 237, 243)
LIGHT_BG, LIGHT_FG = (244, 245, 247), (17, 19, 26)

SLIDES = [
    ("workspace-dark.png", "Workspace", "Dark", DARK_BG, DARK_FG),
    ("requirements-dark.png", "Requirements & coverage", "Dark", DARK_BG, DARK_FG),
    ("run-detail-dark.png", "Run detail", "Dark", DARK_BG, DARK_FG),
    ("command-dark.png", "Command dashboard", "Dark", DARK_BG, DARK_FG),
    ("workspace-light.png", "Workspace", "Light", LIGHT_BG, LIGHT_FG),
    ("requirements-light.png", "Requirements & coverage", "Light", LIGHT_BG, LIGHT_FG),
    ("run-detail-light.png", "Run detail", "Light", LIGHT_BG, LIGHT_FG),
    ("command-light.png", "Command dashboard", "Light", LIGHT_BG, LIGHT_FG),
]


def _font(size: int) -> ImageFont.ImageFont:
    for candidate in (
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ):
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def _build_slide(name: str, label: str, theme: str, bg, fg) -> Image.Image:
    img = Image.open(MEDIA / name).convert("RGB")
    h = round(img.height * WIDTH / img.width)
    img = img.resize((WIDTH, h), Image.LANCZOS)

    canvas = Image.new("RGB", (WIDTH, h + CAPTION_H), bg)
    canvas.paste(img, (0, 0))

    draw = ImageDraw.Draw(canvas)
    font = _font(18)
    text = f"{label}  ·  {theme} mode"
    draw.text((18, h + CAPTION_H // 2), text, fill=fg, font=font, anchor="lm")

    accent = (92, 147, 201)  # GaleQEA's blue accent, same in both themes
    draw.ellipse([WIDTH - 30, h + CAPTION_H // 2 - 4, WIDTH - 22, h + CAPTION_H // 2 + 4], fill=accent)
    return canvas


def _crossfade(a: Image.Image, b: Image.Image, steps: int) -> list[Image.Image]:
    return [Image.blend(a, b, i / steps) for i in range(1, steps)]


def main() -> None:
    stills = [_build_slide(*s) for s in SLIDES]

    frames: list[Image.Image] = []
    durations: list[int] = []

    n = len(stills)
    for i, still in enumerate(stills):
        frames.append(still)
        durations.append(HOLD_MS)
        nxt = stills[(i + 1) % n]
        fade = _crossfade(still, nxt, FADE_STEPS)
        frames.extend(fade)
        durations.extend([FADE_MS // FADE_STEPS] * len(fade))

    palette_frames = [f.convert("P", palette=Image.ADAPTIVE, colors=128) for f in frames]
    palette_frames[0].save(
        OUT,
        save_all=True,
        append_images=palette_frames[1:],
        duration=durations,
        loop=0,
        optimize=True,
    )
    print(f"wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KiB, {len(frames)} frames)")


if __name__ == "__main__":
    main()
