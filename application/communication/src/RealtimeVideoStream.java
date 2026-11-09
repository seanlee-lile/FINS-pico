package com.iwin.communication;

import android.content.Context;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Color;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.ImageView;
import androidx.media3.common.util.UnstableApi;
import java.io.DataInputStream;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * 逐帧 MJPEG 极速实时流播放器：
 * 绕过 ExoPlayer / MediaCodec 缓冲管线，直接接收 (4字节长度 + JPEG) 帧并在收到瞬间解码显示，
 * 零缓冲队列，端到端延迟压缩至单帧级别（< 50ms）。
 */
@UnstableApi
public final class RealtimeVideoStream implements VideoStream {
    private final ImageView view;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private volatile boolean closed;
    private volatile HttpURLConnection activeConnection;
    private Thread streamThread;
    private Listener listener;

    public RealtimeVideoStream(Context context) {
        view = new ImageView(context);
        view.setBackgroundColor(Color.BLACK);
        view.setScaleType(ImageView.ScaleType.FIT_CENTER);
    }

    @Override
    public View view() {
        return view;
    }

    @Override
    public void play(String url, Listener listener) {
        this.listener = listener;
        if (streamThread != null) {
            streamThread.interrupt();
        }
        streamThread = new Thread(() -> runStreamLoop(url), "IwinMjpegReader");
        streamThread.setDaemon(true);
        streamThread.start();
    }

    private void postStatus(String message) {
        mainHandler.post(() -> {
            if (!closed && listener != null) {
                listener.onStatus(message);
            }
        });
    }

    private void runStreamLoop(String url) {
        byte[] packetBuffer = new byte[128 * 1024];
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inPreferredConfig = Bitmap.Config.RGB_565;

        while (!closed && !Thread.currentThread().isInterrupted()) {
            HttpURLConnection connection = null;
            try {
                postStatus("实时帧流连接中…");
                connection = (HttpURLConnection) new URL(url).openConnection();
                activeConnection = connection;
                connection.setConnectTimeout(1000);
                connection.setReadTimeout(1500);
                connection.setUseCaches(false);
                if (connection.getResponseCode() != 200) {
                    throw new IllegalStateException("HTTP " + connection.getResponseCode());
                }
                boolean notifiedPlaying = false;
                try (InputStream raw = connection.getInputStream();
                     DataInputStream input = new DataInputStream(raw)) {
                    while (!closed && !Thread.currentThread().isInterrupted()) {
                        int frameLength = input.readInt();
                        if (frameLength <= 0 || frameLength > 2 * 1024 * 1024) {
                            throw new IllegalStateException("Invalid frame size: " + frameLength);
                        }
                        if (packetBuffer.length < frameLength) {
                            packetBuffer = new byte[frameLength];
                        }
                        input.readFully(packetBuffer, 0, frameLength);
                        Bitmap bitmap = BitmapFactory.decodeByteArray(packetBuffer, 0, frameLength, options);
                        if (bitmap != null && !closed) {
                            if (!notifiedPlaying) {
                                notifiedPlaying = true;
                                postStatus("实时视频播放中（逐帧零缓冲）");
                            }
                            mainHandler.post(() -> {
                                if (!closed) {
                                    view.setImageBitmap(bitmap);
                                }
                            });
                        }
                    }
                }
            } catch (Exception error) {
                if (closed) break;
                postStatus("实时流重连中…");
                try {
                    Thread.sleep(100);
                } catch (InterruptedException interrupted) {
                    break;
                }
            } finally {
                activeConnection = null;
                if (connection != null) {
                    connection.disconnect();
                }
            }
        }
    }

    @Override
    public void close() {
        closed = true;
        listener = null;
        mainHandler.removeCallbacksAndMessages(null);
        HttpURLConnection connection = activeConnection;
        if (connection != null) {
            new Thread(connection::disconnect).start();
        }
        if (streamThread != null) {
            streamThread.interrupt();
        }
    }
}
