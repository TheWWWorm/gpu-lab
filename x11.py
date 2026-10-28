# SPDX-License-Identifier: Apache-2.0
"""X11 operations, always addressed to the lab display supplied by the daemon."""
import ctypes as C
import io
import struct
import subprocess

from PIL import Image


def screenshot(env, path):
    data = subprocess.run(["xwd", "-root", "-silent"], env=env,
                          capture_output=True, check=True, timeout=15).stdout
    h = struct.unpack(">25I", data[:100])
    size, version, fmt, depth, width, height = h[:6]
    byte_order, bpp, stride = h[7], h[11], h[12]
    masks, colors = h[14:17], h[19]
    if version != 7 or fmt != 2 or bpp not in (24, 32) or masks != (0xff0000, 0xff00, 0xff):
        raise ValueError(f"Unsupported XWD pixel format: version={version}, format={fmt}, depth={depth}, bpp={bpp}, masks={masks}, dimensions={width}x{height}")
    offset = size + colors * 12
    # NVIDIA's Vulkan presentation can return packed 24-bit pixels in rows
    # padded to the original 32-bit stride. Honor both fields independently.
    mode = ("BGR" if byte_order == 0 else "RGB") if bpp == 24 else ("BGRX" if byte_order == 0 else "XRGB")
    img = Image.frombytes("RGB", (width, height), data[offset:offset+stride*height],
                          "raw", mode, stride)
    img.save(path)
    img.thumbnail((1600, 1200))
    preview = io.BytesIO()
    img.save(preview, format="PNG")
    return {"path": str(path), "width": width, "height": height,
            "preview_width": img.width, "preview_height": img.height}, preview.getvalue()


class Input:
    def __init__(self, display):
        self.x = C.CDLL("libX11.so.6")
        self.t = C.CDLL("libXtst.so.6")
        self.x.XOpenDisplay.argtypes = [C.c_char_p]
        self.x.XOpenDisplay.restype = C.c_void_p
        self.x.XCloseDisplay.argtypes = [C.c_void_p]
        self.x.XFlush.argtypes = [C.c_void_p]
        self.x.XStringToKeysym.argtypes = [C.c_char_p]
        self.x.XStringToKeysym.restype = C.c_ulong
        self.x.XKeysymToKeycode.argtypes = [C.c_void_p, C.c_ulong]
        self.x.XKeysymToKeycode.restype = C.c_ubyte
        self.x.XSetInputFocus.argtypes = [C.c_void_p, C.c_ulong, C.c_int, C.c_ulong]
        self.x.XRaiseWindow.argtypes = [C.c_void_p, C.c_ulong]
        self.t.XTestFakeKeyEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.t.XTestFakeButtonEvent.argtypes = [C.c_void_p, C.c_uint, C.c_int, C.c_ulong]
        self.t.XTestFakeMotionEvent.argtypes = [C.c_void_p, C.c_int, C.c_int, C.c_int, C.c_ulong]
        self.d = self.x.XOpenDisplay(display.encode())
        if not self.d:
            raise RuntimeError("Cannot open the lab display")

    def close(self):
        self.x.XFlush(self.d)
        self.x.XCloseDisplay(self.d)

    def perform(self, events):
        # Validate the complete batch before sending any input.
        parsed = []
        for e in events:
            kind = e.get("type")
            if kind == "key":
                keys = e["key"].split("+")
                codes = [self.x.XKeysymToKeycode(self.d, self.x.XStringToKeysym(k.encode())) for k in keys]
                if not all(codes) or len(codes) > 8:
                    raise ValueError("Unknown X11 key; use names such as Return, Escape, Control_L+a")
                parsed.append((kind, codes))
            elif kind == "move":
                x, y = int(e["x"]), int(e["y"])
                if not (0 <= x < 8192 and 0 <= y < 8192):
                    raise ValueError("Invalid pointer coordinate")
                parsed.append((kind, (x, y)))
            elif kind == "click":
                button = int(e.get("button", 1))
                if not 1 <= button <= 9:
                    raise ValueError("Invalid pointer button")
                parsed.append((kind, button))
            else:
                raise ValueError("Supported events: key, move, click")
        for kind, value in parsed:
            if kind == "key":
                for code in value:
                    self.t.XTestFakeKeyEvent(self.d, code, 1, 0)
                for code in reversed(value):
                    self.t.XTestFakeKeyEvent(self.d, code, 0, 0)
            elif kind == "move":
                self.t.XTestFakeMotionEvent(self.d, -1, *value, 0)
            elif kind == "click":
                self.t.XTestFakeButtonEvent(self.d, value, 1, 0)
                self.t.XTestFakeButtonEvent(self.d, value, 0, 0)
        self.x.XFlush(self.d)


if __name__ == "__main__":
    import json
    import os
    import sys
    controller = Input(os.environ["DISPLAY"])
    try:
        controller.perform(json.load(sys.stdin))
    finally:
        controller.close()
