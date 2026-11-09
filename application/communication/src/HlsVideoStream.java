package com.iwin.communication;

import android.content.Context;
import android.view.View;
import androidx.media3.common.MediaItem;
import androidx.media3.common.PlaybackException;
import androidx.media3.common.Player;
import androidx.media3.common.util.UnstableApi;
import androidx.media3.exoplayer.ExoPlayer;
import androidx.media3.ui.PlayerView;

/** Media3 负责 HLS 下载、解码和播放；实例由 Activity 生命周期管理。 */
@UnstableApi
public final class HlsVideoStream implements VideoStream {
    private final ExoPlayer player;
    private final PlayerView view;
    private Listener listener;

    public HlsVideoStream(Context context) {
        player = new ExoPlayer.Builder(context).build();
        view = new PlayerView(context);
        view.setPlayer(player);
        view.setUseController(false);
        player.addListener(new Player.Listener() {
            @Override public void onPlaybackStateChanged(int state) {
                if (listener == null) return;
                if (state == Player.STATE_BUFFERING) listener.onStatus("视频缓冲中…");
                if (state == Player.STATE_READY) listener.onStatus("视频播放中");
                if (state == Player.STATE_ENDED) listener.onStatus("视频已结束");
            }
            @Override public void onPlayerError(PlaybackException error) {
                if (listener != null) listener.onStatus("视频连接失败：" + error.getErrorCodeName() + "；请检查服务后重新连接");
            }
        });
    }
    @Override public View view() { return view; }
    @Override public void play(String url, Listener listener) {
        this.listener = listener;
        player.setMediaItem(new MediaItem.Builder().setUri(url)
                .setLiveConfiguration(new MediaItem.LiveConfiguration.Builder().setTargetOffsetMs(2500).build()).build());
        player.prepare();
        player.play();
    }
    @Override public void close() {
        listener = null;
        view.setPlayer(null);
        player.release();
    }
}
