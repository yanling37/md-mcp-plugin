"""Verify 3D frame receipts cannot substitute a different viewport or PNG."""
import base64
import hashlib
from pathlib import Path
import struct
import sys
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from md_mcp_plugin.embedded import world_frame


def png_fixture():
    def chunk(name, payload):
        return struct.pack('>I', len(payload)) + name + payload + struct.pack('>I', zlib.crc32(name + payload))

    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 1, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\x00\x10\x20\x30\x40\x50\x60')) + chunk(b'IEND', b''))


class FrameReceiptTests(unittest.TestCase):
    def test_png_requires_bound_view_dimensions_and_closed_native_capture(self):
        png = png_fixture()
        fingerprint = 'a' * 64
        identity = {'pid': 12, 'creation_filetime': 34}
        observed = {'view': '3d', 'mapping_fingerprint': fingerprint, 'scene_item_grabber_absent': True}
        view = {'mapping_fingerprint': fingerprint, 'mapping': {'viewport_size': [2, 1], 'device_pixel_ratio': 1}}
        receipt = {'schema': world_frame.SCHEMA, 'operation': 'capture', 'return_code': 0, 'status': 'SUCCEEDED',
                   'process_id': 12, 'process_creation_filetime': '34', 'verified': True, 'effects_started': False,
                   'requires_recovery': False, 'capture_effects_started': True, 'gl_state_restored': True,
                   'background_endpoints_verified': True, 'OS_input_calls': 0, 'focus_calls': 0,
                   'activation_calls': 0, 'camera_switch_calls': 0, 'model_setter_calls': 0,
                   'before': observed.copy(), 'after': observed.copy(), 'png_base64': base64.b64encode(png).decode('ascii'),
                   'image': {'format': 'PNG', 'pixel_origin': 'top_left_after_CPU_vertical_flip', 'viewport_size': [2, 1],
                             'device_pixel_ratio': 1, 'mapping_fingerprint': fingerprint, 'width': 2, 'height': 1,
                             'sha256': hashlib.sha256(png).hexdigest(), 'byte_count': len(png)}}
        self.assertEqual(world_frame._frame_bytes(receipt, 0, identity, view), png)
        changes = ((receipt['after'], 'mapping_fingerprint', 'b' * 64),
                   (receipt['image'], 'width', 3), (receipt, 'gl_state_restored', False))
        for target, key, value in changes:
            with self.subTest(key=key):
                original = target[key]
                target[key] = value
                with self.assertRaises(ValueError):
                    world_frame._frame_bytes(receipt, 0, identity, view)
                target[key] = original
