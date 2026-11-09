package com.iwin.input;

import java.util.Collections;
import java.util.EnumSet;
import java.util.HashMap;
import java.util.Map;
import java.util.Set;

/** 按来源合并方向；移除网络源不会清除仍按下的本地按键。 */
public final class DirectionState {
    private final Map<String, Set<Direction>> sources = new HashMap<>();
    public synchronized Set<Direction> update(String source, Set<Direction> pressed) {
        sources.put(source, pressed.isEmpty() ? EnumSet.noneOf(Direction.class) : EnumSet.copyOf(pressed));
        return snapshot();
    }
    public synchronized Set<Direction> snapshot() {
        EnumSet<Direction> merged = EnumSet.noneOf(Direction.class);
        for (Set<Direction> directions : sources.values()) merged.addAll(directions);
        return Collections.unmodifiableSet(merged);
    }
}
