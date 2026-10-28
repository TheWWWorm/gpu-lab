# SPDX-License-Identifier: Apache-2.0
extends SceneTree

func _initialize() -> void:
	print("GPU_LAB_RENDERER: ", RenderingServer.get_video_adapter_name())
	call_deferred("show_pattern")

func show_pattern() -> void:
	for i in range(3):
		var rect := ColorRect.new()
		rect.position = Vector2(i * 266, 0)
		rect.size = Vector2(267, 600)
		rect.color = [Color.RED, Color.GREEN, Color.BLUE][i]
		root.add_child(rect)
	await create_timer(10.0).timeout
	quit()
