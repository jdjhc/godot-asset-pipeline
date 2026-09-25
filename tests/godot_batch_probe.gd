extends SceneTree
## Isolated UI regression. All RPCs are intercepted; nothing can generate assets.

class RecordingPanel:
	extends "res://addons/asset_pipeline/panel.gd"
	var requests: Array = []
	func api_request(method: String, params: Dictionary = {}, callback: Callable = Callable()) -> void:
		requests.append({"method": method, "params": params.duplicate(true)})
		if method.begins_with("batch.") and callback.is_valid():
			callback.call({"id": params.get("batch_id", "")})

var _checks: int = 0
var _failed: bool = false
var _reconnect_count: int = 0

func _init() -> void:
	call_deferred("_run")

func _check(value: bool, label: String) -> void:
	_checks += 1
	if not value:
		_failed = true
		push_error("ASSET_BATCH_UI_PROBE_FAIL: " + label)

func _frames(count: int = 3) -> void:
	for _index: int in range(count):
		await process_frame

func _node(id: String, kind: String, position: Array, path: String = "") -> Dictionary:
	var versions: Array = []
	if not path.is_empty():
		versions.append({"id": id + "_v1", "files": [{"path": path, "role": "image"}], "created_at": "2026-09-25"})
	return {"id": id, "kind": kind, "label": id, "position": position, "prompt": "Saved recipe", "params": {},
		"inputs": [], "state": "ready" if not path.is_empty() else "idle", "stale": false,
		"current_version": id + "_v1" if not path.is_empty() else null, "versions": versions}

func _batch(id: String, state: String, ids: Array, completed: int, date: String) -> Dictionary:
	return {"id": id, "label": id, "status": state, "node_ids": ids, "created_at": date,
		"progress": {"completed": completed, "total": ids.size()}, "total_nodes": ids.size(),
		"completed_nodes": completed, "active_node_ids": [], "blocked_node_ids": [], "error": null}

func _request_count(panel: RecordingPanel, method: String) -> int:
	var count: int = 0
	for request: Dictionary in panel.requests:
		if request.method == method:
			count += 1
	return count

func _run() -> void:
	var image := Image.create(32, 32, false, Image.FORMAT_RGBA8)
	image.fill(Color("de8a42"))
	_check(image.save_png("res://batch_fixture.png") == OK, "temporary preview image created")
	var panel := RecordingPanel.new()
	panel.size = Vector2(1440, 960)
	root.add_child(panel)
	panel.reconnect_requested.connect(func() -> void: _reconnect_count += 1)
	await _frames()
	var project: String = ProjectSettings.globalize_path("res://").trim_suffix("/")
	panel.set_connection({"port": 12345, "token": "offline-fixture", "project": project})
	var empty: Dictionary = {"project": project, "nodes": [], "jobs": [], "batches": []}
	panel._snapshot_received(empty)
	_check(not panel._poll.is_stopped(), "idle connection keeps polling for externally created batches")
	_check(panel._project_label.text.contains(project), "current project path is visible")
	var requests_before: int = _request_count(panel, "snapshot")
	panel._poll_jobs()
	_check(_request_count(panel, "snapshot") == requests_before + 1, "idle timer requests a new snapshot")
	_check(panel._batch_start.disabled, "no batch cannot be started")
	var reference: Dictionary = _node("reference", "reference", [20, 20], "batch_fixture.png")
	var subject: Dictionary = _node("subject", "subject", [340, 20])
	subject.inputs = [{"node_id": "reference", "role": "image"}]
	var model: Dictionary = _node("model", "model", [660, 20])
	model.inputs = [{"node_id": "subject", "role": "front"}]
	var batch: Dictionary = _batch("batch_first", "running", ["reference", "subject", "model"], 1, "2026-09-25T01:00:00")
	batch.active_node_ids = ["subject"]
	var snapshot: Dictionary = {"project": project, "nodes": [reference, subject, model],
		"batches": [batch], "jobs": [{"id": "job_subject", "node_id": "subject", "status": "running", "progress": 42}]}
	panel._snapshot_received(snapshot.duplicate(true))
	await _frames(5)
	_check(panel._graph_cards.size() == 3, "whole chain appears before any generated output exists")
	_check(panel._selected_batch_id == "batch_first", "latest active batch selected by default")
	_check(panel._batch_summary.text.contains("1 / 3"), "batch completion count displayed")
	_check(panel._batch_detail.text.contains("subject") and panel._batch_detail.text.contains("42%"), "current stage and job progress displayed")
	_check(panel._graph_states.subject.text.contains("42%"), "generating node displays progress")
	_check(not panel._batch_pause.disabled and panel._batch_start.disabled, "running batch exposes pause")
	_check(panel._graph_previews.reference.debug_state().get("state") == "ready", "imported image has immediate preview")
	var retained_preview: int = panel._graph_previews.reference.get_instance_id()
	panel._graph.zoom = 0.8
	panel._graph.scroll_offset = Vector2(210, 120)
	var retained_scroll: Vector2 = panel._graph.scroll_offset
	panel._select_preview_card("reference")
	panel._prompt.text = "Unsubmitted local draft"
	panel._params.text = "{\"local\":true}"
	panel._mark_dirty()
	var completed_subject: Dictionary = _node("subject", "subject", [340, 20], "batch_fixture.png")
	completed_subject.inputs = subject.inputs
	var new_node: Dictionary = _node("next_asset", "subject", [980, 20])
	var new_batch: Dictionary = _batch("batch_latest", "running", ["next_asset"], 0, "2026-09-25T02:00:00")
	var changed: Dictionary = snapshot.duplicate(true)
	changed.nodes = [reference, completed_subject, model, new_node]
	changed.batches = [batch, new_batch]
	changed.jobs = [{"id": "job_subject", "node_id": "subject", "status": "completed", "progress": 100}]
	panel._snapshot_received(changed.duplicate(true))
	await _frames()
	_check(panel._graph_cards.has("next_asset"), "external new node reconciles immediately")
	_check(panel._selected_batch_id == "batch_latest", "newest active batch receives default selection")
	_check(panel._graph_previews.reference.get_instance_id() == retained_preview, "existing preview instance retained")
	_check(panel._graph_previews.subject.debug_state().get("state") == "ready", "newly completed image is decoded immediately")
	_check(is_equal_approx(panel._graph.zoom, 0.8) and panel._graph.scroll_offset.is_equal_approx(retained_scroll), "new batch preserves graph zoom and scroll")
	_check(panel._prompt.text == "Unsubmitted local draft" and panel._params.text == "{\"local\":true}", "polling preserves prompt and parameter drafts")
	_check(panel._new_batch_notice, "new batch provides a locate notice without moving camera")
	panel._select_preview_card("subject")
	panel._select_preview_card("reference")
	_check(panel._prompt.text == "Unsubmitted local draft" and panel._dirty, "switching nodes retains each draft")
	var run_count: int = _request_count(panel, "batch.run")
	panel._batch_action("run")
	_check(_request_count(panel, "batch.run") == run_count, "batch never silently ignores unsaved drafts")
	panel._drafts.clear()
	panel._dirty = false
	for action: String in ["run", "pause", "resume"]:
		panel._batch_action(action)
		_check(_request_count(panel, "batch." + action) == 1, "batch " + action + " sent exactly once")
		var matched: bool = false
		for request: Dictionary in panel.requests:
			if request.method == "batch." + action:
				matched = request.params == {"batch_id": "batch_latest"}
		_check(matched, "batch " + action + " carries saved batch ID only")
	_check(_request_count(panel, "node.run") == 0, "UI never schedules individual stages itself")
	new_batch.status = "paused"
	changed.batches = [batch, new_batch]
	panel._batch_user_selected = true
	panel._snapshot_received(changed.duplicate(true))
	_check(not panel._batch_resume.disabled and panel._batch_pause.disabled, "paused batch exposes continue")
	new_batch.status = "blocked"
	new_batch.error = "Front view requires attention"
	changed.batches = [batch, new_batch]
	changed.jobs.append({"id": "job_next", "node_id": "next_asset", "status": "failed", "error": "Provider rejected request"})
	panel._snapshot_received(changed.duplicate(true))
	_check(panel._batch_detail.text.contains("Front view requires attention"), "batch error remains visible")
	_check(panel._graph_details.next_asset.text.contains("Provider rejected request"), "node error remains visible")
	# A backend restart invalidates the socket/token. Recovery must read the
	# new connection without replaying requests whose outcome is unknown.
	var paid_before: int = _request_count(panel, "node.run") + _request_count(panel, "batch.run") + _request_count(panel, "batch.resume")
	panel._active_request = {"method": "node.run", "params": {"node_id": "subject"}, "callback": Callable()}
	panel._request_queue = [{"method": "batch.run", "params": {"batch_id": "batch_latest"}, "callback": Callable()}]
	panel._batch_action_pending = true
	panel._pending_run_id = "subject"
	panel._request_completed(HTTPRequest.RESULT_CANT_CONNECT, 0, PackedStringArray(), PackedByteArray())
	_check(panel._connection.is_empty() and panel._poll.is_stopped(), "transport failure stops stale connection polling")
	_check(panel._request_queue.is_empty() and panel._active_request.is_empty(), "disconnect discards active and queued mutations")
	_check(not panel._batch_action_pending and panel._pending_run_id.is_empty(), "disconnect clears pending production actions")
	_check(_reconnect_count == 0, "reconnect is deferred until failed request has been cleared")
	await _frames()
	_check(_reconnect_count == 1, "transport failure requests one automatic reconnect")
	panel._request_completed(HTTPRequest.RESULT_CANT_CONNECT, 0, PackedStringArray(), PackedByteArray())
	panel._poll_jobs()
	await _frames()
	_check(_reconnect_count == 1, "repeated failures and stale timer events do not start unbounded reconnects")
	# An unverified connection candidate must not replenish the retry budget.
	panel.set_connection({"port": 12345, "token": "stale-fixture", "project": project})
	panel._request_completed(HTTPRequest.RESULT_SUCCESS, 401, PackedStringArray(), "{}".to_utf8_buffer())
	await _frames()
	_check(_reconnect_count == 1, "stale candidate authentication does not loop reconnects")
	for code: int in [401, 403]:
		panel.set_connection({"port": 12345, "token": "offline-fixture", "project": project})
		panel._snapshot_received(changed.duplicate(true))
		panel._active_request = {"method": "snapshot", "params": {}, "callback": Callable()}
		panel._request_queue = [{"method": "node.run", "params": {"node_id": "subject"}, "callback": Callable()}]
		panel._request_completed(HTTPRequest.RESULT_SUCCESS, code, PackedStringArray(), "{}".to_utf8_buffer())
		await _frames()
		_check(_reconnect_count == (2 if code == 401 else 3), "HTTP %d recovers automatically after a proven healthy connection" % code)
		_check(panel._request_queue.is_empty(), "HTTP %d discards queued paid actions" % code)
	_check(_request_count(panel, "node.run") + _request_count(panel, "batch.run") + _request_count(panel, "batch.resume") == paid_before,
		"reconnection never replays paid generation or resume actions")
	panel.set_connection({"port": 12345, "token": "offline-fixture", "project": project})
	panel._snapshot_received(changed.duplicate(true))
	panel._request_completed(HTTPRequest.RESULT_CANT_CONNECT, 0, PackedStringArray(), PackedByteArray())
	var preserved_count: int = panel._graph_cards.size()
	panel._snapshot_received({"project": project + "/wrong-project", "nodes": [], "jobs": [], "batches": []})
	panel._poll_jobs()
	await _frames()
	_check(_reconnect_count == 3, "project mismatch cancels a pending reconnect and stays stopped")
	_check(panel._connection.is_empty() and panel._poll.is_stopped(), "wrong project disconnects without replaying mutations")
	_check(panel._graph_cards.size() == preserved_count, "wrong project cannot replace visible project assets")
	_check(panel._batch_start.disabled and panel._batch_pause.disabled and panel._batch_resume.disabled, "wrong project disables all production actions")
	_check(panel._project_label.text.contains("项目不匹配"), "wrong project is explicitly identified")
	panel.refresh()
	_check(_reconnect_count == 4, "explicit manual refresh can retry after the bounded automatic attempt")
	panel.queue_free()
	await _frames()
	if not _failed:
		print("ASSET_BATCH_UI_PROBE_PASS checks=", _checks)
	quit(1 if _failed else 0)
