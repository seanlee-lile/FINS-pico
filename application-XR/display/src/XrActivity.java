package com.iwin.application;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.os.Bundle;
import android.util.Log;
import android.widget.Toast;
import com.iwin.communication.RealtimeVideoStream;
import com.iwin.communication.VideoStream;

/** OpenXR 入口。复用握手/输入业务，只替换视频输出；不把二维窗口冒充 XR。 */
@androidx.media3.common.util.UnstableApi
public final class XrActivity extends MainActivity {
    static { System.loadLibrary("iwin_xr"); }
    private final Object rendererLock = new Object();
    private long rendererHandle;
    private Thread rendererThread;
    private boolean resumed;
    private boolean destroyed;
    private ControllerStatusHud controllerHud;
    private long lastHudUpdateAt;

    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved);
        rendererHandle = nativeCreate();
        controllerHud = new ControllerStatusHud();
        updateControllerHud(new float[21], 0, 0);
        showMessage("IWIN LAN XR", "请在 PC 控制台下发地址并开始推流");
    }

    @Override protected VideoStream createVideo() {
        return new RealtimeVideoStream(this, this::submitFrame);
    }

    private void submitFrame(Bitmap frame) {
        synchronized (rendererLock) {
            if (rendererHandle != 0) nativeSubmitFrame(rendererHandle, frame);
        }
    }

    private void showMessage(String title, String subtitle) {
        Bitmap image = Bitmap.createBitmap(1280, 720, Bitmap.Config.ARGB_8888);
        Canvas canvas = new Canvas(image);
        canvas.drawColor(Color.rgb(8, 14, 24));
        Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
        paint.setColor(Color.WHITE); paint.setTextSize(64);
        canvas.drawText(title, 70, 290, paint);
        paint.setTextSize(30); paint.setColor(0xff70d9c4);
        canvas.drawText(subtitle, 70, 370, paint);
        submitFrame(image); image.recycle();
    }

    @Override protected void onResume() {
        super.onResume();
        resumed = true;
        startRendererWhenAvailable();
        // 摘下头显时父类 onStop 会断开；再次戴上后按已保存地址重新握手。
        connect();
    }

    private void startRendererWhenAvailable() {
        if (!resumed || destroyed) return;
        if (rendererThread != null && rendererThread.isAlive()) {
            getWindow().getDecorView().postDelayed(this::startRendererWhenAvailable, 100);
            return;
        }
        final long handle = rendererHandle;
        nativeStart(handle);
        rendererThread = new Thread(() -> {
            String failure = nativeRun(handle);
            if (failure != null) runOnUiThread(() -> {
                if (!destroyed) {
                    Log.e("IWIN_XR", failure);
                    Toast.makeText(this, "OpenXR 启动失败：" + failure, Toast.LENGTH_LONG).show();
                }
            });
        }, "IwinOpenXR");
        rendererThread.start();
    }

    @Override protected void onPause() {
        resumed = false;
        updateControllerHud(new float[21], 0, 0);
        if (rendererHandle != 0) nativeStop(rendererHandle);
        super.onPause();
    }

    @Override protected void onDestroy() {
        destroyed = true;
        final long handle;
        synchronized (rendererLock) { handle = rendererHandle; rendererHandle = 0; }
        nativeStop(handle);
        final Thread previous = rendererThread;
        new Thread(() -> {
            try { if (previous != null) previous.join(); }
            catch (InterruptedException error) { Thread.currentThread().interrupt(); return; }
            nativeDestroy(handle);
        }, "IwinXrCleanup").start();
        if (controllerHud != null) controllerHud.close();
        super.onDestroy();
    }

    // JNI 在 XR 线程读取控制器后回调，业务状态仍统一由主线程更新。
    @SuppressWarnings("unused")
    private void onXrController(float[] values, int buttons, int available) {
        runOnUiThread(() -> {
            if (!resumed || destroyed) return;
            acceptXrController(values, buttons);
            long now = android.os.SystemClock.uptimeMillis();
            if (now - lastHudUpdateAt >= 33) {
                updateControllerHud(values, buttons, available);
                lastHudUpdateAt = now;
            }
        });
    }

    private void updateControllerHud(float[] values, int buttons, int available) {
        if (controllerHud == null || destroyed) return;
        Bitmap image = controllerHud.render(values, buttons, available);
        synchronized (rendererLock) {
            if (rendererHandle != 0) nativeSubmitHud(rendererHandle, image);
        }
    }

    private static native long nativeCreate();
    private static native void nativeStart(long handle);
    private native String nativeRun(long handle);
    private static native void nativeStop(long handle);
    private static native void nativeDestroy(long handle);
    private static native void nativeSubmitFrame(long handle, Bitmap bitmap);
    private static native void nativeSubmitHud(long handle, Bitmap bitmap);
}
