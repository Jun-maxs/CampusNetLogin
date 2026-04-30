#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
操作确认服务器 - 所有客户端操作必须经过服务器确认
"""

import http.server
import json
import threading
import os
from datetime import datetime

# 配置文件
CONFIG_FILE = "server_config.json"
LOG_FILE = "server_audit.log"

# 默认配置
DEFAULT_CONFIG = {
    "port": 9999,
    "allowed_operations": {
        "login": True,
        "logout": True,
        "offline": True,
        "cancel_mac": True,
        "force_offline_device": True,
        "kick_device": True,
        "kick_all_devices": True,
        "save_credentials": True,
    }
}


def load_config():
    """加载配置"""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            pass
    # 创建默认配置
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(DEFAULT_CONFIG, f, indent=2, ensure_ascii=False)
    return DEFAULT_CONFIG


def log_audit(operation, allowed, client_ip):
    """记录审计日志"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_line = f"[{timestamp}] {client_ip} | {operation} | {'允许' if allowed else '拒绝'}\n"
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(log_line)
    except:
        pass


class ConfirmHandler(http.server.BaseHTTPRequestHandler):
    config = load_config()

    def log_message(self, format, *args):
        timestamp = datetime.now().strftime("%H:%M:%S")
        print(f"[{timestamp}] {args[0]}")

    def do_POST(self):
        if self.path == "/confirm":
            try:
                content_length = int(self.headers['Content-Length'])
                body = self.rfile.read(content_length)
                data = json.loads(body.decode('utf-8'))

                operation = data.get("operation", "")
                allowed = self.config["allowed_operations"].get(operation, False)

                # 记录审计日志
                client_ip = self.client_address[0]
                log_audit(operation, allowed, client_ip)

                response = {
                    "allowed": allowed,
                    "operation": operation,
                    "timestamp": datetime.now().isoformat()
                }

                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Access-Control-Allow-Origin', '*')
                self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
                self.send_header('Access-Control-Allow-Headers', 'Content-Type')
                self.end_headers()
                self.wfile.write(json.dumps(response).encode('utf-8'))

                print(f"操作: {operation} -> {'允许' if allowed else '拒绝'} ({client_ip})")
            except Exception as e:
                self.send_error(500, str(e))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_GET(self):
        if self.path == "/reload":
            self.config = load_config()
            self.send_response(200)
            self.send_header('Content-Type', 'text/plain')
            self.end_headers()
            self.wfile.write(b"Config reloaded")
            print("配置已重新加载")


def run_server():
    config = load_config()
    port = config.get("port", 9999)
    server = http.server.HTTPServer(('0.0.0.0', port), ConfirmHandler)
    print(f"✅ 确认服务器启动: http://0.0.0.0:{port}")
    print(f"配置文件: {CONFIG_FILE}")
    print(f"审计日志: {LOG_FILE}")
    print(f"重载配置: curl http://localhost:{port}/reload")
    server.serve_forever()


if __name__ == "__main__":
    try:
        run_server()
    except KeyboardInterrupt:
        print("\n服务器已停止")
    except Exception:
        pass
