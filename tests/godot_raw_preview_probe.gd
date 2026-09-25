@tool
extends EditorPlugin
## Editor-mode regression using a tiny synthetic GLB, never production assets.

var _checks: int = 0
var _failed: bool = false

func _enter_tree() -> void:
	call_deferred("_run")

func _check(value: bool, label: String) -> void:
	_checks += 1
	if not value:
		_failed = true
		push_error("RAW_PREVIEW_PROBE_FAIL: " + label)

func _run() -> void:
	_check(Engine.is_editor_hint(), "test exercises editor-only texture extraction behavior")
	for index: int in range(30):
		await get_tree().process_frame
	DirAccess.make_dir_recursive_absolute("res://fresh")
	var fixture := FileAccess.open("res://fresh/fixture.glb", FileAccess.WRITE)
	fixture.store_buffer(FileAccess.get_file_as_bytes("res://fixture.bin"))
	fixture.close()
	var before: PackedStringArray = DirAccess.get_files_at("res://fresh")
	_check(before == PackedStringArray(["fixture.glb"]), "fixture has not been imported")
	var preview: Control = load("res://addons/asset_pipeline/asset_preview.gd").new()
	# Do not enter the tree: exercise the raw load synchronously before the
	# editor's filesystem scanner can produce any imports for this new file.
	for iteration: int in range(2):
		var model: Node = preview._raw_gltf("res://fresh/fixture.glb")
		_check(model is Node3D, "raw GLB becomes a scene on pass %d" % iteration)
		var mesh: Mesh = null
		var pending: Array[Node] = []
		if model != null:
			pending.append(model)
		while not pending.is_empty():
			var node: Node = pending.pop_back()
			if node is MeshInstance3D:
				mesh = node.mesh
			elif node is ImporterMeshInstance3D and node.mesh != null:
				mesh = node.mesh.get_mesh()
			pending.append_array(node.get_children())
		_check(mesh != null, "synthetic triangle mesh is present")
		if mesh != null:
			var material: BaseMaterial3D = mesh.surface_get_material(0) as BaseMaterial3D
			_check(material != null and material.albedo_texture != null, "embedded color texture remains available")
			if material != null and material.albedo_texture != null:
				var image: Image = material.albedo_texture.get_image()
				_check(image != null and image.get_size() == Vector2i(2, 2), "embedded texture pixels were decoded")
				_check(material.albedo_texture.resource_path.is_empty(), "preview texture has no external resource path")
		_check(DirAccess.get_files_at("res://fresh") == before, "raw preview writes no texture or import sidecars")
		if model != null:
			model.free()
	preview.free()
	if not _failed:
		print("RAW_PREVIEW_PROBE_PASS checks=", _checks)
	get_tree().quit(1 if _failed else 0)
