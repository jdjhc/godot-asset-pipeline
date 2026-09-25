extends SceneTree
func _initialize() -> void:
	call_deferred("check")
func check() -> void:
	var panel = load("res://addons/asset_pipeline/panel.gd").new()
	root.add_child(panel)
	panel._nodes = {"a": {"id":"a", "label":"A", "kind":"reference", "params":{}, "inputs":[], "versions":[], "current_version":null}, "b":{"id":"b", "label":"B", "kind":"model", "params":{}, "inputs":[{"node_id":"a", "role":"front"}], "versions":[], "current_version":null}}
	panel._selected_id = "b"
	panel._manual.dependencies()
	assert(panel._manual.edges.size() == 1)
	panel._manual.parameters()
	panel._manual.actions()
	panel._manual.job_actions()
	panel._manual.templates()
	var key := InputEventKey.new()
	key.keycode = KEY_Z
	key.pressed = true
	key.ctrl_pressed = true
	assert(panel._history_shortcut_method(key) == "manual.undo")
	key.shift_pressed = true
	assert(panel._history_shortcut_method(key) == "manual.redo")
	key.ctrl_pressed = false
	key.meta_pressed = true
	key.shift_pressed = false
	assert(panel._history_shortcut_method(key) == "manual.undo")
	key.echo = true
	assert(panel._history_shortcut_method(key).is_empty())
	print("Manual dialogs instantiated: dependencies, parameters, actions, jobs, templates")
	panel.queue_free()
	quit()
