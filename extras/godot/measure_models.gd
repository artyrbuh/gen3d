extends SceneTree

## Print the merged AABB of every .glb in the models dir so scene transforms
## can be baked with real numbers instead of guesses. Headless-safe.
##
## Run from the consuming Godot project's root:
##   godot --headless --path . \
##       --script res://tools/gen3d/extras/godot/measure_models.gd \
##       -- [--models-dir res://assets/models]


func _init() -> void:
	var args := OS.get_cmdline_user_args()
	var models_dir := "res://assets/models"
	var i := args.find("--models-dir")
	if i != -1 and i + 1 < args.size():
		models_dir = args[i + 1]
	var dir := DirAccess.open(models_dir)
	if dir == null:
		print("measure_models: cannot open %s" % models_dir)
		quit(1)
		return
	var names: Array[String] = []
	for f in dir.get_files():
		if f.ends_with(".glb"):
			names.append(f)
	names.sort()
	for f in names:
		var packed := load("%s/%s" % [models_dir, f]) as PackedScene
		if packed == null:
			print("%s  LOAD FAILED" % f)
			continue
		var inst := packed.instantiate() as Node3D
		root.add_child(inst)
		var aabb := _merged_aabb(inst)
		print("%s  size=%v  origin_min=%v" % [f, aabb.size, aabb.position])
		inst.queue_free()
	quit()


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
