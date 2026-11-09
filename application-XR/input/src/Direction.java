package com.iwin.input;

/** 方向领域类型，与键盘、网络和 UI 无关；空集合表示没有方向按下。 */
public enum Direction {
    UP("↑"), DOWN("↓"), LEFT("←"), RIGHT("→");
    public final String symbol;
    Direction(String symbol) { this.symbol = symbol; }
}
