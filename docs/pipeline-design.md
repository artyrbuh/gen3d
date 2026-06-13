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
- **Zero install.** Python 3 stdlib only (urllib, hand-rolled multipart, and
  a ~60-line SigV4 signer for S3 PUTs). No pip, no venv, no MCP server — a
  plain script agents run via shell.

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

### `tripo` — native Tripo developer API

`TRIPO_API_KEY` from platform.tripo3d.ai (separate from any Studio login).
Credits ≈ $0.01 each; a generation ≈ 20–40 credits.

Flow: multipart `POST /v2/openapi/upload` → `image_token` →
`POST /v2/openapi/task` `{"type": "image_to_model", ...}` →
`GET /v2/openapi/task/{id}` until a terminal status → download
`output.pbr_model` (fallback `output.model`).
Auth: `Authorization: Bearer $TRIPO_API_KEY`.

**Result URLs expire ~5 minutes after success** — the CLI downloads
immediately; never defer the download.

## Shared behavior

- Uploads capped at 20 MB (both providers; also Tripo's STS model upload).
- `--max-faces` defaults to 20000 — a mobile-friendly poly budget. `0` means
  provider default.
- Polling every 5 s with a configurable `--timeout` (default 600 s); API
  error bodies are surfaced verbatim.
- GLBs are written atomically (`.part` + rename).
- Paid calls print cost + balance and require interactive confirmation or an
  explicit `--yes` (see the spend contract in AGENTS.md).

## Error handling

- Missing/invalid key → message names the env var, the lookup chain, and the
  signup URL.
- Task failure / ban / timeout → API status and error body printed verbatim.
- Hollow or mis-scaled mesh → caught by the mandatory probe-audit step in the
  skill, not by the CLI; nothing should be wired before the audit passes.
