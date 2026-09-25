extends SceneTree
## Offline capture of the actual addon UI from a sanitized snapshot in a disposable project.
var panel
var output := ""
func _initialize() -> void: call_deferred("run")
func run() -> void:
 output = OS.get_cmdline_user_args()[0]
 DirAccess.make_dir_recursive_absolute(output)
 root.size = Vector2i(1440,900)
 root.content_scale_size = Vector2i(1440,900)
 panel = load("res://addons/asset_pipeline/panel.gd").new()
 root.add_child(panel)
 var snapshot: Dictionary = JSON.parse_string(FileAccess.get_file_as_string("res://snapshot.json"))
 var models: Array = JSON.parse_string(FileAccess.get_file_as_string("res://models.json"))
 snapshot.erase("project")
 panel._snapshot_received(snapshot)
 panel._poll.stop()
 panel._status.text = "本地演示 · 已生成资产 · 不提交生成请求"
 for side in panel._focus_sides: side.hide()
 panel._canvases.current = "demo"
 await process_frame
 await process_frame
 panel._fit_graph()
 var label := Label.new()
 label.text = "DEPENDENCIES → MULTI-VIEW → 3D ASSETS"
 label.add_theme_font_size_override("font_size",20)
 label.position = Vector2(24,855)
 label.modulate = Color("a8cbbd")
 root.add_child(label)
 for frame in range(144):
  if frame == 24:
   for side in panel._focus_sides: side.show()
   # Collapse the log panel, retain the asset inspector.
   panel._focus_sides[-1].hide()
   panel._select_preview_card(str(models[0]))
   enlarge(str(models[0]))
   label.text = "01 / NEWSSTAND · ROTATE THE ACTUAL GLB"
  if frame == 64 or frame == 104:
   var index := 1 if frame == 64 else 2
   panel._select_preview_card(str(models[index]))
   enlarge(str(models[index]))
   label.text = "02 / RAMEN SHOP · SHARED ASSET WORKFLOW" if index == 1 else "03 / ARCADE · INSPECT INPUTS AND OUTPUTS"
  if frame >= 24:
   panel._preview.orbit(Vector2(10,0))
   for item in panel._graph_previews.values(): item.orbit(Vector2(20,0))
  await process_frame
  await RenderingServer.frame_post_draw
  root.get_texture().get_image().save_png(output.path_join("frame_%03d.png" % frame))
 print("CAPTURE_COMPLETE ",output)
 quit()

func enlarge(id: String) -> void:
 panel._graph_cards[id].custom_minimum_size.x = 540
 panel._graph_previews[id].custom_minimum_size = Vector2(520,370)
 panel._graph_previews[id].zoom(0.95)
 panel._inspector_scroll.scroll_vertical = 0
 panel._fit_graph([id])
