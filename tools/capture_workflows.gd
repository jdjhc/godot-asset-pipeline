extends SceneTree
## Captures real UI operations against an isolated local backend. Never calls node.run/batch.run.
var panel
var caption: Label
var output: String
var clip := ""
var frame := 0
var demo: Dictionary
func _initialize() -> void: call_deferred("run")
func settle() -> void:
 for i in range(8): await process_frame
 for i in range(1200):
  if panel._active_request.is_empty() and panel._request_queue.is_empty(): break
  await create_timer(0.025).timeout
 for i in range(6): await process_frame
func shot(count: int, text: String) -> void:
 caption.text = text
 for i in range(count):
  await process_frame
  await RenderingServer.frame_post_draw
  root.get_texture().get_image().save_png(output.path_join(clip).path_join("%04d.png" % frame))
  frame += 1
func start(name: String) -> void:
 clip = name
 frame = 0
 DirAccess.make_dir_recursive_absolute(output.path_join(clip))
func select_board(id: String) -> void:
 panel._canvases.switch_to(id)
 await settle()
 panel._fit_graph()
func mouse(canvas: Control, pos: Vector2, pressed: bool) -> void:
 var event := InputEventMouseButton.new()
 event.button_index = MOUSE_BUTTON_LEFT
 event.position = pos
 event.pressed = pressed
 canvas._gui_input(event)
func run() -> void:
 output = OS.get_cmdline_user_args()[0]
 root.size = Vector2i(1440,960)
 root.gui_embed_subwindows = true
 panel = load("res://addons/asset_pipeline/panel.gd").new()
 root.add_child(panel)
 var footer := ColorRect.new()
 footer.color = Color("101b23")
 footer.position = Vector2(0,902)
 footer.size = Vector2(1440,58)
 root.add_child(footer)
 caption = Label.new()
 caption.position = Vector2(22,916)
 caption.add_theme_font_size_override("font_size",23)
 root.add_child(caption)
 demo = JSON.parse_string(FileAccess.get_file_as_string("res://demo.json"))
 panel.set_connection(JSON.parse_string(FileAccess.get_file_as_string("res://.godot/asset_pipeline_connection.json")))
 await settle()
 panel._poll.stop()
 panel._focus_sides[-1].hide()
 start("01-subject-selection")
 await select_board("select")
 panel._select_preview_card("zzz_reference")
 panel._open_subject_picker()
 await settle()
 var picker = panel._subject_picker
 picker.size = Vector2i(1320,810)
 picker.position = Vector2i(60,60)
 await shot(10,"01  框选主体  /  原图、选区与提示词自动关联")
 var canvas = picker.canvas
 var area: Rect2 = canvas.image_rect()
 var a: Vector2 = area.position + Vector2(145.0/1280,350.0/720)*area.size
 var b: Vector2 = area.position + Vector2(390.0/1280,558.0/720)*area.size
 mouse(canvas,a,true)
 for i in range(16):
  var event := InputEventMouseMotion.new()
  event.position = a.lerp(b,float(i+1)/16)
  canvas._gui_input(event)
  await shot(1,"01  拖出选区：不用输入名称或位置描述")
 mouse(canvas,b,false)
 press_cyan(picker)
 await shot(12,"01  选框可调整、换色；保留原始参考图")
 picker._save()
 await settle()
 picker._return_to_canvas()
 await settle()
 panel._canvases.auto_layout()
 await settle()
 panel._fit_graph()
 await shot(22,"01  已创建主体节点 + 原图与标注依赖；尚未调用生成")
 start("02-video-views")
 panel._open_video_path(str(demo.session.source_path))
 await settle()
 var video = panel._video_picker
 video.size = Vector2i(1380,830)
 video.position = Vector2i(30,40)
 video._sampled(demo.session)
 await settle()
 await shot(12,"02  视频选帧  /  真实视频的 20 张候选帧")
 for role in ["front","left","back","right"]:
  if not demo.selection.has(role): continue
  var index := int(demo.selection[role])
  var menu: OptionButton = video.menus[index]
  menu.select(video.ROLES.find(role))
  menu.item_selected.emit(video.ROLES.find(role))
  var scroll: ScrollContainer = video.grid.get_parent()
  scroll.ensure_control_visible(menu)
  await shot(10,"02  标记物体方位；固定四个输入槽，避免混淆")
 # Deliberately replace one selection using another candidate, then restore it.
 var original := int(demo.selection.front)
 var replacement := -1
 for key in video.menus:
  if not video.selection.values().has(key): replacement = key; break
 if replacement >= 0:
  video.menus[replacement].select(1)
  video.menus[replacement].item_selected.emit(1)
  video.grid.get_parent().ensure_control_visible(video.menus[replacement])
  await shot(12,"02  改选正面：旧选择自动取消，每个方位只保留一帧")
  video.menus[original].select(1)
  video.menus[original].item_selected.emit(1)
 video._commit()
 await settle()
 panel._canvases.auto_layout()
 await settle()
 panel._fit_graph()
 await shot(18,"02  确认后自动创建多视图依赖和模型节点；生成由用户单独发起")
 start("03-versions-rebuild")
 await select_board("trace")
 panel._select_preview_card("zzz_model")
 await settle()
 await shot(12,"03  版本与依赖  /  保留每次产物，追溯当时的输入")
 panel._dependency_mode.select(1)
 panel._dependency_mode.item_selected.emit(1)
 await shot(12,"03  查看此版本生成时的输入，不改变当前依赖")
 var current: String = panel._nodes.zzz_model.current_version
 for i in range(panel._display_versions.size()):
  if panel._display_versions[i].id != current:
   panel._versions.select(i)
   panel._version_selected(i)
   break
 panel._compare_versions()
 await settle()
 await shot(22,"03  并排比较真实历史版本；旧版本仍可查看和采用")
 for child in panel.get_children():
  if child is Window and child.visible: child.hide()
 panel._select_preview_card("zzz_subject")
 panel.api_request("manual.update",{"node_id":"zzz_subject","patch":{"prompt":str(panel._nodes.zzz_subject.prompt)+"\nKeep the kiosk proportions unchanged."}},func(_r): panel.refresh())
 await settle()
 await shot(12,"03  修改主体要求后，下游产物标记为需要更新")
 panel._manual.target = "zzz_subject"
 panel._manual.rebuild("stale")
 await settle()
 await shot(24,"03  预览局部重生成范围，保留原链路；此处不提交付费任务")
 panel._manual.dialog.hide()
 start("04-canvas-management")
 await select_board("work")
 for side in panel._focus_sides: side.hide()
 panel._fit_graph()
 await shot(12,"04  编辑器工作流  /  共享资产，独立画布布局")
 panel._canvases.auto_layout()
 await settle()
 panel._fit_graph()
 await shot(18,"04  自动整理：按依赖从左向右排列，减少手动摆卡片")
 panel.api_request("canvas.remove",{"node_ids":["zzz_model"]},func(_r): panel.refresh())
 await settle()
 await shot(12,"04  从画布移除卡片，原资产和版本仍保留")
 panel.api_request("manual.undo",{},func(_r): panel.refresh())
 await settle()
 await shot(12,"04  撤销：恢复卡片位置与连线")
 await select_board("reuse")
 panel._canvases.open_library(false)
 panel._canvases.fill_library("报刊亭 · 3D")
 var items = panel._canvases.list
 if items.item_count > 0:
  items.select(0)
  items.multi_selected.emit(0,true)
 await settle()
 await shot(16,"04  切换画布，从共享资源池复用同一个资产")
 panel._canvases.add_selected()
 await settle()
 panel._fit_graph()
 await shot(16,"04  添加的是同一资产引用，不复制模型、不丢失版本")
 print("WORKFLOW_CAPTURE_COMPLETE")
 quit()

func press_cyan(node: Node) -> void:
 for child in node.get_children():
  if child is Button and child.modulate == Color.CYAN:
   child.pressed.emit()
   return
  press_cyan(child)
