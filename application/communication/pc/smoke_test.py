"""真实编码/HTTP/解码冒烟测试。需要 FFmpeg（无须 ffplay 或 Android）。

运行：python smoke_test.py --ffmpeg C:/ffmpeg/bin/ffmpeg.exe
生成短 MP4，按实时速度循环发布，通过 HTTP 解码 HLS，覆盖有声/无声源。
"""
import argparse
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time
from urllib.request import urlopen

from lan_server import DirectionState, HlsPublisher, LanServer, handler_factory


def check_variant(ffmpeg, audio):
    with tempfile.TemporaryDirectory(prefix="iwin-smoke-") as temporary:
        directory = Path(temporary)
        source = directory / "source with spaces.mp4"
        output = directory / "hls"
        output.mkdir()
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-f", "lavfi",
                   "-i", "testsrc=size=160x90:rate=30"]
        if audio:
            command += ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=44100"]
        command += ["-t", "3", "-c:v", "libx264", "-pix_fmt", "yuv420p"]
        if audio:
            command += ["-c:a", "aac"]
        command += [str(source)]
        subprocess.run(command, check=True, timeout=20)
        state = DirectionState()
        server = LanServer(("127.0.0.1", 0), handler_factory(output, state))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        publisher = HlsPublisher(ffmpeg, source, output, True)
        thread.start()
        try:
            publisher.start()
            deadline = time.monotonic() + 15
            playlist = output / "live.m3u8"
            while time.monotonic() < deadline:
                if publisher.process.poll() is not None:
                    raise AssertionError("Publisher stopped unexpectedly")
                if playlist.is_file() and "segment_000000003.ts" in playlist.read_text():
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("Live playlist did not advance across the 3-second MP4 loop")
            base = f"http://127.0.0.1:{server.server_port}"
            with urlopen(base + "/hls/live.m3u8", timeout=2) as response:
                manifest = response.read().decode()
            assert "#EXT-X-ENDLIST" not in manifest, "Looping stream must remain live"
            segment = re.findall(r"segment_\d+\.ts", manifest)[-1]
            with urlopen(base + "/hls/" + segment, timeout=2) as response:
                assert len(response.read()) > 188, "Transport stream is empty"
            # 实际经 HTTP 拉取播放列表和分片并解码，不能只验证文件存在。
            subprocess.run([ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
                            "-i", base + "/hls/live.m3u8", "-t", "2", "-f", "null", "-"],
                           check=True, timeout=30)
            print(f"PASS: audio={audio}, realtime MP4 loop -> HLS -> HTTP -> FFmpeg decode", flush=True)
        finally:
            publisher.close()
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ffmpeg", default="ffmpeg")
    args = parser.parse_args()
    executable = shutil.which(args.ffmpeg)
    if not executable:
        parser.error("FFmpeg not found")
    check_variant(executable, audio=True)
    check_variant(executable, audio=False)
