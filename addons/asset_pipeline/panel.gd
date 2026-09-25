@tool
extends Control

signal place_requested(node_id: String, version_id: String, path: String)
signal selected_asset_changed(node_id: String)
signal reconnect_requested()
signal fullscreen_requested(enabled: bool)
var _canvases
var _canvas_fullscreen := false
var _compact_controls: Array[Control] = []
var _focus_sides: Array[Control] = []
var _expand_button: Button
var _tools_button: Button

const AssetPreview = preload("asset_preview.gd")
const DependencyAssets = preload("dependency_assets.gd")
const IMAGE_EXTENSIONS := ["png", "jpg", "jpeg", "webp", "bmp"]
const VIDEO_EXTENSIONS := ["mp4", "mov", "mkv", "webm", "avi", "ogv"]
const MODEL_EXTENSIONS := ["glb", "gltf", "fbx", "tscn", "scn"]
const NODE_KINDS: Array[String] = ["concept", "subject", "view", "multiview", "model", "reference"]
const KIND_LABELS: Array[String] = ["概念图", "主体设定", "单视图", "多视图", "三维模型", "参考资料"]
const STATUS_LABELS: Dictionary = {"idle": "未运行", "ready": "可用", "draft": "待开始", "planned": "待开始", "completed": "已完成", "success": "已完成", "succeeded": "已完成", "imported": "已导入", "running": "运行中", "queued": "排队中", "pending": "等待中", "failed": "失败", "error": "错误", "cancelled": "已取消", "canceled": "已取消", "waiting": "等待中", "blocked": "上游未就绪", "submitting": "正在提交", "submitted": "已提交", "polling": "生成中", "downloading": "下载产物", "paused": "已暂停", "pausing": "正在暂停", "interrupted": "已中断", "submission_uncertain": "提交结果待确认"}
const ACTIVE_STATES: Array[String] = ["queued", "running", "pending", "waiting", "submitted", "polling", "submitting", "downloading", "pausing"]

var _connection: Dictionary = {}
var _snapshot: Dictionary = {"nodes": [], "jobs": []}
var _nodes: Dictionary = {}
var _selected_id: String = ""
var _request_queue: Array = []
var _active_request: Dictionary = {}
var _http: HTTPRequest
var _poll: Timer
var _graph: GraphEdit
var _node_title: Label
var _status: Label
var _project_label: Label
var _project_verified: bool = false
var _auto_reconnect_attempted: bool = false
var _reconnect_pending: bool = false
var _batch_picker: OptionButton
var _batch_summary: Label
var _batch_detail: Label
var _batch_progress: ProgressBar
var _batch_start: Button
var _batch_pause: Button
var _batch_resume: Button
var _batch_focus: Button
var _batches: Dictionary = {}
var _selected_batch_id: String = ""
var _batch_user_selected: bool = false
var _batch_action_pending: bool = false
var _known_batch_ids: Dictionary = {}
var _new_batch_notice: bool = false
var _latest_jobs: Dictionary = {}
var _drafts: Dictionary = {}
var _last_inspector_config: String = ""
var _prompt: TextEdit
var _params: TextEdit
var _versions: OptionButton
var _files: ItemList
var _preview: Control
var _graph_cards: Dictionary = {}
var _graph_previews: Dictionary = {}
var _graph_states: Dictionary = {}
var _graph_details: Dictionary = {}
var _graph_artifact_pickers: Dictionary = {}
var _inspector_artifact: String = ""
var _inspector_version: String = ""
var _inspector_scroll: ScrollContainer
var _last_inspected_id: String = ""
var _dependency_assets: Control
var _dependency_mode: OptionButton
var _dependency_title: Label
var _dependency_hint: Label
var _plan: TextEdit
var _jobs: ItemList
var _log: RichTextLabel
var _kind: OptionButton
var _label: LineEdit
var _save: Button
var _run: Button
var _deferred_subject_snapshot: Dictionary = {}
var _subject_picker: Window
var _manual: RefCounted
var _preview_versions: Dictionary = {}
var _video_picker: Window
var _import: Button
var _place: Button
var _resume: Button
var _dialog: FileDialog
var _confirm_run: ConfirmationDialog
var _display_files: Array = []
var _display_versions: Array = []
var _display_jobs: Array = []
var _rebuilding: bool = false
var _dirty: bool = false
var _pending_selection: String = ""
var _pending_run_id: String = ""
var _placement_updates: bool = false
var _seen_errors: Dictionary = {}
var _ui_scale: float = 1.0
var _initial_graph_fitted: bool = false
var _graph_connection_signature: String = ""
var _pending_positions: Dictionary = {}

func _ready() -> void:
	set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	size_flags_vertical = Control.SIZE_EXPAND_FILL
	_ui_scale = EditorInterface.get_editor_scale() if Engine.is_editor_hint() else 1.0
	_build_theme()
	_build_ui()
	get_window().files_dropped.connect(_native_files_dropped)
	_scale_layout(self)
	_http = HTTPRequest.new()
	_http.timeout = 90.0
	add_child(_http)
	_http.request_completed.connect(_request_completed)
	_poll = Timer.new()
	_poll.wait_time = 2.0
	_poll.timeout.connect(_poll_jobs)
	add_child(_poll)
	_prompt.text_changed.connect(_mark_dirty)
	_params.text_changed.connect(_mark_dirty)
	_set_status("正在连接本项目的资产服务…")

func _build_theme() -> void:
	var palette := Theme.new()
	palette.default_font_size = get_theme_font_size("font_size", "Label")
	for type_name: String in ["Label", "Button", "LineEdit", "TextEdit", "OptionButton", "ItemList", "RichTextLabel"]:
		palette.set_color("font_color", type_name, Color("e0e5e9"))
		palette.set_color("font_disabled_color", type_name, Color("747e88"))
	var background := StyleBoxFlat.new()
	background.bg_color = Color("20252c")
	background.set_corner_radius_all(roundi(8 * _ui_scale))
	background.content_margin_left = 10 * _ui_scale
	background.content_margin_right = 10 * _ui_scale
	background.content_margin_top = 8 * _ui_scale
	background.content_margin_bottom = 8 * _ui_scale
	for type_name: String in ["TextEdit", "LineEdit", "ItemList", "PanelContainer"]:
		palette.set_stylebox("normal" if type_name != "PanelContainer" else "panel", type_name, background)
	var button := background.duplicate() as StyleBoxFlat
	button.bg_color = Color("343d46")
	palette.set_stylebox("normal", "Button", button)
	var hover := button.duplicate() as StyleBoxFlat
	hover.bg_color = Color("46515c")
	palette.set_stylebox("hover", "Button", hover)
	var pressed := button.duplicate() as StyleBoxFlat
	pressed.bg_color = Color("3e6258")
	palette.set_stylebox("pressed", "Button", pressed)
	var card := background.duplicate() as StyleBoxFlat
	card.bg_color = Color("242c33")
	card.border_color = Color("49565f")
	card.set_border_width_all(maxi(1, roundi(_ui_scale)))
	palette.set_stylebox("panel", "GraphNode", card)
	var selected_card := card.duplicate() as StyleBoxFlat
	selected_card.border_color = Color("a1cbbb")
	palette.set_stylebox("panel_selected", "GraphNode", selected_card)
	var card_header := button.duplicate() as StyleBoxFlat
	card_header.bg_color = Color("303b43")
	palette.set_stylebox("titlebar", "GraphNode", card_header)
	var selected_header := card_header.duplicate() as StyleBoxFlat
	selected_header.bg_color = Color("3e6258")
	palette.set_stylebox("titlebar_selected", "GraphNode", selected_header)
	theme = palette

func _build_ui() -> void:
	var margin := MarginContainer.new()
	margin.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	for side: String in ["left", "right", "top", "bottom"]:
		margin.add_theme_constant_override("margin_" + side, 5)
	add_child(margin)
	var main := VBoxContainer.new()
	main.add_theme_constant_override("separation", 4)
	margin.add_child(main)
	var header := HBoxContainer.new()
	main.add_child(header)
	var title := Label.new()
	title.text = "资产工坊"
	title.add_theme_font_size_override("font_size", roundi(18 * _ui_scale))
	title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	header.add_child(title)
	_button(header, "服务状态", func(): api_request("provider.status", {}, _provider_received))
	_button(header, "刷新", _refresh_previews)
	_button(header, "刷新预览", _refresh_previews)
	_button(header, "视频选帧", _open_video_picker)
	_button(header, "框选主体", _open_subject_picker)
	_tools_button = _button(header, "展开工具", _toggle_tools)
	_expand_button = _button(header, "全屏画布", _toggle_canvas_fullscreen)
	_canvases = preload("canvas_controls.gd").new()
	_canvases.setup(self, main)
	_project_label = Label.new()
	_project_label.text = "项目：" + _editor_project()
	_project_label.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_project_label.tooltip_text = _editor_project()
	_project_label.modulate = Color("a5b1bb")
	main.add_child(_project_label)
	_compact_controls.append(_project_label)
	_build_batch_ui(main)
	var toolbar := HBoxContainer.new()
	main.add_child(toolbar)
	_compact_controls.append(toolbar)
	_kind = OptionButton.new()
	for title_text: String in KIND_LABELS:
		_kind.add_item(title_text)
	toolbar.add_child(_kind)
	_label = LineEdit.new()
	_label.placeholder_text = "节点名称（可选）"
	_label.custom_minimum_size.x = 170
	toolbar.add_child(_label)
	_button(toolbar, "新建节点", _create_node)
	_button(toolbar, "报刊亭 · 三视图模板", _create_template)
	_button(toolbar, "查看全链路", _fit_graph)
	var manual_bar := HBoxContainer.new()
	main.add_child(manual_bar)
	_compact_controls.append(manual_bar)
	_manual = preload("manual_controls.gd").new()
	_manual.setup(self, manual_bar)
	var search := LineEdit.new()
	search.placeholder_text = "按名称搜索节点，回车定位"
	search.custom_minimum_size.x = 220
	manual_bar.add_child(search)
	search.text_submitted.connect(func(text):
		for id: String in _nodes:
			if text.to_lower() in str(_nodes[id].label).to_lower():
				_open_dependency(id)
				break)
	var spacer := Control.new()
	spacer.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	toolbar.add_child(spacer)
	var note := Label.new()
	note.text = "批次自动生产 · 独立版本 · 手动放入场景"
	note.modulate = Color("a5b1bb")
	toolbar.add_child(note)
	_status = Label.new()
	_status.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	main.add_child(_status)
	var vertical := VSplitContainer.new()
	vertical.size_flags_vertical = Control.SIZE_EXPAND_FILL
	vertical.split_offset = -120
	main.add_child(vertical)
	var workspace := HSplitContainer.new()
	workspace.size_flags_vertical = Control.SIZE_EXPAND_FILL
	workspace.split_offset = -420
	vertical.add_child(workspace)
	_graph = preload("canvas_graph.gd").new()
	_graph.asset_files_dropped.connect(_import_dropped_files)
	_graph.custom_minimum_size = Vector2(430, 420)
	_graph.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_graph.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_graph.minimap_enabled = true
	_graph.minimap_size = Vector2(160, 100)
	_graph.right_disconnects = true
	_graph.connection_request.connect(_connect_nodes)
	_graph.disconnection_request.connect(_disconnect_nodes)
	_graph.node_selected.connect(_graph_selected)
	_graph.delete_nodes_request.connect(func(_nodes_to_delete): _manual.delete_selected())
	_graph.focus_mode = Control.FOCUS_ALL
	_graph.popup_request.connect(_graph_context_menu)
	workspace.add_child(_graph)
	var inspector_scroll := ScrollContainer.new()
	_inspector_scroll = inspector_scroll
	inspector_scroll.custom_minimum_size.x = 390
	inspector_scroll.horizontal_scroll_mode = ScrollContainer.SCROLL_MODE_DISABLED
	workspace.add_child(inspector_scroll)
	_focus_sides.append(inspector_scroll)
	var inspector := VBoxContainer.new()
	inspector.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	inspector.add_theme_constant_override("separation", 8)
	inspector_scroll.add_child(inspector)
	_node_title = _section(inspector, "选择一个节点")
	var dependencies := VBoxContainer.new()
	dependencies.add_theme_constant_override("separation", 8)
	inspector.add_child(dependencies)
	var dependency_header := HBoxContainer.new()
	dependencies.add_child(dependency_header)
	_dependency_title = _section(dependency_header, "依赖资产")
	_dependency_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_dependency_mode = OptionButton.new()
	_dependency_mode.add_item("下次生成的输入")
	_dependency_mode.add_item("此版本生成时的输入")
	_dependency_mode.item_selected.connect(func(_index): _update_dependencies())
	dependency_header.add_child(_dependency_mode)
	_dependency_hint = Label.new()
	_dependency_hint.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_dependency_hint.modulate = Color("a4adb5")
	dependencies.add_child(_dependency_hint)
	_dependency_assets = DependencyAssets.new()
	dependencies.add_child(_dependency_assets)
	_dependency_assets.configure_scale(_ui_scale)
	_dependency_assets.asset_selected.connect(_open_dependency)
	_dependency_assets.preview_created.connect(func(preview):
		_connect_video_thumbnail(preview)
		preview.video_frames_requested.connect(_open_video_path))
	_dependency_assets.clear_dependencies("选择节点后显示其直接上游资产")
	_section(inspector, "提示词")
	_prompt = TextEdit.new()
	_prompt.custom_minimum_size.y = 140
	_prompt.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_prompt.placeholder_text = "描述主体、风格、视角与约束。"
	inspector.add_child(_prompt)
	_button(inspector, "常用参数表单", func(): _manual.parameters())
	_button(inspector, "当前节点作业管理", func(): _manual.job_actions())
	_section(inspector, "参数 · JSON（高级）")
	_params = TextEdit.new()
	_params.custom_minimum_size.y = 95
	_params.text = "{}"
	inspector.add_child(_params)
	var controls := HBoxContainer.new()
	inspector.add_child(controls)
	_save = _button(controls, "保存设置", _save_node)
	_button(controls, "查看运行计划", _preview_plan)
	_run = _button(inspector, "运行所选节点…", _request_run)
	_run.tooltip_text = "仅运行此节点，不会自动运行上游；按服务配置可能产生费用。失败后不会自动重试。"
	_plan = TextEdit.new()
	_plan.custom_minimum_size.y = 100
	_plan.editable = false
	_plan.wrap_mode = TextEdit.LINE_WRAPPING_BOUNDARY
	_plan.placeholder_text = "运行计划显示依赖、受影响节点和过期状态。"
	inspector.add_child(_plan)
	var artifacts := VBoxContainer.new()
	artifacts.add_theme_constant_override("separation", 8)
	inspector.add_child(artifacts)
	inspector.move_child(artifacts, 2)
	_section(artifacts, "版本与产物")
	_versions = OptionButton.new()
	_versions.item_selected.connect(_version_selected)
	artifacts.add_child(_versions)
	_button(artifacts, "采用正在预览的版本", _adopt_preview_version)
	_button(artifacts, "与当前版本并排比较", _compare_versions)
	_preview = AssetPreview.new()
	_preview.file_context_requested.connect(func(path): _show_file_context(path, _selected_id))
	_preview.video_frames_requested.connect(_open_video_path)
	_connect_video_thumbnail(_preview)
	_preview.custom_minimum_size.y = 240
	artifacts.add_child(_preview)
	_files = ItemList.new()
	_files.custom_minimum_size.y = 85
	_files.item_selected.connect(_file_selected)
	_files.item_activated.connect(_open_file)
	artifacts.add_child(_files)
	var artifact_controls := HBoxContainer.new()
	artifacts.add_child(artifact_controls)
	_import = _button(artifact_controls, "直接导入产物", _open_import)
	_import.tooltip_text = "把本地文件登记为新版本。直接导入，不执行质量检查。"
	_place = _button(artifact_controls, "放入当前场景", _place_selected)
	_place.disabled = true
	var bottom := HSplitContainer.new()
	bottom.custom_minimum_size.y = 90
	vertical.add_child(bottom)
	_focus_sides.append(bottom)
	var job_box := VBoxContainer.new()
	job_box.custom_minimum_size.x = 330
	bottom.add_child(job_box)
	var job_header := HBoxContainer.new()
	job_box.add_child(job_header)
	var job_title := Label.new()
	job_title.text = "作业"
	job_title.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	job_header.add_child(job_title)
	_resume = _button(job_header, "恢复所选作业", _resume_job)
	_resume.tooltip_text = "仅恢复已存在的服务端任务，不自动重新提交付费任务。"
	_jobs = ItemList.new()
	_jobs.size_flags_vertical = Control.SIZE_EXPAND_FILL
	job_box.add_child(_jobs)
	var log_box := VBoxContainer.new()
	log_box.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bottom.add_child(log_box)
	_section(log_box, "消息与错误")
	_log = RichTextLabel.new()
	_log.size_flags_vertical = Control.SIZE_EXPAND_FILL
	_log.selection_enabled = true
	_log.scroll_following = true
	log_box.add_child(_log)
	_dialog = FileDialog.new()
	_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	_dialog.access = FileDialog.ACCESS_FILESYSTEM
	_dialog.title = "直接导入产物 · 不执行质量检查"
	_dialog.file_selected.connect(_import_file)
	add_child(_dialog)
	_confirm_run = ConfirmationDialog.new()
	_confirm_run.title = "运行所选节点"
	_confirm_run.ok_button_text = "确认运行"
	_confirm_run.cancel_button_text = "取消"
	_confirm_run.confirmed.connect(_confirm_selected_run)
	add_child(_confirm_run)
	_set_editor_enabled(false)
	_update_batch_controls()
	for control in _compact_controls:
		control.hide()

func _build_batch_ui(parent: VBoxContainer) -> void:
	var batch_box := VBoxContainer.new()
	batch_box.add_theme_constant_override("separation", 5)
	parent.add_child(batch_box)
	_compact_controls.append(batch_box)
	var row := HBoxContainer.new()
	batch_box.add_child(row)
	var title := Label.new()
	title.text = "生产批次"
	row.add_child(title)
	_batch_picker = OptionButton.new()
	_batch_picker.custom_minimum_size.x = 190
	_batch_picker.fit_to_longest_item = false
	_batch_picker.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	_batch_picker.item_selected.connect(_batch_selected)
	row.add_child(_batch_picker)
	_batch_start = _button(row, "整体开始", func(): _batch_action("run"))
	_batch_start.tooltip_text = "按照已保存的资产清单运行整条依赖链；生成可能产生服务费用。"
	_batch_pause = _button(row, "暂停", func(): _batch_action("pause"))
	_batch_pause.tooltip_text = "暂停批次调度，保留已提交任务及已有产物。"
	_batch_resume = _button(row, "继续", func(): _batch_action("resume"))
	_batch_resume.tooltip_text = "从已保存的批次进度继续，不重复生成已完成资产。"
	_batch_focus = _button(row, "查看批次", _fit_current_batch)
	var progress_row := HBoxContainer.new()
	batch_box.add_child(progress_row)
	_batch_summary = Label.new()
	_batch_summary.text = "暂无批次 · 导入资产清单后显示完整生产链"
	_batch_summary.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	progress_row.add_child(_batch_summary)
	_batch_progress = ProgressBar.new()
	_batch_progress.custom_minimum_size = Vector2(170, 16)
	_batch_progress.show_percentage = false
	progress_row.add_child(_batch_progress)
	_batch_detail = Label.new()
	_batch_detail.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	_batch_detail.modulate = Color("a5b1bb")
	batch_box.add_child(_batch_detail)

func _button(parent: Node, text: String, callback: Callable) -> Button:
	var button := Button.new()
	button.text = text
	button.pressed.connect(callback)
	parent.add_child(button)
	return button

func _section(parent: Node, text: String) -> Label:
	var label := Label.new()
	label.text = text
	label.add_theme_font_size_override("font_size", roundi(15 * _ui_scale))
	parent.add_child(label)
	return label

func set_connection(connection: Dictionary) -> void:
	_project_verified = false
	_connection = connection
	if not _text(connection.get("project", "")).is_empty() and not _verify_project(str(connection.project)):
		return
	_set_status("资产服务已连接 · 正在同步本项目")
	_poll.start()
	refresh()

func _editor_project() -> String:
	return ProjectSettings.globalize_path("res://").replace("\\", "/").simplify_path().trim_suffix("/")

func _verify_project(service_project: String) -> bool:
	var actual: String = service_project.replace("\\", "/").simplify_path().trim_suffix("/")
	var expected: String = _editor_project()
	var same: bool = actual.to_lower() == expected.to_lower() if OS.get_name() == "Windows" else actual == expected
	if not same:
		_mark_disconnected()
		_set_editor_enabled(false)
		_project_label.text = "项目不匹配 · 编辑器：" + expected + " · 服务：" + actual
		_project_label.tooltip_text = _project_label.text
		_project_label.modulate = Color("efa59b")
		_set_status("已停止同步和操作：资产服务属于其他项目。请重新连接当前项目。", true)
		append_log("项目不匹配。编辑器：" + expected + "；服务：" + actual)
		return false
	_project_verified = true
	_project_label.text = "项目：" + expected + " · 服务已核对"
	_project_label.tooltip_text = expected
	_project_label.modulate = Color("a5b1bb")
	return true

func startup_error(message: String) -> void:
	_set_status(message, true)
	append_log(message)

func append_log(message: String) -> void:
	if _log != null:
		_log.append_text(Time.get_time_string_from_system() + "  " + message + "\n")

func _set_status(message: String, error: bool = false) -> void:
	if _status != null:
		_status.text = message
		_status.modulate = Color("efa59b") if error else Color("a8cbbd")

func api_request(method: String, params: Dictionary = {}, callback: Callable = Callable()) -> void:
	if _connection.is_empty():
		_set_status("资产服务未连接。请检查 Python 路径与服务日志后重新启用插件。", true)
		return
	if not _project_verified and method not in ["snapshot", "provider.status", "ping"]:
		_set_status("尚未核对服务所属项目，暂不能修改或开始生产。请刷新连接。", true)
		return
	if method == "snapshot":
		if _active_request.get("method") == "snapshot":
			return
		for request: Dictionary in _request_queue:
			if request.method == "snapshot":
				return
	params = params.duplicate(true)
	if _canvases != null and not params.has("canvas_id"):
		params["canvas_id"] = _canvases.current
	_request_queue.append({"method": method, "params": params, "callback": callback})
	_send_next()

func _send_next() -> void:
	if not _active_request.is_empty() or _request_queue.is_empty():
		return
	_active_request = _request_queue.pop_front()
	var address: String = "http://127.0.0.1:" + str(int(_connection.get("port", 0))) + "/rpc"
	var headers := PackedStringArray(["Content-Type: application/json", "Authorization: Bearer " + str(_connection.get("token", ""))])
	var body: String = JSON.stringify({"method": _active_request.method, "params": _active_request.params})
	var error: Error = _http.request(address, headers, HTTPClient.METHOD_POST, body)
	if error != OK:
		_mark_disconnected(true)
		_fail_request("无法发送请求（" + str(error) + "）。检查资产服务与 Python 配置。")

func _request_completed(result: int, response_code: int, _headers: PackedStringArray, body: PackedByteArray) -> void:
	if result != HTTPRequest.RESULT_SUCCESS:
		_mark_disconnected(true)
		_fail_request("资产服务连接失败（" + str(result) + "）。请查看 .godot/asset_pipeline/server.log。")
		return
	var response: Variant = JSON.parse_string(body.get_string_from_utf8())
	if response_code < 200 or response_code >= 300 or not response is Dictionary or not response.get("ok", false):
		if response_code == 401 or response_code == 403:
			_mark_disconnected(true)
		var error: Variant = response.get("error", "HTTP " + str(response_code)) if response is Dictionary else "响应不是有效 JSON"
		_fail_request(_error_text(error))
		return
	var request: Dictionary = _active_request
	_active_request = {}
	var callback: Callable = request.callback
	if callback.is_valid():
		callback.call(response.get("result"))
	_send_next()

func _error_text(error: Variant) -> String:
	if error is Dictionary:
		return str(error.get("message", error.get("code", JSON.stringify(error))))
	return str(error)

func _fail_request(message: String) -> void:
	if _active_request.get("method", "") == "subject.selection" and _subject_picker != null:
		_subject_picker.request_failed(message)
	if str(_active_request.get("method", "")).begins_with("video.") and _video_picker != null:
		_video_picker.request_failed(message)
	var method: String = str(_active_request.get("method", "请求"))
	_active_request = {}
	_pending_run_id = ""
	if method.begins_with("batch."):
		_batch_action_pending = false
		_update_batch_controls()
	_set_status(message, true)
	append_log(method + "：" + message)
	_send_next()

func refresh() -> void:
	if _connection.is_empty():
		_reconnect_pending = false
		_auto_reconnect_attempted = true
		reconnect_requested.emit()
		return
	api_request("snapshot", {}, _snapshot_received)

func _mark_disconnected(reconnect: bool = false) -> void:
	_connection = {}
	_project_verified = false
	_batch_action_pending = false
	_request_queue.clear() # Never replay uncertain mutations or paid submissions.
	_poll.stop()
	_update_batch_controls()
	if reconnect:
		if not _auto_reconnect_attempted:
			_auto_reconnect_attempted = true
			_reconnect_pending = true
			_request_reconnect.call_deferred()
	else:
		# Project mismatches must remain stopped, including a reconnect already
		# queued by a transport failure in the same frame.
		_reconnect_pending = false
		_auto_reconnect_attempted = true

func _request_reconnect() -> void:
	if not _reconnect_pending:
		return
	_reconnect_pending = false
	if _connection.is_empty():
		# The plugin's existing handshake is bounded to 20 seconds. Only
		# reconnect and read snapshots; never replay the discarded requests.
		reconnect_requested.emit()

func _poll_jobs() -> void:
	if not _connection.is_empty():
		refresh()

func _snapshot_received(value: Variant) -> void:
	if value is Dictionary and is_instance_valid(_subject_picker) and _subject_picker.visible:
		_deferred_subject_snapshot = value
		return
	if not value is Dictionary:
		_set_status("服务快照格式无效。", true)
		return
	if not _text(value.get("project", "")).is_empty() and not _verify_project(str(value.project)):
		return
	# A valid snapshot proves recovery. A connection candidate alone does not:
	# resetting earlier could loop forever on stale authentication data.
	_auto_reconnect_attempted = false
	_reconnect_pending = false
	_snapshot = value
	var previous_nodes := _nodes.duplicate()
	_nodes.clear()
	for node: Dictionary in value.get("nodes", []):
		var id := str(node.id)
		var previous_version := _text(previous_nodes.get(id, {}).get("current_version", ""))
		var pinned := _text(_preview_versions.get(id, ""))
		if pinned.is_empty() or pinned == previous_version:
			_preview_versions.erase(id)
		_nodes[id] = node
	_latest_jobs.clear()
	for job: Dictionary in value.get("jobs", []):
		_latest_jobs[str(job.get("node_id", ""))] = job
	_canvases.sync(value)
	_update_batches()
	if not _pending_selection.is_empty() and _nodes.has(_pending_selection):
		_selected_id = _pending_selection
		_pending_selection = ""
	_rebuild_graph()
	_update_jobs()
	if not _selected_id.is_empty() and _nodes.has(_selected_id):
		if not _dirty:
			_load_inspector()
		else:
			_node_title.text = str(_nodes[_selected_id].get("label", "未命名")) + " · " + _node_state(_nodes[_selected_id])
			_update_versions()
			_update_dependencies()
	else:
		_selected_id = ""
		_set_editor_enabled(false)
		_preview.clear_preview("选择一个节点以预览")
		_update_dependencies()
	var sync_text: String = "%d 个节点 · %d 个作业 · 每 2 秒同步 · %s" % [_nodes.size(), value.get("jobs", []).size(), Time.get_time_string_from_system()]
	if not _drafts.is_empty() or _dirty:
		sync_text += " · 有未保存草稿"
	_set_status(sync_text)
	# External scripts may create a batch while this editor is idle. Do not stop
	# observing just because the previous snapshot contained no active jobs.
	if not _connection.is_empty() and _poll.is_stopped():
		_poll.start()

func _batch_node_ids(batch: Dictionary) -> Array:
	var result: Array = []
	for value: Variant in batch.get("node_ids", []):
		result.append(str(value))
	return result

func _update_batches() -> void:
	_batches.clear()
	var choices: Array = _snapshot.get("batches", []).duplicate()
	choices.sort_custom(func(a: Dictionary, b: Dictionary): return str(a.get("created_at", "")) < str(b.get("created_at", "")))
	var latest_active: String = ""
	for batch: Dictionary in choices:
		var id: String = str(batch.get("id", ""))
		if id.is_empty():
			continue
		_batches[id] = batch
		if not _known_batch_ids.has(id):
			_known_batch_ids[id] = true
			_new_batch_notice = true
		if str(batch.get("status", "")) in ACTIVE_STATES:
			latest_active = id
	if not _batches.has(_selected_batch_id):
		_selected_batch_id = ""
		_batch_user_selected = false
	if not _batch_user_selected and not latest_active.is_empty():
		_selected_batch_id = latest_active
	elif _selected_batch_id.is_empty() and not choices.is_empty():
		_selected_batch_id = str(choices.back().get("id", ""))
	_batch_picker.clear()
	for batch: Dictionary in choices:
		var id: String = str(batch.get("id", ""))
		if id.is_empty():
			continue
		_batch_picker.add_item(str(batch.get("label", batch.get("name", id))))
		var index: int = _batch_picker.item_count - 1
		_batch_picker.set_item_metadata(index, id)
		_batch_picker.set_item_tooltip(index, id)
		if id == _selected_batch_id:
			_batch_picker.select(index)
	_update_batch_display()

func _batch_selected(index: int) -> void:
	if index < 0 or index >= _batch_picker.item_count:
		return
	_selected_batch_id = str(_batch_picker.get_item_metadata(index))
	_batch_user_selected = true
	_update_batch_display()

func _update_batch_display() -> void:
	var batch: Dictionary = _batches.get(_selected_batch_id, {})
	if batch.is_empty():
		_batch_summary.text = "暂无批次 · 导入资产清单后显示完整生产链"
		_batch_detail.text = "连接期间持续同步，外部创建的节点和产物会自动出现。"
		_batch_detail.tooltip_text = _batch_detail.text
		_batch_progress.value = 0
		_update_batch_controls()
		return
	var node_ids: Array = _batch_node_ids(batch)
	var completed: int = 0
	for id: String in node_ids:
		var node: Dictionary = _nodes.get(id, {})
		if not _text(node.get("current_version", "")).is_empty() and not bool(node.get("stale", false)):
			completed += 1
	var total: int = int(batch.get("total_nodes", node_ids.size()))
	completed = int(batch.get("completed_nodes", completed))
	if batch.get("progress") is Dictionary:
		total = int(batch.progress.get("total", total))
		completed = int(batch.progress.get("completed", completed))
	var state: String = str(batch.get("status", "idle"))
	_batch_summary.text = "完成 %d / %d · %s" % [completed, total, str(STATUS_LABELS.get(state, state))]
	_batch_progress.max_value = maxi(total, 1)
	_batch_progress.value = clampi(completed, 0, maxi(total, 0))
	var active_ids: Array = batch.get("active_node_ids", []).duplicate()
	if active_ids.is_empty():
		for id: String in node_ids:
			if str(_latest_jobs.get(id, {}).get("status", "")) in ACTIVE_STATES:
				active_ids.append(id)
	var stages: Array[String] = []
	for id: String in active_ids:
		var node: Dictionary = _nodes.get(id, {})
		stages.append(str(node.get("label", id)) + " · " + _node_state(node))
	var error: String = _text(batch.get("error", batch.get("blocked_reason", "")))
	var detail: String = "当前：" + "；".join(stages) if not stages.is_empty() else "当前无正在生成的阶段"
	if not error.is_empty():
		detail = "错误：" + error
		var key: String = _selected_batch_id + ":" + error
		if not _seen_errors.has(key):
			_seen_errors[key] = true
			append_log("批次：" + error)
	elif _new_batch_notice:
		detail += " · 新批次已同步，点击「查看批次」定位完整链路"
	_batch_detail.text = detail
	_batch_detail.tooltip_text = detail
	_batch_detail.modulate = Color("efa59b") if not error.is_empty() else Color("a5b1bb")
	_update_batch_controls()

func _update_batch_controls() -> void:
	if _batch_start == null:
		return
	var batch: Dictionary = _batches.get(_selected_batch_id, {})
	var state: String = str(batch.get("status", ""))
	var unavailable: bool = batch.is_empty() or _connection.is_empty() or not _project_verified or _batch_action_pending
	_batch_start.disabled = unavailable or state not in ["draft", "planned", "idle", "ready", "created"]
	_batch_pause.disabled = unavailable or state not in ACTIVE_STATES or state == "pausing"
	_batch_resume.disabled = unavailable or state not in ["paused", "interrupted", "failed", "error", "blocked"]
	_batch_focus.disabled = batch.is_empty() or _batch_node_ids(batch).is_empty()
	_batch_picker.disabled = _batches.is_empty() or _batch_action_pending

func _batch_action(action: String) -> void:
	if _batch_action_pending or not _batches.has(_selected_batch_id) or _connection.is_empty() or not _project_verified:
		return
	if action in ["run", "resume"] and not _drafts.is_empty():
		_set_status("请先保存节点草稿，再开始或继续批次。批次只使用已保存的设置。", true)
		return
	var id: String = _selected_batch_id
	_batch_action_pending = true
	_update_batch_controls()
	api_request("batch." + action, {"batch_id": id}, func(_value):
		_batch_action_pending = false
		append_log("批次操作已接受：" + str({"run": "整体开始", "pause": "暂停", "resume": "继续"}.get(action, action)))
		_update_batch_controls()
		refresh())

func _fit_current_batch() -> void:
	var batch: Dictionary = _batches.get(_selected_batch_id, {})
	if batch.is_empty():
		return
	_new_batch_notice = false
	_update_batch_display()
	_fit_graph(_batch_node_ids(batch))

func _rebuild_graph() -> void:
	_rebuilding = true
	# Reconcile by node ID: polling must not recreate GPU resources or reset orbit.
	for id: String in _graph_cards.keys():
		if not _nodes.has(id) or not _card_visible(id):
			var removed: GraphNode = _graph_cards[id]
			_graph.remove_child(removed)
			removed.queue_free()
			_graph_cards.erase(id)
			_graph_previews.erase(id)
			_graph_states.erase(id)
			_graph_details.erase(id)
			_graph_artifact_pickers.erase(id)
			_pending_positions.erase(id)
	for id: String in _nodes:
		var data: Dictionary = _nodes[id]
		if not _card_visible(id):
			continue
		if not _graph_cards.has(id):
			_create_graph_card(id)
		var visual: GraphNode = _graph_cards[id]
		visual.title = ("[回收站] " if data.get("archived", false) else "") + str(data.get("label", "未命名"))
		var position: Array = _canvases.position(id, data.get("position", [80, 80]))
		var next_position: Vector2 = (Vector2(float(position[0]), float(position[1])) if position.size() >= 2 else Vector2(80, 80)) * _ui_scale
		if _pending_positions.has(id):
			next_position = _pending_positions[id]
		if not (visual.selected and Input.is_mouse_button_pressed(MOUSE_BUTTON_LEFT)):
			visual.position_offset = next_position
		# Keep native multiselection when polling snapshots.
		var body: Label = _graph_states[id]
		body.text = _kind_label(str(data.get("kind", "reference"))) + "  ·  " + _node_state(data)
		var job: Dictionary = _latest_jobs.get(id, {})
		var job_state: String = str(job.get("status", ""))
		body.modulate = Color("efa59b") if job_state in ["failed", "error", "submission_uncertain"] else Color("a8cbbd") if job_state in ACTIVE_STATES else Color("e4bd81") if data.get("stale", false) else Color("d1ddd8")
		var detail: Label = _graph_details[id]
		detail.text = "版本 " + _text(data.get("current_version", "—")).left(16) if not _text(data.get("current_version", "")).is_empty() else "尚无产物"
		if not _text(job.get("error", "")).is_empty() and job_state not in ["completed", "success", "succeeded"]:
			detail.text = _text(job.error)
		detail.tooltip_text = detail.text
		_update_graph_preview(id, data)
	var connections: Array = []
	for id: String in _nodes:
		for dependency: Dictionary in _nodes[id].get("inputs", []):
			var source: String = str(dependency.get("node_id", ""))
			if _graph_cards.has(source) and _graph_cards.has(id):
				connections.append([source, id])
	var signature: String = JSON.stringify(connections)
	if signature != _graph_connection_signature:
		_graph_connection_signature = signature
		_graph.clear_connections()
		for connection: Array in connections:
			_graph.connect_node(str(connection[0]), 0, str(connection[1]), 0)
	_rebuilding = false
	if not _initial_graph_fitted and not _graph_cards.is_empty():
		_initial_graph_fitted = true
		if _batches.has(_selected_batch_id):
			_fit_current_batch.call_deferred()
		else:
			_fit_graph.call_deferred()

func _fit_graph(node_ids: Array = []) -> void:
	if _graph_cards.is_empty():
		return
	# Wait for container layout so previews contribute their real card size.
	await get_tree().process_frame
	var bounds := Rect2()
	var first: bool = true
	for card: GraphNode in _graph_cards.values():
		if not node_ids.is_empty() and str(card.name) not in node_ids:
			continue
		var rect := Rect2(card.position_offset, card.size)
		bounds = rect if first else bounds.merge(rect)
		first = false
	if first:
		return
	var available: Vector2 = _graph.size - Vector2(70, 110) * _ui_scale
	if available.x <= 0.0 or available.y <= 0.0:
		return
	_graph.zoom = clampf(minf(available.x / maxf(bounds.size.x, 1.0), available.y / maxf(bounds.size.y, 1.0)), _graph.zoom_min, minf(_graph.zoom_max, 1.0))
	_graph.scroll_offset = bounds.get_center() * _graph.zoom - _graph.size * 0.5 - Vector2(0, 20 * _ui_scale)

func _create_graph_card(id: String) -> void:
	var visual := GraphNode.new()
	visual.name = id
	visual.custom_minimum_size.x = 250 * _ui_scale
	var body := Label.new()
	body.custom_minimum_size = Vector2(230, 24) * _ui_scale
	body.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	visual.add_child(body)
	visual.set_slot(0, true, 0, Color("819ba5"), true, 0, Color("9cc9b6"))
	var preview: Control = AssetPreview.new()
	preview.custom_minimum_size = Vector2(230, 145) * _ui_scale
	visual.add_child(preview)
	preview.interacted.connect(_select_preview_card.bind(id))
	preview.video_frames_requested.connect(_open_video_path)
	_connect_video_thumbnail(preview)
	preview.file_context_requested.connect(func(path): _show_file_context(path, id))
	var picker := OptionButton.new()
	picker.fit_to_longest_item = false
	picker.custom_minimum_size.x = 230 * _ui_scale
	picker.item_selected.connect(_graph_artifact_selected.bind(id))
	visual.add_child(picker)
	var detail := Label.new()
	detail.modulate = Color("a4adb5")
	detail.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	visual.add_child(detail)
	_graph_cards[id] = visual
	_graph_previews[id] = preview
	_graph_states[id] = body
	_graph_details[id] = detail
	_graph_artifact_pickers[id] = picker
	_graph.add_child(visual)
	visual.dragged.connect(_node_dragged.bind(id))

func _current_version(data: Dictionary) -> Dictionary:
	return _version_by_id(data, _text(data.get("current_version", "")))

func _version_by_id(data: Dictionary, version_id: String) -> Dictionary:
	if version_id.is_empty():
		return {}
	for version: Dictionary in data.get("versions", []):
		if str(version.get("id", "")) == version_id:
			return version
	return {}

func _dependency_key(edge: Dictionary) -> String:
	return str(edge.get("node_id", "")) + "|" + str(edge.get("role", "input"))

func _captured_dependencies(version: Dictionary) -> Array:
	var pinned: Dictionary = version.get("input_versions", {})
	# An explicitly empty captured list is authoritative, even after new edges
	# are added to the editable recipe. Never backfill it from current inputs.
	if version.has("inputs") and version.inputs is Array:
		var captured: Array = version.inputs.duplicate(true)
		for edge: Dictionary in captured:
			if not edge.has("version_id"):
				edge["version_id"] = pinned.get(str(edge.get("node_id", "")))
		return captured
	var captured: Array = []
	for source_id: String in pinned:
		captured.append({"node_id": source_id, "role": "input", "version_id": pinned[source_id]})
	return captured

func _dependency_entries(data: Dictionary, historical: bool) -> Array:
	var version: Dictionary = _current_version(data)
	var captured: Array = _captured_dependencies(version)
	var current_edges: Array = data.get("inputs", [])
	var edges: Array = captured if historical else current_edges
	var entries: Array = []
	for edge: Dictionary in edges:
		var source_id: String = str(edge.get("node_id", ""))
		var source: Dictionary = _nodes.get(source_id, {})
		var current_id: String = _text(source.get("current_version", ""))
		var used: Dictionary = edge if historical else {}
		if not historical:
			for previous: Dictionary in captured:
				if _dependency_key(previous) == _dependency_key(edge) or (not version.has("inputs") and str(previous.get("node_id", "")) == source_id):
					used = previous
					break
		var used_id: String = _text(used.get("version_id", ""))
		var shown_id: String = used_id if historical else current_id
		var shown_version: Dictionary = _version_by_id(source, shown_id)
		var files: Array = shown_version.get("files", []).duplicate(true)
		if historical and not shown_id.is_empty() and not edge.get("files", []).is_empty():
			files = edge.files.duplicate(true)
		var status: String = "与本版本使用一致"
		var warning: bool = false
		if source.is_empty():
			status = "上游节点不存在" + (" · 保留历史输入" if historical and not files.is_empty() else "")
			warning = true
		elif shown_id.is_empty():
			status = "未记录输入版本" if historical else "上游尚无产物"
			warning = true
		elif files.is_empty():
			status = "历史产物不可用" if historical else "当前版本没有产物文件"
			warning = true
		elif historical:
			var still_connected: bool = false
			for configured: Dictionary in current_edges:
				if _dependency_key(configured) == _dependency_key(edge) or (not version.has("inputs") and str(configured.get("node_id", "")) == source_id):
					still_connected = true
			if not still_connected:
				status = "已从当前依赖移除"
				warning = true
			elif used_id != current_id:
				status = "上游当前版本已变化 · 此处为历史输入"
				warning = true
			else:
				status = "本版本记录的输入产物"
		elif version.is_empty():
			status = "下次运行将使用此资产"
		elif used.is_empty():
			status = "本版本未使用此依赖" if version.has("inputs") or version.has("input_versions") else "本版本未记录依赖来源"
			warning = true
		elif used_id.is_empty():
			status = "本版本未记录输入版本"
			warning = true
		elif used_id != current_id:
			status = "上游已更新 · 本版本使用旧版本"
			warning = true
		if bool(source.get("stale", false)):
			status += " · 上游产物已过期"
			warning = true
		entries.append({"key": _dependency_key(edge), "node_id": source_id,
			"label": str(source.get("label", source_id)), "role": str(edge.get("role", "input")),
			"version_id": shown_id, "current_version_id": current_id, "used_version_id": used_id,
			"status_text": status, "warning": warning, "files": files, "missing": source.is_empty()})
	return entries

func _update_dependencies() -> void:
	if not _nodes.has(_selected_id):
		_dependency_title.text = "依赖资产"
		_dependency_hint.text = ""
		_dependency_assets.clear_dependencies("选择节点后显示其直接上游资产")
		return
	var historical: bool = _dependency_mode.selected == 1
	var data: Dictionary = _nodes[_selected_id]
	var entries: Array = _dependency_entries(data, historical)
	_dependency_title.text = "依赖资产 · " + str(entries.size())
	_dependency_hint.text = "仅查看此版本生成时使用的输入记录，不改变生成设置。" if historical else "仅查看下次生成所用的上游资产；切换此菜单不会修改依赖。"
	var empty: String = "此节点没有上游依赖"
	if historical:
		empty = "此节点尚无产物，暂无历史输入" if _current_version(data).is_empty() else "此版本未记录上游依赖"
	_dependency_assets.set_dependencies(entries, empty)

func _open_dependency(node_id: String) -> void:
	if _dirty:
		_set_status("当前提示词或参数尚未保存，请先保存后再定位依赖。", true)
		return
	if not _nodes.has(node_id):
		_set_status("该上游节点已不存在。", true)
		return
	if not _graph_cards.has(node_id):
		if _nodes[node_id].get("archived", false):
			_canvases.open_library(true)
			return
		for board_id in _canvases.boards:
			if _canvases.boards[board_id].get("placements", {}).has(node_id):
				_canvases.switch_to(str(board_id))
				break
		if not _graph_cards.has(node_id):
			_canvases.open_library(false)
			_set_status("该资产尚未加入画布，可从共享资源池加入。")
			return
	if _dirty:
		_set_status("当前提示词或参数尚未保存，请先保存后再定位依赖。", true)
		return
	_select_preview_card(node_id)
	var card: GraphNode = _graph_cards[node_id]
	_graph.scroll_offset = (card.position_offset + card.size * 0.5) * _graph.zoom - _graph.size * 0.5
	_inspector_scroll.scroll_vertical = 0

func _preview_files(files: Array, prefer_model: bool) -> Array:
	var images: Array = []
	var models: Array = []
	var videos: Array = []
	for entry: Variant in files:
		var file: Dictionary = entry if entry is Dictionary else {"path": str(entry), "role": "文件"}
		var extension: String = str(file.get("path", "")).get_extension().to_lower()
		if extension in IMAGE_EXTENSIONS:
			images.append(file)
		elif extension in MODEL_EXTENSIONS:
			models.append(file)
		elif extension in VIDEO_EXTENSIONS:
			videos.append(file)
	return models + images + videos if prefer_model else images + models + videos

func _update_graph_preview(id: String, data: Dictionary) -> void:
	var version: Dictionary = _current_version(data)
	var candidates: Array = _preview_files(version.get("files", []), data.get("kind", "") == "model")
	var picker: OptionButton = _graph_artifact_pickers[id]
	var signature: String = JSON.stringify([version.get("id", ""), candidates])
	if picker.get_meta("artifact_signature", "") == signature:
		_graph_previews[id].retry_if_needed()
		return
	picker.set_meta("artifact_signature", signature)
	picker.clear()
	for file: Dictionary in candidates:
		var path: String = str(file.get("path", ""))
		picker.add_item(str(file.get("role", "文件")) + " · " + path.get_file())
		picker.set_item_metadata(picker.item_count - 1, path)
		picker.set_item_tooltip(picker.item_count - 1, path)
	picker.visible = candidates.size() > 1
	if candidates.is_empty():
		_graph_previews[id].clear_preview("尚无图片或模型产物" if version.is_empty() else "此版本没有可预览文件")
	else:
		picker.select(0)
		_graph_artifact_selected(0, id)
	# GraphNode retains its former minimum size after children shrink.
	_graph_cards[id].reset_size()

func _graph_artifact_selected(index: int, id: String) -> void:
	var picker: OptionButton = _graph_artifact_pickers[id]
	if index >= 0 and index < picker.item_count:
		_graph_previews[id].set_artifact(_project_path(str(picker.get_item_metadata(index))))

func _select_preview_card(id: String) -> void:
	if _selected_id == id:
		return
	_rebuilding = true
	for key: String in _graph_cards:
		_graph_cards[key].selected = key == id
	_rebuilding = false
	_graph_selected(_graph_cards[id])

func _kind_label(kind: String) -> String:
	var index: int = NODE_KINDS.find(kind)
	return KIND_LABELS[index] if index >= 0 else kind

func _node_state(data: Dictionary) -> String:
	var job: Dictionary = _latest_jobs.get(str(data.get("id", "")), {})
	var job_state: String = str(job.get("status", ""))
	if job_state in ACTIVE_STATES or job_state in ["paused", "failed", "error", "interrupted", "submission_uncertain"]:
		var label: String = str(STATUS_LABELS.get(job_state, job_state))
		var progress: Variant = job.get("progress")
		if progress is float or progress is int:
			label += " · %d%%" % clampi(int(progress), 0, 100)
		return label
	if data.get("stale", false):
		return "产物已过期"
	var state: String = str(data.get("state", "idle"))
	return str(STATUS_LABELS.get(state, state))

func _graph_selected(node: Node) -> void:
	if _rebuilding:
		return
	_selected_id = str(node.name)
	_dirty = _drafts.has(_selected_id)
	_load_inspector()
	selected_asset_changed.emit(_selected_id)

func _load_inspector() -> void:
	if not _nodes.has(_selected_id):
		return
	var data: Dictionary = _nodes[_selected_id]
	if _last_inspected_id != _selected_id:
		_inspector_scroll.scroll_vertical = 0
		_last_inspected_id = _selected_id
	_rebuilding = true
	_node_title.text = str(data.get("label", "未命名")) + " · " + _node_state(data)
	var draft: Dictionary = _drafts.get(_selected_id, {})
	var prompt_text: String = str(draft.get("prompt", data.get("prompt", "")))
	var params_text: String = str(draft.get("params", JSON.stringify(data.get("params", {}), "  ")))
	var config_signature: String = JSON.stringify([_selected_id, prompt_text, params_text])
	if config_signature != _last_inspector_config:
		_prompt.text = prompt_text
		_params.text = params_text
		_last_inspector_config = config_signature
	_dirty = not draft.is_empty()
	_set_editor_enabled(true)
	_update_versions()
	_update_dependencies()
	_rebuilding = false

func _set_editor_enabled(enabled: bool) -> void:
	_prompt.editable = enabled
	_params.editable = enabled
	_save.disabled = not enabled
	_run.disabled = not enabled
	_import.disabled = not enabled

func _mark_dirty() -> void:
	if not _rebuilding:
		_dirty = true
		if not _selected_id.is_empty():
			_drafts[_selected_id] = {"prompt": _prompt.text, "params": _params.text}
		_set_status("设置尚未保存。保存后再查看计划或运行。")

func _create_node() -> void:
	var kind: String = NODE_KINDS[_kind.selected]
	var title: String = _label.text.strip_edges()
	if title.is_empty():
		title = KIND_LABELS[_kind.selected]
	var position: Vector2 = _graph.scroll_offset / _graph.zoom / _ui_scale + Vector2(100, 100)
	var params: Dictionary = {"view": "front"} if kind == "view" else {}
	api_request("manual.create", {"node": {"kind": kind, "label": title, "prompt": "", "params": params, "inputs": [], "position": [position.x, position.y]}}, _created_node)

func _created_node(value: Variant) -> void:
	if value is Dictionary:
		_pending_selection = str(value.get("id", value.get("node", {}).get("id", "")))
	_label.clear()
	refresh()

func _create_template() -> void:
	api_request("template", {"name": "newsstand"}, func(_value):
		append_log("已创建报刊亭依赖模板。各节点需手动运行。")
		refresh())

func _save_node() -> void:
	if _selected_id.is_empty():
		return
	var parser := JSON.new()
	var parsed: Error = parser.parse(_params.text)
	if parsed != OK or not parser.data is Dictionary:
		_set_status("参数必须是 JSON 对象：" + parser.get_error_message(), true)
		return
	var saved_id: String = _selected_id
	var saved_draft: Dictionary = {"prompt": _prompt.text, "params": _params.text}
	api_request("manual.update", {"node_id": saved_id, "patch": {"prompt": _prompt.text, "params": parser.data}}, func(_value):
		if _drafts.get(saved_id, {}) == saved_draft:
			_drafts.erase(saved_id)
		if _selected_id == saved_id:
			_dirty = _drafts.has(saved_id)
		append_log("节点设置已保存。")
		refresh())

func _node_dragged(_from: Vector2, to: Vector2, id: String) -> void:
	if not _rebuilding:
		_pending_positions[id] = to
		_nodes[id]["position"] = [to.x / _ui_scale, to.y / _ui_scale]
		api_request("canvas.move", {"node_ids": [id], "position": [to.x / _ui_scale, to.y / _ui_scale]}, func(_value):
			if _pending_positions.get(id) == to:
				_pending_positions.erase(id))

func _connect_nodes(from: StringName, _from_port: int, to: StringName, _to_port: int) -> void:
	if from == to or not _nodes.has(str(from)) or not _nodes.has(str(to)):
		return
	var inputs: Array = _nodes[str(to)].get("inputs", []).duplicate(true)
	for input: Dictionary in inputs:
		if str(input.get("node_id")) == str(from):
			return
	_selected_id = str(to)
	_manual.dependencies()
	_manual.edges.append({"node_id": str(from), "role": "reference"})
	_manual.draw_edges()

func _disconnect_nodes(from: StringName, _from_port: int, to: StringName, _to_port: int) -> void:
	if not _nodes.has(str(to)):
		return
	var inputs: Array = []
	for input: Dictionary in _nodes[str(to)].get("inputs", []):
		if str(input.get("node_id")) != str(from):
			inputs.append(input)
	_nodes[str(to)].inputs = inputs
	api_request("manual.update", {"node_id": str(to), "patch": {"inputs": inputs}}, func(_value): refresh())

func _preview_plan() -> void:
	if _dirty:
		_set_status("请先保存当前设置，再查看运行计划。", true)
		return
	if not _selected_id.is_empty():
		api_request("node.plan", {"node_id": _selected_id}, _plan_received)

func _plan_received(value: Variant) -> void:
	_plan.text = JSON.stringify(value, "  ")
	_set_status("运行计划已更新。查看依赖与过期状态后，再决定是否运行。")

func _request_run() -> void:
	if _selected_id.is_empty() or _dirty:
		_set_status("请先选择节点并保存设置。", true)
		return
	var pending: Array[String] = []
	for dependency: Dictionary in _nodes[_selected_id].get("inputs", []):
		var upstream: Dictionary = _nodes.get(str(dependency.get("node_id", "")), {})
		if upstream.is_empty() or _text(upstream.get("current_version", "")).is_empty() or upstream.get("state", "") in ["queued", "running", "pending", "waiting", "blocked", "submitting", "submitted", "polling", "downloading", "pausing"]:
			pending.append(str(upstream.get("label", dependency.get("node_id", "未知节点"))))
	if not pending.is_empty():
		_set_status("上游产物未就绪：" + "、".join(pending) + "。请先单独完成上游。", true)
		return
	_pending_run_id = _selected_id
	api_request("node.plan", {"node_id": _selected_id}, func(value):
		_plan_received(value)
		if value is Dictionary and (value.get("can_run", true) == false or not value.get("blockers", []).is_empty() or not value.get("missing_inputs", []).is_empty() or value.get("provider_preview", {}).has("error")):
			_pending_run_id = ""
			_set_status("当前节点无法运行，请检查运行计划。", true)
			return
		_confirm_run.dialog_text = "仅运行「" + str(_nodes.get(_pending_run_id, {}).get("label", "当前节点")) + "」。\n不会自动运行上游，也不会自动重试。\n按所配置的服务调用，可能产生费用。"
		_confirm_run.popup_centered(Vector2i(430, 170)))

func _confirm_selected_run() -> void:
	if not _pending_run_id.is_empty():
		var id: String = _pending_run_id
		_pending_run_id = ""
		api_request("node.run", {"node_id": id}, func(_value):
			append_log("所选节点已提交。作业结束前会自动刷新状态。")
			refresh())

func _update_versions() -> void:
	_versions.clear()
	_files.clear()
	_display_versions = []
	_display_files = []
	_place.disabled = true
	if not _nodes.has(_selected_id):
		_preview.clear_preview("选择一个节点以预览")
		return
	var data: Dictionary = _nodes[_selected_id]
	var current: String = _text(data.get("current_version", ""))
	_display_versions = data.get("versions", []).duplicate()
	var selected: int = -1
	for index: int in range(_display_versions.size()):
		var version: Dictionary = _display_versions[index]
		var id: String = str(version.get("id", ""))
		_versions.add_item(("当前 · " if id == current else "") + id.left(12) + "  " + str(version.get("created_at", "")).left(19))
		if id == str(_preview_versions.get(_selected_id, current)):
			selected = index
	if selected >= 0:
		_versions.select(selected)
		_show_version(_display_versions[selected])
	else:
		_inspector_version = ""
		_inspector_artifact = ""
		_preview.clear_preview("此节点尚无产物")

func _version_selected(index: int) -> void:
	if _rebuilding or index < 0 or index >= _display_versions.size():
		return
	var version: Dictionary = _display_versions[index]
	_preview_versions[_selected_id] = str(version.id)
	_show_version(version)
	_update_dependencies()

func _adopt_preview_version() -> void:
	if not _inspector_version.is_empty():
		api_request("version.select", {"node_id": _selected_id, "version_id": _inspector_version}, func(_value): refresh())

func _compare_versions() -> void:
	if not _nodes.has(_selected_id):
		return
	var window := Window.new()
	window.title = "左：当前采用版本 · 右：正在预览版本"
	window.size = Vector2i(1000, 550)
	add_child(window)
	window.close_requested.connect(window.queue_free)
	var row := HBoxContainer.new()
	row.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	window.add_child(row)
	for id in [str(_nodes[_selected_id].get("current_version", "")), _inspector_version]:
		var preview := AssetPreview.new()
		preview.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(preview)
		var version: Dictionary = _version_by_id(_nodes[_selected_id], id)
		var files: Array = _preview_files(version.get("files", []), _nodes[_selected_id].kind == "model")
		if not files.is_empty():
			preview.set_artifact(_project_path(str(files[0].path)))
	window.popup_centered()

func _show_version(version: Dictionary) -> void:
	_display_files = version.get("files", []).duplicate()
	_files.clear()
	for file: Variant in _display_files:
		var path: String = str(file.get("path", "")) if file is Dictionary else str(file)
		var role: String = str(file.get("role", "文件")) if file is Dictionary else "文件"
		_files.add_item(role + " · " + path.get_file())
		_files.set_item_tooltip(_files.item_count - 1, path)
	var selected_path: String = _inspector_artifact if _inspector_version == str(version.get("id", "")) else ""
	var candidates: Array = _preview_files(_display_files, _nodes.get(_selected_id, {}).get("kind", "") == "model")
	if selected_path.is_empty() and not candidates.is_empty():
		selected_path = str(candidates[0].get("path", ""))
	_inspector_version = str(version.get("id", ""))
	var selected_index: int = -1
	for index: int in range(_display_files.size()):
		if _artifact_path(index) == selected_path:
			selected_index = index
			break
	if selected_index >= 0:
		_files.select(selected_index)
		_file_selected(selected_index)
	else:
		_preview.clear_preview("此版本没有可预览文件")
	_place.disabled = _model_path().is_empty()

func _artifact_path(index: int) -> String:
	if index < 0 or index >= _display_files.size():
		return ""
	var file: Variant = _display_files[index]
	return str(file.get("path", "")) if file is Dictionary else str(file)

func _project_path(path: String) -> String:
	return path if path.begins_with("res://") else "res://" + path.trim_prefix("./")

func _file_selected(index: int) -> void:
	_inspector_artifact = _artifact_path(index)
	_preview.set_artifact(_project_path(_inspector_artifact))

func _open_file(index: int) -> void:
	var path: String = _artifact_path(index)
	if not path.is_empty():
		OS.shell_open(ProjectSettings.globalize_path(_project_path(path)))

func _open_import() -> void:
	if not _selected_id.is_empty():
		_dialog.popup_centered_ratio(0.65)

func _import_file(path: String) -> void:
	var role: String = "model" if path.get_extension().to_lower() in ["glb", "gltf", "obj", "fbx"] else "image"
	api_request("artifact.import", {"node_id": _selected_id, "path": path, "role": role}, func(_value):
		append_log("产物已直接导入。未执行质量检查。")
		refresh())

func _model_path() -> String:
	for index: int in range(_display_files.size()):
		var path: String = _artifact_path(index)
		if path.get_extension().to_lower() in ["glb", "gltf", "fbx", "tscn", "scn"]:
			return path
	return ""

func _place_selected() -> void:
	if _nodes.has(_selected_id) and not _model_path().is_empty():
		place_requested.emit(_selected_id, _text(_nodes[_selected_id].get("current_version", "")), _model_path())

func set_place_mode(update_existing: bool) -> void:
	_placement_updates = update_existing
	if _place != null:
		_place.text = "更新选中实例" if update_existing else "放入当前场景"

func selected_node_id() -> String:
	return _selected_id

func selected_label() -> String:
	return str(_nodes.get(_selected_id, {}).get("label", "资产"))

func _update_jobs() -> void:
	var selected_job: String = ""
	if not _jobs.get_selected_items().is_empty():
		var previous: int = _jobs.get_selected_items()[0]
		if previous < _display_jobs.size():
			selected_job = str(_display_jobs[previous].get("id", ""))
	_jobs.clear()
	_display_jobs = _snapshot.get("jobs", []).duplicate()
	for index: int in range(_display_jobs.size()):
		var job: Dictionary = _display_jobs[index]
		var state: String = str(job.get("status", "pending"))
		var label: String = str(_nodes.get(str(job.get("node_id", "")), {}).get("label", job.get("node_id", "作业")))
		var progress: Variant = job.get("progress", "")
		_jobs.add_item(label + " · " + str(STATUS_LABELS.get(state, state)) + (" · " + str(progress) if str(progress) != "" else ""))
		_jobs.set_item_tooltip(index, str(job.get("error", "")))
		var error: String = _text(job.get("error", ""))
		var error_key: String = str(job.get("id", "")) + ":" + error
		if not error.is_empty() and not _seen_errors.has(error_key):
			_seen_errors[error_key] = true
			append_log(label + "：" + error)
		if str(job.get("id", "")) == selected_job:
			_jobs.select(index)
	# Snapshot polling is connection-wide, including idle and externally created
	# batches. Job existence must never determine whether the UI observes changes.

func _resume_job() -> void:
	if _jobs.get_selected_items().is_empty():
		_set_status("请先选择一个已存在的作业。", true)
		return
	var index: int = _jobs.get_selected_items()[0]
	var job: Dictionary = _display_jobs[index]
	api_request("job.resume", {"job_id": str(job.id)}, func(_value):
		append_log("已请求恢复所选作业。")
		refresh())

func _provider_received(value: Variant) -> void:
	append_log("服务配置：\n" + JSON.stringify(value, "  "))

func _text(value: Variant) -> String:
	return "" if value == null else str(value)

func _scale_layout(node: Node) -> void:
	if node is Control:
		node.custom_minimum_size *= _ui_scale
	if node is SplitContainer:
		node.split_offset = roundi(node.split_offset * _ui_scale)
	if node is MarginContainer:
		for side: String in ["left", "right", "top", "bottom"]:
			var key: String = "margin_" + side
			if node.has_theme_constant_override(key):
				node.add_theme_constant_override(key, roundi(node.get_theme_constant(key) * _ui_scale))
	if node is BoxContainer and node.has_theme_constant_override("separation"):
		node.add_theme_constant_override("separation", roundi(node.get_theme_constant("separation") * _ui_scale))
	if node is GraphEdit:
		node.minimap_size *= _ui_scale
		return # Internal editor controls already use the editor's scaled theme.
	if node is Window:
		return
	for child: Node in node.get_children():
		_scale_layout(child)

func _open_video_picker() -> void:
	var path := ""
	if _graph_artifact_pickers.has(_selected_id):
		var picker: OptionButton = _graph_artifact_pickers[_selected_id]
		if picker.selected >= 0:
			var selected_path := str(picker.get_item_metadata(picker.selected))
			if selected_path.get_extension().to_lower() in VIDEO_EXTENSIONS: path = selected_path
	if path.is_empty() and _nodes.has(_selected_id):
		for file in _current_version(_nodes[_selected_id]).get("files", []):
			if str(file.get("path", "")).get_extension().to_lower() in VIDEO_EXTENSIONS:
				path = str(file.path)
				break
	_open_video_path(path)

func _open_video_path(path: String) -> void:
	if _video_picker == null:
		_video_picker = preload("res://addons/asset_pipeline/video_picker.gd").new()
		add_child(_video_picker)
		_video_picker.setup(self)
	if not path.is_empty():
		_video_picker.select_video(ProjectSettings.globalize_path(_project_path(path)))
	_video_picker.popup_centered()

func _can_drop_data(_at: Vector2, data: Variant) -> bool:
	return data is Dictionary and data.get("type", "") == "files"

func _drop_data(at: Vector2, data: Variant) -> void:
	_import_dropped_files(Array(data.get("files", [])), at + global_position - _graph.global_position)

func _native_files_dropped(files: PackedStringArray) -> void:
	if not is_visible_in_tree() or not _graph.get_global_rect().has_point(get_global_mouse_position()):
		return
	_import_dropped_files(Array(files), _graph.get_local_mouse_position())

func _import_dropped_files(files: Array, at: Vector2) -> void:
	var paths: Array = []
	for path in files:
		paths.append(ProjectSettings.globalize_path(str(path)))
	if paths.is_empty(): return
	var position: Vector2 = (at + _graph.scroll_offset) / _graph.zoom / _ui_scale
	api_request("manual.import", {"paths": paths, "position": [position.x, position.y]}, func(nodes):
		if nodes is Array and not nodes.is_empty():
			_selected_id = str(nodes[0]["id"])
		append_log("已导入 %d 个资产，每个文件已创建独立卡片。" % paths.size())
		refresh())

func _open_subject_picker() -> void:
	if _subject_picker == null:
		_subject_picker = preload("subject_picker.gd").new()
		add_child(_subject_picker)
		_subject_picker.setup(self)
	_subject_picker.open()

func _toggle_tools() -> void:
	var expanded: bool = not _compact_controls[0].visible
	for control in _compact_controls:
		control.visible = expanded
	_tools_button.text = "收起工具" if expanded else "展开工具"

func _toggle_canvas_fullscreen() -> void:
	_canvas_fullscreen = not _canvas_fullscreen
	for control in _focus_sides:
		control.visible = not _canvas_fullscreen
	_expand_button.text = "退出全屏" if _canvas_fullscreen else "全屏画布"
	if _canvas_fullscreen:
		for control in _compact_controls: control.hide()
		_tools_button.text = "展开工具"
	fullscreen_requested.emit(_canvas_fullscreen)

func _unhandled_key_input(event: InputEvent) -> void:
	if visible and event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE and _canvas_fullscreen:
		_toggle_canvas_fullscreen()
		get_viewport().set_input_as_handled()

func _resume_after_subject_picker() -> void:
	if not _deferred_subject_snapshot.is_empty():
		var latest: Dictionary = _deferred_subject_snapshot
		_deferred_subject_snapshot = {}
		_snapshot_received(latest)
	if not _connection.is_empty(): refresh()

func _history_shortcut_method(event: InputEvent) -> String:
	if not event is InputEventKey or not event.pressed or event.echo or event.alt_pressed:
		return ""
	if not (event.ctrl_pressed or event.meta_pressed): return ""
	if event.keycode == KEY_Z:
		return "manual.redo" if event.shift_pressed else "manual.undo"
	if event.keycode == KEY_Y and event.ctrl_pressed:
		return "manual.redo"
	return ""

func _input(event: InputEvent) -> void:
	if not is_visible_in_tree() or not get_window().has_focus(): return
	var method := _history_shortcut_method(event)
	if method.is_empty(): return
	# Keep text editing and other editor docks on their own undo histories.
	var focused := get_viewport().gui_get_focus_owner()
	if focused is LineEdit or focused is TextEdit: return
	if focused == null or (focused != self and not is_ancestor_of(focused)): return
	for child in get_children():
		if child is Window and child.visible: return
	get_viewport().set_input_as_handled()
	_manual.call_api(method, {})

func _card_visible(id: String) -> bool:
	if bool(_nodes[id].get("archived", false)):
		return _manual.show_archived
	return _canvases.contains(id)

func _graph_context_menu(_at: Vector2) -> void:
	for id in _graph_cards:
		if _graph_cards[id].get_global_rect().has_point(get_global_mouse_position()):
			var picker: OptionButton = _graph_artifact_pickers[id]
			var path := ""
			if picker.selected >= 0: path = str(picker.get_item_metadata(picker.selected))
			_show_file_context(path, id)
			return
	_manual.actions()

func _show_file_context(path: String, node_id: String = "") -> void:
	var menu := PopupMenu.new()
	if is_instance_valid(_canvases.library) and _canvases.library.visible:
		_canvases.library.add_child(menu)
	else:
		add_child(menu)
	menu.add_item("在 Finder 中显示" if OS.get_name() == "macOS" else "打开文件所在位置", 0)
	menu.set_item_disabled(0, path.is_empty())
	if path.is_empty(): menu.set_item_tooltip(0, "此资产尚未生成或导入文件")
	if not node_id.is_empty(): menu.add_item("节点操作…", 1)
	menu.id_pressed.connect(func(id):
		if id == 0: _reveal_asset_file(path)
		elif _graph_cards.has(node_id):
			_select_preview_card(node_id)
			_manual.actions())
	menu.popup_hide.connect(menu.queue_free)
	menu.position = DisplayServer.mouse_get_position()
	menu.popup()

func _reveal_asset_file(path: String) -> void:
	if path.is_empty(): return
	var absolute := ProjectSettings.globalize_path(path if path.is_absolute_path() else _project_path(path))
	if not FileAccess.file_exists(absolute):
		_set_status("文件不存在，无法定位：" + absolute, true)
		append_log("文件不存在：" + absolute)
		return
	var error := OS.shell_show_in_file_manager(absolute)
	if error != OK:
		_set_status("无法打开文件所在位置：" + error_string(error), true)

func _refresh_previews() -> void:
	if not _selected_id.is_empty(): _preview_versions.erase(_selected_id)
	if is_instance_valid(_preview): _preview.force_refresh()
	for preview in _graph_previews.values(): preview.force_refresh()
	if Engine.is_editor_hint():
		var filesystem := EditorInterface.get_resource_filesystem()
		if filesystem != null and not filesystem.is_scanning(): filesystem.scan()
	refresh()

func _connect_video_thumbnail(preview: Control) -> void:
	var reference := weakref(preview)
	preview.video_thumbnail_requested.connect(func(path):
		api_request("video.thumbnail", {"path": path}, func(result):
			var target = reference.get_ref()
			if target != null:
				target.set_video_thumbnail(path, _project_path(str(result.get("path", ""))))))
