# gen3d — agent guide

`gen3d.py` (repo root, Python 3 stdlib only) wraps fal.ai and Tripo's
image→3D + animation APIs. The full step-by-step recipe lives in
`skills/generate-3d-asset/SKILL.md` — prefer invoking that skill; this file is
the quick reference.

## Spend contract (IMPORTANT)

`generate`, `rig`, and `animate` spend the user's real money.

- **Always state the estimated cost and get explicit user approval before any
  paid run.**
- The CLI refuses to spend non-interactively without `--yes`. Only pass
  `--yes` after the user approved that specific run's cost — **never on your
  own initiative.**
- `balance`, `import-model`, `check`, and `--help` are free.

## Commands and costs

```bash
python3 gen3d.py --image <png> --out <glb> [--backend fal|tripo] [--model hunyuan|tripo]
                 [--no-texture] [--max-faces 20000] [--json]     # ~$0.05–0.40
python3 gen3d.py import-model --model <glb|obj|fbx|stl>          # FREE → task id
python3 gen3d.py check --task-id <import-id>                     # FREE → riggable/rig_type
python3 gen3d.py rig --task-id <import-id> --out <glb>           # 25 credits ≈ $0.25
python3 gen3d.py animate --rig-task-id <rig-id> --presets run,jump --out <glb>
                 [--in-place]                                    # 10 credits/preset, max 5
python3 gen3d.py balance                                         # FREE
```

$1 ≈ 100 Tripo credits. A generation on the native Tripo backend is 20–40
credits. Animation presets: idle walk run dive climb jump slash shoot hurt
fall turn, quadruped:walk hexapod:walk octopod:walk serpentine:march
aquatic:march.

## Keys

Lookup order: process env → `$GEN3D_ENV_FILE` → script-adjacent `.env` →
`~/.config/gen3d/env`. `FAL_KEY` (fal backend) from fal.ai/dashboard/keys;
`TRIPO_API_KEY` (tripo backend + all animation commands) from
platform.tripo3d.ai. If a key is missing the error names the signup URL —
hand that errand to the user; don't guess keys.

## Machine-readable output

Pass `--json` to any subcommand: stdout carries exactly one JSON object
(`{"ok": true, "command": ..., "task_id": ..., "output": ..., "output_bytes":
..., "credits_spent": ..., "balance": ...}` — fields vary by command;
`{"ok": false, "error": ...}` on failure, exit code 1). Progress lines move
to stderr.

## Non-negotiable workflow rules

1. **Never use an unaudited GLB.** Image-to-3D output is frequently hollow
   from the back. Render ≥5 angles (front/back/quarters/top) and inspect
   before wiring — Godot projects: `extras/godot/render_probe.gd` (see
   `extras/godot/README.md`); animated output: re-probe with `--anim`.
2. **Download promptly.** Tripo result URLs expire ~5 minutes after task
   success (the CLI downloads immediately — don't re-fetch old task ids).
3. **Wiring is project-specific.** Look for the consuming project's
   conventions skill/doc before integrating a model into scenes; if none,
   ask the user.
4. Rig `spec=mixamo` skeletons cannot be retargeted by Tripo — keep the
   default `spec=tripo` unless exporting to an external Mixamo workflow.
   More gotchas: `docs/animation-chain.md`.
