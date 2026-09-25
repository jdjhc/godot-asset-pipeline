extends SceneTree
## Offline GLB review only. No editor, plugin, network, model edits, or API calls.
## Prefer run_asset_review.py, which runs this in an isolated empty project.
## godot --path EMPTY_PROJECT --script /absolute/render_asset_review.gd -- 
##   --manifest=/absolute/assets.json --output-dir=/absolute/review

var _width: int = 1024
var _height: int = 1024
var _output: String = ""
var _assets: Array = []
var _extra_views: bool = false

func _initialize() -> void:
	call_deferred("_run")

func _run() -> void:
	var parse_error: String = _parse_arguments()
	if not parse_error.is_empty():
		push_error(parse_error)
		quit(2)
		return
	if DisplayServer.get_name() == "headless":
		push_error("Use a real renderer, without --headless; a dummy renderer cannot produce review PNGs.")
		quit(2)
		return
	if DirAccess.make_dir_recursive_absolute(_output) != OK:
		push_error("Cannot create review output directory: " + _output)
		quit(2)
		return
	root.title = "Offline GLB Asset Review"
	root.size = Vector2i(720, 720)
	var report: Dictionary = {"format_version": 1, "created_at": Time.get_datetime_string_from_system(true),
		"engine": Engine.get_version_info(), "renderer": ProjectSettings.get_setting("rendering/renderer/rendering_method", "unknown"),
		"image_size": [_width, _height], "assets": [], "errors": [],
		"notes": ["Raw GLTFDocument import; scripts, animation, imported lights, audio and physics disabled.",
			"Bounds and triangle counts include mesh instances in the imported rest pose, before any normalization.",
			"Orthographic front/right/back views. Front means the declared front_axis (+z by default), not an inferred semantic front.",
			"Material and texture counts are distinct loaded resources, not content-hash deduplicated images.",
			"No source model or current editor scene is modified."]}
	for index: int in range(_assets.size()):
		var reviewed: Dictionary = await _review(_assets[index], index)
		report["assets"].append(reviewed)
		if reviewed.has("error"):
			report["errors"].append({"id": reviewed.get("id", ""), "error": reviewed["error"]})
		print("ASSET_REVIEW_ITEM " + JSON.stringify({"id": reviewed.get("id"), "statistics": reviewed.get("statistics"), "views": reviewed.get("views"), "source_unchanged": reviewed.get("source_unchanged"), "error": reviewed.get("error")}))
	var report_path: String = _output.path_join("review.json")
	if not _write_json(report_path, report):
		push_error("Cannot write report: " + report_path)
		quit(1)
		return
	print("ASSET_REVIEW_COMPLETE " + report_path)
	quit(0 if report["errors"].is_empty() else 1)

func _parse_arguments() -> String:
	var manifest_path: String = ""
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--manifest="):
			manifest_path = argument.trim_prefix("--manifest=")
		elif argument.begins_with("--output-dir="):
			_output = argument.trim_prefix("--output-dir=")
		elif argument.begins_with("--width="):
			_width = argument.trim_prefix("--width=").to_int()
		elif argument.begins_with("--height="):
			_height = argument.trim_prefix("--height=").to_int()
		elif argument == "--extra-views":
			_extra_views = true
		elif argument.begins_with("--"):
			return "Unknown argument: " + argument
		else:
			_assets.append({"id": argument.get_file().get_basename(), "path": argument})
	if _output.is_empty() or not _output.is_absolute_path():
		return "--output-dir must be an absolute path"
	if _width < 128 or _height < 128 or _width > 4096 or _height > 4096:
		return "Render width and height must each be between 128 and 4096"
	if not manifest_path.is_empty():
		if not manifest_path.is_absolute_path() or not FileAccess.file_exists(manifest_path):
			return "Manifest must be an existing absolute path"
		var json := JSON.new()
		if json.parse(FileAccess.get_file_as_string(manifest_path)) != OK:
			return "Invalid manifest JSON: " + json.get_error_message()
		var source: Variant = json.data
		if source is Dictionary and source.has("assets"):
			source = source["assets"]
		if source is Array:
			_assets.append_array(source)
		elif source is Dictionary:
			for asset_id: Variant in source:
				var entry: Variant = source[asset_id]
				if entry is String:
					_assets.append({"id": str(asset_id), "path": entry})
				elif entry is Dictionary:
					var copy: Dictionary = entry.duplicate(true)
					copy["id"] = str(asset_id)
					_assets.append(copy)
				else:
					return "Manifest asset entries must be paths or objects"
		else:
			return "Manifest must contain an assets array or an id-to-path/object mapping"
	if _assets.is_empty():
		return "Supply GLB paths after -- or use --manifest=/absolute/assets.json"
	for entry: Variant in _assets:
		if not entry is Dictionary:
			return "Each asset must be an object with id and path"
	return ""

func _review(entry: Dictionary, index: int) -> Dictionary:
	var path: String = str(entry.get("path", entry.get("glb", "")))
	var asset_id: String = str(entry.get("id", path.get_file().get_basename()))
	var result: Dictionary = {"id": asset_id, "source_path": path, "source_metadata": entry.duplicate(true)}
	if not path.is_absolute_path() or path.get_extension().to_lower() != "glb" or not FileAccess.file_exists(path):
		result["error"] = "Expected an existing absolute .glb path"
		return result
	var axis: String = str(entry.get("front_axis", "+z")).to_lower()
	var directions: Dictionary = {"+z": Vector3(0, 0, 1), "-z": Vector3(0, 0, -1), "+x": Vector3(1, 0, 0), "-x": Vector3(-1, 0, 0)}
	if not directions.has(axis):
		result["error"] = "front_axis must be +z, -z, +x, or -x"
		return result
	var regex := RegEx.new()
	regex.compile("[^A-Za-z0-9_-]+")
	var slug: String = regex.sub(asset_id, "_", true).strip_edges()
	if slug.is_empty():
		slug = "asset"
	var directory: String = _output.path_join("%02d_%s" % [index + 1, slug])
	if DirAccess.make_dir_recursive_absolute(directory) != OK:
		result["error"] = "Cannot create per-asset output directory"
		return result
	result["sha256_before"] = FileAccess.get_sha256(path)
	var file := FileAccess.open(path, FileAccess.READ)
	result["source_bytes"] = file.get_length()
	file.close()
	var document := GLTFDocument.new()
	var state := GLTFState.new()
	var load_error: Error = document.append_from_file(path, state)
	if load_error != OK:
		result["error"] = "Raw GLB import failed: " + error_string(load_error)
		return result
	var imported: Node = document.generate_scene(state)
	if not imported is Node3D:
		if imported != null:
			imported.free()
		result["error"] = "GLB has no 3D scene"
		return result
	_sanitize(imported)
	var viewport := SubViewport.new()
	viewport.size = Vector2i(_width, _height)
	viewport.own_world_3d = true
	viewport.transparent_bg = false
	viewport.msaa_3d = Viewport.MSAA_4X
	viewport.render_target_update_mode = SubViewport.UPDATE_ALWAYS
	root.add_child(viewport)
	var stage := Node3D.new()
	viewport.add_child(stage)
	stage.add_child(imported)
	var stats: Dictionary = _statistics(imported)
	if not stats.get("valid_bounds", false):
		viewport.free()
		result["error"] = "GLB contains no finite, nonempty mesh bounds"
		return result
	var bounds: AABB = stats["_bounds"]
	stats.erase("_bounds")
	result["statistics"] = stats
	result["front_axis"] = axis
	_setup_lighting(stage, bounds)
	var camera := Camera3D.new()
	camera.projection = Camera3D.PROJECTION_ORTHOGONAL
	camera.keep_aspect = Camera3D.KEEP_HEIGHT
	var aspect: float = float(_width) / _height
	camera.size = maxf(bounds.size.y, maxf(bounds.size.x, bounds.size.z) / aspect) * 1.16
	camera.size = maxf(camera.size, 0.05)
	var radius: float = maxf(bounds.size.length() * 0.5, 0.01)
	var distance: float = radius * 4.0
	camera.near = maxf(radius * 0.001, 0.0001)
	camera.far = distance + radius * 8.0
	stage.add_child(camera)
	camera.make_current()
	var display := TextureRect.new()
	display.texture = viewport.get_texture()
	display.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	display.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	display.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	root.add_child(display)
	var front: Vector3 = directions[axis]
	var views: Array = [{"name": "front", "direction": front}, {"name": "right", "direction": front.rotated(Vector3.UP, PI * 0.5)}, {"name": "back", "direction": -front}]
	if _extra_views:
		views.append({"name": "top", "direction": Vector3.UP, "up": -front})
		views.append({"name": "three_quarter", "direction": (front + front.rotated(Vector3.UP, PI * 0.5) * 0.65 + Vector3.UP * 0.65).normalized()})
	result["views"] = []
	for view: Dictionary in views:
		camera.position = bounds.get_center() + view["direction"] * distance
		camera.look_at(bounds.get_center(), view.get("up", Vector3.UP))
		if view["name"] in ["top", "three_quarter"]:
			var horizontal: Vector3 = camera.basis.x.abs()
			var vertical: Vector3 = camera.basis.y.abs()
			camera.size = maxf(vertical.dot(bounds.size), horizontal.dot(bounds.size) / aspect) * 1.16
		for frame: int in range(5):
			await process_frame
		await RenderingServer.frame_post_draw
		var image: Image = viewport.get_texture().get_image()
		var destination: String = directory.path_join(str(view["name"]) + ".png")
		if image == null or image.is_empty() or image.save_png(destination) != OK:
			result["error"] = "Failed to capture " + str(view["name"]) + " PNG"
			break
		result["views"].append({"name": view["name"], "path": destination, "camera_position": _vector(camera.position),
			"target": _vector(bounds.get_center()), "orthographic_height": camera.size, "image_variation": _image_variation(image)})
	result["sha256_after"] = FileAccess.get_sha256(path)
	result["source_unchanged"] = result["sha256_before"] == result["sha256_after"]
	if not result["source_unchanged"]:
		result["error"] = "Source file changed during review"
	display.free()
	viewport.free()
	_write_json(directory.path_join("asset.json"), result)
	return result

func _sanitize(imported: Node) -> void:
	var pending: Array[Node] = [imported]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		node.set_script(null)
		node.process_mode = Node.PROCESS_MODE_DISABLED
		if node is AnimationPlayer:
			node.autoplay = ""
			node.stop()
		if node is AnimationMixer:
			node.active = false
		if node is Camera3D:
			node.current = false
		if node is Light3D:
			node.visible = false
		if node is AudioStreamPlayer or node is AudioStreamPlayer3D or node is AudioStreamPlayer2D:
			node.autoplay = false
			node.stop()
		if node is CollisionObject3D:
			node.collision_layer = 0
			node.collision_mask = 0
		if node is RigidBody3D:
			node.freeze = true
		if node is GPUParticles3D or node is CPUParticles3D:
			node.emitting = false
		pending.append_array(node.get_children())

func _statistics(imported: Node3D) -> Dictionary:
	var mesh_resources: Dictionary = {}
	var materials: Dictionary = {}
	var textures: Dictionary = {}
	var instances: Array = []
	var has_bounds: bool = false
	var bounds := AABB()
	var triangles: int = 0
	var vertices: int = 0
	var surfaces: int = 0
	var pending: Array[Node] = [imported]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		pending.append_array(node.get_children())
		if not node is MeshInstance3D or node.mesh == null:
			continue
		var mesh: Mesh = node.mesh
		mesh_resources[mesh.get_instance_id()] = true
		var world_bounds: AABB = node.global_transform * node.get_aabb()
		if world_bounds.position.is_finite() and world_bounds.size.is_finite():
			bounds = bounds.merge(world_bounds) if has_bounds else world_bounds
			has_bounds = true
		var instance_triangles: int = 0
		for surface: int in range(mesh.get_surface_count()):
			surfaces += 1
			var arrays: Array = mesh.surface_get_arrays(surface)
			var vertex_count: int = arrays[Mesh.ARRAY_VERTEX].size() if arrays[Mesh.ARRAY_VERTEX] != null else 0
			var index_count: int = arrays[Mesh.ARRAY_INDEX].size() if arrays[Mesh.ARRAY_INDEX] != null else 0
			vertices += vertex_count
			var count: int = index_count if index_count > 0 else vertex_count
			var primitive: int = mesh.surface_get_primitive_type(surface) if mesh is ArrayMesh else Mesh.PRIMITIVE_TRIANGLES
			if primitive == Mesh.PRIMITIVE_TRIANGLES:
				instance_triangles += int(count / 3)
			elif primitive == Mesh.PRIMITIVE_TRIANGLE_STRIP:
				instance_triangles += maxi(count - 2, 0)
			var material: Material = node.get_active_material(surface)
			if material != null:
				_collect_material(material, materials, textures)
		triangles += instance_triangles
		instances.append({"name": str(node.name), "path": str(imported.get_path_to(node)), "triangles": instance_triangles,
			"surfaces": mesh.get_surface_count(), "bounds": _bounds_json(world_bounds), "visible": node.is_visible_in_tree()})
	return {"valid_bounds": has_bounds and bounds.size.length() > 0.000001, "_bounds": bounds,
		"bounds": _bounds_json(bounds), "mesh_instances": instances.size(), "unique_meshes": mesh_resources.size(),
		"triangles": triangles, "vertices": vertices, "surfaces": surfaces,
		"materials": materials.size(), "textures": textures.size(), "texture_details": textures.values(), "instances": instances}

func _collect_material(material: Material, materials: Dictionary, textures: Dictionary) -> void:
	if materials.has(material.get_instance_id()):
		return
	materials[material.get_instance_id()] = true
	for property: Dictionary in material.get_property_list():
		if property["type"] != TYPE_OBJECT:
			continue
		var value: Variant = material.get(property["name"])
		if value is Texture2D and not textures.has(value.get_instance_id()):
			textures[value.get_instance_id()] = {"name": value.resource_name, "width": value.get_width(), "height": value.get_height(), "resource_path": value.resource_path}
	if material.next_pass != null:
		_collect_material(material.next_pass, materials, textures)

func _setup_lighting(stage: Node3D, bounds: AABB) -> void:
	var environment := WorldEnvironment.new()
	var settings := Environment.new()
	settings.background_mode = Environment.BG_COLOR
	settings.background_color = Color(0.21, 0.23, 0.26)
	settings.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	settings.ambient_light_color = Color(0.9, 0.94, 1.0)
	settings.ambient_light_energy = 0.6
	settings.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment.environment = settings
	stage.add_child(environment)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-50, -35, 0)
	key.light_energy = 0.9
	key.shadow_enabled = true
	stage.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-25, 140, 0)
	fill.light_energy = 0.4
	stage.add_child(fill)
	var floor_mesh := PlaneMesh.new()
	var floor_size: float = maxf(bounds.size.length() * 6.0, 1.0)
	floor_mesh.size = Vector2(floor_size, floor_size)
	var floor_material := StandardMaterial3D.new()
	floor_material.albedo_color = Color(0.19, 0.21, 0.24)
	floor_material.roughness = 1.0
	floor_mesh.material = floor_material
	var floor_node := MeshInstance3D.new()
	floor_node.mesh = floor_mesh
	floor_node.position = Vector3(bounds.get_center().x, bounds.position.y - maxf(bounds.size.y * 0.002, 0.001), bounds.get_center().z)
	stage.add_child(floor_node)

func _image_variation(image: Image) -> float:
	# A compact renderer sanity metric, not an aesthetic or admission gate.
	var lowest: float = INF
	var highest: float = -INF
	for y: int in range(0, image.get_height(), maxi(1, image.get_height() / 24)):
		for x: int in range(0, image.get_width(), maxi(1, image.get_width() / 24)):
			var color: Color = image.get_pixel(x, y)
			var brightness: float = (color.r + color.g + color.b) / 3.0
			lowest = minf(lowest, brightness)
			highest = maxf(highest, brightness)
	return highest - lowest

func _vector(value: Vector3) -> Array:
	return [value.x, value.y, value.z]

func _bounds_json(bounds: AABB) -> Dictionary:
	return {"position": _vector(bounds.position), "size": _vector(bounds.size), "center": _vector(bounds.get_center())}

func _write_json(path: String, data: Dictionary) -> bool:
	var file := FileAccess.open(path, FileAccess.WRITE)
	if file == null:
		return false
	file.store_string(JSON.stringify(data, "  ") + "\n")
	file.close()
	return true
