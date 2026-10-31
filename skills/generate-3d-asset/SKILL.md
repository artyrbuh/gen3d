---
name: generate-3d-asset
description: Generate a 3D model (GLB) from a concept image via pay-per-use APIs (fal.ai Hunyuan3D / Tripo), or rig + retarget preset animations onto an existing GLB. Use when the user wants to "generate a model", "make a 3D asset", "tripo this image", or animate a character mesh. Runs the image → GLB → multi-angle audit → measure pipeline. Spends real money — always state the cost and get approval first.
---

# Generate a 3D Asset (image → audited GLB)

The CLI is `gen3d.py`, two directories above this file (repo root). Common
locations seen from a consuming project:

- vendored as a git submodule: `python3 tools/gen3d/gen3d.py ...`
- installed as a Claude Code plugin: `python3 "${CLAUDE_PLUGIN_ROOT}/gen3d.py" ...`
- plain clone: `python3 <clone>/gen3d.py ...`

It is stdlib-only Python 3 — no pip install, no venv.

## Spend contract (non-negotiable)

`generate`, `rig`, and `animate` spend the user's money. **State the estimated
cost and get the user's explicit approval before every paid run.** `rig` and
`animate` refuse to spend non-interactively without `--yes`; only pass `--yes`
after the user approved that specific spend — never on your own initiative.
`generate` has no such check and spends as soon as it runs.
`balance`, `import-model`, `check`, and `--help` are free.

## Inputs to collect (ask if missing)

1. **Concept image** — png/jpg/webp, ≤20 MB.
2. **Asset name** — output path for the GLB.
3. **Intended use** — static prop vs character to be animated (changes the
   poly-budget and follow-up steps).

## Step 0 — Obtain the concept image (pluggable)

If your harness can generate images, generate one in-loop — check the project
for art-style conventions first (style prefixes, palettes — e.g. a
`docs/art-prompts*.md` or similar). Otherwise ask the user to supply one.
A clean single-subject image on a plain background converts best; busy
backgrounds and hard shadows bake into the mesh.

## Step 1 — Generate (PAID — say the cost before running)

```bash
python3 <path>/gen3d.py --image <concept.png> --out <name>.glb [--json]
```

- Default backend is fal.ai Hunyuan3D (~$0.05 plain, ~3× textured).
  Alternatives: `--model tripo` (Tripo v2.5 via the same FAL_KEY, ~$0.20–0.40,
  honors `--max-faces`) or `--backend tripo` (native Tripo API V3, TRIPO_API_KEY,
  `v3.1-20260211`, 30 credits = $0.30 textured, 20 credits with `--no-texture`).
- `--max-faces 20000` is the default poly budget (mobile-friendly). Hunyuan
  ignores it; prefer a Tripo model when the budget is strict.
- Keys come from the environment, `$GEN3D_ENV_FILE`, a script-adjacent `.env`,
  or `~/.config/gen3d/env`. Missing-key errors print the signup URL — if no
  key exists, stop and hand the signup errand to the user.
- `--json` puts one machine-readable result object on stdout (task id, output
  path, bytes, balance); progress moves to stderr.

## Step 2 — Audit the mesh (MANDATORY — never use an unaudited GLB)

Image-to-3D output is often hollow from the back or missing faces. Render the
model from at least 5 angles (front/back/two three-quarters/top) and inspect
the images before wiring it anywhere.

- **Godot projects:** use `extras/godot/render_probe.gd` from this repo (see
  `extras/godot/README.md`), or the project's own probe tooling if it has one.
- **Other engines:** any headless/scripted viewport capture works — the
  requirement is *seeing the back and off-side*, not the tool.

Reject if: hollow back/off-side, missing faces, garbage topology, baked-in
floor or background. A rejected mesh stops the pipeline — report with the
probe images, suggest a re-run with a different image/model, and do NOT use it.

## Step 3 — Measure

Get the model's real AABB (size + origin offset) so transforms are baked from
numbers, not guesses. Godot: `extras/godot/measure_models.gd`.

## Step 4 — Wire into the project (project-specific)

Integrating the GLB (wrapper scenes, colliders, naming, commit conventions) is
project-specific. **Look for a project conventions skill or doc first** (e.g. a
`wire-3d-asset` skill, CLAUDE.md/AGENTS.md sections); if none exists, ask the
user how they want it wired. Don't place assets into scenes uninvited.

## Animation chain (existing GLB → rigged + animated)

```bash
python3 <path>/gen3d.py import-model --model hero.glb        # FREE → import task id
python3 <path>/gen3d.py check --task-id <id>                 # FREE → riggable? rig_type?
python3 <path>/gen3d.py rig --task-id <id> --rig-type <rig_type> --out rigged.glb  # 25 cr = $0.25
python3 <path>/gen3d.py animate --rig-task-id <rig-id> --presets run,jump \
    --out anim.glb                                           # 10 cr = $0.10 per preset
```

- A model generated with `--backend tripo` can skip the import: pass its task id
  to `check` and `rig`. `--task-id` also takes a file token or URL.
- Pass the `rig_type` that `check` recommends; the CLI defaults to `biped`.
- `--rig-version` defaults to `v2.5-20260210`, for non-humanoids. Tripo
  recommends `v3.0-20260909` for humanoids.
- Presets: idle, walk, run, dive, climb, jump, slash, shoot, hurt, fall, turn,
  plus quadruped/hexapod/octopod walk and serpentine/aquatic march. Max 5 per
  task. A `v1.0-20240301` rig uses `biped:<name>` presets instead.
- Audit animated output too: re-probe with animation sampling (Godot:
  `render_probe.gd --anim`) and check for skinning smears (capes/loose
  geometry are the usual casualties) BEFORE building anything on the clips.
- Gotchas and clip-naming details: `docs/animation-chain.md`.

## Cost cheat sheet

| Route | Cost/model | Notes |
|---|---|---|
| fal + Hunyuan3D (default) | ~$0.05–0.48 | no face-limit param |
| fal + Tripo v2.5 | ~$0.20–0.40 | honors face_limit, same FAL_KEY |
| native Tripo API V3 | 30 credits ($0.30) textured, 20 untextured | TRIPO_API_KEY; $1 = 100 credits |
| rig | 25 credits ($0.25) | takes a generation or import task id, file token, or URL |
| animate | 10 credits ($0.10) per preset | max 5 per task |
