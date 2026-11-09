package com.iwin.application;

import android.graphics.Bitmap;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Typeface;
import com.iwin.input.PicoControllerSnapshot;
import java.util.Locale;

/** 可复用的手柄状态绘制器。只在主线程更新，复用位图，不依赖 PC 或视频是否就绪。 */
final class ControllerStatusHud implements AutoCloseable {
    private static final int TEXT = 0xffedf4ff;
    private static final int MUTED = 0xff92a5b8;
    private static final int ACTIVE = 0xff70e2bc;
    private final Bitmap bitmap = Bitmap.createBitmap(800, 388, Bitmap.Config.ARGB_8888);
    private final Canvas canvas = new Canvas(bitmap);
    private final Paint paint = new Paint(Paint.ANTI_ALIAS_FLAG);
    private int buttons;
    private int available;

    Bitmap render(float[] values, int buttons, int available) {
        this.buttons = buttons; this.available = available;
        canvas.drawColor(0xff101c2b);
        paint.setTypeface(Typeface.DEFAULT_BOLD);
        text("手柄状态 · OpenXR", 22, 34, 27, TEXT);
        text(available == 0 ? "等待输入 / 未获焦点" : "实时", 534, 34, 22, available == 0 ? MUTED : ACTIVE);
        paint.setTypeface(Typeface.DEFAULT);
        text("左手 LEFT", 22, 73, 23, TEXT);
        text("右手 RIGHT", 412, 73, 23, TEXT);
        button("X", 22, 88, PicoControllerSnapshot.BUTTON_X);
        button("Y", 212, 88, PicoControllerSnapshot.BUTTON_Y);
        button("A", 412, 88, PicoControllerSnapshot.BUTTON_A);
        button("B", 602, 88, PicoControllerSnapshot.BUTTON_B);
        button("L3", 22, 137, PicoControllerSnapshot.BUTTON_L3);
        button("菜单", 212, 137, PicoControllerSnapshot.BUTTON_START);
        button("R3", 412, 137, PicoControllerSnapshot.BUTTON_R3);
        text("PICO/Home: 系统保留", 602, 163, 17, MUTED);
        axis(values, 0, 22, 217); axis(values, 1, 412, 217);
        analog("扳机 LT", values[4], 18, 22, 261);
        analog("扳机 RT", values[5], 19, 412, 261);
        analog("握持 LG", values[6], 20, 22, 305);
        analog("握持 RG", values[7], 21, 412, 305);
        text("绿色 = 按下；模拟键按下阈值 ≥ 0.75；-- = 输入不可用", 22, 346, 20, MUTED);
        text("视野固定：视频与状态栏跟随头部，始终保持在视野中", 22, 376, 19, MUTED);
        return bitmap;
    }

    private void button(String label, int x, int y, int mask) {
        boolean usable = (available & mask) != 0;
        boolean down = usable && (buttons & mask) != 0;
        paint.setColor(down ? 0xff245d50 : 0xff26374b);
        canvas.drawRoundRect(x, y, x + 174, y + 39, 7, 7, paint);
        text(label + "  " + (!usable ? "--" : down ? "按下" : "松开"), x + 12, y + 27, 22,
                !usable ? MUTED : down ? ACTIVE : TEXT);
    }

    private void axis(float[] values, int hand, int x, int y) {
        boolean usable = (available & (1 << (16 + hand))) != 0;
        String value = usable ? String.format(Locale.ROOT, "摇杆 (%+.2f, %+.2f)", values[hand * 2], values[hand * 2 + 1])
                : "摇杆 (--, --)";
        text(value, x, y, 23, usable ? TEXT : MUTED);
    }

    private void analog(String label, float value, int availabilityBit, int x, int y) {
        boolean usable = (available & (1 << availabilityBit)) != 0;
        boolean down = usable && value >= .75f;
        String status = !usable ? "--" : String.format(Locale.ROOT, "%.2f  %s", value, down ? "按下" : "松开");
        text(label + "  " + status, x, y, 23, !usable ? MUTED : down ? ACTIVE : TEXT);
    }

    private void text(String content, int x, int y, int size, int color) {
        paint.setTextSize(size); paint.setColor(color);
        canvas.drawText(content, x, y, paint);
    }

    @Override public void close() { bitmap.recycle(); }
}
