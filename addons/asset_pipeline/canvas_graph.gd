@tool
extends GraphEdit
var _panning := false
var _press := Vector2.ZERO
var _initial_scroll := Vector2.ZERO
var _moved := false

func _gui_input(event: InputEvent) -> void:
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed:
			grab_focus()
			# Preserve node dragging, connections, toolbar widgets and Shift box selection.
			if event.shift_pressed: return
			for child in get_children():
				if child is GraphNode and child.get_global_rect().has_point(get_global_mouse_position()): return
			var hovered := get_viewport().gui_get_hovered_control()
			if hovered != self and hovered != null: return
			_panning = true
			_press = event.position
			_initial_scroll = scroll_offset
			_moved = false
			accept_event()
		elif _panning:
			_panning = false
			mouse_default_cursor_shape = Control.CURSOR_ARROW
			accept_event()
	elif event is InputEventMouseMotion and _panning:
		if not (event.button_mask & MOUSE_BUTTON_MASK_LEFT):
			_panning = false
			return
		if event.position.distance_to(_press) >= 4: _moved = true
		if _moved:
			scroll_offset = _initial_scroll - (event.position - _press)
			mouse_default_cursor_shape = Control.CURSOR_DRAG
		accept_event()

signal asset_files_dropped(paths: Array, at: Vector2)

func _can_drop_data(_at: Vector2, data: Variant) -> bool:
	return data is Dictionary and data.get("type", "") == "files"

func _drop_data(at: Vector2, data: Variant) -> void:
	asset_files_dropped.emit(Array(data.get("files", [])), at)
