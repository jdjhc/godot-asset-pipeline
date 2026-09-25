@tool
extends Control
## A self-contained, on-demand image/model preview for editor cards/inspectors.
## Native scene scripts are never executed by this component.

signal interacted
signal file_context_requested(path: String)
signal video_frames_requested(path: String)
signal video_thumbnail_requested(path: String)
var _video_controls: VBoxContainer
const VIDEO_EXTENSIONS := ["mp4", "mov", "mkv", "webm", "avi", "ogv"]

const IMAGE_EXTENSIONS: Array[String] = ["png", "jpg", "jpeg", "webp", "bmp"]
const MODEL_EXTENSIONS: Array[String] = ["glb", "gltf", "fbx", "tscn", "scn"]
const MAX_IMAGE_SIDE: int = 1024
const MAX_CACHED_IMAGES: int = 12
const MAX_SCENE_NODES: int = 20000
const INITIAL_YAW: float = 0.58
const INITIAL_PITCH: float = 0.30
static var _image_cache: Dictionary = {}

var _artifact_path: String = ""
var _state: String = "empty"
var _type: String = "empty"
var _message: String = "尚无产物"
var _built: bool = false
var _pending_artifact: String = ""
var _pending_retry: bool = false
var _last_retry_ms: int = -10000
var _retry_count := 0
var _image_view: TextureRect
var _model_view: TextureRect
var _message_label: Label
var _hint: Label
var _viewport: SubViewport
var _stage: Node3D
var _camera: Camera3D
var _model_root: Node3D
var _mesh_count: int = 0
var _target: Vector3 = Vector3.ZERO
var _radius: float = 1.0
var _distance: float = 3.0
var _fit_distance: float = 3.0
var _yaw: float = INITIAL_YAW
var _pitch: float = INITIAL_PITCH
var _zoom_ratio: float = 1.0
var _dragging: bool = false
var _render_requests: int = 0

func _ready() -> void:
	mouse_filter = Control.MOUSE_FILTER_STOP
	clip_contents = true
	var background := ColorRect.new()
	background.color = Color("171c23")
	background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_full_rect(background)
	_image_view = TextureRect.new()
	_image_view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_image_view.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_image_view.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_full_rect(_image_view)
	_model_view = TextureRect.new()
	_model_view.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_model_view.stretch_mode = TextureRect.STRETCH_SCALE
	_model_view.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_full_rect(_model_view)
	_viewport = SubViewport.new()
	_viewport.name = "AssetPreviewViewport"
	_viewport.world_3d = World3D.new()
	_viewport.gui_disable_input = true
	_viewport.handle_input_locally = false
	_viewport.transparent_bg = false
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_viewport.render_target_clear_mode = SubViewport.CLEAR_MODE_ALWAYS
	_viewport.msaa_3d = Viewport.MSAA_2X
	add_child(_viewport)
	_model_view.texture = _viewport.get_texture()
	_stage = Node3D.new()
	_stage.name = "PreviewWorld"
	_viewport.add_child(_stage)
	var environment_node := WorldEnvironment.new()
	var environment := Environment.new()
	environment.background_mode = Environment.BG_COLOR
	environment.background_color = Color("171c23")
	environment.ambient_light_source = Environment.AMBIENT_SOURCE_COLOR
	environment.ambient_light_color = Color("d6deed")
	environment.ambient_light_energy = 0.75
	environment.tonemap_mode = Environment.TONE_MAPPER_FILMIC
	environment_node.environment = environment
	_stage.add_child(environment_node)
	var key := DirectionalLight3D.new()
	key.rotation_degrees = Vector3(-38, -32, 0)
	key.light_energy = 1.4
	key.shadow_enabled = false
	_stage.add_child(key)
	var fill := DirectionalLight3D.new()
	fill.rotation_degrees = Vector3(-18, 140, 0)
	fill.light_color = Color("b6cee8")
	fill.light_energy = 0.65
	fill.shadow_enabled = false
	_stage.add_child(fill)
	_camera = Camera3D.new()
	_camera.fov = 42.0
	_camera.keep_aspect = Camera3D.KEEP_HEIGHT
	_camera.current = true
	_stage.add_child(_camera)
	_message_label = Label.new()
	_message_label.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_message_label.vertical_alignment = VERTICAL_ALIGNMENT_CENTER
	_message_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_message_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_message_label.modulate = Color("aab5c1")
	_full_rect(_message_label)
	_hint = Label.new()
	_hint.text = "拖动旋转 · 滚轮缩放 · 双击复位"
	_hint.horizontal_alignment = HORIZONTAL_ALIGNMENT_CENTER
	_hint.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_hint.modulate = Color("bec8d3")
	add_child(_hint)
	_hint.set_anchors_and_offsets_preset(Control.PRESET_BOTTOM_WIDE)
	_hint.offset_top = -_hint.get_theme_font_size("font_size") - 10
	_hint.offset_bottom = -4
	_video_controls = VBoxContainer.new()
	_video_controls.set_anchors_and_offsets_preset(Control.PRESET_CENTER)
	_video_controls.position = Vector2(-90, -35)
	_video_controls.size = Vector2(180, 80)
	add_child(_video_controls)
	var play := Button.new()
	play.text = "▶ 播放视频"
	play.tooltip_text = "使用系统播放器预览，可播放、暂停和拖动进度"
	play.pressed.connect(func():
		interacted.emit()
		var error := OS.shell_open(ProjectSettings.globalize_path(_artifact_path))
		if error != OK: _fail("无法打开系统视频播放器：" + error_string(error)))
	_video_controls.add_child(play)
	var frames := Button.new()
	frames.text = "视频选帧"
	frames.pressed.connect(func():
		interacted.emit()
		video_frames_requested.emit(_artifact_path))
	_video_controls.add_child(frames)
	_video_controls.hide()
	_built = true
	resized.connect(_resized)
	visibility_changed.connect(_visibility_changed)
	if Engine.is_editor_hint():
		var filesystem: EditorFileSystem = EditorInterface.get_resource_filesystem()
		if filesystem != null:
			filesystem.filesystem_changed.connect(_filesystem_changed)
	_resized()
	if not _pending_artifact.is_empty():
		var requested: String = _pending_artifact
		_pending_artifact = ""
		set_artifact(requested)
	else:
		clear_preview(_message)

func _full_rect(control: Control) -> void:
	add_child(control)
	control.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)

func set_artifact(path: String) -> void:
	if not _built:
		_pending_artifact = path
		return
	var normalized: String = _normalize_path(path)
	if normalized.is_empty():
		clear_preview("尚无产物" if path.strip_edges().is_empty() else "仅支持当前项目中的产物")
		return
	if normalized == _artifact_path and _state in ["ready", "loading"]:
		return # Reconciliation must never reset a user's orbit/zoom.
	_clear_model()
	if is_instance_valid(_video_controls): _video_controls.hide()
	_image_view.texture = null
	if _artifact_path != normalized: _retry_count = 0
	_artifact_path = normalized
	_state = "loading"
	_type = "empty"
	_show_message("正在载入预览…")
	if not FileAccess.file_exists(normalized):
		_fail("产物文件不存在\n请检查版本或刷新资产列表")
		return
	var extension: String = normalized.get_extension().to_lower()
	if extension in VIDEO_EXTENSIONS:
		_type = "video"
		_state = "ready"
		_show_message("")
		_video_controls.show()
		video_thumbnail_requested.emit(normalized)
	elif extension in IMAGE_EXTENSIONS:
		_type = "image"
		_load_image(normalized)
	elif extension in MODEL_EXTENSIONS:
		_type = "model"
		_load_model(normalized)
	else:
		_type = "unsupported"
		_fail("此文件类型不支持内嵌预览")

func clear_preview(message: String = "尚无产物") -> void:
	_pending_artifact = ""
	_artifact_path = ""
	_state = "empty"
	_type = "empty"
	_message = message
	if not _built:
		return
	_clear_model()
	if is_instance_valid(_video_controls): _video_controls.hide()
	_image_view.texture = null
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED
	_show_message(message)

func reset_view() -> void:
	_yaw = INITIAL_YAW
	_pitch = INITIAL_PITCH
	_zoom_ratio = 1.0
	if _built and _type == "model" and _state == "ready":
		_fit_camera()

func orbit(delta: Vector2) -> void:
	if not _built or _type != "model" or _state != "ready" or not delta.is_finite():
		return
	_yaw = wrapf(_yaw - delta.x * 0.008, -PI, PI)
	_pitch = clampf(_pitch + delta.y * 0.008, -1.38, 1.38)
	_update_camera()

func zoom(factor: float) -> void:
	if not _built or _type != "model" or _state != "ready" or not is_finite(factor) or factor <= 0.0:
		return
	_zoom_ratio = clampf(_zoom_ratio * factor, 0.15, 12.0)
	_update_camera()

func debug_state() -> Dictionary:
	var image_size: Vector2 = _image_view.texture.get_size() if _built and _image_view.texture != null else Vector2.ZERO
	return {"artifact_path": _artifact_path, "state": _state, "type": _type, "message": _message, "mesh_count": _mesh_count, "camera_position": _camera.position if is_instance_valid(_camera) else Vector3.ZERO, "distance": _distance, "target": _target, "yaw": _yaw, "pitch": _pitch, "zoom_ratio": _zoom_ratio, "image_size": image_size, "model_instance_id": str(_model_root.get_instance_id()) if is_instance_valid(_model_root) else "", "viewport_rid": _viewport.get_viewport_rid() if is_instance_valid(_viewport) else RID(), "world_rid": _viewport.world_3d.scenario if is_instance_valid(_viewport) else RID(), "render_update_mode": _viewport.render_target_update_mode if is_instance_valid(_viewport) else SubViewport.UPDATE_DISABLED, "render_requests": _render_requests}

func _normalize_path(path: String) -> String:
	var value: String = path.strip_edges().replace("\\", "/")
	if value.is_empty():
		return ""
	if not value.begins_with("res://"):
		if value.is_absolute_path() or value.contains("://"):
			return ""
		value = "res://" + value.trim_prefix("./")
	value = value.simplify_path()
	if not value.begins_with("res://") or ".." in value.trim_prefix("res://").split("/"):
		return ""
	return value

func _load_image(path: String) -> void:
	var cache_key: String = ProjectSettings.globalize_path(path)
	var modified: int = FileAccess.get_modified_time(path)
	var texture: Texture2D
	if _image_cache.has(cache_key) and _image_cache[cache_key].modified == modified:
		texture = _image_cache[cache_key].texture
	else:
		var picture := Image.new()
		if picture.load(cache_key) != OK or picture.is_empty():
			_fail("图片无法读取或格式无效")
			return
		var longest: int = maxi(picture.get_width(), picture.get_height())
		if longest > MAX_IMAGE_SIDE:
			var ratio: float = float(MAX_IMAGE_SIDE) / longest
			picture.resize(maxi(1, roundi(picture.get_width() * ratio)), maxi(1, roundi(picture.get_height() * ratio)), Image.INTERPOLATE_LANCZOS)
		texture = ImageTexture.create_from_image(picture)
		if _image_cache.size() >= MAX_CACHED_IMAGES:
			_image_cache.erase(_image_cache.keys()[0])
		_image_cache[cache_key] = {"modified": modified, "texture": texture}
	_image_view.texture = texture
	_state = "ready"
	_message = ""
	_image_view.visible = true
	_model_view.visible = false
	_message_label.visible = false
	_hint.visible = false
	_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED

func _load_model(path: String) -> void:
	var instance: Node = null
	var extension: String = path.get_extension().to_lower()
	# Raw glTF fallback also works while Godot is still importing a new output.
	if extension in ["glb", "gltf"] and not FileAccess.file_exists(path + ".import"):
		instance = _raw_gltf(path)
	else:
		var packed: PackedScene = ResourceLoader.load(path, "PackedScene") as PackedScene if ResourceLoader.exists(path, "PackedScene") else null
		if packed != null:
			if not _scene_is_scriptless(packed):
				_fail("此场景包含脚本，预览不会执行脚本\n请使用导出的 GLB / FBX 模型")
				return
			instance = packed.instantiate(PackedScene.GEN_EDIT_STATE_DISABLED)
		elif extension in ["glb", "gltf"]:
			instance = _raw_gltf(path)
	if not instance is Node3D:
		if instance != null:
			instance.free()
		_state = "waiting_import"
		_show_message("模型尚未就绪\nGodot 导入完成后会自动重试")
		return
	_model_root = instance
	_sanitize_model(_model_root)
	_stage.add_child(_model_root)
	_mesh_count = 0
	var has_bounds: bool = false
	var bounds := AABB()
	var pending: Array[Node] = [_model_root]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		if node is MeshInstance3D and node.mesh != null:
			_mesh_count += 1
			var current: AABB = node.global_transform * node.get_aabb()
			if current.position.is_finite() and current.size.is_finite():
				bounds = bounds.merge(current) if has_bounds else current
				has_bounds = true
		pending.append_array(node.get_children())
	if not has_bounds or _mesh_count == 0:
		_clear_model()
		_fail("模型中没有可预览的网格")
		return
	_target = bounds.get_center()
	_radius = maxf(bounds.size.length() * 0.5, 0.01)
	if not _target.is_finite() or not is_finite(_radius):
		_clear_model()
		_fail("模型范围无效，无法适配预览相机")
		return
	_state = "ready"
	_message = ""
	_image_view.visible = false
	_model_view.visible = true
	_message_label.visible = false
	_hint.visible = true
	_camera.make_current()
	reset_view()

func _raw_gltf(path: String) -> Node:
	var document := GLTFDocument.new()
	var data := GLTFState.new()
	# Previewing must not extract/reimport textures beside the production GLB.
	# The editor's default extraction mode races its normal filesystem import.
	data.set_handle_binary_image(GLTFState.HANDLE_BINARY_EMBED_AS_UNCOMPRESSED)
	var error: Error = document.append_from_file(ProjectSettings.globalize_path(path), data)
	if error != OK:
		return null
	return document.generate_scene(data)

func _scene_is_scriptless(packed: PackedScene) -> bool:
	var pending: Array[SceneState] = [packed.get_state()]
	var visited: Dictionary = {}
	var remaining: int = MAX_SCENE_NODES
	while not pending.is_empty():
		var state: SceneState = pending.pop_back()
		var id: int = state.get_instance_id()
		if visited.has(id):
			continue
		visited[id] = true
		remaining -= state.get_node_count()
		if remaining < 0:
			return false
		var base: SceneState = state.get_base_scene_state()
		if base != null:
			pending.append(base)
		for index: int in range(state.get_node_count()):
			for property_index: int in range(state.get_node_property_count(index)):
				if state.get_node_property_name(index, property_index) == "script" and state.get_node_property_value(index, property_index) != null:
					return false
			var nested: PackedScene = state.get_node_instance(index)
			if nested != null:
				pending.append(nested.get_state())
	return true

func _sanitize_model(root: Node3D) -> void:
	# Do this before adding the model to the preview world. Previewing never
	# starts animation, sound, collision, physics or project process callbacks.
	var pending: Array[Node] = [root]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		node.process_mode = Node.PROCESS_MODE_DISABLED
		if node.get_script() != null:
			node.set_script(null)
		if node is AnimationPlayer:
			node.autoplay = ""
			node.stop()
		if node is AnimationMixer:
			node.active = false
		if node is AudioStreamPlayer or node is AudioStreamPlayer2D or node is AudioStreamPlayer3D:
			node.autoplay = false
			node.stop()
		if node is Camera3D:
			node.current = false
		if node is CollisionObject3D:
			node.collision_layer = 0
			node.collision_mask = 0
		if node is RigidBody3D:
			node.freeze = true
		if node is GPUParticles3D or node is CPUParticles3D:
			node.emitting = false
		pending.append_array(node.get_children())

func _clear_model() -> void:
	_dragging = false
	_mesh_count = 0
	_target = Vector3.ZERO
	if is_instance_valid(_model_root):
		_model_root.free()
	_model_root = null
	if is_instance_valid(_viewport):
		_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED

func _fit_camera() -> void:
	if not _built or _state != "ready" or _type != "model":
		return
	var aspect: float = float(maxi(1, _viewport.size.x)) / maxi(1, _viewport.size.y)
	var vertical_half: float = deg_to_rad(_camera.fov) * 0.5
	var horizontal_half: float = atan(tan(vertical_half) * aspect)
	_fit_distance = _radius / maxf(sin(minf(vertical_half, horizontal_half)), 0.05) * 1.12
	_update_camera()

func _update_camera() -> void:
	_distance = _fit_distance * _zoom_ratio
	var direction := Vector3(sin(_yaw) * cos(_pitch), sin(_pitch), cos(_yaw) * cos(_pitch))
	_camera.position = _target + direction * _distance
	_camera.near = maxf(minf(_radius * 0.005, _distance * 0.05), 0.001)
	_camera.far = maxf(_distance + _radius * 5.0, 20.0)
	_camera.look_at(_target, Vector3.UP)
	_request_render()

func _resized() -> void:
	if not _built:
		return
	var ratio: float = minf(1.0, float(MAX_IMAGE_SIDE) / maxf(maxf(size.x, size.y), 1.0))
	_viewport.size = Vector2i(maxi(2, roundi(size.x * ratio)), maxi(2, roundi(size.y * ratio)))
	_fit_camera()

func _visibility_changed() -> void:
	if not _built:
		return
	if is_visible_in_tree():
		_request_render()
	else:
		_viewport.render_target_update_mode = SubViewport.UPDATE_DISABLED

func _request_render() -> void:
	if _built and _type == "model" and _state == "ready" and is_visible_in_tree():
		_render_requests += 1
		_viewport.render_target_update_mode = SubViewport.UPDATE_ONCE

func _filesystem_changed() -> void:
	if _state in ["waiting_import", "error"] and _type == "model" and not _artifact_path.is_empty() and not _pending_retry:
		_pending_retry = true
		call_deferred("_retry_artifact")

func _retry_artifact() -> void:
	_pending_retry = false
	if _state in ["waiting_import", "error"] and not _artifact_path.is_empty():
		set_artifact(_artifact_path)

func _show_message(message: String) -> void:
	if is_instance_valid(_video_controls): _video_controls.hide()
	_message = message
	_image_view.visible = false
	_model_view.visible = false
	_message_label.text = message
	_message_label.visible = true
	_hint.visible = false

func _fail(message: String) -> void:
	_state = "error"
	_show_message(message)

func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.pressed and event.button_index == MOUSE_BUTTON_RIGHT:
		file_context_requested.emit(_artifact_path)
		accept_event()
		return
	if event is InputEventMouseButton:
		if event.button_index == MOUSE_BUTTON_LEFT:
			if event.pressed:
				interacted.emit()
			if _type == "model" and _state == "ready":
				_dragging = event.pressed and not event.double_click
				if event.pressed and event.double_click:
					reset_view()
				accept_event()
		elif event.pressed and _type == "model" and _state == "ready":
			if event.button_index == MOUSE_BUTTON_WHEEL_UP:
				zoom(0.86)
				accept_event()
			elif event.button_index == MOUSE_BUTTON_WHEEL_DOWN:
				zoom(1.0 / 0.86)
				accept_event()
	elif event is InputEventMouseMotion and _dragging and _type == "model" and _state == "ready":
		orbit(event.relative)
		accept_event()

func retry_if_needed() -> void:
	if _state not in ["waiting_import", "error"] or _artifact_path.is_empty(): return
	if _retry_count >= 3 or Time.get_ticks_msec() - _last_retry_ms < 5000: return
	_retry_count += 1
	_last_retry_ms = Time.get_ticks_msec()
	_filesystem_changed()

func force_refresh() -> void:
	if _artifact_path.is_empty(): return
	var path := _artifact_path
	_image_cache.erase(ProjectSettings.globalize_path(path))
	clear_preview("正在刷新预览…")
	set_artifact(path)

func set_video_thumbnail(source: String, thumbnail_path: String) -> void:
	if _artifact_path != source or _type != "video" or thumbnail_path.is_empty(): return
	var image := Image.load_from_file(ProjectSettings.globalize_path(thumbnail_path))
	if image == null or image.is_empty(): return
	_image_view.texture = ImageTexture.create_from_image(image)
	_image_view.show()
