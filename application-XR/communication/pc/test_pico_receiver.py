import unittest
import socket
import struct
import time
from pico_receiver import PicoControllerReceiver, PICO_PACKET_FMT, PICO_MAGIC

class TestPicoReceiver(unittest.TestCase):
    def test_pack_and_receive(self):
        receiver = PicoControllerReceiver(port=54323)
        receiver.start()
        try:
            # 伪造一个数据包
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            # buttons: A (1) + X (4) + START (256) = 261
            data = struct.pack(
                PICO_PACKET_FMT,
                PICO_MAGIC,
                42, # seq
                1700000000000, # ts
                0.75, -0.5, 0.25, -0.1, # sticks
                0.9, 0.1, 0.0, 0.8,     # triggers/grips
                261,                    # buttons
                0.1, 0.2, 0.3, 0.0, 0.0, 0.0, 1.0, # pose
                0.0, 0.0, 0.0, 0.0, 0.0, 0.0        # vel
            )
            sock.sendto(data, ("127.0.0.1", 54323))
            time.sleep(0.1)

            snap = receiver.snapshot()
            self.assertTrue(snap["connected"])
            self.assertEqual(snap["sequence"], 42)
            self.assertAlmostEqual(snap["left_stick"][0], 0.75, places=2)
            self.assertAlmostEqual(snap["left_stick"][1], -0.5, places=2)
            self.assertAlmostEqual(snap["triggers"][0], 0.9, places=2)
            self.assertIn("A", snap["pressed_buttons"])
            self.assertIn("X", snap["pressed_buttons"])
            self.assertIn("START", snap["pressed_buttons"])
        finally:
            receiver.close()

if __name__ == '__main__':
    unittest.main()
