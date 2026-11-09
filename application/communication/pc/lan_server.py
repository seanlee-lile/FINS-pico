"""PC 实时 MP4 推流、同流预览和键盘输入。仅使用 Python 标准库。

视频适配点：VideoPublisher；输入适配点：DirectionState。
HTTP 只暴露固定协议端点，不提供任意文件或目录访问。
"""
from __future__ import annotations

import argparse
import json
import queue
import socket
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

# 三个模块并列存放；从确定的项目路径加载输入适配器，不依赖当前工作目录。
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "input" / "pc"))
from keyboard_input import DirectionState, run_keyboard_window
from connection_registry import ConnectionRegistry


class VideoPublisher(Protocol):
    """新传输方案只需提供发布生命周期，不依赖键盘窗口。"""
    def start(self) -> None: ...
    def close(self) -> None: ...


def ffmpeg_command(executable: str, video: Path, output: Path, loop: bool,
                   sourcePreview=False, timecodeFont=None, isRov=False):
    command = [executable, "-hide_banner", "-loglevel", "warning", "-nostdin", "-y"]
    if isRov or str(video).endswith('.sdp'):
        command += ["-protocol_whitelist", "file,udp,rtp",
                    "-fflags", "nobuffer", "-flags", "low_delay",
                    "-i", str(video)]
    else:
        if loop:
            command += ["-stream_loop", "-1"]
        # GUI 在解码后按帧 PTS 实时调度；-re 仅控制数据包读取，可能造成解码帧成批到达。
        # 两路仍共用一个输入、同一个媒体时钟，不分别播放或重新读取 HLS。
        if sourcePreview:
            command += ["-threads", "2", "-thread_type", "slice", "-i", str(video)]
        else:
            command += ["-re", "-i", str(video)]
    if sourcePreview:
        sharedFilter = "setpts=PTS-STARTPTS,realtime"
        if timecodeFont:
            # 时间码在分流前绘制，两路同一源帧具有相同的连续媒体时间。
            font = str(timecodeFont).replace("\\", "/").replace(":", "\\:").replace("'", "\\'")
            sharedFilter += (f",drawtext=fontfile='{font}':text='T %{{pts\\:hms}}':"
                             "fontsize=h/20:fontcolor=white:box=1:boxcolor=black@0.7:"
                             "boxborderw=6:x=12:y=12")
        graph = (f"[0:v:0]{sharedFilter},split=2[streamSource][previewSource];"
                 "[streamSource]scale=trunc(iw/2)*2:trunc(ih/2)*2[streamVideo];"
                 "[previewSource]fps=30,scale=640:360:force_original_aspect_ratio=decrease,"
                 "pad=640:360:(ow-iw)/2:(oh-ih)/2[previewVideo]")
        command += ["-filter_complex", graph, "-map", "[streamVideo]", "-map", "0:a:0?"]
    else:
        command += ["-map", "0:v:0", "-map", "0:a:0?",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2"]
    command += ["-r", "30", "-c:v", "libx264", "-preset", "veryfast", "-tune", "zerolatency",
                "-pix_fmt", "yuv420p", "-crf", "23", "-g", "30", "-keyint_min", "30",
                "-sc_threshold", "0", "-c:a", "aac", "-b:a", "128k", "-ac", "2",
                "-f", "hls", "-hls_time", "1", "-hls_list_size", "6",
                "-hls_delete_threshold", "6",
                "-hls_flags", "delete_segments+independent_segments+temp_file",
                "-hls_segment_filename", str(output / "segment_%09d.ts"),
                str(output / "live.m3u8")]
    if sourcePreview:
        # 源帧直接经管道交给窗口：不经过 H.264 编码、HLS 分片或 HTTP 回放。
        command += ["-map", "[previewVideo]", "-an", "-c:v", "ppm", "-f", "image2pipe",
                    "-flush_packets", "1", "pipe:1"]
    return command


def stop_process(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


class HlsPublisher:
    def __init__(self, executable, video, output, loop, logOutput=None,
                 sourcePreview=False, timecodeFont=None):
        self.logOutput = logOutput
        self.sourcePreview = sourcePreview
        self.command = ffmpeg_command(executable, video, output, loop, sourcePreview, timecodeFont)
        self.process = None

    def start(self):
        self.process = subprocess.Popen(
            self.command, stderr=self.logOutput,
            stdout=subprocess.PIPE if self.sourcePreview else None,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def close(self):
        stop_process(self.process)


def handler_factory(directory: Path, state: DirectionState, connections=None, streamHub=None, picoReceiver=None):
    connections = connections if connections is not None else ConnectionRegistry()

    def videoReady():
        return streamHub.isReady() if streamHub else (directory / 'live.m3u8').is_file()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            path = urlsplit(self.path).path
            if path == "/input":
                token = self.headers.get('X-IWIN-Session')
                if token is not None and not connections.heartbeat(token, self.client_address[0]):
                    self.respond(409, 'application/json', b'{"error":"session_expired"}')
                    return
                payload = state.snapshot()
                payload['video_ready'] = videoReady()
                if picoReceiver:
                    payload['pico_controller'] = picoReceiver.snapshot()
                self.respond(200, "application/json; charset=utf-8", json.dumps(payload).encode("utf-8"))
                return
            if path == "/health":
                ready = videoReady()
                self.respond(200 if ready else 503, "application/json",
                             json.dumps({"video_ready": ready,
                                         "connected_clients": len(connections.connectedClients())}).encode())
                return
            if path == '/live.ts' and streamHub:
                if not streamHub.isReady():
                    self.respond(503, 'text/plain', b'Live stream not ready')
                    return
                channel = streamHub.subscribe()
                try:
                    self.connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                    self.connection.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 65536)
                    self.connection.settimeout(1)
                    self.send_response(200)
                    self.send_header('Content-Type', 'application/x-iwin-mjpeg')
                    self.send_header('Cache-Control', 'no-store')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    while not streamHub.closed:
                        try: createdAt, data = channel.get(timeout=1)
                        except queue.Empty:
                            if not streamHub.isReady(): break
                            continue
                        if data is None:
                            break
                        if time.monotonic() - createdAt > .08:
                            continue
                        self.wfile.write(data)
                        self.wfile.flush()
                except (OSError, TimeoutError):
                    pass
                finally:
                    streamHub.unsubscribe(channel)
                    self.close_connection = True
                return
            if path == "/hls/live.m3u8":
                target = directory / "live.m3u8"
                mime = "application/vnd.apple.mpegurl"
            elif re.fullmatch(r"/hls/segment_\d{9,}\.ts", path):
                target = directory / path.rsplit("/", 1)[1]
                mime = "video/mp2t"
            else:
                self.respond(404, "text/plain", b"Not found")
                return
            try:
                # 转码器使用临时文件 + rename；不会读取到写入一半的分片。
                body = target.read_bytes()
            except FileNotFoundError:
                self.respond(404, "text/plain", b"Stream not ready or segment expired")
                return
            self.respond(200, mime, body)

        def do_POST(self):
            if urlsplit(self.path).path != '/handshake':
                self.respond(404, 'application/json', b'{"error":"not_found"}')
                return
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 1 <= length <= 4096:
                    raise ValueError('invalid_body_length')
                request = json.loads(self.rfile.read(length))
                if not isinstance(request, dict):
                    raise ValueError('invalid_body')
                payload = connections.handshake(request, self.client_address[0])
                payload['video_transport'] = 'mpegts-http' if streamHub else 'hls'
                payload['video_ready'] = videoReady()
                self.respond(200, 'application/json', json.dumps(payload).encode('utf-8'))
            except (ValueError, UnicodeDecodeError) as error:
                self.respond(400, 'application/json', json.dumps({'error': str(error)}).encode('utf-8'))

        def respond(self, status, mime, body):
            try:
                self.send_response(status)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # 客户端重连或切后台无需污染控制台。

        def log_message(self, *_args):
            pass

    return Handler


class LanServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def get_request(self):
        sock, address = super().get_request()
        sock.settimeout(5)
        return sock, address


def main(argv=None):
    from realtime_stream import LiveStreamHub, RealtimePublisher
    parser = argparse.ArgumentParser(description="IWIN PC 实时视频推流与转运服务")
    parser.add_argument("video", nargs="?", default=None, type=Path, help="MP4 路径（MP4 模式需要；ROV 模式无需此项）")
    parser.add_argument("--channel", choices=["rov", "mp4"], default=None,
                        help="信道模式：rov（水下 ROV 实时转运，默认）或 mp4（本地 MP4 视频）")
    parser.add_argument("--rov-port", type=int, default=5600,
                        help="ROV 下位机发送端口（UDP，默认 5600，可选 5004）")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP 监听地址（默认 0.0.0.0）")
    parser.add_argument("--port", type=int, default=8080, help="HTTP 监听端口（默认 8080）")
    parser.add_argument("--once", action="store_true", help="只播放一次，默认循环（仅 MP4 模式）")
    parser.add_argument("--no-preview", action="store_true", help="不打开预览窗口")
    parser.add_argument("--headless", action="store_true", help="无头模式（不启动任何图形与键盘窗口）")
    parser.add_argument("--ffmpeg", default="ffmpeg", help="ffmpeg 可执行文件路径")
    args = parser.parse_args(argv)

    channel = args.channel
    if channel is None:
        channel = "mp4" if (args.video is not None and args.video.is_file()) else "rov"

    if channel == "mp4":
        if not args.video or not args.video.is_file():
            parser.error("MP4 模式需要指定有效的视频文件：例如 python lan_server.py test.mp4")
    else:
        if not 1 <= args.rov_port <= 65535:
            parser.error("ROV 端口必须在 1～65535 之间")

    if not 1 <= args.port <= 65535:
        parser.error("HTTP 端口必须在 1～65535 之间")

    executable = shutil.which(args.ffmpeg)
    if not executable:
        # 尝试查找工程自带的 ffmpeg
        candidate = Path(__file__).resolve().parents[2] / ".tools/python/imageio_ffmpeg/binaries/ffmpeg-win-x86_64-v7.1.exe"
        if candidate.is_file():
            executable = str(candidate)
        else:
            parser.error("未找到 ffmpeg，请安装 FFmpeg 或使用 --ffmpeg 指定路径")

    state = DirectionState()
    connections = ConnectionRegistry()
    streamHub = LiveStreamHub()

    with tempfile.TemporaryDirectory(prefix="iwin-session-") as temporary:
        directory = Path(temporary)
        server = LanServer((args.host, args.port),
                           handler_factory(directory, state, connections, streamHub))
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()

        if channel == "rov":
            sdpFile = directory / f"rov_{args.rov_port}.sdp"
            sdpContent = (
                "v=0\r\n"
                "o=- 0 0 IN IP4 127.0.0.1\r\n"
                "s=ROV Stream\r\n"
                "c=IN IP4 0.0.0.0\r\n"
                "t=0 0\r\n"
                f"m=video {args.rov_port} RTP/AVP 96\r\n"
                "a=rtpmap:96 H264/90000\r\n"
                "a=fmtp:96 packetization-mode=1\r\n"
            )
            sdpFile.write_text(sdpContent, encoding="ascii")
            videoSource = sdpFile
            isRov = True
        else:
            videoSource = args.video.resolve()
            isRov = False

        publisher = RealtimePublisher(executable, videoSource, directory,
                                      not args.once if not isRov else False,
                                      sourcePreview=False,
                                      streamHub=streamHub,
                                      isRov=isRov)

        try:
            publisher.start()
            print("=" * 60)
            print(f"IWIN 实时视频服务已启动！")
            if channel == "rov":
                print(f"当前信道：【水下 ROV 实时转运】")
                print(f"ROV 监听：UDP {args.rov_port}（接收 H.264 RTP 流）")
            else:
                print(f"当前信道：【本地 MP4 视频推流】")
                print(f"视频源：{videoSource}")
            print(f"服务端口：HTTP {args.host}:{args.port}")
            print(f"手机/Pico 地址：http://<这台机器的局域网IP>:{args.port}")
            print("=" * 60)
            print("按 Ctrl+C 可停止服务。\n", flush=True)

            lastClients = -1
            while True:
                time.sleep(1)
                code = publisher.process.poll()
                if code is not None:
                    raise RuntimeError(f"FFmpeg 进程退出，退出码: {code}")
                clients = len(connections.connectedClients())
                if clients != lastClients:
                    lastClients = clients
                    readyStr = "视频就绪" if streamHub.isReady() else "等待视频流输入"
                    print(f"[{time.strftime('%H:%M:%S')}] 状态: {readyStr} | 在线设备数: {clients}", flush=True)
        except KeyboardInterrupt:
            print("\n正在停止服务…", flush=True)
        finally:
            state.clear()
            publisher.close()
            streamHub.close()
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=3)
            print("服务已完全停止。", flush=True)


if __name__ == "__main__":
    main()
