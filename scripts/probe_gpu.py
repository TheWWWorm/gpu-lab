# SPDX-License-Identifier: Apache-2.0
"""Exercise an actual GLX context and enumerate Vulkan/CUDA inside a lab job."""
import ctypes as C
import json
import os


def glx():
    x = C.CDLL("libX11.so.6")
    gl = C.CDLL("libGL.so.1")
    x.XOpenDisplay.argtypes = [C.c_char_p]; x.XOpenDisplay.restype = C.c_void_p
    x.XFree.argtypes = [C.c_void_p]
    x.XCloseDisplay.argtypes = [C.c_void_p]
    display = x.XOpenDisplay(os.environ["DISPLAY"].encode())
    assert display, "X display unavailable"
    gl.glXChooseFBConfig.argtypes = [C.c_void_p, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int)]
    gl.glXChooseFBConfig.restype = C.POINTER(C.c_void_p)
    attrs = (C.c_int*13)(0x8010, 4, 0x8011, 1, 8, 8, 9, 8, 10, 8, 11, 8, 0)
    count = C.c_int()
    configs = gl.glXChooseFBConfig(display, 0, attrs, C.byref(count))
    assert count.value, "No GLX framebuffer configuration"
    gl.glXCreateNewContext.argtypes = [C.c_void_p, C.c_void_p, C.c_int, C.c_void_p, C.c_int]
    gl.glXCreateNewContext.restype = C.c_void_p
    ctx = gl.glXCreateNewContext(display, configs[0], 0x8014, None, 1)
    assert ctx, "No GLX context"
    gl.glXCreatePbuffer.argtypes = [C.c_void_p, C.c_void_p, C.POINTER(C.c_int)]
    gl.glXCreatePbuffer.restype = C.c_ulong
    pbuf = gl.glXCreatePbuffer(display, configs[0], (C.c_int*5)(0x8041, 32, 0x8040, 32, 0))
    gl.glXMakeContextCurrent.argtypes = [C.c_void_p, C.c_ulong, C.c_ulong, C.c_void_p]
    assert gl.glXMakeContextCurrent(display, pbuf, pbuf, ctx)
    gl.glGetString.argtypes = [C.c_uint]; gl.glGetString.restype = C.c_char_p
    renderer = gl.glGetString(0x1f01).decode()
    gl.glClearColor.argtypes = [C.c_float]*4
    gl.glClearColor(1, .25, 0, 1); gl.glClear(0x4000); gl.glFinish()
    gl.glReadPixels.argtypes = [C.c_int, C.c_int, C.c_int, C.c_int, C.c_uint, C.c_uint, C.c_void_p]
    pixel = (C.c_ubyte*4)()
    gl.glReadPixels(0, 0, 1, 1, 0x1908, 0x1401, pixel)
    assert pixel[0] > 250 and 60 <= pixel[1] <= 68 and pixel[2] < 5, list(pixel)
    gl.glXMakeContextCurrent(display, 0, 0, None)
    gl.glXDestroyContext.argtypes = [C.c_void_p, C.c_void_p]
    gl.glXDestroyPbuffer.argtypes = [C.c_void_p, C.c_ulong]
    gl.glXDestroyContext(display, ctx); gl.glXDestroyPbuffer(display, pbuf)
    x.XFree(configs); x.XCloseDisplay(display)
    return {"renderer": renderer, "readback_rgba": list(pixel)}


def vulkan():
    class CreateInfo(C.Structure):
        _fields_ = [("sType", C.c_uint32), ("pNext", C.c_void_p), ("flags", C.c_uint32),
                    ("pApplicationInfo", C.c_void_p), ("enabledLayerCount", C.c_uint32),
                    ("ppEnabledLayerNames", C.c_void_p), ("enabledExtensionCount", C.c_uint32),
                    ("ppEnabledExtensionNames", C.c_void_p)]
    vk = C.CDLL("libvulkan.so.1")
    info = CreateInfo(sType=1)
    instance = C.c_void_p()
    vk.vkCreateInstance.argtypes = [C.POINTER(CreateInfo), C.c_void_p, C.POINTER(C.c_void_p)]
    assert vk.vkCreateInstance(C.byref(info), None, C.byref(instance)) == 0
    vk.vkEnumeratePhysicalDevices.argtypes = [C.c_void_p, C.POINTER(C.c_uint32), C.c_void_p]
    count = C.c_uint32()
    assert vk.vkEnumeratePhysicalDevices(instance, C.byref(count), None) == 0
    devices = (C.c_void_p*count.value)()
    assert vk.vkEnumeratePhysicalDevices(instance, C.byref(count), devices) == 0
    vk.vkGetPhysicalDeviceProperties.argtypes = [C.c_void_p, C.c_void_p]
    names = []
    for device in devices:
        properties = C.create_string_buffer(4096)
        vk.vkGetPhysicalDeviceProperties(device, properties)
        names.append(properties.raw[20:276].split(b"\0")[0].decode())
    vk.vkDestroyInstance.argtypes = [C.c_void_p, C.c_void_p]
    vk.vkDestroyInstance(instance, None)
    return names


def cuda():
    lib = C.CDLL("libcuda.so.1")
    assert lib.cuInit(0) == 0
    count = C.c_int()
    assert lib.cuDeviceGetCount(C.byref(count)) == 0
    names = []
    for dev in range(count.value):
        name = C.create_string_buffer(256)
        assert lib.cuDeviceGetName(name, 256, dev) == 0
        names.append(name.value.decode())
    return names


if __name__ == "__main__":
    result = {"glx": glx(), "vulkan": vulkan(), "cuda": cuda()}
    print(json.dumps(result, indent=2), flush=True)
    assert "NVIDIA" in result["glx"]["renderer"], result
    assert len(result["vulkan"]) == 1 and len(result["cuda"]) == 1, result
    assert result["vulkan"][0] == result["cuda"][0], result
    expected = os.environ.get("GPU_LAB_EXPECTED_MODEL")
    if expected:
        assert result["vulkan"] == [expected] and expected in result["glx"]["renderer"], result
