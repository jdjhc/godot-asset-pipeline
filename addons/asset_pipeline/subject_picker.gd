@tool
extends Window
var host: Control
var canvas: Control
var info: Label
var save: Button
var source_id := ""
var version_id := ""
var source_path := ""
var selector: OptionButton
var candidates: Array = []

func setup(panel: Control) -> void:
	host = panel
	title = "框选主体 · 无需填写名称或提示词"
	size = Vector2i(1100, 800)
	close_requested.connect(_return_to_canvas)
	window_input.connect(_window_input)
	var root := VBoxContainer.new()
	root.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(root)
	var navigation := HBoxContainer.new()
	root.add_child(navigation)
	var back := Button.new()
	back.text = "← 返回画布（Esc）"
	back.tooltip_text = "关闭框选窗口，保留未提交的选框；不会提交生成"
	back.pressed.connect(_return_to_canvas)
	navigation.add_child(back)
	var hint := Label.new()
	hint.text = "框选 → 创建节点 → 单独生成"
	navigation.add_child(hint)
	var bar := HBoxContainer.new()
	root.add_child(bar)
	selector = OptionButton.new()
	selector.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	bar.add_child(selector)
	selector.item_selected.connect(_select_source)
	var colors := ColorPickerButton.new()
	colors.color = Color.MAGENTA
	colors.custom_minimum_size = Vector2(90, 36)
	colors.tooltip_text = "改变选择框颜色"
	colors.edit_alpha = false
	bar.add_child(colors)
	colors.color_changed.connect(func(c): canvas.color = c; canvas.queue_redraw())
	for preset in [Color.MAGENTA, Color.CYAN, Color.YELLOW, Color.RED, Color.GREEN]:
		var button := Button.new()
		button.text = "■"
		button.modulate = preset
		button.pressed.connect(func(): colors.color = preset; canvas.color = preset; canvas.queue_redraw())
		bar.add_child(button)
	var reset := Button.new()
	reset.text = "删除选框"
	reset.pressed.connect(_delete_box)
	bar.add_child(reset)
	save = Button.new()
	save.text = "使用选框 → 创建主体节点"
	save.disabled = true
	save.pressed.connect(_save)
	bar.add_child(save)
	info = Label.new()
	info.text = "拖出矩形选择一个主体；拖动框内移动，拖四角调整。颜色只用于指示，生成图不会保留框线。"
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	root.add_child(info)
	canvas = preload("subject_box_canvas.gd").new()
	canvas.size_flags_vertical = Control.SIZE_EXPAND_FILL
	root.add_child(canvas)
	canvas.changed.connect(func(): save.disabled = canvas.box.size.x < 8 or canvas.box.size.y < 8)

func open() -> void:
	var previous_source := source_id
	var previous_version := version_id
	var previous_path := source_path
	selector.clear()
	candidates.clear()
	var chosen := 0
	for id: String in host._nodes:
		var node: Dictionary = host._nodes[id]
		if node.get("archived",false): continue
		var requested: String = str(host._preview_versions.get(id, node.get("current_version", "")))
		var version: Dictionary = host._version_by_id(node, requested)
		for file: Dictionary in version.get("files", []):
			if str(file.path).get_extension().to_lower() in host.IMAGE_EXTENSIONS:
				if id == host._selected_id: chosen = candidates.size()
				candidates.append({"node_id":id, "version_id":version.id, "path":file.path})
				selector.add_item(str(node.label) + " · " + str(file.path).get_file())
	popup_centered()
	if candidates.is_empty():
		info.text = "请先通过「导入资源」导入参考图片。"
		return
	for i in range(candidates.size()):
		var candidate: Dictionary = candidates[i]
		if candidate.node_id == previous_source and candidate.version_id == previous_version and candidate.path == previous_path and canvas.source != null:
			selector.select(i)
			return
	selector.select(chosen)
	_select_source(chosen)

func _select_source(index: int) -> void:
	var candidate: Dictionary = candidates[index]
	source_id = candidate.node_id
	version_id = candidate.version_id
	source_path = candidate.path
	var image := Image.load_from_file(ProjectSettings.globalize_path("res://" + source_path))
	if image == null:
		info.text = "无法读取图片"
		save.disabled = true
		return
	canvas.set_image(image)
	save.disabled = true
	info.text = "拖框选择一个主体，颜色可自由切换；无需输入任何文字。"

func _save() -> void:
	if not canvas.box.has_area(): return
	var folder := "res://artifacts/subject_selections"
	DirAccess.make_dir_recursive_absolute(ProjectSettings.globalize_path(folder))
	var path := folder + "/selection_" + str(Time.get_ticks_usec()) + ".png"
	if canvas.annotated().save_png(ProjectSettings.globalize_path(path)) != OK:
		info.text = "无法保存标注图片"
		return
	save.disabled = true
	info.text = "正在保存框选信息及主体生成节点…"
	host.api_request("subject.selection", {"node_id":source_id, "version_id":version_id, "source_path":source_path, "annotated_path":ProjectSettings.globalize_path(path), "box":[canvas.box.position.x, canvas.box.position.y, canvas.box.size.x, canvas.box.size.y], "image_size":[canvas.source.get_width(),canvas.source.get_height()], "color":canvas.color.to_html(false)}, func(value):
		info.text = "已创建主体节点，统一提示词和图片依赖已填写。点击运行所选节点即可生成。"
		host._pending_selection = str(value.subject_id)
		host.refresh()
		save.disabled = false)

func request_failed(message: String) -> void:
	info.text = message
	save.disabled = false

func _return_to_canvas() -> void:
	canvas.mode = ""
	hide()
	if host != null and host.has_method("_resume_after_subject_picker"):
		host._resume_after_subject_picker()

func _window_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and not event.echo and event.keycode in [KEY_DELETE, KEY_BACKSPACE]:
		var focused := gui_get_focus_owner()
		if focused is LineEdit or focused is TextEdit: return
		_delete_box()
		set_input_as_handled()
		return
	if event is InputEventKey and event.pressed and not event.echo and event.keycode == KEY_ESCAPE:
		_return_to_canvas()
		set_input_as_handled()

func _delete_box() -> void:
	canvas.mode = ""
	canvas.box = Rect2()
	canvas.queue_redraw()
	save.disabled = true
