extends SceneTree
func _initialize() -> void:
	call_deferred("check")
func check() -> void:
	var panel = load("res://addons/asset_pipeline/panel.gd").new()
	root.add_child(panel)
	for c in panel._compact_controls: assert(not c.visible)
	panel._toggle_tools()
	for c in panel._compact_controls: assert(c.visible)
	panel._toggle_canvas_fullscreen()
	for c in panel._focus_sides: assert(not c.visible)
	panel._toggle_canvas_fullscreen()
	for c in panel._focus_sides: assert(c.visible)
	var graph = panel._graph
	graph._panning = true
	graph._press = Vector2(30,30)
	graph._initial_scroll = Vector2(200,200)
	var motion := InputEventMouseMotion.new()
	motion.position = Vector2(80,60)
	motion.button_mask = MOUSE_BUTTON_MASK_LEFT
	graph._gui_input(motion)
	assert(graph._moved)
	print("Pan requested; GraphEdit clamps scroll to available graph bounds: ", graph.scroll_offset)
	print("Compact controls, fullscreen visibility restoration and drag-pan delta passed")
	panel.queue_free()
	quit()
