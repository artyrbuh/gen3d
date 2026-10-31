# Animation chain (existing GLB → rigged → preset clips)

Written against Tripo API V3 from developers.tripo3d.ai/en/docs on 2026-10-31.
Facts marked **measured** come from a live run that day: an image-to-model wolf
rigged as `quadruped` on `v2.5-20260210`, then retargeted with `quadruped:walk`.
Base URL: `https://openapi.tripo3d.ai/v3`. Each step creates a task on its own
endpoint, then polls `GET /tasks/{id}`, the same plumbing as generation.

## The chain

| Step | CLI | Endpoint | Cost |
|---|---|---|---|
| 1. Upload | `import-model --model hero.glb` | `POST /files/presign`, `PUT` to storage, `POST /models/import` | **0** (measured; not on the price list) |
| 2. Pre-rig check | `check --task-id <id>` | `POST /animations/rig-check` → `riggable`, `rig_type` | **FREE** |
| 3. Rig | `rig --task-id <id> --out rigged.glb` | `POST /animations/rig` | **25 cr = $0.25** |
| 4. Retarget | `animate --rig-task-id <rig-id> --presets run,jump --out anim.glb` | `POST /animations/retarget` | **10 cr = $0.10 / preset, max 5/task** |

A full character set (rig + 4 presets) = 65 credits = $0.65. Failed and
cancelled tasks are documented as not charged. Credits are frozen when the task
is created, so `balance` shows the real price within seconds (measured).

Models generated through the API skip step 1: pass the generation task id to
`check` and `rig` (measured). `--task-id` also takes a `file_` token or a URL,
since V3 infers the type from the `input` value.

## Choosing a rig version

| `--rig-version` | Use for | Notes |
|---|---|---|
| `v2.5-20260210` (CLI default) | Non-humanoids | `rig_type=quadruped` works (measured). Bipeds work, but the docs don't recommend it |
| `v3.0-20260909` | Humanoids (docs' recommendation) | Same presets as v2.5. Adds `rig_type=others` and the `actorcore`, `unity`, `unreal` and `vrm` specs. Not exercised live |
| `v1.0-20240301` | Biped only | The API's default, so the CLI always sends a version. Documents its own 90+ `biped:<name>` presets. Not exercised live |

## Presets

For v2.5 and v3.0 rigs: `idle walk run dive climb jump slash shoot hurt fall
turn` plus `quadruped:walk hexapod:walk octopod:walk serpentine:march
aquatic:march`. V1.0 rigs document `biped:<name>` instead, such as
`biped:dance_01`; the CLI passes those through. No preset is listed for `avian`.

Whether `run` works on a v1.0 rig, or `biped:run` on a v2.5 rig, isn't
documented.

Tripo replaced the retargeting algorithm in place in 2026-10, so these notes
from V2-era use may no longer hold: `hurt` was a long multi-flinch reel (~14 s),
and `fall` was a knockdown that read well as a death. There was no slide or
crouch preset, and the V3 list has none either.

## Gotchas

- **Retarget's `input` is the RIG task's id**, not the model's.
- **`spec`** only applies to bipeds. On V2, `spec=mixamo` skeletons could not be
  retargeted by Tripo. V3 docs don't say either way for any non-`tripo` spec, so
  keep the default unless you are exporting to an external workflow.
- **Clip names:** a single-preset export names its clip after the preset, such
  as `preset:quadruped:walk` (measured). Multi-preset V2 exports named clips
  `NlaTrack`, `NlaTrack.001`, ... in request order. V3 doesn't document the
  shape of a batch result, and the CLI hasn't run one live, so check the output
  and identify clips by order and length.
- **The rig can move the model:** the measured quadruped kept its size but came
  back with its feet on y=0, and an older V2 rig export came back half the
  source height. File-token and URL inputs are also normalized, and the task
  reports `normalize_scale` and `normalize_translation` when it does (the
  measured tiny test GLB returned neither). Re-measure the AABB and re-bake
  transforms after rigging.
- **Audit deformation before building on the clips:** sample each clip at
  several timestamps from profile + three-quarter angles (Godot:
  `extras/godot/render_probe.gd --anim`). Capes and loose geometry are the
  usual smear casualties. If deformation fails, stop before spending more.
- **Result URLs:** V3 doesn't document a lifetime. Measured model URLs are
  CloudFront-signed for 24 hours after task completion, and re-querying the task
  returns the same URL. The V2-era "about 5 minutes" no longer applies, but the
  CLI still downloads immediately, and this could change without notice.
- **`GET /account/balance`** is free, and the CLI prints the balance before and
  after every rig and retarget. `credits_spent` in `--json` is the task's real
  `credits_consumed`, which can be a decimal.
- **Concurrency:** rig check, rig and retarget share an account-level pool of 10
  tasks. Over the limit returns HTTP 429, code 2000.
