# gen3d — agent guide

`gen3d.py` (repo root, Python 3 stdlib only) wraps fal.ai and Tripo's
image→3D + animation APIs. The full step-by-step recipe lives in
`skills/generate-3d-asset/SKILL.md` — prefer invoking that skill; this file is
the quick reference.

## Spend contract (IMPORTANT)

`generate`, `rig`, and `animate` spend the user's real money.

- **Always state the estimated cost and get explicit user approval before any
  paid run.**
- `rig` and `animate` refuse to spend non-interactively without `--yes`. Only
  pass `--yes` after the user approved that specific run's cost — **never on
  your own initiative.**
- `generate` has no such check: it spends as soon as it runs, so get the
  approval first.
- `balance`, `import-model`, `check`, and `--help` are free. Import isn't on
  Tripo's price list; it measured 0 credits on 2026-10-31.

## Commands and costs

```bash
python3 gen3d.py --image <png> --out <glb> [--backend fal|tripo] [--model hunyuan|tripo]
                 [--no-texture] [--max-faces 20000] [--json]     # ~$0.05–0.40
python3 gen3d.py import-model --model <glb|obj|fbx|stl>          # FREE → task id, file token
python3 gen3d.py check --task-id <id>                            # FREE → riggable/rig_type
python3 gen3d.py rig --task-id <id> --out <glb> [--rig-type quadruped]
                 [--rig-version v2.5-20260210|v3.0-20260909|v1.0-20240301]  # 25 credits = $0.25
python3 gen3d.py animate --rig-task-id <rig-id> --presets run,jump --out <glb>
                 [--in-place]                                    # 10 credits/preset, max 5
python3 gen3d.py balance                                         # FREE
```

$1 = 100 Tripo credits. A generation on the native Tripo backend (API V3,
`v3.1-20260211`) is 30 credits textured (measured) or 20 untextured (price
list). `--task-id` takes a generation or import task id, a file token, or a URL.
The rig default suits non-humanoids; Tripo recommends `v3.0-20260909` for
humanoids. Animation presets: idle walk run dive climb jump slash shoot hurt
fall turn, quadruped:walk hexapod:walk octopod:walk serpentine:march
aquatic:march, or biped:<name> on a v1.0 rig.

## Keys

Lookup order: process env → `$GEN3D_ENV_FILE` → script-adjacent `.env` →
`~/.config/gen3d/env`. `FAL_KEY` (fal backend) from fal.ai/dashboard/keys;
`TRIPO_API_KEY` (tripo backend + all animation commands) from
platform.tripo3d.ai. If a key is missing the error names the signup URL —
hand that errand to the user; don't guess keys.

## Machine-readable output

Pass `--json` to any subcommand: stdout carries exactly one JSON object
(`{"ok": true, "command": ..., "task_id": ..., "output": ..., "output_bytes":
..., "credits_spent": ..., "balance": ...}` — fields vary by command, and
credit values can be decimals;
`{"ok": false, "error": ...}` on failure, exit code 1). Progress lines move
to stderr.

## Non-negotiable workflow rules

1. **Never use an unaudited GLB.** Image-to-3D output is frequently hollow
   from the back. Render ≥5 angles (front/back/quarters/top) and inspect
   before wiring — Godot projects: `extras/godot/render_probe.gd` (see
   `extras/godot/README.md`); animated output: re-probe with `--anim`.
2. **Download promptly.** Tripo V3 doesn't document how long result URLs live.
   They measured 24 hours on 2026-10-31; the CLI downloads immediately anyway.
3. **Wiring is project-specific.** Look for the consuming project's
   conventions skill/doc before integrating a model into scenes; if none,
   ask the user.
4. Keep the default `spec=tripo` unless exporting to an external workflow:
   `spec=mixamo` skeletons could not be retargeted on API V2, and V3 docs don't
   say either way.
   More gotchas: `docs/animation-chain.md`.
