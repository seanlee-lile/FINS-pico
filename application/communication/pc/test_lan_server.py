"""不依赖 Android/FFmpeg 的协议及状态回归测试。"""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import urlopen

from lan_server import DirectionState, LanServer, handler_factory, ffmpeg_command


class DirectionTest(unittest.TestCase):
    def test_chords_repeat_release_and_focus_loss(self):
        state = DirectionState()
        state.set_pressed("UP", True)
        state.set_pressed("UP", True)
        state.set_pressed("LEFT", True)
        self.assertEqual(state.snapshot(), {"directions": ["LEFT", "UP"], "sequence": 2})
        state.set_pressed("UP", False)
        self.assertEqual(state.snapshot()["directions"], ["LEFT"])
        state.clear()
        self.assertEqual(state.snapshot()["directions"], [])
        with self.assertRaises(ValueError):
            state.set_pressed("FORWARD", True)

    def test_snapshot_is_detached(self):
        state = DirectionState()
        state.set_pressed("RIGHT", True)
        state.snapshot()["directions"].clear()
        self.assertEqual(state.snapshot()["directions"], ["RIGHT"])

    def test_command_realtime_and_paths_are_single_arguments(self):
        command = ffmpeg_command("ffmpeg", Path("video with spaces.mp4"), Path("output"), True)
        self.assertIn("-re", command)
        self.assertIn("-stream_loop", command)
        self.assertEqual(command[command.index("-i") + 1], "video with spaces.mp4")
        self.assertNotIn("-stream_loop", ffmpeg_command("ffmpeg", Path("v.mp4"), Path("o"), False))


class ProtocolTest(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.directory = Path(self.temporary.name)
        self.state = DirectionState()
        self.server = LanServer(("127.0.0.1", 0), handler_factory(self.directory, self.state))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temporary.cleanup()

    def get(self, path):
        return urlopen(self.base + path, timeout=2)

    def test_input_state_and_no_cache(self):
        self.state.set_pressed("DOWN", True)
        with self.get("/input") as response:
            self.assertEqual(json.load(response)["directions"], ["DOWN"])
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    def test_health_and_playlist_and_segment(self):
        with self.assertRaises(HTTPError) as error:
            self.get("/health")
        self.assertEqual(error.exception.code, 503)
        error.exception.close()
        (self.directory / "live.m3u8").write_text("#EXTM3U\n")
        (self.directory / "segment_000000001.ts").write_bytes(b"sample transport stream")
        with self.get("/health") as response:
            self.assertTrue(json.load(response)["video_ready"])
        with self.get("/hls/live.m3u8") as response:
            self.assertEqual(response.headers["Content-Type"], "application/vnd.apple.mpegurl")
        with self.get("/hls/segment_000000001.ts") as response:
            self.assertEqual(response.read(), b"sample transport stream")

    def test_cannot_read_other_files_or_temporary_segments(self):
        (self.directory / "secret.txt").write_text("private")
        for path in ["/", "/secret.txt", "/hls/../secret.txt", "/hls/%2e%2e/secret.txt",
                     "/hls/live.m3u8.tmp", "/hls/segment_000000001.ts.tmp", "/hls/segment_000000099.ts"]:
            with self.subTest(path=path), self.assertRaises(HTTPError) as error:
                self.get(path)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()


if __name__ == "__main__":
    unittest.main()
