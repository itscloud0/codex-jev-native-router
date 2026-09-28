import io
import struct
import unittest

import cli_bridge


def client_frame(data: bytes, opcode: int = 1, fin: bool = True) -> bytes:
    mask = b"test"
    size = len(data)
    length = bytes([0x80 | size]) if size < 126 else b"\xfe" + struct.pack("!H", size)
    return bytes([(0x80 if fin else 0) | opcode]) + length + mask + bytes(
        value ^ mask[index & 3] for index, value in enumerate(data))


class WebSocketTests(unittest.TestCase):
    def test_bridge_failure_strips_alias_override(self):
        self.assertEqual(cli_bridge.fallback_args(["-m", "jev-auto", "resume", "--last"], "gpt-6-sol"),
                         ["-m", "gpt-6-sol", "resume", "--last"])
        self.assertEqual(cli_bridge.fallback_args(["-c", 'model="jev-shadow"'], "gpt-6-sol"),
                         ["-m", "gpt-6-sol"])
        self.assertEqual(cli_bridge.fallback_args(['--config=model="jev-auto"'], "gpt-6-sol"),
                         ["-m", "gpt-6-sol"])

    def test_masked_text_and_fragmentation(self):
        wire = client_frame(b'{"method":', fin=False) + client_frame(b'"initialize"}', opcode=0)
        self.assertEqual(cli_bridge.read_message(io.BytesIO(wire)), b'{"method":"initialize"}')

    def test_rejects_unmasked_or_oversize(self):
        with self.assertRaisesRegex(ValueError, "invalid WebSocket frame"):
            cli_bridge.read_message(io.BytesIO(b"\x81\x02hi"))
        with self.assertRaisesRegex(ValueError, "too large"):
            cli_bridge.read_message(io.BytesIO(b"\x81\xff" + struct.pack("!Q", cli_bridge.MAX_MESSAGE + 1)))

    def test_ping_returns_pong(self):
        class Sink:
            data = b""

            def sendall(self, data):
                self.data += data

        sink = Sink()
        payload = client_frame(b"alive", opcode=9) + client_frame(b"ok")
        self.assertEqual(cli_bridge.read_message(io.BytesIO(payload), sink), b"ok")
        self.assertEqual(sink.data, b"\x8a\x05alive")

    def test_handshake_requires_loopback_host(self):
        key = "dGhlIHNhbXBsZSBub25jZQ=="
        class Sink:
            data = b""

            def sendall(self, data):
                self.data += data

        good = (f"GET / HTTP/1.1\r\nHost: 127.0.0.1:1234\r\nUpgrade: websocket\r\n"
                f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n\r\n").encode()
        sink = Sink()
        cli_bridge._handshake(io.BytesIO(good), sink)
        self.assertIn(b"101 Switching Protocols", sink.data)
        with self.assertRaisesRegex(ValueError, "unauthorized"):
            cli_bridge._handshake(io.BytesIO(good), Sink(), "secret")
        authorized = good.replace(b"\r\n\r\n", b"\r\nAuthorization: Bearer secret\r\n\r\n")
        cli_bridge._handshake(io.BytesIO(authorized), Sink(), "secret")
        with self.assertRaisesRegex(ValueError, "invalid WebSocket upgrade"):
            cli_bridge._handshake(io.BytesIO(good.replace(b"127.0.0.1", b"example.com")), Sink())


if __name__ == "__main__":
    unittest.main()
