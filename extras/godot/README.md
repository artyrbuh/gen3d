# Godot verification helpers

Standalone `SceneTree` scripts for the mandatory post-generation audit. They
run against a Godot 4.x project (they need its import cache), so call them
**from the consuming project's root** with `--path .`. With this repo vendored
as a submodule at `tools/gen3d`, the script paths are
`res://tools/gen3d/extras/godot/...`.

## render_probe.gd — multi-angle geometry audit

Captures 5 angles (front/back/two three-quarters/top) of a GLB in a lit
stage. The back + off-side views are the hollow-geometry detectors for
image-to-3D models.

```bash
<godot-binary> --path . --script res://tools/gen3d/extras/godot/render_probe.gd \
    -- --models <name>[,<name>...] [--anim] \
       [--models-dir res://assets/models] [--out-dir res://dev/probes]
```

- A bare name probes `<models-dir>/<name>.glb`; anything containing `/` is
  treated as a full `res://` scene path.
- `--anim` samples every animation clip at 6 timestamps from profile +
  three-quarter angles — the skinning/deformation judge for rigged output.
- **Must run WINDOWED (no `--headless`)** — the headless driver cannot
  rasterize. Runs can hang at exit while the Godot editor is open, so wrap in
  a watchdog: launch with `&`, poll the out-dir for PNGs, then `kill` the pid.
- Output PNGs land in `--out-dir` (default `res://dev/probes`, created if
  missing — worth gitignoring in the consuming project).

## measure_models.gd — AABB measurement

Prints `size` and `origin_min` for every `.glb` in the models dir, so scene
transforms get baked from real numbers instead of guesses.

```bash
<godot-binary> --headless --path . \
    --script res://tools/gen3d/extras/godot/measure_models.gd \
    -- [--models-dir res://assets/models]
```

This one is headless-safe (no rendering).

Note: Godot generates `.uid` companions for these scripts inside the
submodule checkout on first import — this repo's `.gitignore` covers them, so
the submodule stays clean.
