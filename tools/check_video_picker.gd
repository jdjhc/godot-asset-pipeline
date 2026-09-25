extends SceneTree
func _initialize() -> void:
	var picker = load("res://addons/asset_pipeline/video_picker.gd").new()
	root.add_child(picker)
	picker.setup(Control.new())
	assert(picker.count.value == 20)
	picker.selection = {"front": 0, "left": 5}
	var a := OptionButton.new()
	var b := OptionButton.new()
	for label in ["none", "front", "left"]:
		a.add_item(label)
		b.add_item(label)
	picker.menus = {0: a, 5: b}
	picker._assign(5, 1)
	assert(picker.selection == {"front": 5})
	assert(picker.save.disabled)
	assert("正面：第 6 帧" in picker.selected_summary.text)
	assert("左侧：未选" in picker.selected_summary.text)
	picker.session = {"session_id":"old"}
	picker.select_video("/missing/new_video.mp4")
	assert(picker.session.is_empty())
	assert(picker.selection.is_empty())
	assert(picker.extract.disabled)
	assert(picker.save.disabled)
	print("Video picker UI construction and role replacement passed")
	picker.host.free()
	a.free()
	b.free()
	picker.queue_free()
	quit()
