# SPDX-License-Identifier: Apache-2.0
import struct
import subprocess
import tempfile
from pathlib import Path
from unittest import TestCase, mock

from PIL import Image
from x11 import screenshot


class CaptureTests(TestCase):
    def test_packed_24_bit_pixels_with_padded_rows(self):
        self.capture(24, bytes([0,0,255, 0,255,0, 255,0,0])+b"\0"*3)

    def test_32_bit_pixels(self):
        self.capture(32, bytes([0,0,255,0, 0,255,0,0, 255,0,0,0]))

    def capture(self, bpp, row):
        header = [100,7,2,24,3,2,0,0,32,0,8,bpp,12,5,0xff0000,0xff00,0xff,8,256,0,3,2,0,0,0]
        data = struct.pack('>25I', *header)+row*2
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch('x11.subprocess.run', return_value=subprocess.CompletedProcess([],0,data,b'')):
                result, _ = screenshot({}, Path(tmp)/'screen.png')
            image = Image.open(result['path'])
            self.assertEqual([image.getpixel((x,1)) for x in range(3)], [(255,0,0),(0,255,0),(0,0,255)])
