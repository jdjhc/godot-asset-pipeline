extends SceneTree
func _initialize() -> void: call_deferred("check")
func check() -> void:
	var panel = load("res://addons/asset_pipeline/panel.gd").new()
	root.add_child(panel)
	panel._nodes = {"a":{"id":"a","label":"Asset","kind":"reference","position":[0,0],"inputs":[],"versions":[],"current_version":null}}
	panel._canvases.sync({"canvases":[{"id":"one","name":"第一张","placements":{"a":[12,34]}},{"id":"two","name":"第二张","placements":{}}]})
	panel._canvases.switch_to("one")
	assert(panel._graph_cards.has("a"))
	assert(panel._graph_cards["a"].position_offset == Vector2(12,34))
	panel._canvases.switch_to("two")
	assert(panel._graph_cards.is_empty())
	panel._canvases.open_library(false)
	assert(panel._canvases.list.item_count == 1)
	panel._canvases.library.hide()
	panel._nodes["a"]["archived"] = true
	panel._canvases.open_library(true)
	assert(panel._canvases.list.item_count == 1)
	print("Canvas filtering, placement and shared library/trash verified")
	panel.queue_free()
	quit()
