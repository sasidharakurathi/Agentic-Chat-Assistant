# Assistant Studio design system: "Line diagram"

The source of truth for how the web app looks, reads and moves. Chosen
2026-09-30 from three critiqued directions (patch bay, line diagram,
instrument bench); this is the line-diagram direction with the critics'
fixes applied and the patch-bay's signal families folded in.

## 1. The idea

An assistant's pipeline really is a route. A message comes in, passes a
few stops in a fixed order (Input, Guardrails, Router, Agent, Output),
and everything the assistant can use joins that route at the Agent. The
app borrows only what makes transit maps and signalling diagrams readable
at a glance:

- **one ink main line with numbered stops** (the pipeline is a true
  sequence, so numbers are information here, not decoration);
- **a small set of line colours** saying what kind of capability joins the
  Agent: knowledge, actions, helpers;
- **lamps** for state (ready, check this, broken), which never share a
  colour with a line;
- **a yellow "you are here" marker** for what is selected or current.

The boldness is spent in one place: **the main line on the builder
canvas**, and its vertical twin, the route strip in Run details. Every
other surface is calm ink on enamel paper, set in Fira.

The metaphor stays in the drawing, never in the words. No "stations",
"stops", "boarding" or train puns in the UI copy. The only borrowed words
are "Key" (the canvas legend) and "Route" (in Run details).

## 2. Colour

Tokens live in `apps/web/app/globals.css` on `:root` and `.dark`, mapped
to Tailwind through `@theme inline`. Values are final: they were checked
for WCAG contrast (text 4.5:1, UI boundaries 3:1) and for OKLab distance
between every line colour and every lamp colour (all at least 0.10).

### Light ("enamel")

| Token | Value | Hex | Role |
|---|---|---|---|
| `background` | `oklch(0.972 0.004 160)` | #F4F7F5 | Enamel paper: page ground, rail, headers |
| `card` | `oklch(0.995 0.002 160)` | #FCFEFD | Plate: stations, plates, drawers, menus, dialogs |
| `foreground` | `oklch(0.24 0.018 165)` | #17221D | Ink: all text |
| `muted` | `oklch(0.94 0.006 160)` | #E8ECEA | Hover rows, active nav fill, user message plate, code ground, segmented track |
| `muted-foreground` | `oklch(0.47 0.015 165)` | #535E59 | Secondary text and hints (5.2:1 on muted) |
| `border` | `oklch(0.885 0.007 160)` | #D5DAD7 | Rules between rows and groups, plate edges. Grouping only, never a control's only edge |
| `primary` | `oklch(0.26 0.02 165)` | #1B2722 | Ink buttons, the main line, active tab bar, focus ring |
| `primary-foreground` | `oklch(0.99 0.003 160)` | #FAFCFB | Text on ink |
| `ring` | = primary | | 2px focus ring with a 2px background-coloured offset |
| `marker` (new) | `oklch(0.85 0.16 92)` | #F3C935 | "You are here": selected station band, active nav bar. Never alone: always beside an ink edge or ink text |
| `marker-foreground` (new) | = foreground | | Text on the marker |
| `destructive` | `oklch(0.53 0.19 27)` | #C22826 | Things that block: errors, failed steps, final delete |
| `destructive-foreground` (new) | `oklch(0.99 0.003 160)` | | Text on a red fill |
| `success` | `oklch(0.5 0.12 148)` | #277539 | Ready, connected, saved, passed, valid wiring target |
| `success-foreground` (new) | `oklch(0.99 0.003 160)` | | Text on a green fill |
| `warning` | `oklch(0.53 0.11 70)` | #945F0E | Caution ochre: warnings, approval bar, budget over 80%. Text-safe on paper, plate and muted |
| `warning-foreground` (new) | `oklch(0.99 0.003 160)` | | Text on an ochre fill (replaces hard-coded `text-white`) |
| `field` | = card | | Input fill |
| `field-border` | `oklch(0.62 0.012 165)` | #808984 | Input edge, 3.0:1 or better on field, paper and muted |
| `field-border-hover` | `oklch(0.5 0.015 165)` | #5C6661 | Input edge on hover |
| `canvas` (new) | `oklch(0.945 0.006 160)` | #EAEEEB | React Flow pane, a step below paper so plates read as signs |
| `canvas-grid` (new) | `oklch(0.8 0.008 160)` | #BABFBC | 1.25px dots every 24px, which is the snap grid |
| `station-border` (new) | `oklch(0.6 0.012 165)` | #7A837E | 1.5px station plate edge, 3.2:1 on canvas |
| `line-unlit` (new) | `oklch(0.83 0.006 160)` | #C4C8C6 | Lines and bullets not on a traced route, or dimmed by the Key |
| `shadow-color` | `oklch(0.24 0.018 165 / 0.12)` | | Only for floating layers |
| `scrollbar-thumb` | `oklch(0.24 0.018 165 / 0.22)` | | |

### Dark ("signal panel")

Same hue family as light (green-grey, hue 160 to 165), so the two modes
are one product.

| Token | Value | Hex |
|---|---|---|
| `background` | `oklch(0.2 0.008 165)` | #131715 |
| `card` | `oklch(0.235 0.009 165)` | #1A1F1D |
| `foreground` | `oklch(0.94 0.006 160)` | #E8ECEA |
| `muted` | `oklch(0.27 0.01 165)` | #222825 |
| `muted-foreground` | `oklch(0.74 0.012 165)` | #A4ADA9 |
| `border` | `oklch(0.33 0.01 165)` | #313734 |
| `primary` | `oklch(0.93 0.008 160)` | #E4EAE6 (lamp-white buttons with ink text) |
| `primary-foreground` | `oklch(0.22 0.018 165)` | #131D18 |
| `ring` | = primary | |
| `marker` | `oklch(0.88 0.15 100)` | #EFD956 |
| `marker-foreground` | `oklch(0.22 0.018 165)` | |
| `destructive` | `oklch(0.72 0.17 25)` | #FD736D |
| `destructive-foreground` | `oklch(0.2 0.03 25)` | |
| `success` | `oklch(0.77 0.14 150)` | #6CCD83 |
| `success-foreground` | `oklch(0.2 0.03 150)` | |
| `warning` | `oklch(0.78 0.14 62)` | #F6A14F |
| `warning-foreground` | `oklch(0.22 0.03 62)` | |
| `field` | `oklch(0.18 0.008 165)` | #0E1311 (recessed) |
| `field-border` | `oklch(0.56 0.012 165)` | #6E7773 |
| `field-border-hover` | `oklch(0.68 0.012 165)` | #929B96 |
| `canvas` | `oklch(0.18 0.008 165)` | #0E1311 |
| `canvas-grid` | `oklch(0.31 0.01 165)` | #2C322F |
| `station-border` | `oklch(0.55 0.012 165)` | #6B7470 |
| `line-unlit` | `oklch(0.38 0.01 165)` | #3E4441 |
| `shadow-color` | `oklch(0 0 0 / 0.5)` | |
| `scrollbar-thumb` | `oklch(0.94 0.006 160 / 0.2)` | |

### Lines (node colours)

A line colour says **what kind of capability** something is. There are
three capability families plus the main line, not ten hues. Stations in
one family are told apart by their glyph and their name, never by colour
alone.

| Family | Tokens | Light | Dark |
|---|---|---|---|
| Main line (Input, Guardrails, Router, Agent, Output) | `node-main`, and `node-agent`, `node-guardrail`, `node-router` = `var(--node-main)` | `oklch(0.26 0.02 165)` | `oklch(0.9 0.008 160)` |
| Knowledge (Knowledge base, Memory) | `node-kb`, `node-memory` | `oklch(0.52 0.1 215)` #00778D | `oklch(0.75 0.1 212)` |
| Knowledge, lighter tint (Data source: feeds the KB) | `node-datasource` | `oklch(0.6 0.09 212)` #2F8E9F | `oklch(0.82 0.08 210)` |
| Actions (Tool, Database, MCP server) | `node-tool`, `node-database`, `node-mcp` = `var(--node-action)` | `oklch(0.5 0.17 345)` #A12D79 | `oklch(0.74 0.15 348)` |
| Helpers (Subagent) | `node-subagent` | `oklch(0.5 0.16 285)` #5D4FB9 | `oklch(0.74 0.12 285)` |
| Glyph inside a bullet | `node-icon` (new) | `oklch(0.995 0.002 160)` | `oklch(0.2 0.015 165)` |

Rules:

- Red, ochre and green are **lamps** only. A line is never red, ochre or
  green; a lamp is never petrol, magenta or violet.
- Keep every existing token name (`--node-*`, `--color-node-*`). Code
  that uses `border-node-kb` and friends keeps working.
- No other saturated colour anywhere in the app. No Tailwind palette
  colours (`emerald-*`, `amber-*`, `red-*` ...), no inline colour styles,
  no hard-coded oklch outside `globals.css`.

## 3. Type

Fira, one superfamily in three cuts, via `next/font/google`:

- `Fira_Sans`: weights 400, 500, 600; styles normal and italic;
  `variable: "--font-fira-sans"`. All UI, reading and headings.
- `Fira_Sans_Condensed`: weights 500, 600;
  `variable: "--font-fira-condensed"`. Station names, route step names,
  stop numerals, the wordmark, page titles (H1).
- `Fira_Code`: variable font; `variable: "--font-fira-code"`. **Code
  only**: SQL, JSON, tool input and output, compiled config, slash
  command names. Never for small data labels, ids in running text,
  durations or money.

Why Fira: it descends from FF Meta, and Spiekermann's studio designed
Berlin's BVG transit signage, so it comes from wayfinding rather than
dressing up as it. It has a true condensed width for station names,
tabular figures for money and time, and open forms that hold up at 12px.

In `@theme inline`: `--font-sans: var(--font-fira-sans), ui-sans-serif,
system-ui, sans-serif`; `--font-condensed: var(--font-fira-condensed),
var(--font-fira-sans), sans-serif` (utility `font-condensed`);
`--font-mono: var(--font-fira-code), ui-monospace, monospace`. Inter and
Source Serif are removed. `--font-serif` is kept only as a transitional
alias of the sans stack; no component may use `font-serif` any more.

Scale (size/line-height, weight):

| Role | Spec | Where |
|---|---|---|
| Display | 40/44 condensed 600, -0.01em | Landing only (headline on a phone) |
| Display XL | 56/60 condensed 600, -0.015em | Landing headline from `sm` up (`text-display-xl`) |
| H1 | 26/32 condensed 600, -0.005em | Page titles |
| H2 | 20/26 sans 600 | Page sections, settings sections, manager headers |
| H3 | 16/22 sans 600 | Plate headings, drawer title, dialog title |
| H4 | 14/20 sans 600 | Sub-sections (replaces every all-caps label) |
| Body | 14/21 sans 400 | UI default |
| Reading | 15/24 sans 400, max 68ch | Chat answers, Markdown, long help |
| Label | 13/18 sans 500 | Form labels, tabs, buttons (sm buttons) |
| Small | 12/17 sans 400 | Hints and meta. The floor: no `text-[10px]` or `text-[11px]` anywhere |
| Station name | 14/18 condensed 600 | Canvas |
| Station detail | 12/16 sans 400 muted | Canvas |
| Code | 13/20 Fira Code (12/18 in the trace) | |

Rules: sentence case everywhere and no `uppercase` anywhere; no positive
tracking; negative tracking only at 20px and up; every cost, token count,
duration, countdown, version and numeric column uses the `.num` utility
(`font-variant-numeric: tabular-nums lining-nums`) and numeric columns
are right-aligned; hints max 60ch, reading 68ch, forms max 640px.
Italic only for Markdown emphasis. 600 is the heaviest weight.

## 4. Space, surfaces, radius, elevation

- 4px base; steps 8, 12, 16, 24, 32, 48. Page gutters 32px desktop, 24px
  tablet, 16px mobile. No horizontal page scroll at any width.
- One left edge per page: the title, the first column and plate edges
  line up.

Surfaces, instead of the SaaS card kit:

1. **Ruled content.** Lists and settings sections sit directly on the
   page, separated by `border` rules. No container. This is the default.
2. **Plate.** `card` fill, 1px `border`, radius 8, **no shadow**. Only for
   a bounded group that acts together (an add form, budget limits, the
   invite form, the sign-in form, the approval card).
3. **Floating layer.** `card` fill, 1px `border`, radius 8,
   `shadow-float`. Only things that float over other content: canvas
   overlays, menus, popovers, slash-command list, toasts, sheets,
   dialogs (radius 12).

Only floating layers cast a shadow. No gradients, no hover lifts, no
radial glows.

Radius: 4px badges, citation chips, inline code; 6px buttons, inputs,
station plates, menu items; 8px plates and floating layers; 12px
dialogs; full circles for bullets, lamps, avatars. In `@theme`:
`--radius-sm: 0.25rem`, `--radius-md: 0.375rem`, `--radius-lg: 0.5rem`,
`--radius-xl: 0.75rem`. Stock unsuffixed `rounded` should become one of
these.

`shadow-float` (new utility): light `0 1px 2px oklch(0.24 0.018 165 /
0.06), 0 8px 24px -4px oklch(0.24 0.018 165 / 0.14)`; dark `0 8px 24px
-4px oklch(0 0 0 / 0.5)`.

## 5. Primitives (`apps/web/components/ui/`)

All existing exports and props stay backward compatible (other files
import them). New variants and components are additive.

- **Button / buttonVariants.** Radius 6. Sizes: `sm` 32px (13px label),
  `default` 36px, `lg` 44px, `icon` 32x32 (requires `aria-label`).
  Variants: `default` ink fill (hover mixes 88% primary into background,
  pressed 80%); `outline` card fill, 1px `field-border`, ink text, muted
  on hover; `ghost` text only, muted on hover; `destructive` red fill,
  `destructive-foreground` text, only for final confirms; `link` (new) ink
  text, 1px underline at 3px offset, thicker on hover. 16px lucide icons,
  8px gap, **no arrows appended**. Every button: `focus-visible` 2px ring
  with 2px offset. No `active:scale`.
- **Input / Select / Textarea.** 36px (textarea auto height), radius 6,
  `field` fill, 1px `field-border`, hover `field-border-hover`. Focus:
  border `ring` plus a 1px `ring` box-shadow, a crisp 2px ink edge, no
  soft halo. `aria-invalid` gets a destructive border.
- **Label** 13/18 500. Hints 12px muted below the field, linked with
  `aria-describedby` where the code already has an id.
- **Switch.** 36x20 track: `field-border` off, `primary` on, card-coloured
  thumb, focus ring. Accepts `className` and `aria-label` /
  `aria-labelledby` (additive props).
- **Badge = status plate.** Radius 4, 22px tall, 8px padding, 12/500,
  sentence case. Variant names stay (`default`, `muted`, `success`,
  `warning`, `destructive`). Status variants are a **muted plate with a
  leading 8px lamp** in the status colour and `foreground` text, which
  avoids status text on its own tint (that pairing fails AA). `default` is
  ink fill. Optional `live` prop pulses the lamp (reduced motion: steady).
- **Lamp** (new, `components/ui/lamp.tsx`). An 8px (or 20px with a count)
  circle in success, warning, destructive, or hollow for "not checked".
  Always with a text label next to it or an accessible name.
- **Card = plate.** `Card` becomes the plate (radius 8, 1px border, no
  shadow). `CardTitle` is sans H3 16/22 600 (not serif). Other exports
  unchanged.
- **Tabs.** Underline tabs, 40px, 14/500 muted; active is ink with a 2px
  primary bar underneath; every tab has a visible focus ring; add
  `aria-controls` / tabpanel linkage where possible without changing the
  public API.
- **Segmented** (new, `components/ui/segmented.tsx`). A bordered group,
  radius 6, active segment filled ink. Same props shape as Tabs, but
  `role="radiogroup"` with `role="radio"` items. For period pickers and
  small mode choices.
- **Dialog.** Radius 12, 24px padding, H3 title, 14px muted description,
  footer right-aligned with the primary action last. Scrim
  `oklch(0.24 0.018 165 / 0.35)` (dark `oklch(0 0 0 / 0.55)`). The default
  confirm label is never "OK": fall back to "Confirm". Destructive
  confirms keep Cancel focused first. Prompt dialogs do not close on a
  backdrop click (typed input would be lost).
- **Toast.** Floating layer, bottom-right (full width on mobile), a lamp
  for its tone, a close button (icon button, `aria-label="Dismiss"`),
  success auto-dismisses after 5s and pauses on hover; errors stay until
  closed. Keep `useToast()` and its tones.
- **PageHeader** (new, `components/ui/page-header.tsx`). Props: `back?:
  {href, label}`, `title`, `status?: ReactNode`, `description?`,
  `actions?`. Back row is a ChevronLeft plus a text label (13/500). H1
  26/32 condensed 600 with the status inline. Description 14 muted, max
  60ch. Actions right-aligned, wrapping below under 768px. 24px bottom
  margin. Used on Assistants, Guided setup, Usage, Members, LoadFailed.
- **SectionHeading** (new, `components/ui/section-heading.tsx`). `level`
  2, 3 or 4, `title`, optional `description`, optional `actions`. Replaces
  every uppercase tracking-wide label.
- **EmptyState** (new, `components/ui/empty-state.tsx`). A plate with a
  **dashed** border (dashed means "not in service yet", the same meaning
  as a dashed invalid line on the canvas), an H3, one sentence on what
  goes here and why, and the action.
- **Loading** (new, `components/ui/loading.tsx`). `<Loading what="…">`:
  a 12px muted line "Loading assistants…" shown after 300ms, with
  `role="status"`. Optional skeleton rows at real row height (no shimmer
  under reduced motion).
- **LineBullet** (new, `components/ui/line-bullet.tsx`). `<LineBullet
  type size={16|20|24} number? hollow?>`: a circle in the type's line
  colour holding a lucide glyph in `node-icon`, or the stop number
  (condensed 600) for main-line stations. Accessible name is the type
  label. Glyphs: input `LogIn`, guardrail `ShieldCheck`, router `Split`,
  agent `Bot` is not used (the Agent shows its number), output `LogOut`,
  knowledge_base `Library`, data_source `FileText`, memory
  `NotebookText`, tool `Wrench`, database `Database`, mcp_server `Plug`,
  subagent `Users` (helpers). Hollow (2px ring, no fill) means switched
  off.

## 6. The canvas (the signature)

Files: `components/canvas/StudioNode.tsx`, a new
`components/canvas/RouteEdge.tsx`, `Canvas.tsx`, `graph-sync.ts`, and
the React Flow theme rules in `globals.css`.

### Stations (`StudioNode`)

- Plate: `card` fill, 1.5px `station-border`, radius 6, padding 8/12,
  min width 176, max 240, **every station 52px tall** (two lines), so
  handles on one row line up and the main line stays straight.
- A 24px `LineBullet` at the left. Capability stations show their glyph;
  main-line stations show their **stop number**, computed from the real
  order (Input 1, Guardrails 2, Router 3 if present, then Agent, Output;
  numbers close up when a stop is missing). Nodes that aren't wired onto
  the main line get no number.
- Name: 14/18 condensed 600, truncates. Detail: 12/16 muted, plain words
  ("Haiku 4.5", "Shop PG", "8 results per search", "Picks the effort for
  each message"). No middle-dot strings.
- **Agent = interchange.** Wider (min 216) with a 2.5px `node-main`
  outline instead of the station border. It has three target handles:
  `left` for the main line, `top` for capability lines from stations
  above the main row, `bottom` for those below. Capability lines join
  from above and below so they never paint over the main line.
- Input and Output are termini: a 6px `node-main` bar on the outer edge.
- Switched off (MCP server off, disabled tool): muted name, hollow bullet.
- **Selected:** a 4px `marker` band outside a 1.5px ink edge (the "you
  are here" marker). Not the focus-ring colour, and not the trace style.
- **Problems:** a 20px lamp at the top-right with the count
  (`destructive` + `destructive-foreground` for errors, `warning` +
  `warning-foreground` for warnings). The detail line switches to the
  first message in the status colour, so the message is visible, not
  only in a tooltip. The bullet and line colour never change.
- **Traced run:** lit stations keep their colours and their detail line
  shows the step time (`.num`, e.g. "226 ms") when the trace has it; unlit
  stations get a `line-unlit` bullet and border and muted text.
- Hide the detail line when zoom < 0.7 (bullet and name remain).

### Lines (`RouteEdge`, a custom edge type)

- Orthogonal smooth-step path (`getSmoothStepPath`, `borderRadius` 16),
  butt caps, no arrowheads. Direction comes from the left-to-right layout.
- **Main line** (an edge whose both ends are main-line stations): 5px
  `node-main`. **Capability lines**: 3px in the **source** station's line
  colour. `interactionWidth` 16.
- Hover: a 7px `muted` underlay. Selected: a 7px `marker` underlay.
- **Dashed means "won't run", and nothing else.** An invalid edge is a
  3px dashed (6 4) `destructive` line with a label plate. No other dashed
  lines on the canvas (the infinite "animated" marching edges go).
- **Safety marker** (only where the data already exists on the node, e.g.
  a database with writes exposed): an 18px `warning` lamp with a
  TriangleAlert glyph, 28px before the line's end, with an accessible
  label "Can change data" and a tooltip. No marker means read-only or
  unknown. Don't invent data the node doesn't hold.
- Trace: lit edges are full colour, unlit are `line-unlit`. Opening a run
  draws the lit route once (stroke-dashoffset, 600ms ease-out); under
  reduced motion it is simply drawn.
- While dragging a new connection the line takes the source's colour; the
  target handle turns `success` over a valid target and `destructive`
  over an invalid one (replaces the hard-coded green).

### Canvas ground, handles, key, tidy

- Pane: `canvas` fill, React Flow `Background` dots gap 24, size 1.25,
  colour `var(--canvas-grid)`; `snapToGrid` with `[24, 24]`.
- Handles: 10px, 2px `card` border, `station-border` fill, 24px invisible
  hit area; the existing `.studio-handle` class and unlayered rules stay.
- Theme React Flow's Controls, MiniMap (if any) and attribution through
  its `--xy-*` CSS variables in `globals.css` (unlayered).
- **Key**: a floating layer at the bottom-left next to zoom, collapsed to
  a "Key" button. Lists only the families present on this assistant, each
  with bullet, name and count ("Data sources, 5"), then the lamps ("Red:
  a problem to fix", "Ochre: check this", "Can change data"). Hovering
  or focusing an entry dims every other family to `line-unlit`.
- **Tidy up** keeps the column order, puts the main line on one row
  (row 0), and never places a capability station on row 0: branch
  columns fill rows -1, +1, -2, +2 and so on. Rows land on the 24px grid.
  Update `graph-sync.test.ts` expectations to match ("never on the main
  row, balanced within one row").
- **Zoom** goes down to 0.1, so "fit everything in view" can show a
  pipeline of a hundred stations. Below 0.7 a station shows only its
  bullet and name.
- **Reading order**: stations are in the page in the order the diagram
  reads: the main line stop by stop, then the rest by column and row.
  That is the order Tab and a screen reader meet them in.

### Comparing two versions (History)

The same canvas, read-only, showing both versions as one drawing. It
reuses the run trace's grammar: what did not change is **unlit**, so what
changed is what you see.

- A changed station keeps its colours and gets a **tag** on its top edge,
  a small plate with the word: "Added" (success), "Removed"
  (destructive), "Changed" (warning). The word carries the meaning; the
  colour repeats it. Its border takes the tag's colour. A removed
  station's name is struck through and its border dashed.
- A changed station's detail line says how many settings changed.
- An added line sits on a 7px success band (where selection's marker band
  goes). A removed line is 3px dashed destructive: dashed still means
  "won't run", which is exactly what a removed line is.
- The drawing is tidied, not shown as either version was arranged, and
  kept whole in view. Picking a station narrows the settings table below
  it to that station.

## 7. Surfaces

### App shell (`app/(app)/layout.tsx`)

```
>=1024, list pages           builder and chat              <768
+-------------+---------+    +----+---------------+    +------------------------+
|(mark) Asst. |         |    |(m) |               |    | [=] Assistant Studio   |
|  Studio     | page    |    |(A) | full-bleed    |    +------------------------+
|             |         |    |(U) | work surface  |    | page, 16px gutters     |
|| Assistants |         |    |(M) |               |    | [=] opens a sheet with |
|  Usage      |         |    |    |               |    | nav, org, theme, sign  |
|  Members    |         |    |(QA)|               |    | out                    |
|             |         |    +----+---------------+    +------------------------+
| Org [Acme v]|         |
| Theme [S L D]         |
| (QA) email  Sign out  |
+-------------+---------+
```

- Rail 232px, `background` with a right `border` rule, **sticky, full
  height, own scroll**. Brand mark (a short ink line through two ticks
  into a ring, drawn in inline SVG, not a roundel) plus the wordmark in
  condensed 600.
- Nav items with lucide icons: Assistants `Waypoints`, Usage `Gauge`,
  Members `Users`. Active: ink text 600, `muted` fill, a 3px `marker` bar
  on the left edge, and `aria-current="page"`. Focus ring on every item.
- Footer: org select with a visible label "Organization" (shown even with
  one org, as plain text then), theme as a Segmented control (System,
  Light, Dark, icon + text), then the account row: initials disc, email
  (with `title`), and a "Sign out" ghost button.
- On `/assistants/[id]/build` and `/assistants/[id]/chat` the rail
  collapses to 64px icons with tooltips (labels still reachable by screen
  readers), giving the canvas and thread the width.
- Below 768px: a 52px top bar with the wordmark and a menu button that
  opens a left sheet with the same content (focus trapped, Esc closes).
- Auth loading: `<Loading what="your workspace">` centred, no layout jump.

### Landing (`app/page.tsx`) and auth

- Landing (revamped after 1.0.0): one column, max 1360px, gutters
  16/24/40/48 (base, `sm`, `lg`, `xl`): wide enough that a wide screen
  is not framed in empty margins, narrow enough that the page keeps its
  scale (1760px was tried and read as too big). Every section after the hero
  opens with a full-width rule; on `lg` its condensed H2 sits on the left
  and its one sentence on the right, aligned to the heading's last line,
  as in the hero. No cards, no eyebrows, no section-entry animations.
  - **Header:** wordmark, "Sign in" (ghost), "Create an account"
    (default). Below `md` the wordmark only.
  - **Signing in is for a computer.** Below `md` (768px) every "Sign in"
    and "Create an account" on the page is gone, and the hero and the
    closing section show the note "Sign in from a computer" instead
    (`components/desktop-only.tsx`). The auth pages (`/login`,
    `/register`, invites) show the same note in place of their forms.
  - **Hero:** the headline "Build chat assistants on your own documents,
    databases and tools." in Display XL, max 18ch. On `lg` the sentence and
    the two actions sit in a right column aligned to the headline's last
    line; below `lg` they follow it.
  - **The route (`components/landing/RouteHero.tsx`):** one support
    assistant drawn the way the builder draws it, on the dot-grid canvas
    ground (`landing-panel`). Main line Input 1, Guardrails 2, Router 3,
    the Agent as a plate (4), Output 5; Refund policy.pdf feeding a
    knowledge base from above; Orders DB and a calculator joining from
    below, no two lines crossing. Every word in it is the product's own.
    Below `md` a second drawing takes its place, the same route down the
    screen (the branches on the right, joining the plate from above and
    below), at a size a phone can read. No SVG `<title>`: browsers show
    it as a tooltip on hover; the drawing is named by `aria-label`.
  - **Steps:** Draw it, Try it, Measure it, Publish it, numbered because
    they are a sequence, on an ink main line (across on `md`, down on a
    phone).
  - **What an assistant can use:** three ruled columns in the family
    colours: Knowledge, Actions, Helpers.
  - **Oversight:** a drawn approval card and a drawn Run details strip,
    each with three lamp points under it.
  - **Runs on your own machine:** the compose commands, the preflight
    output, and six ruled facts.
  - **Start from a sample:** the actions again, ending on a main-line
    terminus. Footer: wordmark and the version.
- Auth layout: paper, no gradient. Mark plus wordmark above a single 400px
  plate at about 28vh. Errors in a bordered message above the submit
  button with `role="alert"`, saying what to do. Below 480px the plate
  drops its border and fills the width.

### Assistants list

Ruled rows, no cards, no hover lift, inside max-width 960.
`PageHeader` title "Assistants", description "Build an assistant by
connecting what it can use: documents, databases, tools.", actions
"Guided setup" (outline) and the quick-create input plus "Create"
(default). Row: name (links to the builder, 15/500) with the slug below in
muted 12, a status plate ("Published", "Draft"), "Edited" relative time
if the list returns it, then row actions "Chat" (ghost) and "Delete"
(ghost, destructive text only on hover/focus). A "Uses" column of line
bullets only if the list API returns what the assistant uses; otherwise
omit it rather than fake it. Empty state: "Build your first assistant".

### Builder (`app/(app)/assistants/[id]/build/page.tsx`)

Header in two rows plus tabs; nothing wraps at 1024px and above:

```
< Assistants
Shop Helper  [Published]  Saved              2 problems   [Chat]  [Publish]
Canvas  Settings  |  Sources  Databases  MCP servers  |  Compiled config  History
```

- Name in H1 condensed; save state announced via `aria-live` ("Saving…",
  "Saved"); the problem count is a button that opens the Problems list;
  when Publish is disabled it says why ("Fix 2 problems to publish").
- Tab labels: "Canvas", "Settings" (was Panels), "Sources", "Databases",
  "MCP servers" (was MCP), "Compiled config" (was Config JSON), "History".
  Groups separated by short vertical rules. On narrow widths the tab row
  scrolls horizontally with an edge fade instead of wrapping.
- "Recommend" becomes "Suggest a pipeline".
- Canvas overlays: Add capability and Suggest at top-left, Tidy up at
  top-right, zoom and Key at bottom-left. The node drawer and the
  validation list must not overlap each other or the trace overlay: the
  drawer is a right column (360px) that reserves its width while open,
  and the validation list docks at the bottom-right above nothing else.

### Settings tab (`components/config/panels.tsx` and friends)

- One column max 640, plus a sticky "On this page" list (208px) on the
  left from 1024px up: it numbers only the pipeline stops (Input,
  Guardrails, Router, Agent, Output: a true sequence), indents the Agent's
  sub-settings, and shows a lamp next to sections with problems. Clicking
  an entry scrolls and moves focus to the section's H2. Below 1024px it
  becomes a "Jump to" select.
- Sections are an H2 plus a one-line description, separated by rules, not
  eight identical cards. Sub-groups use H4 (Models, Chunking, Retrieval,
  Answers ...), never uppercase labels.
- One Toggle row pattern: label and hint on the left, Switch on the
  right, the whole row clickable. Replaces raw checkboxes.

### Resource managers (Sources, Databases, MCP servers)

Same column. H2, a description and the primary action; the add form in
one plate; items as ruled rows with status plates (lamp + word:
"Ready", "Indexing" (live pulse), "Failed", "Connected", "Not checked",
"Switched off"); actions as ghost buttons in the row. Raw enums always
map to words. Drop internal numbers users can't act on ("relevance
0.87", "chars 120–480"). Errors say what failed and what to do.

### Chat (`app/(app)/assistants/[id]/chat/page.tsx`, `components/chat/*`)

```
+rail+-- Conversations 272 ---+------------- thread -----------------------------+
|    | < Shop Helper settings  | Refund question          Cost $0.0642  2.7k tokens|
|    | [+ New chat]            |---------------------------------------------------|
|    | Today                   |            column max 72ch, centred               |
|    | |Refund question        |                    [ Is ticket 3 still open? ]    |
|    |  Order status           |  (D) Listed tables in Shop PG           354 ms     |
|    | Yesterday               |   |                                               |
|    |  Maths                  |  (D) Ran a query on Shop PG  [Show SQL] 226 ms     |
|    |                         |   Yes, ticket 3 is still open. [1]                |
|    |                         |   Sources (1)                     Run details     |
|    |                         |---------------------------------------------------|
|    |                         | [ Message Shop Helper                    ] [Send] |
|    |                         | Enter sends. Shift+Enter adds a line. / for commands|
+----+-------------------------+---------------------------------------------------+
```

- Conversation list grouped by date (Today, Yesterday, Earlier) using
  the timestamps the list already has; 36px rows; active row has `muted`
  fill, the 3px `marker` bar and `aria-current`. Rename and Archive stay
  reachable (ghost icon buttons on hover, focus, or on the active row).
- Thread header: the conversation title and spend as labelled figures
  (`.num`).
- User turns: right-aligned `muted` plates with ink text (not a tomato
  bubble). Assistant answers: unbubbled Reading text (15/24), max 68ch.
- Tool steps: the vertical step gutter, each step with its `LineBullet`
  (in the tool's family colour), a plain sentence, its time (`.num`,
  right), expandable (`aria-expanded`) to show SQL or input in a Fira Code
  block with Copy. **No stop numbers in the chat gutter.**
- Composer: textarea with a visually hidden label, placeholder "Message
  {assistant name}", hint line below. The running lamp and Stop live in
  the composer while a turn runs (TypingDots can become the pulsing lamp
  beside "Answering…").
- Approval card: a plate with a 4px left bar in `warning` (or
  `destructive` at high risk), H3 "Approve this database change?" (or the
  tool's plain name), the tool's bullet, the exact statement in a code
  block, the countdown as tabular text ("1:45 left"). Buttons Approve
  (default) and Deny (outline); at high risk Deny is default and Approve
  becomes destructive "Approve and run".
- Run details (`RunTrace`, `RunDetails`): labelled figures (Status,
  Model, Cost, Time, Tokens) and the **Route**: the vertical strip map of
  the run's stops and steps in line colours with times, plus "Show on
  canvas". If it is a sheet, it is focus-managed (Esc closes, focus
  returns to the trigger).
- Errors in the thread say what happened and what to do ("The answer
  stopped partway. Check your connection, then try again.").

### Usage, Budgets, Members

- `PageHeader`; the period picker is a Segmented control.
- Tables are ruled with right-aligned `.num` money and token columns;
  spend-share bars 6px in `primary`.
- Budget meter: 8px `muted` track filled with `budgetTone` (keep the
  `bg-success`, `bg-warning`, `bg-destructive` class strings that
  `lib/budgets.test.ts` asserts) with a 1px ink tick at 80% and a text
  readout ("$31.20 of $50.00").
- Members: ruled rows, role as a neutral outline plate (Owner, Admin,
  Member, never green), invite form in a plate, and an empty state.
  Copy: "Everyone who can open this organization's assistants. Owners and
  admins can invite people." Spell "organization" consistently.

## 8. Motion

Motion only answers something the user did, or shows live state.

- Colour changes on hover and press: 120ms ease-out. Nothing scales
  except connection handles (1.25 over 120ms).
- Menus and popovers: 160ms fade plus a 2px rise. Sheets: 220ms slide,
  `cubic-bezier(0.2, 0, 0, 1)`. Dialogs: 160ms fade plus scale from 0.98.
- The one signature moment: the lit route draws once when a run opens on
  the canvas or the route strip mounts. On the landing page it is the
  hero: the main line draws and the stops come in once, then a message
  travels it on a 9 s loop (behind the stops and the Agent's plate, never
  popping in or out; the plate's edge lights while it works; a marker dash
  down the knowledge base line, then the database line; out to Output;
  the cited answer comes in, holds, and clears). No button: the user
  asked for a loop. It pauses off screen. Below the hero, each block
  comes in once as it scrolls into view, along the line: lines draw
  across or down, stops pop in where the line reaches them, words rise
  after. The approval still's lamp pulses and its clock counts down.
  Only transform, opacity and dash offsets are animated.
- Live lamps (running step, Indexing, Checking) pulse only while live.
- Tidy up animates stations to their new positions over 240ms.
- `prefers-reduced-motion: reduce`: everything above appears in its end
  state; lamps are steady (the state is spelled out in text anyway). The
  landing hero shows its answered state, still, and nothing below it
  waits to come in.

## 9. Words

Voice: a calm, exact signal-box operator. Plain words, active voice,
sentence case, exact numbers, correct plurals. Errors say what happened,
then what to do; never "Something went wrong" alone (use the ApiError
message plus the fix). Empty states invite an action. Buttons say what
they do ("Delete Shop Helper", not "OK"). No middle-dot meta strings (use
labelled fields or separate lines), no "WORD — fragment" labels, no
arrows in button or link text, no raw enums, env vars or internal task
numbers shown to users.

Rewrites to apply:

| Now | Becomes |
|---|---|
| Landing pill "Phase 0 · foundations" | removed |
| "Each assistant is a configurable agentic pipeline." | "Build an assistant by connecting what it can use: documents, databases, tools." |
| "{n} error(s) block publishing" / "{n} warning(s)" | "2 problems to fix before you can publish" / "1 thing to check" (correct plurals) |
| Node lamp label "{e} error(s), {w} warning(s)" | "2 problems, 1 to check" |
| "Recommend" / "Config JSON" / "MCP" / "Panels" | "Suggest a pipeline" / "Compiled config" / "MCP servers" / "Settings" |
| "saving…" | "Saving…" then "Saved" |
| bare "←" back links | ChevronLeft + "Assistants" / "Shop Helper settings" |
| "routes effort · model" | "Picks the effort for each message" |
| "? results" | "Results per search not set" |
| "unknown connection" | "Connection removed. Pick another." |
| "Approval needed — sql_query" | "Approve this database change?" |
| "stream failed" | "The answer stopped partway. Check your connection, then try again." |
| "Message the assistant… (Enter to send, / for commands)" | placeholder "Message Shop Helper" + hint "Enter sends. Shift+Enter adds a line. Type / for commands." |
| "Only one knowledge base per assistant — a second one would be ignored." | "This assistant already has a knowledge base. Add sources to it instead." |
| "RAG_OFFLINE=1" wording | "Search runs offline on this server, so results are keyword-only." |
| "People in this organization." | "Everyone who can open this organization's assistants. Owners and admins can invite people." |
| Dialog default "OK" | "Confirm" (callers should pass a verb phrase) |
| "Something went wrong" | the server's message plus what to do |

## 10. Contracts a restyle must not break

- `document.title` stays "Assistant Studio" (`lib/approvals.test.ts`).
- Exports and props of every `components/ui/*` primitive, `load-state`,
  `ThemeToggle`, `ThemeProvider`; provider order in `app/layout.tsx`;
  `@xyflow/react/dist/style.css` import.
- Class hooks `.studio-canvas`, `.studio-handle` (with React Flow's
  `.connectingfrom`, `.connectingto`, `.valid`), `.typing-dot`,
  `.running-pulse` (if still used).
- `budgetTone` class strings; `graph-sync` exports (`NODE_LABEL`,
  `toFlow`, `fromFlow`, `tidyLayout`, `wiredInto` ...). Graph data
  written back to the API stays `{source, target}` edges and the same node
  shape; handle ids are render-time only and never persisted.
- Existing aria-labels, roles and visible labels that navigation or tests
  rely on (the full list from the UI inventory is in the implementation
  notes); auth form ids, autocomplete values and `?next=` handling.
- No new npm dependencies. No API contract changes.
