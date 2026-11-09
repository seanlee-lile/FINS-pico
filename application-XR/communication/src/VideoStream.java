package com.iwin.communication;

import android.view.View;

/** UI 只依赖这个接口；后续可用 WebRTC/RTSP 实现替换 HLS。所有方法在主线程调用。 */
public interface VideoStream extends AutoCloseable {
    interface Listener { void onStatus(String message); }
    View view();
    void play(String url, Listener listener);
    @Override void close();
}
