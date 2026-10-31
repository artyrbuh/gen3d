# Pipeline design notes (image → 3D)

Why this tool exists and how it talks to the providers. Extracted from the
original project spec (2026-06-12); engine- and project-specific details live
with the consuming project.

## Goals

- **Pay-per-use instead of a subscription.** $0.05–0.50 per model via APIs
  replaces a ~$20/mo studio subscription for occasional generation.
- **One CLI, two providers.** Not single-vendored: if one provider's pricing
  or hosting changes, the other backend keeps working.
- **Agent-drivable end to end.** Everything around the diffusion step —
  submission, polling, download, geometry audit, measurement — is what a
  coding agent automates well. The 3D generation itself is a reconstruction
  model; no LLM can do that step.
- **Zero install.** Python 3 stdlib only (urllib). No pip, no venv, no MCP
  server — a plain script agents run via shell.

## Backends

### `fal` — fal.ai queue API (default)

One `FAL_KEY`, two selectable models:

- `--model hunyuan` → `fal-ai/hunyuan3d/v2` (~$0.05 plain, ~3× textured).
  No face-limit parameter.
- `--model tripo` → `tripo3d/tripo/v2.5/image-to-3d` (~$0.20–0.40). Honors
  `face_limit`, and matches the style of assets generated on Tripo elsewhere.

Flow: POST the queue submit with the image as a base64 data URI
(`input_image_url` for Hunyuan, `image_url` for Tripo-on-fal) → poll the
returned `status_url` → fetch `response_url` → download `model_mesh.url`.
Auth: `Authorization: Key $FAL_KEY`.

### `tripo` — native Tripo developer API V3

`TRIPO_API_KEY` from platform.tripo3d.ai. API credits are a separate pool from
any Studio login. 1 credit = $0.01. Image → 3D is 30 credits textured (measured
on `v3.1-20260211`) or 20 untextured (price list).

Base URL: `https://openapi.tripo3d.ai/v3`.
Auth: `Authorization: Bearer $TRIPO_API_KEY`.

Flow: `POST /files/presign` → `PUT` the bytes to `presigned_url` with no auth
header → `POST /generation/image-to-model` with `{"input": <file_token>,
"model": ...}` → `GET /tasks/{id}` every 2 s until a final status → download
`output.model_url`.

The CLI always presigns: `POST /files` lists only JPEG and PNG, while presign
also takes WebP and suits large models.

V3 requires `model` on every generation; the CLI sends `v3.1-20260211`, the
docs' recommendation, from a constant so a flag can't land on the far pricier
P series.

V3 doesn't document result URL lifetime. Measured URLs are signed for 24 hours
after completion. The CLI still downloads immediately.

Task-creation POSTs are never retried, because a lost response may hide a
created, paid task. GETs retry up to 5 times on 429, 5xx and network errors,
honouring `Retry-After`, so a blip can't lose a finished result.

## Shared behavior

- Images capped at 20 MB (both providers). Tripo model input is capped at 150 MB.
- `--max-faces` defaults to 20000 — a mobile-friendly poly budget. `0` means
  provider default.
- Polling with a configurable `--timeout` (default 600 s): every 5 s on fal,
  every 2 s on Tripo. API errors are surfaced with their code and message.
- GLBs are written atomically (`.part` + rename).
- `rig` and `animate` print cost + balance and require interactive confirmation
  or an explicit `--yes`. `generate` prints its cost but doesn't ask (see the
  spend contract in AGENTS.md).

## Error handling

- Missing/invalid key → message names the env var, the lookup chain, and the
  signup URL.
- Task failure / ban / timeout → API status and error body printed. A status
  like `unknown` isn't final: per the V3 docs it waits until `--timeout`.
- Success without a usable download → the error names the task id, so a paid
  result can still be fetched with `GET /tasks/{id}`.
- Hollow or mis-scaled mesh → caught by the mandatory probe-audit step in the
  skill, not by the CLI; nothing should be wired before the audit passes.
