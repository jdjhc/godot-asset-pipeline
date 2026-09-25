@tool
extends EditorPlugin

const PipelinePanel = preload("panel.gd")
const CONNECTION_PATH: String = "res://.godot/asset_pipeline_connection.json"
const PYTHON_SETTING: String = "asset_pipeline/python_executable"
var _previous_window_mode := Window.MODE_WINDOWED
var _previous_distraction := false
var _workshop_fullscreen := false
var _panel: Control
var _connection_timer: Timer
var _started_at: int = 0
var _started_pid: int = -1
var _placement_busy: bool = false
var _handshake: HTTPRequest
var _handshake_busy: bool = false
var _candidate_connection: Dictionary = {}
var _backend_start_attempted: bool = false

func _enter_tree() -> void:
	add_tool_menu_item("打开 AI 资产工坊", _open_workshop)
	_panel = PipelinePanel.new()
	get_editor_interface().get_editor_main_screen().add_child(_panel)
	_panel.place_requested.connect(_place_asset)
	_panel.fullscreen_requested.connect(_fullscreen_workshop)
	if bool(ProjectSettings.get_setting("asset_pipeline/open_on_startup", false)):
		_open_workshop.call_deferred()
	_panel.reconnect_requested.connect(_reconnect)
	_panel.selected_asset_changed.connect(_selection_changed.unbind(1))
	get_editor_interface().get_selection().selection_changed.connect(_selection_changed)
	_make_visible(false)
	var settings: EditorSettings = get_editor_interface().get_editor_settings()
	if not settings.has_setting(PYTHON_SETTING):
		settings.set_setting(PYTHON_SETTING, "")
		settings.add_property_info({"name": PYTHON_SETTING, "type": TYPE_STRING, "hint": PROPERTY_HINT_GLOBAL_FILE, "hint_string": ""})
	_connection_timer = Timer.new()
	_connection_timer.wait_time = 0.4
	_connection_timer.timeout.connect(_try_connection)
	add_child(_connection_timer)
	_handshake = HTTPRequest.new()
	_handshake.timeout = 3.0
	_handshake.request_completed.connect(_handshake_completed)
	add_child(_handshake)
	_reconnect()

func _exit_tree() -> void:
	if _workshop_fullscreen: _fullscreen_workshop(false)
	remove_tool_menu_item("打开 AI 资产工坊")
	if get_editor_interface().get_selection().selection_changed.is_connected(_selection_changed):
		get_editor_interface().get_selection().selection_changed.disconnect(_selection_changed)
	if is_instance_valid(_connection_timer):
		_connection_timer.stop()
		_connection_timer.queue_free()
	if is_instance_valid(_handshake):
		_handshake.cancel_request()
		_handshake.queue_free()
	if is_instance_valid(_panel):
		_panel.queue_free()
	# The project backend and existing jobs survive a UI reload. Re-enabling
	# connects to the same authenticated local service rather than resubmitting.

func _has_main_screen() -> bool:
	return true

func _open_workshop() -> void:
	get_editor_interface().set_main_screen_editor("资产工坊")

func _get_plugin_name() -> String:
	return "资产工坊"

func _get_plugin_icon() -> Texture2D:
	return get_editor_interface().get_base_control().get_theme_icon("GraphEdit", "EditorIcons")

func _make_visible(visible: bool) -> void:
	if not visible and _workshop_fullscreen and is_instance_valid(_panel):
		_panel._toggle_canvas_fullscreen()
	if is_instance_valid(_panel):
		_panel.visible = visible

func _reconnect() -> void:
	_handshake.cancel_request()
	_handshake_busy = false
	_backend_start_attempted = false
	_started_at = Time.get_ticks_msec()
	_panel.append_log("正在验证本项目的本地服务连接…")
	_connection_timer.start()
	_try_connection()

func _try_connection() -> bool:
	if Time.get_ticks_msec() - _started_at > 20000:
		_connection_timer.stop()
		_handshake.cancel_request()
		_handshake_busy = false
		_panel.startup_error("资产服务 20 秒内未连接。请检查 Python 配置和服务日志，再点击刷新重连。")
		return false
	if _handshake_busy:
		return false
	if FileAccess.file_exists(CONNECTION_PATH):
		var parsed: Variant = JSON.parse_string(FileAccess.get_file_as_string(CONNECTION_PATH))
		if parsed is Dictionary and int(parsed.get("port", 0)) > 0 and int(parsed.get("port", 0)) <= 65535 and not str(parsed.get("token", "")).is_empty():
			_candidate_connection = parsed
			var address: String = "http://127.0.0.1:" + str(int(parsed.port)) + "/rpc"
			var headers := PackedStringArray(["Content-Type: application/json", "Authorization: Bearer " + str(parsed.token)])
			_handshake_busy = true
			var error: Error = _handshake.request(address, headers, HTTPClient.METHOD_POST, JSON.stringify({"method": "ping", "params": {}}))
			if error == OK:
				return true
			_handshake_busy = false
	if not _backend_start_attempted:
		_start_backend()
	return false

func _handshake_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	_handshake_busy = false
	var response: Variant = JSON.parse_string(body.get_string_from_utf8())
	if result == HTTPRequest.RESULT_SUCCESS and response_code == 200 and response is Dictionary and response.get("ok", false):
		if str(response.get("result", {}).get("version", "")) != "0.4.0":
			_connection_timer.stop()
			_panel.startup_error("资产后台版本过旧，请重新运行插件安装程序以同步后台；仅重启 Godot 不会更新后台。")
			return
		_connection_timer.stop()
		_panel.set_connection(_candidate_connection)
		return
	# A separate process may own the healthy backend. Child-process liveness is
	# not a portable health check; a duplicate launcher may also exit normally.
	if not _backend_start_attempted:
		_start_backend()

func _start_backend() -> void:
	_backend_start_attempted = true
	var settings: EditorSettings = get_editor_interface().get_editor_settings()
	var python: String = str(settings.get_setting(PYTHON_SETTING)).strip_edges()
	if python.is_empty():
		var output: Array = []
		var finder: String = "where" if OS.get_name() == "Windows" else "which"
		if OS.execute(finder, PackedStringArray(["python3"]), output, true) == 0 and not output.is_empty():
			python = str(output[0]).strip_edges().split("\n")[0]
		if python.is_empty() and FileAccess.file_exists("/usr/bin/python3"):
			python = "/usr/bin/python3"
	if python.is_empty():
		_panel.startup_error("找不到 Python 3。请在编辑器设置 asset_pipeline/python_executable 中填写可执行文件完整路径，然后重新启用插件。")
		return
	var backend: String = get_script().resource_path.get_base_dir().path_join("backend/server.py")
	if not FileAccess.file_exists(backend):
		_panel.startup_error("资产服务文件缺失：addons/asset_pipeline/backend/server.py。请完整安装此插件。")
		return
	_started_pid = OS.create_process(python, PackedStringArray([ProjectSettings.globalize_path(backend), "--project", ProjectSettings.globalize_path("res://")]))
	if _started_pid <= 0:
		_panel.startup_error("无法启动 Python。请检查编辑器设置中的完整路径与执行权限：" + python)
		return
	_connection_timer.start()
	_panel.append_log("正在启动本项目的资产服务。日志：.godot/asset_pipeline/server.log")

func _selected_wrapper(node_id: String) -> Node3D:
	if node_id.is_empty():
		return null
	var scene: Node = get_editor_interface().get_edited_scene_root()
	if scene == null:
		return null
	for node: Node in get_editor_interface().get_selection().get_selected_nodes():
		if node is Node3D and node != scene and node.owner == scene and scene.is_ancestor_of(node) and str(node.get_meta("pipeline_node_id", "")) == node_id:
			return node
	return null

func _selection_changed() -> void:
	if is_instance_valid(_panel):
		_panel.set_place_mode(_selected_wrapper(_panel.selected_node_id()) != null)

func _place_asset(node_id: String, version_id: String, source: String) -> void:
	if _placement_busy:
		return
	var scene: Node = get_editor_interface().get_edited_scene_root()
	if not scene is Node3D:
		_panel.startup_error("请先打开一个 3D 场景，再放入模型。")
		return
	var path: String = source if source.begins_with("res://") else "res://" + source.trim_prefix("./")
	if not path.begins_with("res://") or path.contains("..") or not FileAccess.file_exists(path):
		_panel.startup_error("模型文件不在当前项目内或已不存在。请刷新版本产物。")
		return
	_placement_busy = true
	var packed: PackedScene = ResourceLoader.load(path, "PackedScene") as PackedScene
	if packed == null:
		get_editor_interface().get_resource_filesystem().scan()
		_panel.startup_error("Godot 尚未完成模型导入，已请求刷新文件系统。导入结束后再次点击放入场景。")
		_placement_busy = false
		return
	var generated: Node = packed.instantiate(PackedScene.GEN_EDIT_STATE_INSTANCE)
	if not generated is Node3D:
		if generated != null:
			generated.free()
		_panel.startup_error("该产物不是可放入 3D 场景的模型。")
		_placement_busy = false
		return
	generated.name = "GeneratedModel"
	generated.set_meta("pipeline_generated", true)
	var wrapper: Node3D = _selected_wrapper(node_id)
	var updating: bool = wrapper != null
	var manager: EditorUndoRedoManager = get_undo_redo()
	manager.create_action("资产工坊：" + ("更新实例" if updating else "放入模型"), UndoRedo.MERGE_DISABLE, scene, false)
	if not updating:
		wrapper = Node3D.new()
		wrapper.name = ("资产_" + _panel.selected_label()).validate_node_name()
		wrapper.set_meta("pipeline_node_id", node_id)
		wrapper.set_meta("pipeline_version_id", version_id)
		wrapper.set_meta("pipeline_source", path)
		wrapper.set_meta("pipeline_instance_id", "instance_" + Crypto.new().generate_random_bytes(16).hex_encode())
		wrapper.add_child(generated)
		generated.owner = wrapper
		manager.add_do_method(scene, "add_child", wrapper, true)
		manager.add_do_property(wrapper, "owner", scene)
		manager.add_do_property(generated, "owner", scene)
		manager.add_undo_method(scene, "remove_child", wrapper)
		manager.add_do_reference(wrapper)
	else:
		var previous: Array = []
		for child: Node in wrapper.get_children():
			if bool(child.get_meta("pipeline_generated", false)):
				previous.append({"node": child, "index": child.get_index()})
				manager.add_do_method(wrapper, "remove_child", child)
				manager.add_undo_reference(child)
		manager.add_do_method(wrapper, "add_child", generated, true)
		manager.add_do_property(generated, "owner", scene)
		if not previous.is_empty():
			manager.add_do_method(wrapper, "move_child", generated, previous[0].index)
		manager.add_undo_method(wrapper, "remove_child", generated)
		for item: Dictionary in previous:
			manager.add_undo_method(wrapper, "add_child", item.node, true)
			manager.add_undo_property(item.node, "owner", scene)
			manager.add_undo_method(wrapper, "move_child", item.node, item.index)
		manager.add_do_reference(generated)
		for key: String in ["pipeline_version_id", "pipeline_source"]:
			manager.add_do_method(wrapper, "set_meta", key, version_id if key == "pipeline_version_id" else path)
			manager.add_undo_method(wrapper, "set_meta", key, wrapper.get_meta(key, ""))
	manager.commit_action()
	get_editor_interface().get_selection().clear()
	get_editor_interface().get_selection().add_node(wrapper)
	get_editor_interface().edit_node(wrapper)
	_panel.append_log("已" + ("更新选中实例" if updating else "放入当前场景") + "。保留实例位置与手动子节点；可使用编辑器撤销。场景尚未自动保存。")
	var record: Dictionary = {"scene": scene.scene_file_path, "node_path": str(scene.get_path_to(wrapper)), "node_id": node_id, "version_id": version_id, "source": path}
	if wrapper.has_meta("pipeline_instance_id"):
		record.id = str(wrapper.get_meta("pipeline_instance_id"))
	_panel.api_request("instance.record", {"instance": record})
	_placement_busy = false

func _fullscreen_workshop(enabled: bool) -> void:
	var editor := get_editor_interface()
	var window := editor.get_base_control().get_window()
	if enabled and not _workshop_fullscreen:
		_previous_window_mode = window.mode
		_previous_distraction = editor.is_distraction_free_mode_enabled()
		window.mode = Window.MODE_FULLSCREEN
		editor.set_distraction_free_mode(true)
	elif not enabled and _workshop_fullscreen:
		window.mode = _previous_window_mode
		editor.set_distraction_free_mode(_previous_distraction)
	_workshop_fullscreen = enabled
