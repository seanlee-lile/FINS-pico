package com.iwin.communication;

import com.iwin.input.Direction;
import com.iwin.input.DirectionSource;
import com.iwin.input.PicoControllerSnapshot;
import org.json.JSONArray;
import org.json.JSONObject;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;
import java.util.EnumSet;
import java.util.UUID;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;

/** 先握手建立会话，再轮询方向快照；每次轮询同时作为心跳。
 * 断线释放远端方向并重新握手。回调来自后台线程，由调用方切换 UI 线程。
 */
public final class HttpDirectionSource implements DirectionSource {
    private final String baseUrl;
    private final String clientId;
    private final String clientName;
    private final Consumer<Boolean> videoReady;
    private final ScheduledExecutorService worker = Executors.newSingleThreadScheduledExecutor();
    private volatile boolean closed;
    private volatile HttpURLConnection active;
    private String sessionId;
    private Boolean lastVideoReady;

    public HttpDirectionSource(String baseUrl) {
        this(baseUrl, UUID.randomUUID().toString(), "Android", ready -> {});
    }

    public HttpDirectionSource(String baseUrl, String clientId, String clientName, Consumer<Boolean> videoReady) {
        this.baseUrl = baseUrl;
        this.clientId = clientId;
        this.clientName = clientName;
        this.videoReady = videoReady;
    }

    @Override public void start(Listener listener) {
        worker.scheduleWithFixedDelay(() -> poll(listener), 0, 100, TimeUnit.MILLISECONDS);
    }

    private void poll(Listener listener) {
        if (closed) return;
        try {
            if (sessionId == null) {
                listener.onStatus("正在与 PC 握手…");
                String nonce = UUID.randomUUID().toString();
                JSONObject request = new JSONObject().put("protocol_version", 2)
                        .put("client_id", clientId).put("client_name", clientName).put("nonce", nonce);
                JSONObject response = exchange("/handshake", request);
                if (response.getInt("protocol_version") != 2 || !nonce.equals(response.getString("nonce"))) {
                    throw new IllegalStateException("握手响应不匹配");
                }
                if (!"mpegts-http".equals(response.optString("video_transport"))) {
                    throw new IllegalStateException("PC 尚未更新到实时视频协议");
                }
                String token = response.getString("session_id");
                if (!token.matches("[a-f0-9]{32}")) throw new IllegalStateException("会话无效");
                sessionId = token;
            }
            JSONObject response = exchange("/input", null);
            JSONArray array = response.getJSONArray("directions");
            EnumSet<Direction> directions = EnumSet.noneOf(Direction.class);
            for (int i = 0; i < array.length(); i++) directions.add(Direction.valueOf(array.getString(i)));
            if (!closed) {
                boolean ready = response.getBoolean("video_ready");
                listener.onDirections(directions);
                notifyVideoReady(ready);
                listener.onStatus(ready ? "握手成功 · 方向输入已连接" : "握手成功 · 等待 PC 开始推流");

                JSONObject picoObj = response.optJSONObject("pico_controller");
                if (picoObj != null && picoObj.optBoolean("connected", false)) {
                    PicoControllerSnapshot snapshot = new PicoControllerSnapshot();
                    snapshot.sequence = picoObj.optInt("sequence", 0);
                    snapshot.timestampMs = picoObj.optLong("timestamp_ms", System.currentTimeMillis());
                    JSONArray ls = picoObj.optJSONArray("left_stick");
                    if (ls != null && ls.length() >= 2) {
                        snapshot.leftStickX = (float) ls.optDouble(0, 0.0);
                        snapshot.leftStickY = (float) ls.optDouble(1, 0.0);
                    }
                    JSONArray rs = picoObj.optJSONArray("right_stick");
                    if (rs != null && rs.length() >= 2) {
                        snapshot.rightStickX = (float) rs.optDouble(0, 0.0);
                        snapshot.rightStickY = (float) rs.optDouble(1, 0.0);
                    }
                    JSONArray tr = picoObj.optJSONArray("triggers");
                    if (tr != null && tr.length() >= 2) {
                        snapshot.leftTrigger = (float) tr.optDouble(0, 0.0);
                        snapshot.rightTrigger = (float) tr.optDouble(1, 0.0);
                    }
                    snapshot.buttons = picoObj.optInt("buttons", 0);
                    listener.onControllerSnapshot(snapshot);
                }
            }
        } catch (Exception error) {
            sessionId = null;
            if (!closed) {
                listener.onDirections(Collections.emptySet());
                notifyVideoReady(false);
                listener.onStatus("PC 连接中断，正在重新握手…");
            }
        }
    }

    private void notifyVideoReady(boolean ready) {
        if (lastVideoReady == null || lastVideoReady != ready) {
            lastVideoReady = ready;
            videoReady.accept(ready);
        }
    }

    /** 所有 HTTP 请求串行执行；关闭时可中断当前请求。 */
    private JSONObject exchange(String path, JSONObject request) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(baseUrl + path).openConnection();
        active = connection;
        try {
            if (closed) throw new IllegalStateException("连接已关闭");
            connection.setConnectTimeout(1000);
            connection.setReadTimeout(1000);
            connection.setUseCaches(false);
            if (sessionId != null) connection.setRequestProperty("X-IWIN-Session", sessionId);
            if (request != null) {
                byte[] payload = request.toString().getBytes(StandardCharsets.UTF_8);
                connection.setRequestMethod("POST");
                connection.setDoOutput(true);
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                connection.setFixedLengthStreamingMode(payload.length);
                try (java.io.OutputStream output = connection.getOutputStream()) { output.write(payload); }
            }
            if (connection.getResponseCode() != 200) throw new IllegalStateException("HTTP " + connection.getResponseCode());
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            try (java.io.InputStream stream = connection.getInputStream()) {
                byte[] buffer = new byte[512];
                int count;
                while ((count = stream.read(buffer)) != -1) {
                    if (bytes.size() + count > 8192) throw new IllegalStateException("响应过大");
                    bytes.write(buffer, 0, count);
                }
            }
            return new JSONObject(new String(bytes.toByteArray(), StandardCharsets.UTF_8));
        } finally {
            connection.disconnect();
            active = null;
        }
    }

    @Override public void close() {
        closed = true;
        worker.shutdownNow();
        HttpURLConnection connection = active;
        if (connection != null) connection.disconnect();
    }
}
