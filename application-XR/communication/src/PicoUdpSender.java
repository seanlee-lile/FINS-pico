package com.iwin.communication;

import com.iwin.input.PicoControllerSnapshot;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.InetAddress;
import java.util.concurrent.atomic.AtomicReference;

/**
 * PICO 手柄高频 UDP 推送服务。
 * 结合输入事件即时触发与 50Hz 保底定时心跳，确保极低延迟并防止网络丢包。
 */
public final class PicoUdpSender implements AutoCloseable {
    private final String targetHost;
    private final int targetPort;
    private final DatagramSocket socket;
    private final InetAddress targetAddress;
    private final AtomicReference<PicoControllerSnapshot> currentSnapshot = new AtomicReference<>(new PicoControllerSnapshot());
    private final Thread heartbeatThread;
    private volatile boolean running = true;
    private int sequenceCounter = 0;

    public interface Listener {
        void onPacketSent(int sequence, long timestampMs);
        void onError(String message);
    }

    private volatile Listener listener;

    public PicoUdpSender(String targetHost, int targetPort) throws Exception {
        this.targetHost = targetHost;
        this.targetPort = targetPort;
        this.socket = new DatagramSocket();
        this.targetAddress = InetAddress.getByName(targetHost);

        this.heartbeatThread = new Thread(this::heartbeatLoop, "PicoUdpHeartbeat");
        this.heartbeatThread.setDaemon(true);
        this.heartbeatThread.start();
    }

    public void setListener(Listener listener) {
        this.listener = listener;
    }

    public void updateAndSend(PicoControllerSnapshot snapshot) {
        if (!running || snapshot == null) return;
        snapshot.sequence = ++sequenceCounter;
        snapshot.timestampMs = System.currentTimeMillis();
        currentSnapshot.set(snapshot);
        transmit(snapshot);
    }

    private void heartbeatLoop() {
        while (running) {
            try {
                Thread.sleep(20); // 50Hz 定时心跳与防丢包
                PicoControllerSnapshot snapshot = currentSnapshot.get();
                if (snapshot != null) {
                    transmit(snapshot);
                }
            } catch (InterruptedException e) {
                break;
            } catch (Exception e) {
                if (listener != null && running) {
                    listener.onError("UDP 发送异常: " + e.getMessage());
                }
            }
        }
    }

    private void transmit(PicoControllerSnapshot snapshot) {
        try {
            byte[] bytes = snapshot.toBytes();
            DatagramPacket packet = new DatagramPacket(bytes, bytes.length, targetAddress, targetPort);
            socket.send(packet);
            if (listener != null) {
                listener.onPacketSent(snapshot.sequence, snapshot.timestampMs);
            }
        } catch (Exception e) {
            if (listener != null && running) {
                listener.onError("UDP 发送失败: " + e.getMessage());
            }
        }
    }

    @Override
    public void close() {
        running = false;
        heartbeatThread.interrupt();
        if (socket != null && !socket.isClosed()) {
            socket.close();
        }
    }

    public String getTargetHost() { return targetHost; }
    public int getTargetPort() { return targetPort; }
}
