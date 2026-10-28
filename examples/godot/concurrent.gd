# SPDX-License-Identifier: Apache-2.0
extends SceneTree

func _initialize() -> void:
	print("GPU_LAB_RENDERER: ", RenderingServer.get_video_adapter_name())
	root.title = "GPU Lab " + OS.get_environment("GPU_LAB_TEST_COLOR")
	root.window_input.connect(_on_input)
	call_deferred("show_color")

func show_color() -> void:
	var rect := ColorRect.new()
	rect.size = Vector2(800, 600)
	rect.color = Color(OS.get_environment("GPU_LAB_TEST_COLOR"))
	root.add_child(rect)
	await create_timer(70.0).timeout
	quit()

func _on_input(event: InputEvent) -> void:
	if event is InputEventKey and event.pressed and event.keycode == KEY_ENTER:
		print("GPU_LAB_ENTER_RECEIVED")
		quit()
