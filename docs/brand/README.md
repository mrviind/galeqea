# QE Agent brand

## The mark

A capital **T** with a break in the stem — the gate every change passes through.
Three rectangles, no curves, no gradient.

```
████████████████   crossbar
      ██           stem
      ▁▁           ← the gate
      ██           stem
```

`mark.svg` uses `currentColor`, so it takes the colour of whatever it sits in.

### Why monochrome

In a test platform, **colour is signal**. Green means passed, red means failed,
amber means unstable. A brand colour that competes with those makes the signal
harder to read, and a violet-to-cyan gradient additionally reads as "generic AI
product" — a category, not an identity. So the brand is expressed through
geometry and typography, and every drop of saturation is reserved for status.

This follows the same reasoning Linear and Vercel apply: sparse visually,
dense in behaviour.

### Rules

- **Clear space:** one crossbar-height on all sides.
- **Minimum size:** 16px. Below that, use the wordmark alone.
- **Never:** add a gradient, rotate it, outline it, or place it on a busy image.
- **Inverted:** swap to the canvas colour on a light ground. Nothing else changes.

## Colour

The surface palette is a neutral near-black — deliberately not blue-tinted, so
status hues render true against it.

| Token | Hex | Use |
|---|---|---|
| `canvas` | `#0b0c0e` | page ground |
| `surface` | `#111214` | panels |
| `surface-2` | `#17181b` | controls, inputs |
| `surface-3` | `#1e2024` | hover, wells |
| `line` | `#26282d` | hairlines |
| `ink` | `#e8e9ec` | primary text |
| `ink-2` | `#a0a4ad` | secondary text |
| `ink-3` | `#6e727c` | tertiary text |
| `accent` | `#58a6ff` | focus, links, live state |

Status colours are GitHub Primer's dark-mode set — chosen because they are
already proven for contrast on a near-black ground:

| State | Hex |
|---|---|
| passed | `#3fb950` |
| failed | `#f85149` |
| flaky | `#d29922` |
| running | `#58a6ff` |
| blocked | `#db6d28` |
| needs review | `#39c5cf` |

## Geometry

A radius ramp tuned to element size — a 20px chip and a 600px panel need
different radii to *look* equally soft:

| Token | Value | Applied to |
|---|---|---|
| `sm` | 6px | small inline affordances |
| `md` | 10px | chips, risk badges, code wells |
| `lg` | 14px | panels, buttons, inputs, nav items |
| `xl` | 20px | chat bubbles, the composer, agent cards |

**Status badges are capsules.** A fully-rounded shape reads as a *label* rather
than a button — which is what a status is. Meters are capsules for the same
reason. Everything you can click is rectangular-with-soft-corners, so shape
alone tells you what is interactive.

## The agent border

A surface the agent is currently acting on carries a light travelling around its
border — the `.agent-glow` class. It is drawn as a masked gradient on a
pseudo-element so it traces the rounded corners exactly rather than sitting as a
bar across the top of them.

It sweeps through the accent blue rather than a multi-hue gradient: the motion
is what communicates "working", and a rainbow would put saturation somewhere
status has not authorised.

## Type

**Inter** for the interface — the de-facto typeface for developer tooling, with
tabular figures enabled so numbers in a table do not shift as they update.
**JetBrains Mono** for logs, locators and code.

## Sheets

- `system.html` — the whole visual system on one page: mark, surfaces, status,
  controls, type. Open it in a browser to check a change against the system.
- `concepts.html` — the logo alternatives and why each was rejected.

## Concepts considered

Three alternatives were drawn and rejected: a bracketed `[T]` (most ownable, but
muddy below 20px), ascending bars (clean, but it is every analytics logo ever
made), and stacked gate rails (handsome, but it never says "T").

Worth recording: the first round's mark put the stem *above* the crossbar and
read as a crucifix rather than a T. A reminder to check a mark's silhouette, not
just the idea behind it.
