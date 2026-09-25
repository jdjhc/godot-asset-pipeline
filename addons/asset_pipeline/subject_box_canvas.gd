@tool
extends Control
signal changed
var source: Image
var texture: ImageTexture
var _background: TextureRect
const PREVIEW_MAX_SIDE := 1600
var box := Rect2()
var color := Color.MAGENTA
var mode := ""
var anchor := Vector2.ZERO
var original := Rect2()

func _ready() -> void:
	resized.connect(queue_redraw)
	_ensure_background()

func set_image(image: Image) -> void:
	source = image
	var preview := image.duplicate() as Image
	var longest := maxi(preview.get_width(), preview.get_height())
	if longest > PREVIEW_MAX_SIDE:
		var ratio := float(PREVIEW_MAX_SIDE) / float(longest)
		preview.resize(maxi(1, roundi(preview.get_width() * ratio)), maxi(1, roundi(preview.get_height() * ratio)), Image.INTERPOLATE_BILINEAR)
	texture = ImageTexture.create_from_image(preview)
	_ensure_background()
	_background.texture = texture
	box = Rect2()
	queue_redraw()

func image_rect() -> Rect2:
	if source == null: return Rect2()
	var dims := Vector2(source.get_size())
	var scale_value := minf(size.x / dims.x, size.y / dims.y)
	return Rect2((size - dims * scale_value) / 2.0, dims * scale_value)

func pixel_point(point: Vector2) -> Vector2:
	var area := image_rect()
	return ((point - area.position) / area.size * Vector2(source.get_size())).clamp(Vector2.ZERO, Vector2(source.get_size()))

func _draw() -> void:
	if texture == null: return
	var area := image_rect()
	# Image is a separate cached draw item; dragging only redraws the outline.
	if box.has_area():
		var ratio := area.size / Vector2(source.get_size())
		var screen := Rect2(area.position + box.position * ratio, box.size * ratio)
		draw_rect(screen.grow(1), Color.BLACK, false, 5)
		draw_rect(screen, color, false, 3)
		for p in [screen.position, Vector2(screen.end.x, screen.position.y), screen.end, Vector2(screen.position.x, screen.end.y)]:
			draw_rect(Rect2(p - Vector2(5,5), Vector2(10,10)), color)

func _gui_input(event: InputEvent) -> void:
	if source == null: return
	if event is InputEventMouseButton and event.button_index == MOUSE_BUTTON_LEFT:
		if event.pressed:
			if not image_rect().has_point(event.position): return
			anchor = pixel_point(event.position)
			original = box
			var threshold := 12.0 * float(source.get_width()) / image_rect().size.x
			mode = "new"
			if box.has_area():
				var corners := [box.position, Vector2(box.end.x, box.position.y), box.end, Vector2(box.position.x, box.end.y)]
				for i in range(4):
					if anchor.distance_to(corners[i]) < threshold:
						mode = "resize"
						anchor = corners[(i + 2) % 4]
						break
				if mode == "new" and box.has_point(anchor): mode = "move"
		else:
			mode = ""
			changed.emit()
		accept_event()
	elif event is InputEventMouseMotion and not mode.is_empty():
		var point := pixel_point(event.position)
		if mode == "move":
			box.position = (original.position + point - anchor).clamp(Vector2.ZERO, Vector2(source.get_size()) - box.size)
		else:
			box = Rect2(anchor, point - anchor).abs()
		queue_redraw()
		changed.emit()
		accept_event()

func annotated() -> Image:
	var result := source.duplicate() as Image
	result.convert(Image.FORMAT_RGBA8)
	var bounds := Rect2i(Vector2i(box.position), Vector2i(box.size)).intersection(Rect2i(Vector2i.ZERO, result.get_size()))
	var thickness := maxi(3, roundi(float(maxi(result.get_width(), result.get_height())) / 350.0))
	result.fill_rect(Rect2i(bounds.position, Vector2i(bounds.size.x, mini(thickness,bounds.size.y))), color)
	result.fill_rect(Rect2i(Vector2i(bounds.position.x, maxi(bounds.position.y,bounds.end.y-thickness)), Vector2i(bounds.size.x,mini(thickness,bounds.size.y))), color)
	result.fill_rect(Rect2i(bounds.position, Vector2i(mini(thickness,bounds.size.x),bounds.size.y)), color)
	result.fill_rect(Rect2i(Vector2i(maxi(bounds.position.x,bounds.end.x-thickness),bounds.position.y), Vector2i(mini(thickness,bounds.size.x),bounds.size.y)), color)
	return result

func _ensure_background() -> void:
	if _background != null: return
	_background = TextureRect.new()
	_background.set_anchors_and_offsets_preset(Control.PRESET_FULL_RECT)
	_background.expand_mode = TextureRect.EXPAND_IGNORE_SIZE
	_background.stretch_mode = TextureRect.STRETCH_KEEP_ASPECT_CENTERED
	_background.mouse_filter = Control.MOUSE_FILTER_IGNORE
	_background.show_behind_parent = true
	add_child(_background)
