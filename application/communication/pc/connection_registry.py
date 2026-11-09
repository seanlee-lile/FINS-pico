"""握手会话与心跳状态。用于确认协议兼容和在线状态，不作为身份认证。"""
from __future__ import annotations
import re
import threading
import time
import uuid

PROTOCOL_VERSION = 2
HEARTBEAT_TIMEOUT = 5.0


class ConnectionRegistry:
    def __init__(self):
        self.lock = threading.Lock()
        self.sessions = {}
        self.serverId = uuid.uuid4().hex

    def removeExpired(self, now):
        self.sessions = {token: session for token, session in self.sessions.items()
                         if now - session['lastSeen'] <= HEARTBEAT_TIMEOUT}

    def handshake(self, request, peer):
        if type(request.get('protocol_version')) is not int or request['protocol_version'] != PROTOCOL_VERSION:
            raise ValueError('unsupported_protocol')
        clientId = request.get('client_id', '')
        nonce = request.get('nonce', '')
        name = request.get('client_name', 'Android')
        if not isinstance(clientId, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,64}', clientId):
            raise ValueError('invalid_client_id')
        if not isinstance(nonce, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,64}', nonce):
            raise ValueError('invalid_nonce')
        if not isinstance(name, str) or len(name) > 128:
            raise ValueError('invalid_client_name')
        name = ' '.join(name.split()) or 'Android'
        with self.lock:
            self.removeExpired(time.monotonic())
            # 同一应用重连时替换旧会话，避免重复统计。
            self.sessions = {token: value for token, value in self.sessions.items() if value['clientId'] != clientId}
            if len(self.sessions) >= 16:
                raise ValueError('too_many_clients')
            token = uuid.uuid4().hex
            self.sessions[token] = dict(clientId=clientId, name=name, peer=peer, lastSeen=time.monotonic())
        return dict(protocol_version=PROTOCOL_VERSION, session_id=token, server_id=self.serverId,
                    nonce=nonce, heartbeat_timeout_ms=int(HEARTBEAT_TIMEOUT * 1000))

    def heartbeat(self, token, peer):
        with self.lock:
            now = time.monotonic()
            self.removeExpired(now)
            session = self.sessions.get(token)
            if session is None or session['peer'] != peer:
                return False
            session['lastSeen'] = now
            return True

    def connectedClients(self):
        with self.lock:
            self.removeExpired(time.monotonic())
            return [dict(name=value['name'], address=value['peer']) for value in self.sessions.values()]
