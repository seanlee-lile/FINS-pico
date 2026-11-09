package com.iwin.input;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;

/**
 * PICO 手柄完整遥测快照，包含左右摇杆、模拟扳机/握持键、按键位掩码及 6DoF 空间运动学数据。
 * 支持固定 104 字节紧凑小端序二进制序列化/反序列化。
 */
public final class PicoControllerSnapshot {
    public static final int PACKET_MAGIC = 0x5049434F; // "PICO"
    public static final int PACKET_SIZE = 104;

    // 按键位掩码常量
    public static final int BUTTON_A = 1 << 0;
    public static final int BUTTON_B = 1 << 1;
    public static final int BUTTON_X = 1 << 2;
    public static final int BUTTON_Y = 1 << 3;
    public static final int BUTTON_L1 = 1 << 4; // LB
    public static final int BUTTON_R1 = 1 << 5; // RB
    public static final int BUTTON_L3 = 1 << 6; // 左摇杆按压
    public static final int BUTTON_R3 = 1 << 7; // 右摇杆按压
    public static final int BUTTON_START = 1 << 8;
    public static final int BUTTON_SELECT = 1 << 9; // BACK
    public static final int BUTTON_DPAD_UP = 1 << 10;
    public static final int BUTTON_DPAD_DOWN = 1 << 11;
    public static final int BUTTON_DPAD_LEFT = 1 << 12;
    public static final int BUTTON_DPAD_RIGHT = 1 << 13;
    public static final int BUTTON_TRIGGER_CLICK_L = 1 << 14;
    public static final int BUTTON_TRIGGER_CLICK_R = 1 << 15;

    public int sequence;
    public long timestampMs;

    public float leftStickX;
    public float leftStickY;
    public float rightStickX;
    public float rightStickY;

    public float leftTrigger;
    public float rightTrigger;
    public float leftGrip;
    public float rightGrip;

    public int buttons;

    // 6DoF 空间位姿 (米 / 四元数)
    public float posX;
    public float posY;
    public float posZ;
    public float rotX;
    public float rotY;
    public float rotZ;
    public float rotW = 1.0f;

    // 动力学量 (m/s, rad/s)
    public float linearVelX;
    public float linearVelY;
    public float linearVelZ;
    public float angularVelX;
    public float angularVelY;
    public float angularVelZ;

    public PicoControllerSnapshot() {
        this.timestampMs = System.currentTimeMillis();
    }

    public byte[] toBytes() {
        byte[] bytes = new byte[PACKET_SIZE];
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        buffer.putInt(PACKET_MAGIC);
        buffer.putInt(sequence);
        buffer.putLong(timestampMs);

        buffer.putFloat(leftStickX);
        buffer.putFloat(leftStickY);
        buffer.putFloat(rightStickX);
        buffer.putFloat(rightStickY);

        buffer.putFloat(leftTrigger);
        buffer.putFloat(rightTrigger);
        buffer.putFloat(leftGrip);
        buffer.putFloat(rightGrip);

        buffer.putInt(buttons);

        buffer.putFloat(posX);
        buffer.putFloat(posY);
        buffer.putFloat(posZ);
        buffer.putFloat(rotX);
        buffer.putFloat(rotY);
        buffer.putFloat(rotZ);
        buffer.putFloat(rotW);

        buffer.putFloat(linearVelX);
        buffer.putFloat(linearVelY);
        buffer.putFloat(linearVelZ);
        buffer.putFloat(angularVelX);
        buffer.putFloat(angularVelY);
        buffer.putFloat(angularVelZ);

        return bytes;
    }

    public static PicoControllerSnapshot fromBytes(byte[] bytes) {
        if (bytes == null || bytes.length < PACKET_SIZE) return null;
        ByteBuffer buffer = ByteBuffer.wrap(bytes).order(ByteOrder.LITTLE_ENDIAN);
        if (buffer.getInt() != PACKET_MAGIC) return null;

        PicoControllerSnapshot snapshot = new PicoControllerSnapshot();
        snapshot.sequence = buffer.getInt();
        snapshot.timestampMs = buffer.getLong();

        snapshot.leftStickX = buffer.getFloat();
        snapshot.leftStickY = buffer.getFloat();
        snapshot.rightStickX = buffer.getFloat();
        snapshot.rightStickY = buffer.getFloat();

        snapshot.leftTrigger = buffer.getFloat();
        snapshot.rightTrigger = buffer.getFloat();
        snapshot.leftGrip = buffer.getFloat();
        snapshot.rightGrip = buffer.getFloat();

        snapshot.buttons = buffer.getInt();

        snapshot.posX = buffer.getFloat();
        snapshot.posY = buffer.getFloat();
        snapshot.posZ = buffer.getFloat();
        snapshot.rotX = buffer.getFloat();
        snapshot.rotY = buffer.getFloat();
        snapshot.rotZ = buffer.getFloat();
        snapshot.rotW = buffer.getFloat();

        snapshot.linearVelX = buffer.getFloat();
        snapshot.linearVelY = buffer.getFloat();
        snapshot.linearVelZ = buffer.getFloat();
        snapshot.angularVelX = buffer.getFloat();
        snapshot.angularVelY = buffer.getFloat();
        snapshot.angularVelZ = buffer.getFloat();

        return snapshot;
    }

    public boolean isButtonPressed(int mask) {
        return (buttons & mask) != 0;
    }

    public void setButton(int mask, boolean pressed) {
        if (pressed) {
            buttons |= mask;
        } else {
            buttons &= ~mask;
        }
    }
}
