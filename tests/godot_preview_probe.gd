extends SceneTree
## Disposable, real-resource regression. run_preview_probe.py creates the project
## and copies actual generated GLBs and images. No backend/plugin is enabled.

var _checks: int = 0
var _failed: bool = false
var _interactions: int = 0
var _canvas: Control
var _output_dir: String = ""
var _rendered_before: int = 0

func _init() -> void:
	call_deferred("_run")

func _check(value: bool, label: String) -> bool:
	if not value:
		_failed = true
		push_error("ASSET_PREVIEW_PROBE_FAIL: " + label)
		return false
	_checks += 1
	return true

func _frames(count: int = 3) -> void:
	for _index: int in range(count):
		await process_frame

func _rendered_image(preview: Control, filename: String) -> Image:
	if DisplayServer.get_name() == "headless":
		return null
	await _frames(4)
	await RenderingServer.frame_post_draw
	var viewport: SubViewport = preview.get_node("AssetPreviewViewport")
	var image: Image = viewport.get_texture().get_image()
	if _check(image != null and not image.is_empty(), "renderer returns nonempty model framebuffer"):
		if not _output_dir.is_empty():
			_check(image.save_png(_output_dir.path_join(filename)) == OK, "model framebuffer saved")
		return image
	return null

func _has_rendered_detail(image: Image) -> bool:
	if image == null or image.is_empty():
		return false
	var corner: Color = image.get_pixel(0, 0)
	var different: int = 0
	for y: int in range(0, image.get_height(), maxi(1, int(image.get_height() / 40.0))):
		for x: int in range(0, image.get_width(), maxi(1, int(image.get_width() / 40.0))):
			if not image.get_pixel(x, y).is_equal_approx(corner):
				different += 1
	return different > 50

func _mouse_button(position: Vector2, button: MouseButton, pressed: bool) -> void:
	var event := InputEventMouseButton.new()
	event.position = position
	event.global_position = position
	event.button_index = button
	event.pressed = pressed
	event.button_mask = MOUSE_BUTTON_MASK_LEFT if pressed and button == MOUSE_BUTTON_LEFT else 0
	root.push_input(event, true)

func _mouse_motion(position: Vector2, relative: Vector2, dragging: bool) -> void:
	var event := InputEventMouseMotion.new()
	event.position = position
	event.global_position = position
	event.relative = relative
	event.button_mask = MOUSE_BUTTON_MASK_LEFT if dragging else 0
	root.push_input(event, true)

func _new_preview(script: Script, at: Vector2) -> Control:
	var preview: Control = script.new()
	preview.position = at
	preview.size = Vector2(420, 300)
	_canvas.add_child(preview)
	return preview

func _find_image_texture(root_node: Node) -> Texture2D:
	for child: Node in root_node.get_children(true):
		if child is TextureRect and child.texture != null and child.texture is ImageTexture:
			return child.texture
		var nested: Texture2D = _find_image_texture(child)
		if nested != null:
			return nested
	return null

func _version(version_id: String, path: String, role: String) -> Dictionary:
	return {"id": version_id, "created_at": "2026-09-25", "files": [{"path": path, "role": role}]}

func _node(node_id: String, kind: String, version_id: String, versions: Array, position: Array) -> Dictionary:
	return {"id": node_id, "label": node_id, "kind": kind, "current_version": version_id,
		"versions": versions, "position": position, "prompt": "Preserved prompt", "params": {},
		"inputs": [], "state": "ready", "stale": false}

func _run() -> void:
	for argument: String in OS.get_cmdline_user_args():
		if argument.begins_with("--output-dir="):
			_output_dir = argument.trim_prefix("--output-dir=")
			DirAccess.make_dir_recursive_absolute(_output_dir)
	_canvas = Control.new()
	_canvas.size = Vector2(1440, 960)
	root.add_child(_canvas)
	var preview_script: Script = load("res://addons/asset_pipeline/asset_preview.gd") as Script
	if not _check(preview_script != null and preview_script.can_instantiate(), "preview script loads"):
		quit(1)
		return
	var preview: Control = _new_preview(preview_script, Vector2(5, 5))
	preview.connect("interacted", func(): _interactions += 1)
	preview.call("set_artifact", "res://image_a.png")
	await _frames()
	var image_state: Dictionary = preview.call("debug_state")
	_check(image_state.get("state") == "ready" and image_state.get("type") == "image", "image artifact reaches ready state")
	var texture: Texture2D = _find_image_texture(preview)
	_check(texture != null and texture.get_width() > 0 and texture.get_height() > 0, "actual image texture decoded")
	_check(int(image_state.get("mesh_count", -1)) == 0, "image preview has no imported meshes")
	preview.call("set_artifact", "res://model_a.glb")
	await _frames(5)
	var model_state: Dictionary = preview.call("debug_state")
	if not _check(model_state.get("state") == "ready" and model_state.get("type") == "model", "real GLB reaches ready state"):
		print("MODEL_STATE ", model_state)
		quit(1)
		return
	_check(int(model_state.get("mesh_count", 0)) > 0, "real GLB mesh instances loaded")
	var original_camera: Vector3 = model_state.get("camera_position", Vector3.ZERO)
	var original_distance: float = float(model_state.get("distance", 0.0))
	_check(original_camera.is_finite() and original_distance > 0, "camera fitted to finite real model bounds")
	var rendered_before: Image = await _rendered_image(preview, "model_before_orbit.png")
	if rendered_before != null:
		_check(_has_rendered_detail(rendered_before), "actual model framebuffer contains visible model detail")
		_rendered_before = hash(rendered_before.get_data())
	var center: Vector2 = preview.get_global_rect().get_center()
	_mouse_motion(center, Vector2.ZERO, false)
	_mouse_button(center, MOUSE_BUTTON_LEFT, true)
	_mouse_motion(center + Vector2(32, 12), Vector2(32, 12), true)
	_mouse_button(center + Vector2(32, 12), MOUSE_BUTTON_LEFT, false)
	await _frames()
	_check(_interactions == 1, "viewport GUI route delivers model click")
	_check(not (preview.call("debug_state").get("camera_position") as Vector3).is_equal_approx(original_camera), "viewport mouse drag orbits real preview camera")
	var distance_before_wheel: float = float(preview.call("debug_state").get("distance"))
	_mouse_button(center, MOUSE_BUTTON_WHEEL_UP, true)
	_mouse_button(center, MOUSE_BUTTON_WHEEL_UP, false)
	_check(float(preview.call("debug_state").get("distance")) < distance_before_wheel, "viewport mouse wheel zooms preview")
	preview.call("reset_view")
	preview.call("orbit", Vector2(60, 15))
	var orbited: Dictionary = preview.call("debug_state")
	_check(not (orbited.get("camera_position") as Vector3).is_equal_approx(original_camera), "orbit changes actual camera position")
	preview.call("zoom", 0.75)
	var zoomed: Dictionary = preview.call("debug_state")
	_check(float(zoomed.get("distance", 0.0)) < original_distance, "zoom changes camera distance")
	var rendered_after: Image = await _rendered_image(preview, "model_after_orbit.png")
	if rendered_after != null:
		_check(hash(rendered_after.get_data()) != _rendered_before, "orbit changes actual rendered model pixels")
	var retained_camera: Vector3 = zoomed.get("camera_position")
	var retained_world: RID = zoomed.get("world_rid")
	preview.call("set_artifact", "res://model_a.glb")
	await _frames()
	var same: Dictionary = preview.call("debug_state")
	_check((same.get("camera_position") as Vector3).is_equal_approx(retained_camera), "same path keeps orbit and zoom")
	_check(same.get("world_rid") == retained_world, "same path retains preview world")
	preview.call("zoom", 1e20)
	var far_state: Dictionary = preview.call("debug_state")
	_check(float(far_state.get("distance")) < original_distance * 1000.0, "zoom out bounded")
	preview.call("zoom", 1e-20)
	var close_state: Dictionary = preview.call("debug_state")
	_check(float(close_state.get("distance")) > 0.00001, "zoom in bounded away from center")
	preview.call("orbit", Vector2(0, 1000000))
	_check((preview.call("debug_state").get("camera_position") as Vector3).is_finite(), "extreme orbit remains finite")
	preview.call("reset_view")
	var reset: Dictionary = preview.call("debug_state")
	_check(is_equal_approx(float(reset.get("distance")), original_distance), "reset restores fitted distance")
	var second: Control = _new_preview(preview_script, Vector2(440, 5))
	second.call("set_artifact", "res://model_b.glb")
	await _frames(5)
	var second_state: Dictionary = second.call("debug_state")
	_check(int(second_state.get("mesh_count", 0)) > 0, "second real model loaded")
	_check(second_state.get("world_rid") != reset.get("world_rid"), "previews use isolated 3D worlds")
	_check(second_state.get("viewport_rid") != reset.get("viewport_rid"), "previews use isolated viewports")
	preview.call("set_artifact", "res://model_b.glb")
	await _frames(5)
	var replaced: Dictionary = preview.call("debug_state")
	_check(str(replaced.get("artifact_path", "")).ends_with("model_b.glb") and int(replaced.get("mesh_count", 0)) > 0, "model version replacement loads new GLB")
	preview.call("set_artifact", "res://image_b.png")
	await _frames()
	_check(preview.call("debug_state").get("type") == "image", "model to image replacement works")
	_check(int(preview.call("debug_state").get("mesh_count", -1)) == 0, "model to image clears old meshes")
	preview.call("clear_preview", "没有产物")
	_check(preview.call("debug_state").get("state") == "empty", "clear resets preview state")
	_check(int(preview.call("debug_state").get("mesh_count", -1)) == 0, "clear removes model content")
	preview.queue_free()
	second.queue_free()
	await _frames()
	await _test_graph_refresh()
	await _test_dependencies()
	_canvas.queue_free()
	await _frames()
	if not _failed:
		print("ASSET_PREVIEW_PROBE_PASS checks=", _checks)
	quit(1 if _failed else 0)

func _test_graph_refresh() -> void:
	var panel_script: Script = load("res://addons/asset_pipeline/panel.gd") as Script
	if not _check(panel_script != null and panel_script.can_instantiate(), "real panel script loads"):
		return
	var panel: Control = panel_script.new()
	panel.size = Vector2(1400, 900)
	_canvas.add_child(panel)
	await _frames()
	var image_node: Dictionary = _node("image", "multiview", "image_v1", [_version("image_v1", "image_a.png", "front")], [20, 20])
	image_node["versions"][0]["files"].append({"path": "image_b.png", "role": "back"})
	var model_node: Dictionary = _node("model", "model", "model_v1", [_version("model_v1", "model_a.glb", "model")], [380, 20])
	model_node["versions"][0]["files"].push_front({"path": "image_a.png", "role": "preview"})
	model_node["inputs"] = [{"node_id": "image", "role": "front"}]
	var snapshot: Dictionary = {"revision": 1, "nodes": [image_node, model_node], "jobs": []}
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames(5)
	var previews: Dictionary = panel.get("_graph_previews")
	if not _check(previews.has("image") and previews.has("model"), "graph embeds previews on both image and model nodes"):
		panel.queue_free()
		return
	if DisplayServer.get_name() != "headless" and not _output_dir.is_empty():
		await RenderingServer.frame_post_draw
		var framebuffer: Image = root.get_texture().get_image()
		_check(framebuffer != null and not framebuffer.is_empty() and framebuffer.save_png(_output_dir.path_join("pipeline_graph.png")) == OK, "complete graph framebuffer saved")
	var graph_image: Control = previews["image"]
	var graph_model: Control = previews["model"]
	_check(graph_image.call("debug_state").get("type") == "image", "graph image node displays its real artifact")
	_check(int(graph_model.call("debug_state").get("mesh_count", 0)) > 0, "graph model node contains real mesh")
	var graph: GraphEdit = panel.get("_graph")
	var cards: Dictionary = panel.get("_graph_cards")
	var model_card: GraphNode = cards["model"]
	var card_position_before: Vector2 = model_card.position_offset
	var graph_zoom_before: float = graph.zoom
	var graph_camera_before: Vector3 = graph_model.call("debug_state").get("camera_position")
	var graph_center: Vector2 = graph_model.get_global_rect().get_center()
	_mouse_motion(graph_center, Vector2.ZERO, false)
	_mouse_button(graph_center, MOUSE_BUTTON_LEFT, true)
	_mouse_motion(graph_center + Vector2(22, 7), Vector2(22, 7), true)
	_mouse_button(graph_center + Vector2(22, 7), MOUSE_BUTTON_LEFT, false)
	await _frames()
	_check(panel.get("_selected_id") == "model", "clicking inline model selects its graph node")
	_check(not (graph_model.call("debug_state").get("camera_position") as Vector3).is_equal_approx(graph_camera_before), "mouse drag inside GraphEdit reaches model camera")
	_check(model_card.position_offset.is_equal_approx(card_position_before), "model orbit does not drag graph card")
	var graph_distance_before: float = float(graph_model.call("debug_state").get("distance"))
	_mouse_button(graph_center, MOUSE_BUTTON_WHEEL_UP, true)
	_mouse_button(graph_center, MOUSE_BUTTON_WHEEL_UP, false)
	_check(float(graph_model.call("debug_state").get("distance")) < graph_distance_before, "wheel over inline model zooms model camera")
	_check(is_equal_approx(graph.zoom, graph_zoom_before), "model wheel does not zoom entire graph")
	var pickers: Dictionary = panel.get("_graph_artifact_pickers")
	var image_picker: OptionButton = pickers["image"]
	_check(image_picker.item_count == 2 and image_picker.visible, "multiview node exposes both generated images")
	image_picker.select(1)
	image_picker.item_selected.emit(1)
	_check(str(graph_image.call("debug_state").get("artifact_path", "")).ends_with("image_b.png"), "multiview picker changes displayed image")
	panel.set("_selected_id", "model")
	panel.call("_load_inspector")
	await _frames(3)
	var inspector: Control = panel.get("_preview")
	_check(inspector.call("debug_state").get("type") == "model", "inspector prioritizes real model over preview thumbnail")
	inspector.call("orbit", Vector2(30, 6))
	var inspector_camera: Vector3 = inspector.call("debug_state").get("camera_position")
	graph_model.call("orbit", Vector2(44, 9))
	graph_model.call("zoom", 0.8)
	var before_refresh: Dictionary = graph_model.call("debug_state")
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames()
	previews = panel.get("_graph_previews")
	_check(previews.get("model") == graph_model and previews.get("image") == graph_image, "unchanged snapshot reuses graph preview instances")
	_check(str(graph_image.call("debug_state").get("artifact_path", "")).ends_with("image_b.png"), "refresh preserves multiview image choice")
	_check((inspector.call("debug_state").get("camera_position") as Vector3).is_equal_approx(inspector_camera), "inspector orbit survives polling refresh")
	_check((graph_model.call("debug_state").get("camera_position") as Vector3).is_equal_approx(before_refresh.get("camera_position")), "poll refresh preserves model orbit")
	model_node["versions"].append(_version("model_v2", "model_b.glb", "model"))
	model_node["current_version"] = "model_v2"
	snapshot["revision"] = 2
	snapshot["nodes"] = [image_node, model_node]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames(5)
	previews = panel.get("_graph_previews")
	_check(previews.get("image") == graph_image, "new model version leaves unrelated image preview intact")
	var changed: Dictionary = (previews["model"] as Control).call("debug_state")
	_check(str(changed.get("artifact_path", "")).ends_with("model_b.glb"), "current version changes visible graph model")
	_check(int(changed.get("mesh_count", 0)) > 0, "replacement graph version has real meshes")
	model_node["current_version"] = "model_v1"
	snapshot["nodes"] = [image_node, model_node]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames(5)
	previews = panel.get("_graph_previews")
	_check(str((previews["model"] as Control).call("debug_state").get("artifact_path", "")).ends_with("model_a.glb"), "switching old version updates graph preview")
	snapshot["nodes"] = [image_node]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames()
	previews = panel.get("_graph_previews")
	_check(not previews.has("model") and previews.has("image"), "removed graph node drops its preview")
	panel.queue_free()
	await _frames()

func _dependency_entry(entries: Array, node_id: String, role: String = "") -> Dictionary:
	for entry: Dictionary in entries:
		if str(entry.get("node_id", "")) == node_id and (role.is_empty() or str(entry.get("role", "")) == role):
			return entry
	return {}

func _entry_file_path(entry: Dictionary, index: int = 0) -> String:
	var files: Array = entry.get("files", [])
	if index < 0 or index >= files.size():
		return ""
	return str(files[index].get("path", ""))

func _show_dependency_mode(panel: Control, historical: bool) -> Array:
	var mode: OptionButton = panel.get("_dependency_mode")
	mode.select(1 if historical else 0)
	mode.item_selected.emit(mode.selected)
	panel.call("_update_dependencies")
	var nodes: Dictionary = panel.get("_nodes")
	return panel.call("_dependency_entries", nodes.get(str(panel.get("_selected_id")), {}), historical)

func _test_dependencies() -> void:
	var panel_script: Script = load("res://addons/asset_pipeline/panel.gd") as Script
	var panel: Control = panel_script.new()
	panel.size = Vector2(1400, 900)
	_canvas.add_child(panel)
	await _frames()
	var front_old: Dictionary = _version("front_old", "image_a.png", "front")
	var front_new: Dictionary = _version("front_new", "image_b.png", "front")
	var right_version: Dictionary = _version("right_v1", "image_c.png", "right")
	var back_version: Dictionary = _version("back_v1", "image_b.png", "back")
	var front: Dictionary = _node("dep_front", "view", "front_new", [front_old, front_new], [20, 10])
	front["label"] = "正面参考"
	var right: Dictionary = _node("dep_right", "view", "right_v1", [right_version], [20, 220])
	right["label"] = "右侧参考"
	var back: Dictionary = _node("dep_back", "view", "back_v1", [back_version], [20, 430])
	back["label"] = "背面参考"
	var output: Dictionary = _version("consumer_v1", "model_b.glb", "model")
	output["input_versions"] = {"dep_front": "front_old", "dep_right": "right_v1", "dep_back": "back_v1"}
	output["inputs"] = [
		{"node_id": "dep_front", "role": "front", "version_id": "front_old", "files": front_old["files"].duplicate(true)},
		{"node_id": "dep_right", "role": "right", "version_id": "right_v1", "files": right_version["files"].duplicate(true)},
		{"node_id": "dep_back", "role": "back", "version_id": "back_v1", "files": back_version["files"].duplicate(true)}
	]
	var consumer: Dictionary = _node("consumer", "model", "consumer_v1", [output], [400, 220])
	consumer["label"] = "报刊亭 · 三视图建模"
	consumer["inputs"] = [{"node_id": "dep_front", "role": "front"}, {"node_id": "dep_right", "role": "right"}, {"node_id": "dep_back", "role": "back"}]
	var snapshot: Dictionary = {"revision": 1, "nodes": [front, right, back, consumer], "jobs": []}
	panel.set("_selected_id", "consumer")
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames(5)
	var component: Control = panel.get("_dependency_assets")
	if not _check(component != null, "inspector has dependency asset component"):
		panel.queue_free()
		return
	var current: Array = _show_dependency_mode(panel, false)
	_check(current.size() == 3, "current mode lists three current input edges")
	var current_front: Dictionary = _dependency_entry(current, "dep_front", "front")
	_check(current_front.get("version_id") == "front_new" and _entry_file_path(current_front) == "image_b.png", "current mode shows latest upstream version")
	var historical: Array = _show_dependency_mode(panel, true)
	_check(historical.size() == 3, "historical mode lists all captured reference edges")
	var used_front: Dictionary = _dependency_entry(historical, "dep_front", "front")
	_check(used_front.get("version_id") == "front_old" and _entry_file_path(used_front) == "image_a.png", "historical mode keeps actual old input artifact")
	_check(used_front.get("current_version_id") == "front_new", "historical entry records differing current upstream version")
	_check(bool(used_front.get("warning", false)) and not str(used_front.get("status_text", "")).is_empty(), "historical mismatch has a visible status")
	var dependency_previews: Dictionary = component.get("_previews")
	_check(dependency_previews.size() == 3, "three references have real inline preview controls")
	for preview: Control in dependency_previews.values():
		_check(preview.call("debug_state").get("type") == "image", "dependency reference uses actual decoded image")
	await _frames(5)
	if DisplayServer.get_name() != "headless" and not _output_dir.is_empty():
		await RenderingServer.frame_post_draw
		var framebuffer: Image = root.get_texture().get_image()
		_check(framebuffer != null and not framebuffer.is_empty() and framebuffer.save_png(_output_dir.path_join("dependencies.png")) == OK, "dependency inspector framebuffer saved")
	# Uncommitted prompt text must survive a dependency navigation request.
	var prompt: TextEdit = panel.get("_prompt")
	prompt.text = "My unsaved prompt survives navigation"
	panel.set("_dirty", true)
	panel.call("_snapshot_received", snapshot.duplicate(true))
	_check(prompt.text == "My unsaved prompt survives navigation" and panel.get("_dirty"), "dependency polling refresh preserves unsaved prompt")
	component.call("_locate_asset", str(used_front.get("key", "")))
	_check(panel.get("_selected_id") == "consumer", "dependency navigation blocks while prompt has unsaved edits")
	_check(prompt.text == "My unsaved prompt survives navigation" and panel.get("_dirty"), "blocked navigation preserves unsaved text and dirty state")
	panel.set("_dirty", false)
	component.call("_locate_asset", str(used_front.get("key", "")))
	_check(panel.get("_selected_id") == "dep_front", "dependency link navigates to upstream node")
	var graph_cards: Dictionary = panel.get("_graph_cards")
	_check((graph_cards["dep_front"] as GraphNode).selected, "dependency navigation updates graph selection")
	panel.call("_open_dependency", "consumer")
	# A removed/replaced current edge must not rewrite captured historical edges.
	var alternate: Dictionary = _node("dep_alternate", "view", "alt_v1", [_version("alt_v1", "image_b.png", "right")], [20, 650])
	consumer["inputs"] = [{"node_id": "dep_front", "role": "front"}, {"node_id": "dep_alternate", "role": "right"}]
	snapshot["nodes"] = [front, right, back, alternate, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	current = _show_dependency_mode(panel, false)
	historical = _show_dependency_mode(panel, true)
	_check(current.size() == 2 and not _dependency_entry(current, "dep_alternate").is_empty(), "current mode reflects changed and removed edges")
	_check(historical.size() == 3 and not _dependency_entry(historical, "dep_right").is_empty() and _dependency_entry(historical, "dep_alternate").is_empty(), "historical mode retains old edge set after graph edits")
	consumer["inputs"] = [{"node_id": "dep_front", "role": "back"}]
	var changed_role: Array = panel.call("_dependency_entries", consumer, false)
	_check(_dependency_entry(changed_role, "dep_front", "back").get("used_version_id", "") == "", "changed role does not claim use by an older generation")
	# Missing upstream nodes and not-yet-generated inputs must remain explicit.
	var empty: Dictionary = _node("dep_empty", "view", "", [], [20, 10])
	consumer["inputs"] = [{"node_id": "dep_deleted", "role": "front"}, {"node_id": "dep_empty", "role": "back"}]
	snapshot["nodes"] = [front, right, back, empty, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	current = _show_dependency_mode(panel, false)
	_check(current.size() == 2, "missing inputs remain visible in dependency list")
	_check(bool(_dependency_entry(current, "dep_deleted").get("missing", false)), "deleted upstream is marked missing")
	_check(bool(_dependency_entry(current, "dep_empty").get("warning", false)) and _dependency_entry(current, "dep_empty").get("files", []).is_empty(), "ungenerated upstream is warned without fabricated files")
	# A captured file survives even when the old source node was removed.
	snapshot["nodes"] = [right, back, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	historical = _show_dependency_mode(panel, true)
	_check(_entry_file_path(_dependency_entry(historical, "dep_front")) == "image_a.png", "captured historical file survives deleted upstream node")
	# Legacy provenance stores only input_versions. Resolve those exact versions.
	var legacy: Dictionary = _version("legacy", "model_a.glb", "model")
	legacy["input_versions"] = {"dep_front": "front_old"}
	consumer["versions"].append(legacy)
	consumer["current_version"] = "legacy"
	consumer["inputs"] = [{"node_id": "dep_front", "role": "front"}, {"node_id": "dep_right", "role": "right"}]
	snapshot["nodes"] = [front, right, back, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	historical = _show_dependency_mode(panel, true)
	_check(historical.size() == 1 and _entry_file_path(_dependency_entry(historical, "dep_front")) == "image_a.png", "legacy version map resolves exact old version only")
	legacy["input_versions"] = {"dep_front": "unknown_old_version"}
	consumer["versions"][-1] = legacy
	snapshot["nodes"] = [front, right, back, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	historical = _show_dependency_mode(panel, true)
	var unavailable: Dictionary = _dependency_entry(historical, "dep_front")
	_check(bool(unavailable.get("warning", false)) and unavailable.get("files", []).is_empty(), "missing pinned version never substitutes current upstream file")
	# Explicitly empty original inputs are a valid root generation, even if the
	# node has since acquired new dependencies.
	legacy["inputs"] = []
	legacy["input_versions"] = {}
	consumer["versions"][-1] = legacy
	snapshot["nodes"] = [front, right, back, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	_check(_show_dependency_mode(panel, true).is_empty(), "explicit empty historical input set stays empty")
	_check((panel.call("_dependency_entries", front, false) as Array).is_empty(), "root node has empty current dependencies")
	# Generic dependency lists can show a model (e.g. a texture stage), and polling
	# the same entry must preserve its 3D camera and selected file.
	var mesh: Dictionary = _node("dep_mesh", "model", "mesh_v1", [_version("mesh_v1", "model_a.glb", "model")], [20, 10])
	mesh["versions"][0]["files"].append({"path": "image_a.png", "role": "preview"})
	consumer["inputs"] = [{"node_id": "dep_mesh", "role": "source_model"}]
	snapshot["nodes"] = [mesh, consumer]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	current = _show_dependency_mode(panel, false)
	await _frames(5)
	var mesh_entry: Dictionary = _dependency_entry(current, "dep_mesh")
	_check(mesh_entry.get("files", []).size() == 2, "dependency preserves multiple output roles")
	dependency_previews = component.get("_previews")
	var mesh_preview: Control = dependency_previews.get(str(mesh_entry.get("key", "")))
	if _check(mesh_preview != null, "model dependency owns reusable preview"):
		_check(int(mesh_preview.call("debug_state").get("mesh_count", 0)) > 0, "model dependency loads real mesh")
		var key: String = str(mesh_entry.get("key", ""))
		component.call("_file_selected", 1, key)
		var selected_entry: Dictionary = _dependency_entry(component.call("debug_entries"), "dep_mesh")
		_check(int(selected_entry.get("file_count", 0)) == 2 and str(selected_entry.get("selected_path", "")).ends_with("image_a.png"), "dependency file selector shows alternate output with preserved role")
		_check(mesh_preview.call("debug_state").get("type") == "image", "dependency alternate output decodes real preview image")
		component.call("_file_selected", 0, key)
		await _frames(3)
		mesh_preview.call("orbit", Vector2(55, 12))
		mesh_preview.call("zoom", 0.8)
		var retained: Vector3 = mesh_preview.call("debug_state").get("camera_position")
		panel.call("_snapshot_received", snapshot.duplicate(true))
		await _frames()
		dependency_previews = component.get("_previews")
		_check(dependency_previews.get(str(mesh_entry.get("key", ""))) == mesh_preview, "dependency refresh reuses model preview")
		_check((mesh_preview.call("debug_state").get("camera_position") as Vector3).is_equal_approx(retained), "dependency model orbit survives refresh")
	snapshot["nodes"] = [mesh]
	panel.call("_snapshot_received", snapshot.duplicate(true))
	await _frames()
	_check(panel.get("_selected_id") == "", "disappearing selected node clears inspector selection")
	_check((component.call("debug_entries") as Array).is_empty(), "disappearing selected node clears dependency entries")
	_check((component.get("_previews") as Dictionary).is_empty(), "disappearing selected node releases dependency previews")
	panel.queue_free()
	await _frames()
