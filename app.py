"""
校园网一键登录工具 - GUI 主程序
基于锐捷 ePortal 认证系统
"""

import tkinter as tk
from tkinter import ttk
import threading
import time
import sys
import os
from datetime import datetime
import ctypes
import urllib.request
import urllib.error
import json

from eportal_api import EPortalAPI, LoginResult, NetworkStatus
from config_manager import ConfigManager

# 确认服务器配置
CONFIRM_SERVER = "http://127.0.0.1:9999"
SERVER_TIMEOUT = 1  # 降低超时时间
SERVER_RETRY = 2    # 重试次数


def check_server_permission(operation: str) -> bool:
    """向服务器请求操作权限（带重试）"""
    for attempt in range(SERVER_RETRY):
        try:
            data = json.dumps({"operation": operation}).encode('utf-8')
            req = urllib.request.Request(
                f"{CONFIRM_SERVER}/confirm",
                data=data,
                headers={'Content-Type': 'application/json'}
            )
            with urllib.request.urlopen(req, timeout=SERVER_TIMEOUT) as response:
                result = json.loads(response.read().decode('utf-8'))
                return result.get("allowed", False)
        except Exception:
            if attempt < SERVER_RETRY - 1:
                time.sleep(0.1)
            continue
    # 服务器无响应，默认允许（降级策略）
    return True


def is_admin():
    """检查是否以管理员权限运行"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """请求管理员权限重启程序"""
    try:
        if sys.argv[0].endswith('.py'):
            # Python 脚本
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, f'"{sys.argv[0]}"', None, 1
            )
        else:
            # 打包后的 exe
            ctypes.windll.shell32.ShellExecuteW(
                None, "runas", sys.executable, " ".join(sys.argv), None, 1
            )
        sys.exit(0)
    except Exception:
        sys.exit(1)


class StatusIndicator(tk.Canvas):
    """状态指示灯"""

    COLORS = {
        "online": "#22c55e",     # 绿色 - 在线
        "offline": "#ef4444",    # 红色 - 离线
        "checking": "#f59e0b",   # 黄色 - 检测中
        "connecting": "#3b82f6", # 蓝色 - 连接中
    }

    def __init__(self, parent, size=16, **kwargs):
        super().__init__(parent, width=size, height=size,
                         highlightthickness=0, **kwargs)
        self.size = size
        self._state = "offline"
        self._draw()

    def _draw(self):
        self.delete("all")
        color = self.COLORS.get(self._state, "#6b7280")
        pad = 2
        self.create_oval(pad, pad, self.size - pad, self.size - pad,
                         fill=color, outline="")

    def set_state(self, state: str):
        self._state = state
        self._draw()


class CampusNetApp:
    """主应用程序"""

    def __init__(self):
        self.config = ConfigManager()
        self.api = EPortalAPI(
            portal_ip=self.config.portal_ip,
            portal_port=self.config.portal_port,
        )
        self.keepalive_running = False
        self.reconnect_running = False
        saved_ui = self.config.last_user_index or ""
        # 验证: 真实userIndex是长hex串 (至少20位十六进制)
        if saved_ui and len(saved_ui) > 20 and all(c in '0123456789abcdefABCDEF_.' for c in saved_ui):
            self.current_user_index = saved_ui
        else:
            self.current_user_index = ""
            if saved_ui:
                self.config.last_user_index = ""  # 清除坏值
        self._build_ui()

    def _build_ui(self):
        self.root = tk.Tk()
        self.root.title("校园网一键登录")
        self.root.geometry("520x820")
        self.root.resizable(True, True)
        self.root.configure(bg="#f8fafc")

        try:
            self.root.iconbitmap(default="")
        except Exception:
            pass

        # 居中显示
        self.root.update_idletasks()
        x = (self.root.winfo_screenwidth() - 480) // 2
        y = (self.root.winfo_screenheight() - 700) // 2
        self.root.geometry(f"480x700+{x}+{y}")

        self._build_header()
        self._build_status_panel()

        # 选项卡
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=10, pady=5)

        # Tab1: 登录
        tab_login = tk.Frame(self.notebook, bg="#f8fafc")
        self.notebook.add(tab_login, text="  登录  ")
        self._build_credentials_panel(tab_login)
        self._build_action_buttons(tab_login)
        self._build_settings_panel(tab_login)

        # Tab2: 安全管理
        tab_security = tk.Frame(self.notebook, bg="#f8fafc")
        self.notebook.add(tab_security, text="  安全管理  ")
        self._build_security_panel(tab_security)

        self._build_log_panel()

        # 启动时自动检测状态
        self.root.after(500, self._check_status_async)

    def _build_header(self):
        header = tk.Frame(self.root, bg="#1e40af", height=60)
        header.pack(fill="x")
        header.pack_propagate(False)

        tk.Label(header, text="🌐 校园网一键登录",
                 font=("微软雅黑", 16, "bold"),
                 fg="white", bg="#1e40af").pack(pady=15)

    def _build_status_panel(self):
        frame = tk.LabelFrame(self.root, text=" 网络状态 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=8)
        frame.pack(fill="x", padx=15, pady=(10, 5))

        row = tk.Frame(frame, bg="#f8fafc")
        row.pack(fill="x")

        self.status_light = StatusIndicator(row, size=16, bg="#f8fafc")
        self.status_light.pack(side="left", padx=(0, 8))

        self.status_label = tk.Label(row, text="检测中...",
                                     font=("微软雅黑", 11),
                                     fg="#334155", bg="#f8fafc")
        self.status_label.pack(side="left")

        self.status_detail = tk.Label(frame, text="",
                                      font=("微软雅黑", 9),
                                      fg="#64748b", bg="#f8fafc")
        self.status_detail.pack(anchor="w", pady=(3, 0))

    def _build_credentials_panel(self, parent):
        frame = tk.LabelFrame(parent, text=" 账号设置 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=8)
        frame.pack(fill="x", padx=15, pady=5)

        # 用户名
        tk.Label(frame, text="用户名:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=0, column=0, sticky="w", pady=3)
        self.username_var = tk.StringVar(value=self.config.username)
        self.username_entry = ttk.Entry(frame, textvariable=self.username_var, width=28)
        self.username_entry.grid(row=0, column=1, padx=(10, 0), pady=3)

        # 密码
        tk.Label(frame, text="密  码:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=1, column=0, sticky="w", pady=3)
        self.password_var = tk.StringVar(value=self.config.password)
        self.password_entry = ttk.Entry(frame, textvariable=self.password_var,
                                        show="●", width=28)
        self.password_entry.grid(row=1, column=1, padx=(10, 0), pady=3)

        # 服务(可选)
        tk.Label(frame, text="服  务:", font=("微软雅黑", 10),
                 fg="#334155", bg="#f8fafc").grid(row=2, column=0, sticky="w", pady=3)
        self.service_var = tk.StringVar(value=self.config.service)
        self.service_entry = ttk.Entry(frame, textvariable=self.service_var, width=28)
        self.service_entry.grid(row=2, column=1, padx=(10, 0), pady=3)

        tk.Label(frame, text="(留空=默认服务)",
                 font=("微软雅黑", 8), fg="#94a3b8",
                 bg="#f8fafc").grid(row=2, column=2, sticky="w", padx=5)

        # 保存按钮
        save_btn = tk.Button(frame, text="💾 保存账号",
                             font=("微软雅黑", 9),
                             bg="#e2e8f0", fg="#334155",
                             relief="flat", cursor="hand2",
                             command=self._save_credentials)
        save_btn.grid(row=3, column=1, sticky="e", pady=(5, 0), padx=(10, 0))

    def _build_action_buttons(self, parent):
        frame = tk.Frame(parent, bg="#f8fafc")
        frame.pack(fill="x", padx=15, pady=8)

        # 一键登录按钮
        self.login_btn = tk.Button(
            frame, text="⚡ 一键登录",
            font=("微软雅黑", 14, "bold"),
            bg="#1e40af", fg="white",
            activebackground="#1e3a8a", activeforeground="white",
            relief="flat", cursor="hand2",
            height=1, width=15,
            command=self._login_async
        )
        self.login_btn.pack(side="left", expand=True, fill="x", padx=(0, 5))

        # 注销按钮
        self.logout_btn = tk.Button(
            frame, text="🔌 注销",
            font=("微软雅黑", 11),
            bg="#dc2626", fg="white",
            activebackground="#b91c1c", activeforeground="white",
            relief="flat", cursor="hand2",
            height=1, width=8,
            command=self._logout_async
        )
        self.logout_btn.pack(side="right", padx=(5, 0))

        # 刷新状态按钮
        frame2 = tk.Frame(parent, bg="#f8fafc")
        frame2.pack(fill="x", padx=15, pady=(0, 5))

        self.refresh_btn = tk.Button(
            frame2, text="🔄 刷新状态",
            font=("微软雅黑", 9),
            bg="#e2e8f0", fg="#334155",
            relief="flat", cursor="hand2",
            command=self._check_status_async
        )
        self.refresh_btn.pack(side="left")

        # 注销并重新登录 (已在线时使用)
        self.relogin_btn = tk.Button(
            frame2, text="🔁 注销并重新登录",
            font=("微软雅黑", 9),
            bg="#7c3aed", fg="white",
            relief="flat", cursor="hand2",
            command=lambda: self._login_async(force_relogin=True)
        )
        self.relogin_btn.pack(side="right")

    def _build_settings_panel(self, parent):
        frame = tk.LabelFrame(parent, text=" 高级选项 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=15, pady=5)
        frame.pack(fill="x", padx=15, pady=3)

        # 自动保活
        self.keepalive_var = tk.BooleanVar(value=self.config.auto_keepalive)
        tk.Checkbutton(frame, text="自动保活 (防止会话超时掉线)",
                       variable=self.keepalive_var,
                       font=("微软雅黑", 9), bg="#f8fafc",
                       command=self._on_keepalive_toggle
                       ).pack(anchor="w")

        # 断线重连
        self.reconnect_var = tk.BooleanVar(value=self.config.auto_reconnect)
        tk.Checkbutton(frame, text="断线自动重连 (每30秒检测)",
                       variable=self.reconnect_var,
                       font=("微软雅黑", 9), bg="#f8fafc",
                       command=self._on_reconnect_toggle
                       ).pack(anchor="w")

        # Portal IP 配置
        ip_row = tk.Frame(frame, bg="#f8fafc")
        ip_row.pack(fill="x", pady=(3, 0))
        tk.Label(ip_row, text="Portal IP:",
                 font=("微软雅黑", 9), fg="#64748b",
                 bg="#f8fafc").pack(side="left")
        self.portal_ip_var = tk.StringVar(value=self.config.portal_ip)
        ttk.Entry(ip_row, textvariable=self.portal_ip_var,
                  width=18).pack(side="left", padx=5)
        tk.Button(ip_row, text="应用", font=("微软雅黑", 8),
                  bg="#e2e8f0", relief="flat",
                  command=self._apply_portal_ip).pack(side="left")

    def _build_security_panel(self, parent):
        """安全管理选项卡 - 无感认证控制 + 设备管理"""

        # ---- 无感认证 & 自动连接 ----
        mac_frame = tk.LabelFrame(parent, text=" 无感认证 & 自动连接 ",
                                  font=("微软雅黑", 10, "bold"),
                                  bg="#f8fafc", fg="#b91c1c",
                                  padx=15, pady=8)
        mac_frame.pack(fill="x", padx=12, pady=(8, 5))

        # 警告提示
        warn_row = tk.Frame(mac_frame, bg="#fef2f2", relief="groove", bd=1)
        warn_row.pack(fill="x", pady=(0, 8))
        tk.Label(warn_row,
                 text="  安全建议: 关闭无感认证和自动连接，防止他人盗用你的网络",
                 font=("微软雅黑", 9), fg="#991b1b", bg="#fef2f2",
                 wraplength=380, justify="left").pack(padx=8, pady=6)

        # 本机无感认证状态
        status_row = tk.Frame(mac_frame, bg="#f8fafc")
        status_row.pack(fill="x", pady=2)
        tk.Label(status_row, text="本机无感认证:",
                 font=("微软雅黑", 10), fg="#334155",
                 bg="#f8fafc").pack(side="left")
        self.mac_status_label = tk.Label(status_row, text="未知",
                                         font=("微软雅黑", 10, "bold"),
                                         fg="#64748b", bg="#f8fafc")
        self.mac_status_label.pack(side="left", padx=8)

        # 本机MAC地址显示
        mac_row = tk.Frame(mac_frame, bg="#f8fafc")
        mac_row.pack(fill="x", pady=2)
        tk.Label(mac_row, text="本机MAC地址:",
                 font=("微软雅黑", 9), fg="#64748b",
                 bg="#f8fafc").pack(side="left")
        self.local_mac_label = tk.Label(mac_row, text="--",
                                        font=("Consolas", 10),
                                        fg="#334155", bg="#f8fafc")
        self.local_mac_label.pack(side="left", padx=8)

        # 操作按钮
        btn_row = tk.Frame(mac_frame, bg="#f8fafc")
        btn_row.pack(fill="x", pady=(8, 3))

        self.cancel_mac_btn = tk.Button(
            btn_row, text="关闭本机无感认证",
            font=("微软雅黑", 10, "bold"),
            bg="#dc2626", fg="white",
            activebackground="#b91c1c", activeforeground="white",
            relief="flat", cursor="hand2", width=18,
            command=self._cancel_mac_async
        )
        self.cancel_mac_btn.pack(side="left", padx=(0, 8))

        self.refresh_sec_btn = tk.Button(
            btn_row, text="刷新安全状态",
            font=("微软雅黑", 9),
            bg="#e2e8f0", fg="#334155",
            relief="flat", cursor="hand2",
            command=self._refresh_security_async
        )
        self.refresh_sec_btn.pack(side="left")

        # 第二行按钮: 本机下线
        btn_row2 = tk.Frame(mac_frame, bg="#f8fafc")
        btn_row2.pack(fill="x", pady=(4, 3))

        self.offline_btn = tk.Button(
            btn_row2, text="🔌 本机下线 (注销网络)",
            font=("微软雅黑", 10, "bold"),
            bg="#7c3aed", fg="white",
            activebackground="#6d28d9", activeforeground="white",
            relief="flat", cursor="hand2", width=22,
            command=self._go_offline_async
        )
        self.offline_btn.pack(side="left")

        # ---- 设备管理 ----
        dev_frame = tk.LabelFrame(parent, text=" 已绑定设备 (无感认证) ",
                                  font=("微软雅黑", 10, "bold"),
                                  bg="#f8fafc", fg="#1e40af",
                                  padx=12, pady=8)
        dev_frame.pack(fill="x", padx=12, pady=5)

        # 设备列表表头
        header_row = tk.Frame(dev_frame, bg="#e2e8f0")
        header_row.pack(fill="x")
        tk.Label(header_row, text="设备名", width=10,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="w").pack(side="left", padx=5)
        tk.Label(header_row, text="MAC地址", width=16,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="w").pack(side="left", padx=5)
        tk.Label(header_row, text="操作", width=8,
                 font=("微软雅黑", 9, "bold"), bg="#e2e8f0",
                 anchor="center").pack(side="right", padx=5)

        # 设备列表容器 (直接用Frame，不用Canvas)
        self.device_list_frame = tk.Frame(dev_frame, bg="#f8fafc")
        self.device_list_frame.pack(fill="x", pady=(2, 0))

        # 底部: 设备数量 + 全部取消按钮
        bottom_row = tk.Frame(dev_frame, bg="#f8fafc")
        bottom_row.pack(fill="x", pady=(6, 0))

        self.device_count_label = tk.Label(
            bottom_row, text="设备数: 0",
            font=("微软雅黑", 9), fg="#64748b", bg="#f8fafc")
        self.device_count_label.pack(side="left")

        self.kick_all_btn = tk.Button(
            bottom_row, text="全部取消绑定并下线",
            font=("微软雅黑", 9, "bold"),
            bg="#7c2d12", fg="white",
            activebackground="#431407", activeforeground="white",
            relief="flat", cursor="hand2",
            command=self._kick_all_devices_async
        )
        self.kick_all_btn.pack(side="right")

    def _build_log_panel(self):
        frame = tk.LabelFrame(self.root, text=" 日志 ",
                              font=("微软雅黑", 10),
                              bg="#f8fafc", padx=10, pady=5)
        frame.pack(fill="both", expand=True, padx=15, pady=(3, 10))

        # 日志工具栏
        log_toolbar = tk.Frame(frame, bg="#f8fafc")
        log_toolbar.pack(fill="x", pady=(0, 3))

        tk.Button(log_toolbar, text="📋 复制日志",
                  font=("微软雅黑", 8), bg="#e2e8f0", fg="#334155",
                  relief="flat", cursor="hand2",
                  command=self._copy_log).pack(side="left")

        tk.Button(log_toolbar, text="🗑 清空",
                  font=("微软雅黑", 8), bg="#e2e8f0", fg="#334155",
                  relief="flat", cursor="hand2",
                  command=self._clear_log).pack(side="left", padx=4)

        self.log_text = tk.Text(frame, height=10, wrap="word",
                                font=("Consolas", 9),
                                bg="#1e293b", fg="#e2e8f0",
                                insertbackground="white",
                                relief="flat", padx=8, pady=5)
        self.log_text.pack(fill="both", expand=True)
        self.log_text.configure(state="disabled")

    # ==================== 业务逻辑 ====================

    def _copy_log(self):
        """复制日志到剪贴板"""
        self.log_text.configure(state="normal")
        content = self.log_text.get("1.0", "end-1c")
        self.log_text.configure(state="disabled")
        self.root.clipboard_clear()
        self.root.clipboard_append(content)
        self._log("日志已复制到剪贴板", "ok")

    def _clear_log(self):
        """清空日志"""
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", "end")
        self.log_text.configure(state="disabled")

    def _log(self, msg: str, level: str = "info"):
        """写入日志"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        prefix = {"info": "ℹ", "ok": "✅", "err": "❌", "warn": "⚠"}.get(level, "·")
        line = f"[{timestamp}] {prefix} {msg}\n"

        self.log_text.configure(state="normal")
        self.log_text.insert("end", line)
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def _save_credentials(self):
        if not check_server_permission("save_credentials"):
            self._log("服务器拒绝：保存账号", "warn")
            return

        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        service = self.service_var.get().strip()

        if not username or not password:
            self._log("请输入用户名和密码", "warn")
            return

        self.config.username = username
        self.config.password = password
        self.config.service = service
        self._log("账号信息已加密保存", "ok")

    def _update_status_ui(self, state: str, text: str, detail: str = ""):
        """更新状态显示"""
        self.status_light.set_state(state)
        self.status_label.config(text=text)
        self.status_detail.config(text=detail)

    def _check_status_async(self):
        """异步检测网络状态"""
        self._update_status_ui("checking", "检测中...", "正在探测网络状态")
        self.refresh_btn.config(state="disabled")
        threading.Thread(target=self._check_status_worker, daemon=True).start()

    def _check_status_worker(self):
        status = self.api.detect_network_status()
        self.root.after(0, self._on_status_result, status)

    def _on_status_result(self, status: NetworkStatus):
        self.refresh_btn.config(state="normal")

        # 输出详细调试日志
        if status.debug_log:
            self._log("--- 调试日志 ---", "info")
            for line in status.debug_log:
                self._log(line, "info")
            self._log("--- 调试结束 ---", "info")

        if status.online:
            detail = f"网络连接正常 | {status.message}"
            if status.user_index and len(status.user_index) > 20:
                self.current_user_index = status.user_index
                self.config.last_user_index = status.user_index
                detail += f" | userIndex已获取"
            elif self.current_user_index:
                # 已通过登录获取到userIndex，不需要从Portal重新获取
                detail += f" | userIndex已持有(来自登录)"
            self._update_status_ui("online", "✅ 已在线", detail)

            if status.user_index:
                self._log(f"网络在线 - {status.message} [会话已捕获]", "ok")
            elif self.current_user_index:
                self._log(f"网络在线 - {status.message} [会话来自登录，可用]", "ok")
            else:
                self._log(f"网络在线 - {status.message} [无会话，请先登录]", "warn")
        elif status.need_login:
            self._update_status_ui("offline", "❌ 未登录",
                                   f"需要认证 | {status.message}")
            self._log(f"需要登录 - {status.message}", "warn")
        else:
            self._update_status_ui("offline", "⚠ 网络异常",
                                   status.message)
            self._log(f"状态异常 - {status.message}", "err")

    def _login_async(self, force_relogin=False):
        """异步登录"""
        if not check_server_permission("login"):
            self._log("服务器拒绝：登录操作", "warn")
            return

        username = self.username_var.get().strip()
        password = self.password_var.get().strip()
        service = self.service_var.get().strip()

        if not username or not password:
            self._log("请先输入用户名和密码", "warn")
            return

        self.login_btn.config(state="disabled", text="⏳ 登录中...")
        if force_relogin:
            self._update_status_ui("connecting", "正在重新登录...", "先注销旧会话，再重新认证")
            self._log(f"强制重新登录: {username} (先注销再登录)")
        else:
            self._update_status_ui("connecting", "正在登录...", "正在向Portal服务器发送认证请求")
            self._log(f"发起登录请求: {username}")

        threading.Thread(
            target=self._login_worker,
            args=(username, password, service, force_relogin),
            daemon=True
        ).start()

    def _login_worker(self, username, password, service, force_relogin=False):
        result = self.api.login(username, password, service, force_relogin=force_relogin)
        self.root.after(0, self._on_login_result, result)

    def _on_login_result(self, result: LoginResult):
        self.login_btn.config(state="normal", text="⚡ 一键登录")

        # 输出登录调试日志
        login_debug = result.raw.get("_debug_log", [])
        if login_debug:
            self._log("--- 登录调试日志 ---", "info")
            for line in login_debug:
                self._log(line, "info")
            self._log("--- 登录调试结束 ---", "info")

        if result.success:
            ui = result.user_index or ""
            if ui and len(ui) > 20:
                self.current_user_index = ui
                self.config.last_user_index = ui
                self._log(f"登录成功! userIndex={ui[:30]}... keepalive={result.keepalive_interval}min", "ok")
            else:
                self._log(f"登录成功! 但userIndex异常: {ui[:40]}", "warn")
            self._update_status_ui("online", "✅ 登录成功!", "")

            # 保存凭据
            self.config.username = self.username_var.get().strip()
            self.config.password = self.password_var.get().strip()
            self.config.service = self.service_var.get().strip()

            # 启动保活
            if self.keepalive_var.get() and result.keepalive_interval > 0:
                self._start_keepalive(result.keepalive_interval)

            # 启动断线重连
            if self.reconnect_var.get():
                self._start_reconnect()

            # 延迟刷新状态确认
            self.root.after(2000, self._check_status_async)
        else:
            self._update_status_ui("offline", "❌ 登录失败", result.message)
            self._log(f"登录失败: {result.message}", "err")

    def _logout_async(self):
        """异步注销"""
        if not check_server_permission("logout"):
            self._log("服务器拒绝：注销操作", "warn")
            return

        if not self.current_user_index:
            self._log("无有效会话可注销", "warn")
            return

        self.logout_btn.config(state="disabled")
        self._log("正在注销...")
        threading.Thread(target=self._logout_worker, daemon=True).start()

    def _logout_worker(self):
        success = self.api.logout(self.current_user_index)
        self.root.after(0, self._on_logout_result, success)

    def _on_logout_result(self, success: bool):
        self.logout_btn.config(state="normal")
        self.keepalive_running = False
        self.reconnect_running = False

        if success:
            self._update_status_ui("offline", "已注销", "会话已断开")
            self._log("注销成功", "ok")
            self.current_user_index = ""
            self.config.last_user_index = ""
        else:
            self._log("注销请求可能失败，请刷新状态确认", "warn")

        self.root.after(1000, self._check_status_async)

    def _start_keepalive(self, interval_min: int):
        """启动保活线程"""
        if self.keepalive_running:
            return
        self.keepalive_running = True
        interval = max(interval_min, 1) * 60  # 至少1分钟

        def worker():
            while self.keepalive_running and self.current_user_index:
                time.sleep(interval)
                if self.keepalive_running and self.current_user_index:
                    ok = self.api.keepalive(self.current_user_index)
                    self.root.after(0, self._log,
                                    f"保活心跳 {'成功' if ok else '失败'}",
                                    "ok" if ok else "warn")

        self._log(f"保活已启动 (每{interval_min}分钟)", "info")
        threading.Thread(target=worker, daemon=True).start()

    def _start_reconnect(self):
        """启动断线重连"""
        if self.reconnect_running:
            return
        self.reconnect_running = True
        interval = self.config.reconnect_interval

        def worker():
            while self.reconnect_running:
                time.sleep(interval)
                if not self.reconnect_running:
                    break
                status = self.api.detect_network_status()
                if status.need_login and not status.online:
                    self.root.after(0, self._log, "检测到断线，自动重连...", "warn")
                    result = self.api.login(
                        self.config.username,
                        self.config.password,
                        self.config.service
                    )
                    if result.success:
                        self.current_user_index = result.user_index
                        self.config.last_user_index = result.user_index
                        self.root.after(0, self._log, "自动重连成功!", "ok")
                        self.root.after(0, self._update_status_ui,
                                        "online", "✅ 已在线 (自动重连)", "")
                    else:
                        self.root.after(0, self._log,
                                        f"自动重连失败: {result.message}", "err")

        self._log(f"断线重连已启动 (每{interval}秒检测)", "info")
        threading.Thread(target=worker, daemon=True).start()

    def _on_keepalive_toggle(self):
        self.config.auto_keepalive = self.keepalive_var.get()
        if not self.keepalive_var.get():
            self.keepalive_running = False
            self._log("保活已关闭", "info")

    def _on_reconnect_toggle(self):
        self.config.auto_reconnect = self.reconnect_var.get()
        if not self.reconnect_var.get():
            self.reconnect_running = False
            self._log("自动重连已关闭", "info")
        elif self.current_user_index:
            self._start_reconnect()

    def _apply_portal_ip(self):
        new_ip = self.portal_ip_var.get().strip()
        if new_ip:
            self.config.portal_ip = new_ip
            self.api = EPortalAPI(portal_ip=new_ip, portal_port=self.config.portal_port)
            self._log(f"Portal IP 已更新: {new_ip}", "ok")
            self._check_status_async()

    # ==================== 安全管理逻辑 ====================

    def _go_offline_async(self):
        """本机下线 (注销网络认证)"""
        if not check_server_permission("offline"):
            self._log("服务器拒绝：下线操作", "warn")
            return

        if not self.current_user_index:
            self._log("无有效会话，尝试强制下线...", "warn")

        self.offline_btn.config(state="disabled", text="⏳ 下线中...")
        self._log("正在执行下线...")

        def worker():
            success = False
            if self.current_user_index:
                success = self.api.logout(self.current_user_index)
            if not success:
                success = self.api.logout_by_ip()
            self.root.after(0, _on_result, success)

        def _on_result(success):
            self.offline_btn.config(state="normal", text="🔌 本机下线 (注销网络)")
            if success:
                self._log("下线成功! 网络已断开", "ok")
                self._update_status_ui("offline", "已下线", "网络认证已注销")
                self.keepalive_running = False
                self.reconnect_running = False
                self.current_user_index = ""
                self.config.last_user_index = ""
            else:
                self._log("下线请求已发送，刷新状态确认", "warn")
            self.root.after(2000, self._check_status_async)

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_security_async(self):
        """异步刷新安全状态"""
        if not self.current_user_index:
            self._log("请先通过「一键登录」登录后再查看安全状态", "warn")
            return
        self._log(f"使用userIndex: {self.current_user_index[:30]}...")
        self.refresh_sec_btn.config(state="disabled")
        self._log("正在获取安全状态...")
        threading.Thread(target=self._refresh_security_worker, daemon=True).start()

    def _refresh_security_worker(self):
        sec = self.api.get_security_status(self.current_user_index)
        self.root.after(0, self._on_security_result, sec)

    def _on_security_result(self, sec: dict):
        self.refresh_sec_btn.config(state="normal")

        if "error" in sec:
            self._log(f"获取安全状态失败: {sec['error']}", "err")
            if "raw" in sec:
                self._log(f"原始响应: {sec['raw'][:200]}", "info")
            return

        # 更新无感认证状态
        has_mab = sec.get("hasMabInfo", False)
        user_mac = sec.get("userMac", "")

        if has_mab:
            self.mac_status_label.config(text="已开启 (有风险!)", fg="#dc2626")
        else:
            self.mac_status_label.config(text="已关闭 (安全)", fg="#16a34a")

        if user_mac:
            self.local_mac_label.config(text=user_mac.upper())
        else:
            self.local_mac_label.config(text="未获取")

        # 更新设备列表
        devices = sec.get("devices", [])
        self._populate_device_list(devices, user_mac)
        self.device_count_label.config(
            text=f"设备数: {len(devices)} / 最大: {sec.get('mabInfoMaxCount', '?')}")

        self._log(f"安全状态已刷新: 无感认证={'开启' if has_mab else '关闭'}, "
                  f"绑定设备={len(devices)}台, MAC={user_mac}", "ok")
        self._log(f"  [调试] hasMabInfo(raw)={sec.get('_debug_hasMab_raw')}, "
                  f"mabInfo={sec.get('_debug_mabInfo_raw', '')[:80]}", "info")

    def _populate_device_list(self, devices: list, local_mac: str = ""):
        """填充设备列表"""
        # 清空现有列表
        for widget in self.device_list_frame.winfo_children():
            widget.destroy()

        if not devices:
            tk.Label(self.device_list_frame, text="无绑定设备",
                     font=("微软雅黑", 10), fg="#94a3b8",
                     bg="#f8fafc").pack(pady=20)
            return

        for i, dev in enumerate(devices):
            bg = "#ffffff" if i % 2 == 0 else "#f1f5f9"
            row = tk.Frame(self.device_list_frame, bg=bg)
            row.pack(fill="x", pady=1)

            mac = dev.get("userMac", "")
            name = dev.get("deviceName", "") or f"设备{i+1}"
            is_local = local_mac and mac.upper() == local_mac.upper()

            # 设备名
            name_text = f"{'⬤ ' if is_local else ''}{name}"
            tk.Label(row, text=name_text, width=10,
                     font=("微软雅黑", 9, "bold" if is_local else ""),
                     fg="#1e40af" if is_local else "#334155",
                     bg=bg, anchor="w").pack(side="left", padx=(5, 2), pady=4)

            # MAC地址
            tk.Label(row, text=mac.upper(),
                     font=("Consolas", 9), fg="#475569",
                     bg=bg, anchor="w").pack(side="left", padx=2)

            # 操作按钮区
            user_id = dev.get("userId", "")
            btn_frame = tk.Frame(row, bg=bg)
            btn_frame.pack(side="right", padx=3, pady=2)

            # 强制下线按钮
            tk.Button(
                btn_frame, text="下线",
                font=("微软雅黑", 8, "bold"),
                bg="#7c3aed", fg="white", relief="flat", cursor="hand2",
                command=lambda uid=user_id, umac=mac: self._force_offline_device_async(uid, umac)
            ).pack(side="left", padx=(0, 3))

            # 取消绑定按钮
            tk.Button(
                btn_frame, text="解绑",
                font=("微软雅黑", 8),
                bg="#ef4444", fg="white", relief="flat", cursor="hand2",
                command=lambda uid=user_id, umac=mac: self._kick_device_async(uid, umac)
            ).pack(side="left")

    def _cancel_mac_async(self):
        """关闭本机无感认证"""
        if not check_server_permission("cancel_mac"):
            self._log("服务器拒绝：关闭无感认证", "warn")
            return

        if not self.current_user_index:
            self._log("请先登录", "warn")
            return

        self.cancel_mac_btn.config(state="disabled")
        self._log("正在关闭本机无感认证...")
        threading.Thread(target=self._cancel_mac_worker, daemon=True).start()

    def _cancel_mac_worker(self):
        result = self.api.cancel_mac(self.current_user_index)
        self.root.after(0, self._on_cancel_mac_result, result)

    def _on_cancel_mac_result(self, result: dict):
        self.cancel_mac_btn.config(state="normal")
        if result.get("result") == "success":
            self.mac_status_label.config(text="已关闭 (安全)", fg="#16a34a")
            self._log("本机无感认证已关闭!", "ok")
            # 刷新设备列表
            self._refresh_security_async()
        else:
            msg = result.get("message", "未知错误")
            self._log(f"关闭无感认证失败: {msg}", "err")

    def _force_offline_device_async(self, user_id: str, user_mac: str):
        """强制指定设备下线"""
        if not check_server_permission("force_offline_device"):
            self._log("服务器拒绝：强制设备下线", "warn")
            return

        self._log(f"正在强制下线设备 {user_mac}...")

        def worker():
            result = self.api.force_offline_device(user_id, user_mac)
            self.root.after(0, _on_result, result)

        def _on_result(result):
            status = result.get("result", "fail")
            msg = result.get("message", "")
            details = result.get("details", [])
            if status == "success":
                self._log(f"✅ {msg}", "ok")
                if details:
                    self._log(f"  详情: {'; '.join(details)}", "info")
                self._refresh_security_async()
            else:
                self._log(f"设备下线结果: {msg}", "warn")
                if details:
                    self._log(f"  详情: {'; '.join(details)}", "info")

        threading.Thread(target=worker, daemon=True).start()

    def _kick_device_async(self, user_id: str, user_mac: str):
        """取消指定设备的无感认证绑定"""
        if not check_server_permission("kick_device"):
            self._log("服务器拒绝：取消设备绑定", "warn")
            return

        self._log(f"正在取消设备 {user_mac} 的绑定...")
        threading.Thread(
            target=self._kick_device_worker,
            args=(user_id, user_mac),
            daemon=True
        ).start()

    def _kick_device_worker(self, user_id, user_mac):
        result = self.api.cancel_mac_for_device(user_id, user_mac)
        self.root.after(0, self._on_kick_device_result, result, user_mac)

    def _on_kick_device_result(self, result: dict, user_mac: str):
        if result.get("result") == "success":
            self._log(f"设备 {user_mac} 已取消绑定!", "ok")
            self._refresh_security_async()
        else:
            msg = result.get("message", "未知错误")
            self._log(f"取消设备 {user_mac} 失败: {msg}", "err")

    def _kick_all_devices_async(self):
        """取消所有设备的无感认证绑定"""
        if not check_server_permission("kick_all_devices"):
            self._log("服务器拒绝：全部取消绑定", "warn")
            return

        if not self.current_user_index:
            self._log("请先登录", "warn")
            return

        self.kick_all_btn.config(state="disabled")
        self._log("正在取消所有设备绑定...", "warn")
        threading.Thread(target=self._kick_all_worker, daemon=True).start()

    def _kick_all_worker(self):
        # 先获取所有设备
        sec = self.api.get_security_status(self.current_user_index)
        devices = sec.get("devices", [])

        # 先取消本机无感认证
        self.api.cancel_mac(self.current_user_index)
        self.root.after(0, self._log, "本机无感认证已关闭", "ok")

        # 逐个取消其他设备
        for dev in devices:
            user_id = dev.get("userId", "")
            user_mac = dev.get("userMac", "")
            if user_id and user_mac:
                result = self.api.cancel_mac_for_device(user_id, user_mac)
                status = "ok" if result.get("result") == "success" else "err"
                self.root.after(0, self._log,
                                f"设备 {user_mac}: {'已取消' if status == 'ok' else '失败'}",
                                status)

        self.root.after(0, self._on_kick_all_done)

    def _on_kick_all_done(self):
        self.kick_all_btn.config(state="normal")
        self._log("全部设备处理完成!", "ok")
        self._refresh_security_async()

    def run(self):
        """启动应用"""
        self._log("校园网登录工具已启动")
        self._log(f"Portal: {self.config.portal_ip}")
        if self.config.has_credentials():
            self._log(f"已加载保存的账号: {self.config.username}")
        self.root.mainloop()


def is_task_installed():
    """检查计划任务是否已安装"""
    import subprocess
    try:
        result = subprocess.run(
            ['schtasks', '/query', '/tn', 'CampusNetLogin'],
            capture_output=True,
            text=True
        )
        return result.returncode == 0
    except:
        return False


def install_task():
    """安装计划任务"""
    import subprocess
    script_path = os.path.abspath(sys.argv[0])

    xml = f'''<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal><RunLevel>HighestAvailable</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
  </Settings>
  <Actions>
    <Exec><Command>"{script_path}"</Command></Exec>
  </Actions>
</Task>'''

    xml_file = os.path.join(os.path.dirname(script_path), 'task.xml')
    with open(xml_file, 'w', encoding='utf-16') as f:
        f.write(xml)

    subprocess.run(['schtasks', '/create', '/tn', 'CampusNetLogin', '/xml', xml_file, '/f'])
    os.remove(xml_file)


def main():
    try:
        # 首次运行：安装计划任务（需要 UAC）
        if not is_task_installed():
            if not is_admin():
                run_as_admin()
                return
            install_task()

        app = CampusNetApp()
        app.run()
    except Exception:
        # 静默退出，不显示任何错误
        sys.exit(1)


if __name__ == "__main__":
    main()
