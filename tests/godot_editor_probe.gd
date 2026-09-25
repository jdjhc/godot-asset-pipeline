@tool
extends EditorPlugin
## Disposable editor regression. Install as a second enabled plugin in an isolated
## project containing main.tscn and model_a.glb / model_b.glb; never enable in a
## user project. Tests the real pipeline plugin and its native UndoRedo commands.
var _checks: int = 0
var _failed: bool = false

func _enter_tree() -> void:
	call_deferred("_run")

func _check(value: bool, label: String) -> bool:
	if not value:
		_failed = true
		push_error("ASSET_EDITOR_PROBE_FAIL: " + label)
		get_tree().quit(1)
		return false
	_checks += 1
	return true

func _find_pipeline() -> EditorPlugin:
	var pending: Array[Node] = [get_tree().root]
	while not pending.is_empty():
		var node: Node = pending.pop_back()
		var script: Script = node.get_script() as Script
		if node is EditorPlugin and script != null and script.resource_path == "res://addons/asset_pipeline/plugin.gd":
			return node
		pending.append_array(node.get_children(true))
	return null

func _run() -> void:
	for index: int in range(300):
		await get_tree().process_frame
		if EditorInterface.get_edited_scene_root() != null:
			break
	var scene: Node = EditorInterface.get_edited_scene_root()
	if not _check(scene is Node3D, "isolated scene opened"):
		return
	var pipeline: EditorPlugin = _find_pipeline()
	if not _check(pipeline != null, "real pipeline editor plugin loaded"):
		return
	var panel: Control = pipeline.get("_panel")
	panel.set("_selected_id", "asset_probe")
	panel.set("_nodes", {"asset_probe": {"id": "asset_probe", "label": "回归模型", "kind": "model", "current_version": "v1", "params": {}, "inputs": [], "versions": []}})
	pipeline.call("_place_asset", "asset_probe", "v1", "model_a.glb")
	var wrapper: Node3D = scene.get_child(0) as Node3D
	if not _check(wrapper != null and wrapper.get_meta("pipeline_node_id", "") == "asset_probe", "first wrapper metadata"):
		return
	_check(wrapper.get_meta("pipeline_version_id", "") == "v1", "first version metadata")
	_check(wrapper.owner == scene, "wrapper serializable owner")
	var original: Node = wrapper.get_child(0)
	_check(bool(original.get_meta("pipeline_generated", false)), "generated marker")
	_check(original.owner == scene, "generated model serializable owner")
	_check(not str(wrapper.get_meta("pipeline_instance_id", "")).is_empty(), "stable instance identity")
	wrapper.position = Vector3(3.0, 2.0, -5.0)
	wrapper.rotation = Vector3(0.0, 0.6, 0.0)
	var retained_transform: Transform3D = wrapper.transform
	var manual := Node3D.new()
	manual.name = "ManualLamp"
	wrapper.add_child(manual)
	manual.owner = scene
	manual.position = Vector3(0.0, 4.0, 0.0)
	EditorInterface.get_selection().clear()
	EditorInterface.get_selection().add_node(wrapper)
	pipeline.call("_place_asset", "asset_probe", "v2", "model_b.glb")
	var replacement: Node = wrapper.get_child(0)
	_check(replacement != original and bool(replacement.get_meta("pipeline_generated", false)), "replacement inserted")
	_check(original.get_parent() == null, "original detached for undo")
	_check(wrapper.get_meta("pipeline_version_id", "") == "v2", "version updated")
	_check(wrapper.transform.is_equal_approx(retained_transform), "wrapper transform preserved")
	_check(manual.get_parent() == wrapper and manual.position == Vector3(0, 4, 0), "manual child preserved")
	_check(replacement.owner == scene, "replacement owner")
	# Exercise exactly the native editor history action registered by the real
	# plugin. This disposable probe does not reuse the manager after this check.
	var manager: EditorUndoRedoManager = pipeline.get_undo_redo()
	var history: UndoRedo = manager.get_history_undo_redo(manager.get_object_history_id(scene))
	_check(history.undo(), "native history undo")
	_check(original.get_parent() == wrapper and original.owner == scene, "undo restores old model and owner")
	_check(replacement.get_parent() == null, "undo removes replacement")
	_check(wrapper.get_meta("pipeline_version_id", "") == "v1", "undo restores version metadata")
	_check(wrapper.get_meta("pipeline_source", "") == "res://model_a.glb", "undo restores source metadata")
	_check(wrapper.transform.is_equal_approx(retained_transform) and manual.get_parent() == wrapper, "undo retains wrapper and manual child")
	_check(history.redo(), "native history redo")
	_check(replacement.get_parent() == wrapper and replacement.owner == scene, "redo restores replacement")
	_check(original.get_parent() == null and wrapper.get_meta("pipeline_version_id", "") == "v2", "redo restores new version")
	_check(wrapper.transform.is_equal_approx(retained_transform) and manual.get_parent() == wrapper, "redo retains manual edits")
	if not _failed:
		print("ASSET_EDITOR_PROBE_PASS checks=", _checks)
	get_tree().quit(1 if _failed else 0)
