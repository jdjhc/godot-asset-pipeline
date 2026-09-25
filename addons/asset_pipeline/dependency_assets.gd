@tool
extends VBoxContainer
## Displays resolver-provided dependency versions without resolving or modifying
## the graph. Stable cards retain their file selection and 3D camera on refresh.

signal asset_selected(node_id: String)
signal preview_created(preview: Control)

const AssetPreview = preload("asset_preview.gd")
const ROLE_LABELS: Dictionary = {"selection_reference": "选区标注", "original": "原始图片", "source_video": "来源视频", "input": "输入", "front": "正面", "back": "背面", "right": "右侧", "left": "左侧", "top": "俯视", "bottom": "底面", "image": "图片", "reference": "参考", "model": "模型", "concept": "概念", "subject": "主体", "multiview": "多视图"}
const PREVIEW_EXTENSIONS: Array[String] = ["png", "jpg", "jpeg", "webp", "bmp", "glb", "gltf", "fbx", "tscn", "scn", "mp4", "mov", "webm", "mkv", "avi", "ogv"]

var _previews: Dictionary = {}
var _cards: Dictionary = {}
var _entries: Dictionary = {}
var _order: Array[String] = []
var _selected_paths: Dictionary = {}
var _scale: float = 1.0
var _empty_label: Label
var _changing_picker: bool = false

func _ready() -> void:
	_ensure_ui()

func configure_scale(value: float) -> void:
	if is_finite(value) and value > 0.0:
		_scale = value
	add_theme_constant_override("separation", roundi(8 * _scale))
	for key: String in _cards:
		_style_card(key)

func set_dependencies(entries: Array, empty_message: String = "没有上游依赖") -> void:
	_ensure_ui()
	var next_entries: Dictionary = {}
	var next_order: Array[String] = []
	for value: Variant in entries:
		if not value is Dictionary:
			continue
		var entry: Dictionary = value.duplicate(true)
		var node_id: String = _text(entry.get("node_id", ""))
		var role: String = _text(entry.get("role", "reference"))
		var key: String = _text(entry.get("key", node_id + ":" + role))
		if key.is_empty() or next_entries.has(key):
			continue
		entry.node_id = node_id
		entry.key = key
		entry.role = role
		entry.files = _clean_files(entry.get("files", []))
		next_entries[key] = entry
		next_order.append(key)
	for key: String in _cards.keys():
		if not next_entries.has(key):
			var obsolete: Control = _cards[key].root
			remove_child(obsolete)
			obsolete.queue_free()
			_cards.erase(key)
			_previews.erase(key)
			_selected_paths.erase(key)
	_entries = next_entries
	_order = next_order
	for index: int in range(_order.size()):
		var key: String = _order[index]
		if not _cards.has(key):
			_create_card(key)
		_update_card(key)
		move_child(_cards[key].root, index + 1)
	_empty_label.text = empty_message
	_empty_label.visible = _order.is_empty()

func clear_dependencies(message: String) -> void:
	set_dependencies([], message)

func debug_entries() -> Array:
	var result: Array = []
	for key: String in _order:
		var entry: Dictionary = _entries[key]
		var preview: Dictionary = _previews[key].debug_state()
		result.append({"key": key, "node_id": entry.node_id, "label": _text(entry.get("label", "")), "role": entry.role, "version_id": _text(entry.get("version_id", "")), "current_version_id": _text(entry.get("current_version_id", "")), "used_version_id": _text(entry.get("used_version_id", "")), "preview_path": preview.artifact_path, "preview_type": preview.type, "preview_state": preview.state, "selected_path": _selected_paths.get(key, ""), "file_count": entry.files.size(), "missing": bool(entry.get("missing", false)), "warning": bool(entry.get("warning", false)), "status_text": _text(entry.get("status_text", "")), "navigation_enabled": not _cards[key].button.disabled, "preview_instance_id": str(_previews[key].get_instance_id())})
	return result

func _ensure_ui() -> void:
	if is_instance_valid(_empty_label):
		return
	size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_theme_constant_override("separation", roundi(8 * _scale))
	_empty_label = Label.new()
	_empty_label.text = "没有上游依赖"
	_empty_label.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	_empty_label.modulate = Color("8e9ba8")
	_empty_label.mouse_filter = Control.MOUSE_FILTER_IGNORE
	add_child(_empty_label)

func _create_card(key: String) -> void:
	var card := PanelContainer.new()
	card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	add_child(card)
	var row := HBoxContainer.new()
	row.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	card.add_child(row)
	var visual := VBoxContainer.new()
	visual.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	row.add_child(visual)
	var preview := AssetPreview.new()
	preview.size_flags_horizontal = Control.SIZE_FILL
	visual.add_child(preview)
	preview_created.emit(preview)
	var picker := OptionButton.new()
	picker.fit_to_longest_item = false
	picker.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	picker.size_flags_horizontal = Control.SIZE_FILL
	picker.item_selected.connect(_file_selected.bind(key))
	visual.add_child(picker)
	var details := VBoxContainer.new()
	details.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	row.add_child(details)
	var title := Label.new()
	title.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	title.max_lines_visible = 2
	title.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	details.add_child(title)
	var version := Label.new()
	version.text_overrun_behavior = TextServer.OVERRUN_TRIM_ELLIPSIS
	version.modulate = Color("a6b3bf")
	details.add_child(version)
	var status := Label.new()
	status.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	details.add_child(status)
	var spacer := Control.new()
	spacer.size_flags_vertical = Control.SIZE_EXPAND_FILL
	details.add_child(spacer)
	var button := Button.new()
	button.text = "定位节点"
	button.size_flags_horizontal = Control.SIZE_SHRINK_BEGIN
	button.pressed.connect(_locate_asset.bind(key))
	details.add_child(button)
	_cards[key] = {"root": card, "row": row, "visual": visual, "preview": preview, "picker": picker, "title": title, "version": version, "status": status, "button": button, "files_signature": ""}
	_previews[key] = preview
	_style_card(key)

func _style_card(key: String) -> void:
	var card: Dictionary = _cards[key]
	var entry: Dictionary = _entries.get(key, {})
	var warning: bool = bool(entry.get("warning", false)) or bool(entry.get("missing", false))
	var style := StyleBoxFlat.new()
	style.bg_color = Color("242b33")
	style.border_color = Color("806441") if warning else Color("3a4652")
	style.set_border_width_all(maxi(1, roundi(_scale)))
	style.set_corner_radius_all(roundi(6 * _scale))
	style.content_margin_left = 8 * _scale
	style.content_margin_right = 8 * _scale
	style.content_margin_top = 8 * _scale
	style.content_margin_bottom = 8 * _scale
	card.root.add_theme_stylebox_override("panel", style)
	card.row.add_theme_constant_override("separation", roundi(9 * _scale))
	card.visual.add_theme_constant_override("separation", roundi(4 * _scale))
	card.visual.custom_minimum_size.x = 120 * _scale
	card.preview.custom_minimum_size = Vector2(120, 95) * _scale
	card.picker.custom_minimum_size.x = 120 * _scale

func _update_card(key: String) -> void:
	var entry: Dictionary = _entries[key]
	var card: Dictionary = _cards[key]
	_style_card(key)
	var role: String = _text(ROLE_LABELS.get(entry.role, entry.role))
	var label: String = _text(entry.get("label", entry.node_id))
	card.title.text = role + " · " + (label if not label.is_empty() else "未知节点")
	card.title.tooltip_text = card.title.text
	var version_id: String = _text(entry.get("version_id", ""))
	card.version.text = "版本 " + _short_version(version_id) if not version_id.is_empty() else "尚无可用版本"
	card.version.tooltip_text = "显示版本：" + version_id + "\n当前上游版本：" + _text(entry.get("current_version_id", "")) + "\n生成时使用：" + _text(entry.get("used_version_id", ""))
	card.status.text = _text(entry.get("status_text", ""))
	card.status.visible = not card.status.text.is_empty()
	card.status.modulate = Color("e8be80") if bool(entry.get("warning", false)) or bool(entry.get("missing", false)) else Color("a5c9b8")
	card.button.disabled = bool(entry.get("missing", false)) or entry.node_id.is_empty()
	card.button.tooltip_text = "此上游节点已不可用" if card.button.disabled else "在依赖图中选中此节点"
	var files: Array = entry.files
	var selected_path: String = _text(_selected_paths.get(key, ""))
	var selected_index: int = -1
	for index: int in range(files.size()):
		if files[index].path == selected_path:
			selected_index = index
			break
	if selected_index < 0:
		selected_index = _preferred_file(files, entry.role)
	var signature: String = JSON.stringify(files)
	if signature != card.files_signature:
		_changing_picker = true
		card.picker.clear()
		for file: Dictionary in files:
			var file_role: String = _text(ROLE_LABELS.get(file.role, file.role))
			card.picker.add_item((file_role + " · " if not file_role.is_empty() else "") + str(file.path).get_file())
			card.picker.set_item_tooltip(card.picker.item_count - 1, str(file.path))
		card.files_signature = signature
		_changing_picker = false
	card.picker.visible = files.size() > 1
	if selected_index >= 0:
		_changing_picker = true
		card.picker.select(selected_index)
		_changing_picker = false
		_selected_paths[key] = files[selected_index].path
		card.preview.set_artifact(files[selected_index].path)
	else:
		_selected_paths[key] = ""
		card.preview.clear_preview("依赖产物不可用" if bool(entry.get("missing", false)) else "尚无产物")

func _preferred_file(files: Array, role: String) -> int:
	# A front/right/back edge can point at a multi-output version. Respect its
	# semantic role before choosing a generic supported preview.
	for index: int in range(files.size()):
		if _text(files[index].role) == role and _is_previewable(files[index].path):
			return index
	for index: int in range(files.size()):
		if _text(files[index].role) == role:
			return index
	for index: int in range(files.size()):
		if _is_previewable(files[index].path):
			return index
	return 0 if not files.is_empty() else -1

func _file_selected(index: int, key: String) -> void:
	if _changing_picker or not _entries.has(key):
		return
	var files: Array = _entries[key].files
	if index < 0 or index >= files.size():
		return
	_selected_paths[key] = files[index].path
	_previews[key].set_artifact(files[index].path)

func _locate_asset(key: String) -> void:
	if not _entries.has(key) or _cards[key].button.disabled:
		return
	asset_selected.emit(_entries[key].node_id)

func _clean_files(value: Variant) -> Array:
	var result: Array = []
	if not value is Array:
		return result
	for item: Variant in value:
		if item is Dictionary:
			var path: String = _text(item.get("path", ""))
			if not path.is_empty():
				result.append({"path": path, "role": _text(item.get("role", ""))})
	return result

func _is_previewable(path: String) -> bool:
	return path.get_extension().to_lower() in PREVIEW_EXTENSIONS

func _short_version(value: String) -> String:
	return value if value.length() <= 18 else value.left(15) + "…"

func _text(value: Variant) -> String:
	return "" if value == null else str(value)
