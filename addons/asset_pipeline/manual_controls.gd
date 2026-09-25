@tool
extends RefCounted
var host: Control
var dialog: AcceptDialog
var body: VBoxContainer
var edges: Array = []
var target := ""
var show_archived := false

func setup(panel: Control, toolbar: HBoxContainer) -> void:
	host = panel
	for entry in [["导入资源", import_files], ["节点操作", actions], ["编辑依赖", dependencies], ["流程模板", templates], ["撤销", func(): call_api("manual.undo", {})], ["重做", func(): call_api("manual.redo", {})]]:
		button(toolbar, entry[0], entry[1])
	dialog = AcceptDialog.new()
	dialog.title = "画布编辑"
	dialog.min_size = Vector2i(780, 420)
	dialog.ok_button_text = "关闭"
	host.add_child(dialog)
	var scroll := ScrollContainer.new()
	scroll.custom_minimum_size = Vector2(760, 380)
	dialog.add_child(scroll)
	body = VBoxContainer.new()
	body.size_flags_horizontal = Control.SIZE_EXPAND_FILL
	scroll.add_child(body)

func button(parent: Node, text: String, fn: Callable) -> Button:
	var b := Button.new()
	b.text = text
	b.pressed.connect(fn)
	parent.add_child(b)
	return b

func clear(title: String) -> void:
	for child in body.get_children():
		body.remove_child(child)
		child.queue_free()
	dialog.title = title
	dialog.popup_centered()

func label(text: String) -> void:
	var l := Label.new()
	l.text = text
	l.autowrap_mode = TextServer.AUTOWRAP_WORD_SMART
	body.add_child(l)

func call_api(method: String, params: Dictionary) -> void:
	host.api_request(method, params, func(_value): host.refresh())

func ids() -> Array:
	var result: Array = []
	for id: String in host._graph_cards:
		if host._graph_cards[id].selected:
			result.append(id)
	if result.is_empty() and not host._selected_id.is_empty():
		result.append(host._selected_id)
	return result

func import_files() -> void:
	var d := FileDialog.new()
	d.access = FileDialog.ACCESS_FILESYSTEM
	d.file_mode = FileDialog.FILE_MODE_OPEN_FILES
	host.add_child(d)
	d.files_selected.connect(func(paths: PackedStringArray):
		call_api("manual.import", {"paths": Array(paths)})
		d.queue_free())
	d.canceled.connect(d.queue_free)
	d.popup_centered_ratio(0.7)

func actions() -> void:
	clear("节点操作")
	var archived_count := 0
	for node in host._nodes.values():
		if node.get("archived", false): archived_count += 1
	button(body, ("隐藏回收站节点" if show_archived else "显示回收站节点") + "（%d）" % archived_count, toggle_archived)
	var selected := ids()
	if selected.is_empty():
		label("请先选择节点")
		return
	target = host._selected_id
	var name := LineEdit.new()
	name.text = str(host._nodes[target].label)
	body.add_child(name)
	button(body, "重命名", func(): call_api("manual.update", {"node_id": target, "patch": {"label": name.text}}))
	button(body, "复制选中节点（保留当前产物）", func(): call_api("manual.copy", {"node_ids": selected}))
	button(body, "复制当前节点与全部下游", func():
		host.api_request("node.plan", {"node_id": target}, func(plan): call_api("manual.copy", {"node_ids": [target] + plan.affected})))
	label("归档只隐藏节点，保留文件、历史和依赖；不执行永久删除。")
	var affected: Array = []
	for id: String in host._nodes:
		for edge: Dictionary in host._nodes[id].get("inputs", []):
			if selected.has(str(edge.node_id)) and not selected.has(id) and not affected.has(id):
				affected.append(id)
	for id in affected:
		label("仍被依赖：" + str(host._nodes[id].label))
	button(body, "仅从当前画布移除（保留资源池资产）", func():
		call_api("canvas.remove", {"node_ids": selected})
		dialog.hide())
	button(body, "移入回收站（影响所有画布，可恢复）", delete_selected)
	button(body, "从回收站恢复选中节点", func(): call_api("manual.archive", {"node_ids": selected, "archived": false}))
	button(body, "重新生成范围：此节点及全部下游…", func(): rebuild("all"))
	button(body, "重新生成范围：仅缺失或需更新的节点…", func(): rebuild("stale"))

func rebuild(mode: String) -> void:
	host.api_request("manual.plan", {"node_id": target, "mode": mode}, func(plan):
		clear("确认修复范围 · 尚未提交生成")
		label(str(plan.notice))
		if not plan.node_ids.is_empty():
			button(body, "创建修复分支（保留原版本，尚不生成）", func():
				call_api("manual.rebuild", {"node_ids": plan.node_ids})
				dialog.hide())
		label("将处理 %d 个节点：" % plan.node_ids.size())
		for text in plan.labels:
			label("• " + str(text)))

func dependencies() -> void:
	if host._selected_id.is_empty():
		return
	target = host._selected_id
	edges = host._nodes[target].get("inputs", []).duplicate(true)
	draw_edges()

func draw_edges() -> void:
	clear("输入依赖 · " + str(host._nodes.get(target, {}).get("label", "")))
	label("按列表顺序传入图片；角色必须明确。修改后点击保存。")
	for i in range(edges.size()):
		var edge: Dictionary = edges[i]
		var row := HBoxContainer.new()
		body.add_child(row)
		var asset := OptionButton.new()
		var keys: Array = host._nodes.keys()
		for id in keys:
			asset.add_item(str(host._nodes[id].label))
		asset.select(keys.find(str(edge.node_id)))
		asset.size_flags_horizontal = Control.SIZE_EXPAND_FILL
		row.add_child(asset)
		asset.item_selected.connect(func(idx): edges[i].node_id = keys[idx])
		var role := OptionButton.new()
		var roles := ["reference", "image", "subject", "layout_reference", "front", "left", "back", "right"]
		if not roles.has(str(edge.role)):
			roles.append(str(edge.role))
		for text in roles:
			role.add_item(str({"reference":"参考图", "image":"图像输入", "subject":"主体参考", "layout_reference":"布局参考", "front":"正面", "left":"左侧", "back":"背面", "right":"右侧"}.get(text, text)))
		role.select(roles.find(str(edge.role)))
		role.item_selected.connect(func(idx): edges[i].role = roles[idx])
		row.add_child(role)
		button(row, "↑", func(): move_edge(i, -1))
		button(row, "↓", func(): move_edge(i, 1))
		button(row, "移除", func(): edges.remove_at(i); draw_edges())
	button(body, "添加已有资产", func():
		for id: String in host._nodes:
			if id != target:
				edges.append({"node_id": id, "role": "reference"})
				break
		draw_edges())
	button(body, "导入文件作为依赖…", func():
		var d := FileDialog.new()
		d.access = FileDialog.ACCESS_FILESYSTEM
		d.file_mode = FileDialog.FILE_MODE_OPEN_FILES
		host.add_child(d)
		d.files_selected.connect(func(paths):
			host.api_request("manual.import", {"paths": Array(paths)}, func(nodes):
				for node in nodes:
					host._nodes[node.id] = node
					edges.append({"node_id": node.id, "role": "reference"})
				draw_edges()
				host.refresh())
			d.queue_free())
		d.canceled.connect(d.queue_free)
		d.popup_centered_ratio(0.7))
	button(body, "保存依赖与顺序", func():
		var roles: Array = []
		if host._nodes[target].kind == "model":
			for edge: Dictionary in edges:
				if edge.role in ["front", "left", "back", "right"]:
					if roles.has(edge.role):
						label("同一视角不能重复，请修改角色")
						return
					roles.append(edge.role)
		call_api("manual.update", {"node_id": target, "patch": {"inputs": edges}})
		dialog.hide())

func move_edge(i: int, delta: int) -> void:
	var j := i + delta
	if j < 0 or j >= edges.size():
		return
	var edge: Dictionary = edges[i]
	edges[i] = edges[j]
	edges[j] = edge
	draw_edges()

func templates() -> void:
	clear("自定义流程模板")
	label("框选节点后保存模板。模板保存配方和连线，不携带生成产物。")
	var name := LineEdit.new()
	name.placeholder_text = "模板名称"
	body.add_child(name)
	button(body, "保存选中节点为模板", func():
		call_api("manual.template_save", {"node_ids": ids(), "label": name.text if not name.text.is_empty() else "自定义流程"}))
	host.api_request("manual.templates", {}, func(items):
		for item: Dictionary in items:
			button(body, "添加流程：" + str(item.label), func(): call_api("manual.template_load", {"template_id": item.id})))

func parameters() -> void:
	if host._selected_id.is_empty():
		return
	target = host._selected_id
	clear("常用生成参数")
	var params: Dictionary = host._nodes[target].get("params", {}).duplicate(true)
	label("未修改的高级参数会保留。服务不支持的组合会在运行计划中报告。")
	for field in ["model_version", "view", "face_limit", "model_seed", "texture", "pbr", "quad"]:
		var row := HBoxContainer.new()
		body.add_child(row)
		var l := Label.new()
		l.text = str({"model_version":"模型版本（留空用默认）", "view":"视图方向", "face_limit":"目标面数", "model_seed":"随机种子", "texture":"生成贴图", "pbr":"PBR 材质", "quad":"四边面"}.get(field, field))
		l.custom_minimum_size.x = 220
		row.add_child(l)
		if field in ["texture", "pbr", "quad"]:
			var value := OptionButton.new()
			for text in ["服务默认", "开启", "关闭"]:
				value.add_item(text)
			value.select(0 if not params.has(field) else (1 if params[field] else 2))
			value.item_selected.connect(func(i):
				if i == 0: params.erase(field)
				else: params[field] = i == 1)
			row.add_child(value)
		elif field == "view":
			var value := OptionButton.new()
			var choices := ["", "front", "left", "back", "right"]
			for text in choices: value.add_item(text if not text.is_empty() else "未设置")
			value.select(maxi(0, choices.find(str(params.get(field, "")))))
			value.item_selected.connect(func(i):
				if i == 0: params.erase(field)
				else: params[field] = choices[i])
			row.add_child(value)
		else:
			var value := LineEdit.new()
			value.text = str(params.get(field, ""))
			value.text_changed.connect(func(text):
				if text.is_empty(): params.erase(field)
				elif field in ["face_limit", "model_seed"]:
					params[field] = int(text) if text.is_valid_int() else text
				else: params[field] = text)
			row.add_child(value)
	button(body, "应用到参数编辑区", func():
		host._params.text = JSON.stringify(params, "  ")
		host._mark_dirty()
		dialog.hide())

func job_actions() -> void:
	clear("作业恢复")
	label("继续查询已有任务不会重新提交生成。暂停仅停止本地查询，不取消远端计费。")
	for job: Dictionary in host._snapshot.get("jobs", []):
		if job.node_id != host._selected_id:
			continue
		label(str(job.id) + " · " + str(job.status))
		button(body, "暂停本地查询", func(): call_api("job.pause", {"job_id":job.id}))
		button(body, "恢复已有任务", func(): call_api("job.resume", {"job_id":job.id}))
		if job.status == "submission_uncertain":
			var task := LineEdit.new()
			task.placeholder_text = "填写在服务端核实过的任务 ID"
			body.add_child(task)
			button(body, "关联远端任务", func(): call_api("job.attach", {"job_id":job.id, "task_id":task.text}))

func delete_selected() -> void:
	var selected := ids()
	if selected.is_empty(): return
	host.api_request("manual.archive", {"node_ids": selected, "archived": true}, func(_value):
		host._selected_id = ""
		host._pending_selection = ""
		host.append_log("已移入回收站；文件与历史保留，可撤销或从回收站恢复。")
		dialog.hide()
		host.refresh())

func toggle_archived() -> void:
	show_archived = not show_archived
	var archived: Array = []
	for id in host._nodes:
		if host._nodes[id].get("archived", false): archived.append(id)
	# A local display preference should not wait for the service request queue.
	host._rebuild_graph()
	dialog.hide()
	if show_archived and not archived.is_empty():
		host.call_deferred("_fit_graph", archived)
	var message := "回收站为空，没有已删除的节点。" if archived.is_empty() else ("已显示 %d 个回收站节点，已定位到这些卡片。" % archived.size() if show_archived else "已隐藏回收站节点。")
	host.append_log(message)
	if archived.is_empty() and show_archived:
		clear("回收站")
		label(message)
