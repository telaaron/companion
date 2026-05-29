# Companion — Brand Guide

Single source of truth for visual identity. Anyone shipping a screen,
asset, or doc that has the Companion mark on it should read this once.

---

## Concept

Companion is **the calm AI in your local workspace**. Not the loud
chatbot; the quiet collaborator that stays out of your way until you
need it. The visual language is built around two ideas:

- **Orbit** — concentric arcs that open toward the user, suggesting an
  AI that surrounds and supports work without enclosing it.
- **Restraint** — no faces, no sci-fi neon, no marketing-illustration
  smiles. The product is a tool; the visuals are precision instruments.

---

## Logo

The mark is three concentric arcs forming an opening "C", rendered on
a soft dark canvas with a rounded square cut.

Files:

- `branding/icon.svg` — primary 1024×1024 icon, transparent corners
- `branding/social-preview.svg` — 1280×640 GitHub repo header

### Construction rules

- **Centre:** geometric centre of the square
- **Padding:** ~14 % of the canvas width on all sides
- **Arcs:** outer (r=340), middle (r=240), inner (r=140) on the 1024 grid
- **Stroke weights:** 56 / 48 / 40 px (outer → inner)
- **Opacity ramp:** 1.0 / 0.85 / 0.7 (outer → inner)
- **Gradient direction:** top-left → bottom-right, always
- **Corner radius:** 232 px on 1024 (≈ 22.6 %) — matches the iOS
  rounded-rect

### What not to do

- ❌ Do not rotate the mark — the "C" opens to the right, always
- ❌ Do not invert the gradient — start violet (#8b5cf6), end indigo (#6366f1)
- ❌ Do not place the mark on bright backgrounds — it lives on dark
- ❌ Do not add the wordmark *inside* the icon
- ❌ Do not change the arc count

---

## Colour palette

### Brand

| Token | Hex | Use |
|---|---|---|
| `--brand-violet` | `#8b5cf6` | Gradient start |
| `--brand-indigo` | `#6366f1` | Gradient end + accent |
| `--brand-soft-violet` | `#a78bfa` | Inner-arc gradient start |
| `--brand-soft-indigo` | `#818cf8` | Inner-arc gradient end |

### Surface (dark theme, default)

| Token | Hex |
|---|---|
| `--bg` | `#0c0d10` |
| `--bg-elev` | `#14161b` |
| `--bg-card` | `#1a1d24` |
| `--border` | `#262a32` |
| `--fg` | `#e6e9ef` |
| `--fg-muted` | `#8b91a0` |
| `--fg-dim` | `#5d6373` |

### Status

| Token | Hex |
|---|---|
| `--ok` | `#4ade80` |
| `--warn` | `#f59e0b` |
| `--error` | `#ef4444` |

Source of truth: `web/src/app.css` `:root` block.

---

## Typography

| Role | Family | Tracking | Weight |
|---|---|---|---|
| UI body + product text | system-ui (SF Pro, Segoe UI, Roboto) | 0 | 400 |
| Headings | system-ui | -0.5 to -1 | 600 |
| Monospace / code / IDs | ui-monospace (SF Mono, Menlo, Consolas) | 0 | 400 |

Never bundle a custom font with the desktop app — startup time and disk
budget come first.

---

## Voice

- **Concise.** A label is two words, not five.
- **Honest.** Empty states say what's missing and how to add it.
- **No marketing copy.** No "supercharge", no "10×", no "unleash".
- **English first**, German is welcome but route through translation
  files if more than a handful of strings.

Examples (good → bad):

- ✅ "Send a message to start." vs ❌ "Get ready to unlock your AI superpowers!"
- ✅ "No skills installed" vs ❌ "Looks like you haven't activated any abilities yet"
- ✅ "Voice mode is disabled (VOICE_NOTE_ENABLED=false)." vs ❌ "Oops! Microphone unavailable 😢"

---

## Asset checklist

Each release ships:

| Asset | Size | Format | Location |
|---|---|---|---|
| App icon | 1024×1024 | PNG | `tauri/src-tauri/icons/icon.png` |
| macOS bundle | various | `.icns` | `tauri/src-tauri/icons/icon.icns` |
| Windows bundle | various | `.ico` | `tauri/src-tauri/icons/icon.ico` |
| GitHub social preview | 1280×640 | PNG | uploaded via repo Settings → Social preview |
| Favicon (web UI) | 32×32 | SVG | `web/src/lib/assets/favicon.svg` |
| README hero | 1280×640 | PNG | `branding/social-preview.png` (committed) |

Regen flow:

```bash
# 1. Edit branding/icon.svg if the source changed
# 2. Rasterize to PNG
sips -s format png branding/icon.svg --out branding/icon.png -Z 1024

# 3. Run Tauri's icon generator — produces .icns + .ico + iOS + Android sizes
cd tauri/src-tauri && cargo tauri icon ../../branding/icon.png

# 4. For the social-preview banner:
sips -s format png branding/social-preview.svg --out branding/social-preview.png -Z 640
```

(`sips` ships with macOS. On Linux use `rsvg-convert` or ImageMagick.)

---

## Future: AI-generated illustrations

When we add docs / marketing pages that need illustrations beyond the
icon, follow the `image-gen` skill rules:

1. **One style bucket.** Pick *modern flat vector* and stay in it.
2. **Recraft v3** (`fal-ai/recraft-v3`, `style: vector_illustration`) is
   the default. Flux-Pro only for premium photography.
3. **No people**, no faces, no realistic photography of devices unless
   it's a real product shot.
4. **Palette lock**: every illustration uses the brand violet + indigo
   plus the dark surface tokens. No off-brand colours.
5. **Negative cues**: "no AI artifacts, no warped hands, no fake logos,
   no text inside the image".

Sample prompt template (for a hypothetical "Projects" page hero):

```
Modern flat vector illustration. A clean isometric stack of three
translucent project cards floating against a dark indigo background.
Each card hints at a different workflow (notes, code, tasks) using
abstract glyphs — no real UI screens. Subtle violet-to-indigo gradient
overlay across the stack. Composition centered, generous negative space.
No people, no faces, no text inside the image, no AI watermarks.
```

Save generated outputs into `branding/illustrations/<slug>.png` plus a
matching `.svg` if Recraft returns one.
