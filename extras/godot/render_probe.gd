extends SceneTree

## Render PNG probes of raw GLBs so models can be reviewed without opening
## the editor. Captures 5 angles (front/back/two three-quarters/top) in a lit
## stage — the back + off-side views are the hollow-geometry detectors for
## image-to-3D models.
##
## Run from the consuming Godot project's root (WINDOWED — the headless
## driver cannot rasterize; wrap in a watchdog, runs can hang at exit while
## the editor is open):
##
##   Godot --path . --script res://tools/gen3d/extras/godot/render_probe.gd \
##       -- --models castle,cloud_puffy [--models-dir res://assets/models] \
##          [--out-dir res://dev/probes]
##
## Animated GLBs: add `--anim` to sample every clip at 6 timestamps from
## profile + three-quarter angles (skinning/deformation judge, e.g. Tripo rigs):
##   ... -- --models fantasy_hero_run --anim

var out_dir := "res://dev/probes"
var models_dir := "res://assets/models"

# name -> [yaw_deg, pitch_deg]
const ANGLES := {
	"front": [180.0, -12.0],
	"back": [0.0, -12.0],
	"quarter": [135.0, -20.0],
	"quarter2": [315.0, -20.0],
	"top": [180.0, -75.0],
}


func _init() -> void:
	_main()


func _main() -> void:
	var args := OS.get_cmdline_user_args()
	models_dir = _arg_value(args, "--models-dir", models_dir)
	out_dir = _arg_value(args, "--out-dir", out_dir)
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(out_dir))
	var models := _arg_value(args, "--models")
	if not models.is_empty():
		await _probe_models(models.split(","), args.has("--anim"))
	else:
		print("usage: -- --models a,b [--anim] [--models-dir res://...] [--out-dir res://...]")
	quit()


func _arg_value(args: PackedStringArray, key: String, fallback := "") -> String:
	var i := args.find(key)
	return args[i + 1] if i != -1 and i + 1 < args.size() else fallback


# ---- Model mode ----

func _probe_models(names: PackedStringArray, anim_mode := false) -> void:
	root.size = Vector2i(800, 800)
	var stage := _build_stage()
	root.add_child(stage)
	var slot := stage.get_node("Slot") as Node3D
	var camera := stage.get_node("Camera") as Camera3D
	for name in names:
		# A bare name probes <models_dir>/<name>.glb; anything with a "/" is
		# treated as a full res:// scene path (probe wired wrappers/scenes).
		var trimmed := name.strip_edges()
		var path := trimmed if trimmed.contains("/") else "%s/%s.glb" % [models_dir, trimmed]
		var packed := load(path) as PackedScene
		if packed == null:
			print("SKIP %s — load failed" % path)
			continue
		var model := packed.instantiate() as Node3D
		model.visible = true  # some wrappers ship hidden
		slot.add_child(model)
		await process_frame
		var label := trimmed.get_file().get_basename()
		var aabb := _merged_aabb(model)
		print("%s  size=%v" % [label, aabb.size])
		var center := aabb.get_center()
		var dist: float = aabb.size.length() * 0.5 / tan(deg_to_rad(camera.fov * 0.5)) * 1.35
		if anim_mode:
			await _probe_animation(label, model, camera, center, dist)
		else:
			for angle_name in ANGLES:
				_aim(camera, center, dist, ANGLES[angle_name][0], ANGLES[angle_name][1])
				await _capture("%s_%s" % [label, angle_name])
		model.queue_free()
		await process_frame


## Sample every animation clip at 6 timestamps from two fixed angles
## (profile = "front" yaw because GLB rest pose faces mesh +X).
func _probe_animation(name: String, model: Node3D, camera: Camera3D,
		center: Vector3, dist: float) -> void:
	var player := model.find_child("AnimationPlayer", true, false) as AnimationPlayer
	if player == null:
		print("SKIP %s — no AnimationPlayer in GLB" % name)
		return
	var clips := player.get_animation_list()
	if clips.is_empty():
		print("SKIP %s — AnimationPlayer has no clips" % name)
		return
	for c in clips.size():
		var clip: String = clips[c]
		var length := player.get_animation(clip).length
		print("%s  clip %d '%s' length=%.2fs" % [name, c, clip, length])
		player.play(clip)
		player.pause()
		for i in 6:
			var t := length * i / 6.0
			player.seek(t, true)
			for angle_name in ["front", "quarter2"]:
				_aim(camera, center, dist, ANGLES[angle_name][0], ANGLES[angle_name][1])
				await _capture("%s_c%d_t%d_%s" % [name, c, i, angle_name])


func _aim(camera: Camera3D, center: Vector3, dist: float,
		yaw_deg: float, pitch_deg: float) -> void:
	var offset := Vector3(0, 0, -dist) \
		.rotated(Vector3.RIGHT, -deg_to_rad(pitch_deg)) \
		.rotated(Vector3.UP, deg_to_rad(yaw_deg))
	camera.position = center + offset
	camera.look_at(center)


## Lit stage: sky + key/fill lights so back views aren't black.
func _build_stage() -> Node3D:
	var stage := Node3D.new()
	var env := Environment.new()
	var sky_mat := ProceduralSkyMaterial.new()
	sky_mat.sky_top_color = Color(0.36, 0.57, 0.82)
	sky_mat.sky_horizon_color = Color(0.87, 0.8, 0.62)
	env.background_mode = Environment.BG_SKY
	env.sky = Sky.new()
	env.sky.sky_material = sky_mat
	env.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	env.ambient_light_color = Color(0.6, 0.58, 0.52)
	env.ambient_light_energy = 0.8
	var world_env := WorldEnvironment.new()
	world_env.environment = env
	stage.add_child(world_env)

	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-40, -30, 0)
	key.light_energy = 1.3
	stage.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-20, 150, 0)
	fill.light_energy = 0.5
	stage.add_child(fill)

	var slot := Node3D.new()
	slot.name = "Slot"
	stage.add_child(slot)
	var camera := Camera3D.new()
	camera.name = "Camera"
	camera.fov = 50.0
	stage.add_child(camera)
	camera.make_current()
	return stage


# ---- Shared ----

func _capture(file_name: String) -> void:
	for i in 3:
		await process_frame
	var img := root.get_texture().get_image()
	var out := "%s/%s.png" % [out_dir, file_name]
	img.save_png(ProjectSettings.globalize_path(out))
	print("probe: %s" % out)


func _merged_aabb(node: Node) -> AABB:
	var result := AABB()
	var has_any := false
	if node is VisualInstance3D:
		var vi := node as VisualInstance3D
		result = vi.global_transform * vi.get_aabb()
		has_any = true
	for child in node.get_children():
		var child_aabb := _merged_aabb(child)
		if child_aabb.size != Vector3.ZERO:
			result = result.merge(child_aabb) if has_any else child_aabb
			has_any = true
	return result
