package com.iwin.application;

import android.app.Activity;
import android.content.Intent;
import android.graphics.Color;
import android.os.Build;
import android.os.Bundle;
import android.util.Log;
import android.view.Gravity;
import android.view.InputDevice;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.ViewGroup;
import android.view.WindowManager;
import android.widget.*;
import androidx.media3.common.util.UnstableApi;
import com.iwin.communication.HttpDirectionSource;
import com.iwin.communication.PicoUdpSender;
import com.iwin.communication.RealtimeVideoStream;
import com.iwin.communication.VideoStream;
import com.iwin.input.*;
import java.net.URI;
import java.util.Collections;
import java.util.EnumSet;
import java.util.Set;
import java.util.UUID;

/** 视频显示、PC 远程控制回显与 PICO 原生手柄 UDP 发送中枢。 */
@UnstableApi
public class MainActivity extends Activity {
    private static final String TAG = "PICO_INPUT";
    private final DirectionState state = new DirectionState();
    private final EnumSet<Direction> local = EnumSet.noneOf(Direction.class);
    private final PicoControllerSnapshot controllerSnapshot = new PicoControllerSnapshot();
    private AutoCompleteTextView address;
    private TextView videoStatus, inputStatus, overlay, controllerOverlay;
    private FrameLayout videoHost;
    private VideoStream video;
    private DirectionSource input;
    private PicoUdpSender udpSender;
    private int generation;
    private static final String CONFIGURE_ACTION = "com.iwin.application.CONFIGURE";

    private String lastEventDebug = "等待按键或摇杆事件…";
    private int eventCount = 0;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setFocusableInTouchMode(true);
        root.setPadding(16, 16, 16, 16);
        root.setOnApplyWindowInsetsListener((view, insets) -> {
            view.setPadding(16 + insets.getSystemWindowInsetLeft(), 16 + insets.getSystemWindowInsetTop(),
                    16 + insets.getSystemWindowInsetRight(), 16 + insets.getSystemWindowInsetBottom());
            return insets;
        });

        address = new AutoCompleteTextView(this);
        address.setSingleLine(true);
        address.setHint("PC 地址，例如 http://192.168.131.58:8080");
        address.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        String[] servers = {
            "http://192.168.131.58:8080",
            "http://10.181.183.234:8080",
            "http://100.84.247.10:8080",
            "http://192.168.0.10:8080"
        };
        address.setAdapter(new ArrayAdapter<>(this, android.R.layout.simple_dropdown_item_1line, servers));
        address.setThreshold(1);
        address.setOnClickListener(v -> address.showDropDown());
        String defaultServer = getPreferences(0).getString("server", "http://192.168.131.58:8080");
        address.setText(defaultServer);
        address.setOnFocusChangeListener((view, hasFocus) -> {
            if (hasFocus) {
                address.showDropDown();
                if (overlay != null) {
                    local.clear(); render(state.update("local", local));
                }
            }
        });
        root.addView(address);

        Button connect = new Button(this);
        connect.setText("连接 / 重新连接");
        connect.setOnClickListener(v -> connect());
        root.addView(connect);

        videoStatus = new TextView(this); videoStatus.setText("输入 PC 局域网地址后连接"); root.addView(videoStatus);
        inputStatus = new TextView(this); inputStatus.setText("方向输入未连接 · 手柄待命中"); root.addView(inputStatus);

        FrameLayout stage = new FrameLayout(this);
        videoHost = new FrameLayout(this);
        stage.addView(videoHost, new FrameLayout.LayoutParams(-1, -1));

        overlay = new TextView(this);
        overlay.setTextColor(Color.WHITE); overlay.setTextSize(20);
        overlay.setBackgroundColor(0xCC17212B); overlay.setPadding(18, 10, 18, 10);
        overlay.setText("方向：—");
        stage.addView(overlay, new FrameLayout.LayoutParams(-2, -2, Gravity.TOP | Gravity.END));

        controllerOverlay = new TextView(this);
        controllerOverlay.setTextColor(0xFF70D9C4); controllerOverlay.setTextSize(13);
        controllerOverlay.setBackgroundColor(0xDD0B132B); controllerOverlay.setPadding(16, 12, 16, 12);
        controllerOverlay.setText("PICO 手柄遥测监听就绪\n等待手柄输入…");
        stage.addView(controllerOverlay, new FrameLayout.LayoutParams(-2, -2, Gravity.BOTTOM | Gravity.START));

        root.addView(stage, new LinearLayout.LayoutParams(-1, 0, 1));
        setContentView(root);
        applyConnectionIntent(getIntent());
    }

    protected VideoStream createVideo() {
        if (isFinishing() || isDestroyed()) return null;
        return new RealtimeVideoStream(this);
    }

    private DirectionSource createInput(String base, int session) {
        String clientId = getPreferences(0).getString("clientId", null);
        if (clientId == null) {
            clientId = UUID.randomUUID().toString();
            getPreferences(0).edit().putString("clientId", clientId).apply();
        }
        return new HttpDirectionSource(base, clientId, Build.MANUFACTURER + " " + Build.MODEL,
                ready -> runOnUiThread(() -> {
                    if (session == generation) updateVideoReady(base, ready);
                }));
    }

    private void updateVideoReady(String base, boolean ready) {
        if (ready && video == null) {
            video = createVideo();
            if (video != null) {
                videoHost.addView(video.view(), new FrameLayout.LayoutParams(-1, -1));
                video.play(base + "/live.ts", message -> videoStatus.setText(message));
            }
        } else if (!ready) {
            if (video != null) { video.close(); video = null; }
            videoHost.removeAllViews();
            videoStatus.setText("等待 PC 视频就绪 / 网络恢复…");
        }
    }

    @Override protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        applyConnectionIntent(intent);
    }

    private void applyConnectionIntent(Intent intent) {
        if (intent == null || !CONFIGURE_ACTION.equals(intent.getAction())) return;
        String host = intent.getStringExtra("iwin_server_host");
        int port = intent.getIntExtra("iwin_server_port", -1);
        if (host == null || !host.matches("[a-zA-Z0-9.-]{1,253}") || port < 1 || port > 65535) {
            videoStatus.setText("ADB 配置无效：请检查 PC 地址和端口");
            return;
        }
        try {
            URI uri = new URI("http", null, host, port, null, null, null);
            if (uri.getHost() == null) throw new IllegalArgumentException();
            address.setText(uri.toString());
            getPreferences(0).edit().putString("server", uri.toString()).apply();
            if (intent.getBooleanExtra("iwin_connect", false)) connect();
            else {
                disconnect();
                inputStatus.setText("已接收 PC 配置，点击连接开始握手");
            }
        } catch (Exception error) {
            videoStatus.setText("ADB 配置中的地址无效");
        }
    }

    protected void connect() {
        String base;
        String pcHost;
        try {
            URI uri = URI.create(address.getText().toString().trim());
            if (!("http".equals(uri.getScheme()) || "https".equals(uri.getScheme())) || uri.getHost() == null
                    || uri.getUserInfo() != null || uri.getQuery() != null || uri.getFragment() != null
                    || !(uri.getPath().isEmpty() || uri.getPath().equals("/"))) throw new IllegalArgumentException();
            base = uri.getScheme() + "://" + uri.getRawAuthority();
            pcHost = uri.getHost();
        } catch (Exception error) {
            videoStatus.setText("地址无效，请填写 http://PC局域网IP:8080"); return;
        }
        disconnect();
        getPreferences(0).edit().putString("server", base).apply();
        final int session = generation;
        videoStatus.setText("正在与 PC 建立连接…");
        inputStatus.setText("正在发出握手信号，等待 PC 响应…");

        input = createInput(base, session);
        input.start(new DirectionSource.Listener() {
            @Override public void onDirections(Set<Direction> directions) {
                runOnUiThread(() -> { if (session == generation) render(state.update("remote", directions)); });
            }
            @Override public void onStatus(String message) {
                runOnUiThread(() -> {
                    if (session != generation) return;
                    inputStatus.setText(message);
                    // 仅在接收到 PC 握手确认信号后，建立双向同步的控制与手柄通路
                    if (message.startsWith("握手成功")) {
                        if (udpSender == null) {
                            try {
                                udpSender = new PicoUdpSender(pcHost, 54322);
                                udpSender.setListener(new PicoUdpSender.Listener() {
                                    @Override public void onPacketSent(int sequence, long timestampMs) {}
                                    @Override public void onError(String err) {
                                        runOnUiThread(() -> {
                                            if (session == generation) controllerOverlay.setText("UDP 错误: " + err);
                                        });
                                    }
                                });
                            } catch (Exception e) {
                                controllerOverlay.setText("手柄 UDP 初始化失败: " + e.getMessage());
                            }
                        }
                    } else if (message.contains("中断") || message.contains("重新握手")) {
                        // 通路异常断开时同步关闭发送通道，避免单向孤儿数据流
                        if (udpSender != null) {
                            udpSender.close();
                            udpSender = null;
                        }
                    }
                });
            }
            @Override public void onControllerSnapshot(PicoControllerSnapshot snapshot) {
                runOnUiThread(() -> {
                    if (session == generation && snapshot != null) {
                        controllerSnapshot.sequence = snapshot.sequence;
                        controllerSnapshot.leftStickX = snapshot.leftStickX;
                        controllerSnapshot.leftStickY = snapshot.leftStickY;
                        controllerSnapshot.rightStickX = snapshot.rightStickX;
                        controllerSnapshot.rightStickY = snapshot.rightStickY;
                        controllerSnapshot.leftTrigger = snapshot.leftTrigger;
                        controllerSnapshot.rightTrigger = snapshot.rightTrigger;
                        controllerSnapshot.buttons = snapshot.buttons;
                        renderControllerOverlay();
                    }
                });
            }
        });

        address.clearFocus();
        ((ViewGroup) address.getParent()).requestFocus();
        ((android.view.inputmethod.InputMethodManager) getSystemService(INPUT_METHOD_SERVICE))
                .hideSoftInputFromWindow(address.getWindowToken(), 0);
    }

    private void render(Set<Direction> directions) {
        StringBuilder text = new StringBuilder("方向：");
        for (Direction direction : directions) text.append(direction.symbol).append(' ');
        overlay.setText(directions.isEmpty() ? "方向：—" : text.toString());
    }

    private void renderControllerOverlay() {
        if (controllerOverlay == null) return;
        StringBuilder sb = new StringBuilder();
        sb.append(String.format("PICO 手柄遥测 (#%d)\n", controllerSnapshot.sequence));
        sb.append(String.format("左摇杆: (%+.2f, %+.2f)  右摇杆: (%+.2f, %+.2f)\n",
                controllerSnapshot.leftStickX, controllerSnapshot.leftStickY,
                controllerSnapshot.rightStickX, controllerSnapshot.rightStickY));
        sb.append(String.format("LT:%3d%% LG:%3d%% | RT:%3d%% RG:%3d%%\n",
                (int)(controllerSnapshot.leftTrigger * 100), (int)(controllerSnapshot.leftGrip * 100),
                (int)(controllerSnapshot.rightTrigger * 100), (int)(controllerSnapshot.rightGrip * 100)));

        sb.append("左: ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_X) ? "[X:按] " : "X:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_Y) ? "[Y:按] " : "Y:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_L3) ? "[L3:按] " : "L3:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_SELECT) ? "[BACK] " : "BACK:-- ")
          .append("\n");

        sb.append("右: ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_A) ? "[A:按] " : "A:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_B) ? "[B:按] " : "B:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_R3) ? "[R3:按] " : "R3:-- ")
          .append(controllerSnapshot.isButtonPressed(PicoControllerSnapshot.BUTTON_START) ? "[MENU] " : "MENU:-- ");

        controllerOverlay.setText(sb.toString());
    }

    @Override public boolean dispatchGenericMotionEvent(MotionEvent event) {
        eventCount++;
        float lx = event.getAxisValue(MotionEvent.AXIS_X);
        float ly = event.getAxisValue(MotionEvent.AXIS_Y);
        float rx = event.getAxisValue(MotionEvent.AXIS_Z);
        float ry = event.getAxisValue(MotionEvent.AXIS_RZ);
        float lt = Math.max(event.getAxisValue(MotionEvent.AXIS_LTRIGGER), event.getAxisValue(MotionEvent.AXIS_BRAKE));
        float rt = Math.max(event.getAxisValue(MotionEvent.AXIS_RTRIGGER), event.getAxisValue(MotionEvent.AXIS_GAS));

        lastEventDebug = String.format("MotionEvent: src=0x%X, act=%d, X=%.2f, Y=%.2f, Z=%.2f, RZ=%.2f",
                event.getSource(), event.getAction(), lx, ly, rx, ry);
        Log.i(TAG, lastEventDebug);

        // 不做过窄的 source 过滤，只要存在摇杆或扳机偏移均记录
        controllerSnapshot.leftStickX = Math.abs(lx) > 0.03f ? lx : 0f;
        controllerSnapshot.leftStickY = Math.abs(ly) > 0.03f ? ly : 0f;
        controllerSnapshot.rightStickX = Math.abs(rx) > 0.03f ? rx : 0f;
        controllerSnapshot.rightStickY = Math.abs(ry) > 0.03f ? ry : 0f;
        controllerSnapshot.leftTrigger = Math.max(0f, Math.min(1f, lt));
        controllerSnapshot.rightTrigger = Math.max(0f, Math.min(1f, rt));

        if (udpSender != null) {
            udpSender.updateAndSend(controllerSnapshot);
        }
        renderControllerOverlay();
        return super.dispatchGenericMotionEvent(event);
    }

    @Override public boolean dispatchTouchEvent(MotionEvent event) {
        // 当手柄以空间激光触控形式悬停/点击时也会更新调试状态
        if (event.getAction() == MotionEvent.ACTION_MOVE || event.getAction() == MotionEvent.ACTION_DOWN) {
            eventCount++;
            lastEventDebug = String.format("TouchEvent: src=0x%X, act=%d, Pos=(%.1f, %.1f)",
                    event.getSource(), event.getAction(), event.getX(), event.getY());
            renderControllerOverlay();
        }
        return super.dispatchTouchEvent(event);
    }

    @Override public boolean dispatchKeyEvent(KeyEvent event) {
        eventCount++;
        int keyCode = event.getKeyCode();
        int action = event.getAction();
        boolean isDown = action == KeyEvent.ACTION_DOWN;

        lastEventDebug = String.format("KeyEvent: keyCode=%d (%s), action=%d", keyCode, KeyEvent.keyCodeToString(keyCode), action);
        Log.i(TAG, lastEventDebug);

        if (address != null && address.hasFocus()) return super.dispatchKeyEvent(event);

        int btnMask = 0;
        switch (keyCode) {
            case KeyEvent.KEYCODE_BUTTON_A: btnMask = PicoControllerSnapshot.BUTTON_A; break;
            case KeyEvent.KEYCODE_BUTTON_B: btnMask = PicoControllerSnapshot.BUTTON_B; break;
            case KeyEvent.KEYCODE_BUTTON_X: btnMask = PicoControllerSnapshot.BUTTON_X; break;
            case KeyEvent.KEYCODE_BUTTON_Y: btnMask = PicoControllerSnapshot.BUTTON_Y; break;
            case KeyEvent.KEYCODE_BUTTON_L1: btnMask = PicoControllerSnapshot.BUTTON_L1; break;
            case KeyEvent.KEYCODE_BUTTON_R1: btnMask = PicoControllerSnapshot.BUTTON_R1; break;
            case KeyEvent.KEYCODE_BUTTON_THUMBL: btnMask = PicoControllerSnapshot.BUTTON_L3; break;
            case KeyEvent.KEYCODE_BUTTON_THUMBR: btnMask = PicoControllerSnapshot.BUTTON_R3; break;
            case KeyEvent.KEYCODE_BUTTON_START:
            case KeyEvent.KEYCODE_MENU:
                btnMask = PicoControllerSnapshot.BUTTON_START; break;
            case KeyEvent.KEYCODE_BUTTON_SELECT:
            case KeyEvent.KEYCODE_BACK:
                btnMask = PicoControllerSnapshot.BUTTON_SELECT; break;

            case KeyEvent.KEYCODE_DPAD_UP:
                btnMask = PicoControllerSnapshot.BUTTON_DPAD_UP;
                if (isDown) local.add(Direction.UP); else local.remove(Direction.UP);
                render(state.update("local", local));
                break;
            case KeyEvent.KEYCODE_DPAD_DOWN:
                btnMask = PicoControllerSnapshot.BUTTON_DPAD_DOWN;
                if (isDown) local.add(Direction.DOWN); else local.remove(Direction.DOWN);
                render(state.update("local", local));
                break;
            case KeyEvent.KEYCODE_DPAD_LEFT:
                btnMask = PicoControllerSnapshot.BUTTON_DPAD_LEFT;
                if (isDown) local.add(Direction.LEFT); else local.remove(Direction.LEFT);
                render(state.update("local", local));
                break;
            case KeyEvent.KEYCODE_DPAD_RIGHT:
                btnMask = PicoControllerSnapshot.BUTTON_DPAD_RIGHT;
                if (isDown) local.add(Direction.RIGHT); else local.remove(Direction.RIGHT);
                render(state.update("local", local));
                break;
        }

        if (btnMask != 0) {
            controllerSnapshot.setButton(btnMask, isDown);
        }

        if (udpSender != null) {
            udpSender.updateAndSend(controllerSnapshot);
        }
        renderControllerOverlay();
        return super.dispatchKeyEvent(event);
    }

    @Override public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (!hasFocus && overlay != null) {
            local.clear(); render(state.update("local", local));
        }
    }

    /** OpenXR 控制器快照沿用原有 UDP 协议；仅替换输入采集来源。 */
    protected void acceptXrController(float[] values, int buttons) {
        controllerSnapshot.leftStickX = values[0]; controllerSnapshot.leftStickY = values[1];
        controllerSnapshot.rightStickX = values[2]; controllerSnapshot.rightStickY = values[3];
        controllerSnapshot.leftTrigger = values[4]; controllerSnapshot.rightTrigger = values[5];
        controllerSnapshot.leftGrip = values[6]; controllerSnapshot.rightGrip = values[7];
        controllerSnapshot.posX = values[8]; controllerSnapshot.posY = values[9]; controllerSnapshot.posZ = values[10];
        controllerSnapshot.rotX = values[11]; controllerSnapshot.rotY = values[12];
        controllerSnapshot.rotZ = values[13]; controllerSnapshot.rotW = values[14];
        controllerSnapshot.linearVelX = values[15]; controllerSnapshot.linearVelY = values[16];
        controllerSnapshot.linearVelZ = values[17]; controllerSnapshot.angularVelX = values[18];
        controllerSnapshot.angularVelY = values[19]; controllerSnapshot.angularVelZ = values[20];
        controllerSnapshot.buttons = buttons;
        if (udpSender != null) udpSender.updateAndSend(controllerSnapshot);
    }

    private void disconnect() {
        generation++;
        if (udpSender != null) {
            udpSender.close();
            udpSender = null;
        }
        if (input != null) { input.close(); input = null; }
        if (video != null) { video.close(); video = null; }
        videoHost.removeAllViews();
        local.clear(); state.update("local", local);
        render(state.update("remote", Collections.emptySet()));
    }

    @Override protected void onStop() {
        disconnect();
        videoStatus.setText("播放已停止，返回后点击连接");
        inputStatus.setText("方向与手柄输入已断开");
        super.onStop();
    }
}
