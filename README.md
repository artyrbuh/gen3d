# gen3d — image → 3D asset pipeline for coding agents

A single-file, stdlib-only Python CLI that turns concept images into textured
GLB models (fal.ai Hunyuan3D or Tripo), and can rig + retarget preset
animations onto them via the Tripo API. Built to be driven by coding agents
(Claude Code, Codex, anything that can run a shell) with a human approving the
spend.

```
generate      image → 3D GLB                    ~$0.05–0.40
import-model  upload an existing GLB → task id  FREE
check         pre-rig check → riggable verdict  FREE
rig           rig a model for animation         25 credits ≈ $0.25
animate       retarget preset animations        10 credits ≈ $0.10 each
balance       remaining Tripo API credits       FREE
```

No pip install — Python 3 stdlib only (including a hand-rolled SigV4 S3 PUT
for Tripo's STS upload).

## Install

**As a git submodule (recommended for projects):**

```bash
git submodule add https://github.com/artyrbuh/gen3d.git tools/gen3d
# expose the skill to agents that read .agents/skills (Codex) / .claude/skills (Claude Code):
mkdir -p .agents/skills .claude/skills
ln -s ../../tools/gen3d/skills/generate-3d-asset .agents/skills/generate-3d-asset
ln -s ../../tools/gen3d/skills/generate-3d-asset .claude/skills/generate-3d-asset
```

**As a Claude Code plugin:**

```
/plugin marketplace add artyrbuh/gen3d
/plugin install gen3d@gen3d
```

Plugin installs are copied to a cache that is replaced on update — put your
keys in `~/.config/gen3d/env`, not next to the script.

**Plain clone:** `git clone` anywhere and call `gen3d.py` by path.

## Keys

| Variable | Backend | Get one at |
|---|---|---|
| `FAL_KEY` | fal.ai (Hunyuan3D + Tripo v2.5) | https://fal.ai/dashboard/keys |
| `TRIPO_API_KEY` | native Tripo API + all rig/animate commands | https://platform.tripo3d.ai |

Lookup order: process environment → `$GEN3D_ENV_FILE` → `.env` next to
`gen3d.py` → `~/.config/gen3d/env` (all `KEY=value` lines; `.env` is
gitignored).

## Quickstart

```bash
python3 gen3d.py --image concept.png --out model.glb           # generate (fal/Hunyuan)
python3 gen3d.py --image concept.png --out model.glb \
    --model tripo --max-faces 15000                            # Tripo v2.5, strict poly budget
python3 gen3d.py import-model --model hero.glb                 # → import task id (free)
python3 gen3d.py check --task-id <import-id>                   # riggable? (free)
python3 gen3d.py rig --task-id <import-id> --out rigged.glb    # 25 credits
python3 gen3d.py animate --rig-task-id <rig-id> --presets run,jump --out anim.glb
python3 gen3d.py balance                                       # credits left (free)
```

Add `--json` to any subcommand for one machine-readable result object on
stdout (progress moves to stderr) — handy when an agent or script is parsing.

## Safety

Paid subcommands print the cost and current balance, then ask for
confirmation. Non-interactive runs **refuse to spend** unless `--yes` is
passed — the contract for agents is in [AGENTS.md](AGENTS.md): never `--yes`
without explicit human approval of that specific spend.

## Workflow (the part the agent automates)

1. **Concept image** — generate or collect one (clean subject, plain background).
2. **Generate** — `gen3d.py` (state cost, get approval).
3. **Audit (mandatory)** — image-to-3D output is often hollow from the back;
   render ≥5 angles and look before using. Godot helpers ship in
   [`extras/godot/`](extras/godot/README.md).
4. **Measure** — real AABB numbers for transforms, not guesses.
5. **Wire** — project-specific; see the skill's conventions hook.

The full agent recipe is the [`generate-3d-asset` skill](skills/generate-3d-asset/SKILL.md)
(open Agent Skills format — works in Claude Code, Codex, and anything else
that reads `SKILL.md`). Design notes: [`docs/pipeline-design.md`](docs/pipeline-design.md)
and [`docs/animation-chain.md`](docs/animation-chain.md).
