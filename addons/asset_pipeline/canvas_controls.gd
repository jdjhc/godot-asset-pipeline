@tool
extends RefCounted
var host: Control
var picker: OptionButton
var current := "default"
var boards: Dictionary = {}
var library: AcceptDialog
var list: ItemList
var preview: Control
var trash := false

func setup(panel: Control, parent: VBoxContainer) -> void:
	host = panel
	var row := HBoxContainer.new()
	parent.add_child(row)
	picker = OptionButton.new()
	picker.custom_minimum_size.x = 190
	row.add_child(picker)
	picker.item_selected.connect(func(index): switch_to(str(picker.get_item_metadata(index))))
	host._button(row, "自动整理", auto_layout)
	host._button(row, "新建画布", func(): name_dialog(false))
	host._button(row, "重命名", func(): name_dialog(true))
	host._button(row, "删除画布", delete_dialog)
	host._button(row, "共享资源池", func(): open_library(false))
	host._button(row, "共享回收站", func(): open_library(true))
	var config := ConfigFile.new()
	if config.load("res://.godot/asset_pipeline_canvas.cfg") == OK:
		current = str(config.get_value("canvas", "selected", "default"))

func sync(snapshot: Dictionary) -> void:
	boards.clear()
	for board in snapshot.get("canvases", []): boards[str(board.id)] = board
	# Older snapshots in UI probes, or before the upgraded service reconnects.
	if boards.is_empty():
		var placements := {}
		for node in snapshot.get("nodes", []): placements[str(node.id)] = node.get("position", [0, 0])
		boards["default"] = {"id":"default", "name":"默认画布", "placements":placements}
	if not boards.has(current):
		current = str(boards.keys()[0])
		host._pending_positions.clear()
		host._selected_id = ""
	picker.clear()
	for id in boards:
		picker.add_item(str(boards[id].name))
		picker.set_item_metadata(picker.item_count - 1, id)
		if id == current: picker.select(picker.item_count - 1)

func contains(id: String) -> bool:
	return boards.get(current, {}).get("placements", {}).has(id)

func position(id: String, fallback: Array) -> Array:
	return boards.get(current, {}).get("placements", {}).get(id, fallback)

func switch_to(id: String) -> void:
	if not boards.has(id): return
	current = id
	host._pending_positions.clear()
	host._selected_id = ""
	host._dirty = false # Drafts remain in the panel's per-node draft dictionary.
	host._initial_graph_fitted = false
	host._rebuild_graph()
	host._set_editor_enabled(false)
	host._preview.clear_preview("选择一个节点以预览")
	host._update_dependencies()
	host._graph.grab_focus()
	var config := ConfigFile.new()
	config.set_value("canvas", "selected", current)
	config.save("res://.godot/asset_pipeline_canvas.cfg")
	for i in picker.item_count:
		if str(picker.get_item_metadata(i)) == current: picker.select(i)

func name_dialog(rename: bool) -> void:
	var dialog := ConfirmationDialog.new()
	dialog.title = "重命名画布" if rename else "新建画布"
	var edit := LineEdit.new()
	edit.placeholder_text = "输入画布名称"
	edit.max_length = 80
	edit.custom_minimum_size = Vector2(380, 45)
	if rename: edit.text = str(boards.get(current, {}).get("name", ""))
	dialog.add_child(edit)
	host.add_child(dialog)
	var target := current
	dialog.confirmed.connect(func():
		host.api_request("canvas.rename" if rename else "canvas.create", {"canvas_id":target, "name":edit.text}, func(result):
			current = str(result.canvas_id)
			host._pending_positions.clear()
			host._selected_id = ""
			host._initial_graph_fitted = false
			host.refresh())
		dialog.queue_free())
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered()
	edit.grab_focus()

func delete_dialog() -> void:
	var dialog := ConfirmationDialog.new()
	dialog.title = "删除画布"
	dialog.dialog_text = "删除「%s」？\n只移除画布和卡片摆位，资产、历史和共享回收站全部保留。\n可通过撤销恢复画布。" % str(boards.get(current, {}).get("name", ""))
	host.add_child(dialog)
	var target := current
	dialog.confirmed.connect(func():
		host.api_request("canvas.delete", {"canvas_id":target}, func(_result): host.refresh())
		dialog.queue_free())
	dialog.canceled.connect(dialog.queue_free)
	dialog.popup_centered()

func open_library(is_trash: bool) -> void:
	trash = is_trash
	if is_instance_valid(library): library.queue_free()
	library = AcceptDialog.new()
	library.title = "共享回收站" if trash else "共享资源池 · 所有画布共用"
	library.ok_button_text = "关闭"
	host.add_child(library)
	var body := VBoxContainer.new()
	body.custom_minimum_size = Vector2(820, 480)
	library.add_child(body)
	var search := LineEdit.new()
	search.placeholder_text = "搜索资产名称"
	body.add_child(search)
	var row := HBoxContainer.new()
	row.size_flags_vertical = Control.SIZE_EXPAND_FILL
	body.add_child(row)
	list = ItemList.new()
	list.custom_minimum_size = Vector2(340, 350)
	list.select_mode = ItemList.SELECT_MULTI
	list.allow_rmb_select = true
	list.item_clicked.connect(func(index, _at, button):
		if button != MOUSE_BUTTON_RIGHT: return
		var node: Dictionary = host._nodes.get(str(list.get_item_metadata(index)), {})
		var files: Array = host._preview_files(host._current_version(node).get("files", []), node.get("kind", "") == "model")
		host._show_file_context(str(files[0].get("path", "")) if not files.is_empty() else ""))
	row.add_child(list)
	preview = preload("asset_preview.gd").new()
	host._connect_video_thumbnail(preview)
	preview.file_context_requested.connect(func(path): host._show_file_context(path))
	preview.video_frames_requested.connect(func(path):
		library.hide()
		host._open_video_path(path))
	preview.custom_minimum_size = Vector2(440, 350)
	row.add_child(preview)
	search.text_changed.connect(fill_library)
	list.multi_selected.connect(func(index, selected):
		if not selected: return
		var node: Dictionary = host._nodes.get(str(list.get_item_metadata(index)), {})
		var version: Dictionary = host._current_version(node)
		var files: Array = host._preview_files(version.get("files", []), node.get("kind", "") == "model")
		if files.is_empty(): preview.clear_preview("尚无产物")
		else: preview.set_artifact(str(files[0].get("path", ""))))
	host._button(body, "恢复并加入当前画布" if trash else "加入当前画布（共享资产，不复制文件）", add_selected)
	fill_library("")
	library.popup_centered()

func fill_library(query: String) -> void:
	list.clear()
	for id in host._nodes:
		var node: Dictionary = host._nodes[id]
		if bool(node.get("archived", false)) != trash: continue
		if not query.is_empty() and not str(node.label).to_lower().contains(query.to_lower()): continue
		list.add_item(str(node.label) + (" · 已在此画布" if contains(id) else ""))
		list.set_item_metadata(list.item_count - 1, id)
	if list.item_count == 0: preview.clear_preview("回收站为空" if trash else "没有匹配的资产")

func add_selected() -> void:
	var ids: Array = []
	for index in list.get_selected_items(): ids.append(str(list.get_item_metadata(index)))
	if ids.is_empty(): return
	var target := current
	var pos: Vector2 = host._graph.scroll_offset / host._graph.zoom / host._ui_scale + Vector2(80, 100)
	var add := func():
		host.api_request("canvas.add", {"canvas_id":target, "node_ids":ids, "position":[pos.x,pos.y]}, func(_result): host.refresh())
		library.hide()
	if trash:
		host.api_request("manual.archive", {"node_ids":ids,"archived":false}, func(_result): add.call())
	else: add.call()

func auto_layout() -> void:
	var sizes := {}
	for id in host._graph_cards:
		var size: Vector2 = host._graph_cards[id].size / host._ui_scale
		sizes[id] = [maxf(size.x, 250), maxf(size.y, 240)]
	var target := current
	host.api_request("canvas.layout", {"canvas_id":target, "sizes":sizes}, func(_result):
		if current == target:
			host._pending_positions.clear()
			host._initial_graph_fitted = false
		host.append_log("已按依赖从左到右整理当前画布，可用 Ctrl+Z / ⌘Z 撤销。")
		host.refresh())
