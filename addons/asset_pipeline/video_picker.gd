@tool
extends Window

var host: Control
var count: SpinBox
var grid: GridContainer
var info: Label
var extract: Button
var save: Button
var file_dialog: FileDialog
var video_path := ""
var session: Dictionary = {}
var selection: Dictionary = {}
var menus: Dictionary = {}
var selected_summary: Label
const ROLES := ["", "front", "left", "back", "right"]

func setup(panel: Control) -> void:
	host = panel
	title = "视频选帧 · 标记多视图"
	size = Vector2i(1050, 760)
	close_requested.connect(hide)
	window_input.connect(func(event):
		if event is InputEventKey and event.pressed and event.keycode == KEY_ESCAPE: hide())
	var box := VBoxContainer.new()
	box.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	add_child(box)
	var bar := HBoxContainer.new()
	box.add_child(bar)
	var back := Button.new()
	back.text = "← 返回画布"
	back.pressed.connect(hide)
	bar.add_child(back)
	var choose := Button.new()
	choose.text = "选择视频"
	choose.pressed.connect(func(): file_dialog.popup_centered_ratio(0.7))
	bar.add_child(choose)
	var play := Button.new()
	play.text = "▶ 预览视频"
	play.pressed.connect(func():
		if FileAccess.file_exists(video_path): OS.shell_open(video_path))
	bar.add_child(play)
	var history := Button.new()
	history.text = "历史选帧"
	bar.add_child(history)
	history.pressed.connect(_history)
	count = SpinBox.new()
	count.min_value = 4
	count.max_value = 100
	count.value = 20
	count.prefix = "抽帧数量"
	bar.add_child(count)
	extract = Button.new()
	extract.text = "提取候选帧"
	extract.disabled = true
	extract.pressed.connect(_sample)
	bar.add_child(extract)
	save = Button.new()
	save.text = "使用选中视图 → 创建模型节点"
	save.disabled = true
	save.pressed.connect(_commit)
	bar.add_child(save)
	info = Label.new()
	info.text = "默认抽 20 帧；选择正面及至少一个其他方向。只导入图片，不消耗生成积分。"
	info.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(info)
	selected_summary = Label.new()
	selected_summary.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	box.add_child(selected_summary)
	_update_save()
	var scroll := ScrollContainer.new()
	scroll.size_flags_vertical = Control.SIZE_EXPAND_FILL
	box.add_child(scroll)
	grid = GridContainer.new()
	grid.columns = 4
	grid.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(grid)
	size_changed.connect(func(): grid.columns = maxi(1, int((size.x - 30) / 250.0)))
	file_dialog = FileDialog.new()
	file_dialog.access = FileDialog.ACCESS_FILESYSTEM
	file_dialog.file_mode = FileDialog.FILE_MODE_OPEN_FILE
	file_dialog.filters = PackedStringArray(["*.mp4,*.mov,*.mkv,*.webm ; 视频"])
	file_dialog.file_selected.connect(select_video)
	add_child(file_dialog)

func _sample() -> void:
	extract.disabled = true
	save.disabled = true
	info.text = "正在解码原视频并保存候选帧…"
	host.api_request("video.sample", {"path": video_path, "count": int(count.value)}, _sampled)

func request_failed(message: String) -> void:
	extract.disabled = video_path.is_empty()
	info.text = message
	_update_save()

func _sampled(value: Variant) -> void:
	session = value
	selection.clear()
	menus.clear()
	for child in grid.get_children():
		grid.remove_child(child)
		child.queue_free()
	for frame: Dictionary in session.get("frames", []):
		var card := VBoxContainer.new()
		card.custom_minimum_size.x = 240
		card.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		grid.add_child(card)
		var image := Image.load_from_file(ProjectSettings.globalize_path("res://" + str(frame.path)))
		var preview := TextureRect.new()
		preview.custom_minimum_size = Vector2(240, 165)
		preview.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
		preview.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
		if image != null:
			preview.texture = ImageTexture.create_from_image(image)
		card.add_child(preview)
		var label := Label.new()
		label.text = "第 %d 帧 · %.3f 秒" % [int(frame.index) + 1, float(frame.seconds)]
		card.add_child(label)
		var menu := OptionButton.new()
		menu.tooltip_text = "方位以物体自身为准；每个方位只保留一帧，改选会替换旧选择。"
		for text in ["不使用此帧", "正面", "物体左侧", "背面", "物体右侧"]:
			menu.add_item(text)
		var index := int(frame.index)
		menus[index] = menu
		menu.item_selected.connect(func(item: int): _assign(index, item))
		card.add_child(menu)
		var full := Button.new()
		full.text = "查看原图"
		full.pressed.connect(func(): OS.shell_open(ProjectSettings.globalize_path("res://" + str(frame.path))))
		card.add_child(full)
	extract.disabled = false
	info.text = "共 %d 帧，已抽取 %d 帧。每个方向只能选一帧；改选会替换之前的选择。" % [int(session.total_frames), session.frames.size()]
	_update_save()

func _assign(index: int, item: int) -> void:
	for role: String in selection.keys():
		if int(selection[role]) == index:
			selection.erase(role)
	var role: String = ROLES[item]
	if not role.is_empty():
		if selection.has(role):
			menus[int(selection[role])].select(0)
		selection[role] = index
	_update_save()

func _update_save() -> void:
	save.disabled = not selection.has("front") or selection.size() < 2
	if selected_summary != null:
		var parts: Array[String] = []
		for role in ["front", "left", "back", "right"]:
			var title: String = {"front":"正面", "left":"左侧", "back":"背面", "right":"右侧"}[role]
			parts.append(title + ("：第 %d 帧" % (int(selection[role]) + 1) if selection.has(role) else "：未选"))
		selected_summary.text = "   |   ".join(parts) + ("\n请选择正面和至少一个其他方位。" if save.disabled else "\n已满足要求。创建节点不会立即生成或扣费。")

func _commit() -> void:
	save.disabled = true
	host.api_request("video.commit", {"session_id": session.session_id, "selection": selection, "label": video_path.get_file().get_basename().left(40)}, func(value):
		info.text = "已创建模型节点。可回到画布检查后生成。"
		host._pending_selection = str(value.model_node_id)
		host.refresh()
		hide())

func _history() -> void:
	host.api_request("video.sessions", {}, func(items):
		var d := AcceptDialog.new()
		d.title = "恢复选帧会话"
		add_child(d)
		var list := VBoxContainer.new()
		d.add_child(list)
		for item: Dictionary in items:
			var b := Button.new()
			b.text = str(item.session_id).left(18) + " · " + str(item.frames.size()) + " 帧"
			list.add_child(b)
			b.pressed.connect(func():
				video_path = ProjectSettings.globalize_path("res://" + str(item.source_path))
				count.value = int(item.requested_count)
				_sampled(item)
				host.api_request("video.selection", {"session_id": item.session_id}, func(receipt):
					selection = receipt.get("selection", {}).duplicate()
					for role in selection:
						menus[int(selection[role])].select(ROLES.find(role))
					_update_save())
				d.queue_free())
		d.confirmed.connect(d.queue_free)
		d.canceled.connect(d.queue_free)
		d.popup_centered(Vector2i(650, 400)))

func select_video(path: String) -> void:
	if path != video_path:
		session.clear()
		selection.clear()
		menus.clear()
		for child in grid.get_children():
			grid.remove_child(child)
			child.queue_free()
		save.disabled = true
	video_path = path
	extract.disabled = not FileAccess.file_exists(path)
	info.text = path.get_file() + " · 点击均匀抽帧" if not extract.disabled else "视频文件不存在：" + path
