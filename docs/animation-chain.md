# Animation chain (existing GLB → rigged → preset clips)

Verified against Tripo's API and SDK (2026-06-13, docs.tripo3d.ai +
github.com/VAST-AI-Research/tripo-python-sdk) and exercised end-to-end on a
real character model. All steps are `POST /v2/openapi/task` + polling — the
same plumbing as generation.

## The chain

| Step | CLI | Task type | Cost |
|---|---|---|---|
| 1. Upload | `import-model --model hero.glb` | STS upload + `import_model` | **FREE** |
| 2. Pre-rig check | `check --task-id <import-id>` | `animate_prerigcheck` → `riggable`, `rig_type` | **FREE** |
| 3. Rig | `rig --task-id <import-id> --out rigged.glb` | `animate_rig` | **25 cr ≈ $0.25** |
| 4. Retarget | `animate --rig-task-id <rig-id> --presets run,jump --out anim.glb` | `animate_retarget` | **10 cr ≈ $0.10 / preset, max 5/task** |

A full character set (rig + 4 presets) ≈ 65 credits ≈ $0.65.

## Presets

`idle walk run dive climb jump slash shoot hurt fall turn` plus
`quadruped:walk hexapod:walk octopod:walk serpentine:march aquatic:march`.

Notes from real use: `hurt` is a long multi-flinch stagger reel (~14 s — play
slices of it); `fall` is a knockdown that reads well as a death. There is no
slide/crouch preset — keep procedural animation or an external clip for those.

## Gotchas (learned the expensive way)

- **`spec` must be `tripo`** on the rig task if you intend to retarget with
  Tripo — `spec=mixamo` skeletons CANNOT be retargeted by Tripo; that spec
  exists only for exporting into an external Mixamo workflow.
- **Rig version:** try `v2.5-20260210` first; fall back to `v1.0-20240301` if
  it rejects the source model.
- **Retarget's `original_model_task_id` is the RIG task's id**, not the
  import id (the SDK source is explicit; the docs are ambiguous).
- **Multi-preset exports name clips `NlaTrack`, `NlaTrack.001`, ... in
  request order** (importers may sanitize dots). Identify clips by order and
  length, not by name.
- **Models with no API task id** (generated elsewhere/manually) enter the
  chain via the free STS upload + `import_model` — no regeneration needed.
  Upload cap 20 MB.
- **The rig can change scale** — one observed rig export came back half the
  source height. Re-measure the AABB and re-bake transforms after rigging.
- **Audit deformation before building on the clips:** sample each clip at
  several timestamps from profile + three-quarter angles (Godot:
  `extras/godot/render_probe.gd --anim`). Capes and loose geometry are the
  usual smear casualties — if deformation fails, stop before spending more.
- Result URLs expire ~5 min after task success; the CLI downloads
  immediately.
- `GET /user/balance` is free — the CLI prints balance before and after every
  paid call.
