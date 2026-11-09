"""连续 MJPEG 逐帧发布：每帧独立编码为 JPEG（4 字节长度头 + JPEG 字节流），
每个观众仅保留最新 1 帧，彻底消除流缓冲、探测等待与累积延迟。
"""
from __future__ import annotations
import os
from pathlib import Path
import queue
import socket
import struct
import subprocess
import threading
import time
from lan_server import ffmpeg_command, stop_process


class LiveStreamHub:
    """仅在回环地址接收 FFmpeg 逐帧 JPEG 输出；每个 HTTP 观众使用 maxsize=1 的最新帧槽位。"""
    def __init__(self):
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.listen(1)
        self.listener.settimeout(.5)
        self.port = self.listener.getsockname()[1]
        self.closed = False
        self.connected = False
        self.lastDataAt = 0.0
        self.receivedBytes = 0
        self.latestFrame = None
        self.viewers = set()
        self.lock = threading.Lock()
        self.encoderSocket = None
        self.thread = threading.Thread(target=self.receive, daemon=True)
        self.thread.start()

    def isReady(self):
        return not self.closed and self.connected and time.monotonic() - self.lastDataAt < 1

    def subscribe(self):
        channel = queue.Queue(maxsize=1)
        with self.lock:
            self.viewers.add(channel)
            if self.latestFrame is not None and self.isReady():
                try:
                    channel.put_nowait((self.lastDataAt, self.latestFrame))
                except queue.Full:
                    pass
        return channel

    def unsubscribe(self, channel):
        with self.lock:
            self.viewers.discard(channel)

    def broadcast(self, framePacket):
        now = time.monotonic()
        with self.lock:
            if framePacket is not None:
                self.latestFrame = framePacket
            for channel in list(self.viewers):
                if framePacket is None:
                    while not channel.empty():
                        try: channel.get_nowait()
                        except queue.Empty: break
                    try: channel.put_nowait((now, None))
                    except queue.Full: pass
                    continue
                if channel.full():
                    try: channel.get_nowait()
                    except queue.Empty: pass
                try: channel.put_nowait((now, framePacket))
                except queue.Full: pass

    def receive(self):
        try:
            while not self.closed:
                try:
                    connection, _address = self.listener.accept()
                except socket.timeout:
                    continue
                self.encoderSocket = connection
                connection.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                connection.settimeout(.5)
                self.connected = True
                buffer = bytearray()
                try:
                    while not self.closed:
                        try: data = connection.recv(65536)
                        except socket.timeout: continue
                        if not data: break
                        self.lastDataAt = time.monotonic()
                        self.receivedBytes += len(data)
                        buffer.extend(data)
                        # 从字节流中切出完整的 JPEG 帧 (FF D8 ... FF D9)
                        while True:
                            soi = buffer.find(b'\xff\xd8')
                            if soi < 0:
                                if len(buffer) > 1:
                                    del buffer[:-1]
                                break
                            if soi > 0:
                                del buffer[:soi]
                            eoi = buffer.find(b'\xff\xd9', 2)
                            if eoi < 0:
                                if len(buffer) > 2 * 1024 * 1024:
                                    del buffer[:]
                                break
                            jpegBytes = bytes(buffer[:eoi + 2])
                            del buffer[:eoi + 2]
                            packet = struct.pack('>I', len(jpegBytes)) + jpegBytes
                            self.broadcast(packet)
                finally:
                    self.connected = False
                    self.latestFrame = None
                    connection.close()
                    self.encoderSocket = None
                    self.broadcast(None)
        except OSError:
            pass

    def close(self):
        self.closed = True
        self.connected = False
        self.listener.close()
        connection = self.encoderSocket
        if connection:
            try: connection.shutdown(socket.SHUT_RDWR)
            except OSError: pass
        self.broadcast(None)
        self.thread.join(timeout=2)


class RealtimePublisher:
    """复用共同源帧/时间码图，将网络输出替换为逐帧 MJPEG 流（零帧间缓冲）。"""
    def __init__(self, executable, video, output, loop, logOutput=None,
                 sourcePreview=True, timecodeFont=None, streamHub=None,
                 isRov=False):
        if streamHub is None:
            raise ValueError('RealtimePublisher requires a LiveStreamHub')
        self.process = None
        self.logOutput = logOutput
        baseCommand = ffmpeg_command(executable, Path(video), Path(output), loop,
                                     sourcePreview, timecodeFont, isRov=isRov)
        outputStart = baseCommand.index('-r', baseCommand.index('-map'))
        previewStart = baseCommand.index('-map', outputStart) if sourcePreview else len(baseCommand)
        previewCommand = baseCommand[previewStart:]
        self.sourcePreview = sourcePreview
        # 逐帧 MJPEG 编码：无音频、无帧间预测、无 GOP 等待，编码一帧立即发出。
        prefix = baseCommand[:outputStart]
        # 手机端缩放至最高 720p，单帧 JPEG 约 25~45KB，Wi-Fi 传输耗时 < 2ms
        for index, token in enumerate(prefix):
            if '[streamSource]scale=' in token:
                prefix[index] = token.replace(
                    '[streamSource]scale=trunc(iw/2)*2:trunc(ih/2)*2[streamVideo]',
                    "[streamSource]fps=30,scale='min(1280,trunc(iw/2)*2)':-2[streamVideo]")
        if '-map' in prefix and '0:a:0?' in prefix:
            audioMapIndex = prefix.index('0:a:0?')
            del prefix[audioMapIndex - 1:audioMapIndex + 1]
        self.command = prefix + [
            '-an',
            '-c:v', 'mjpeg',
            '-pix_fmt', 'yuvj420p',
            '-q:v', '6',
            '-f', 'mjpeg',
            '-flush_packets', '1',
            f'tcp://127.0.0.1:{streamHub.port}?tcp_nodelay=1'
        ] + previewCommand

    def start(self):
        self.process = subprocess.Popen(self.command, stdout=subprocess.PIPE if self.sourcePreview else None, stderr=self.logOutput,
                                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)

    def close(self):
        stop_process(self.process)

