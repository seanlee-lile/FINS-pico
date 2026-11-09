package com.iwin.input;

import java.util.Set;

/** 可插拔输入源。传递完整按键快照，避免丢失 key-up 后永久卡住。 */
public interface DirectionSource extends AutoCloseable {
    interface Listener {
        void onDirections(Set<Direction> directions);
        void onStatus(String status);
        default void onControllerSnapshot(PicoControllerSnapshot snapshot) {}
    }
    void start(Listener listener);
    @Override void close();
}
