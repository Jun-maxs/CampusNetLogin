"""
校园网一键登录 - Android App
基于 Kivy 框架，可编译为 APK
"""

import json
import os
import re
import socket
import threading
import time
import urllib.request
import urllib.parse
import urllib.error

from kivy.app import App
from kivy.clock import Clock
from kivy.core.window import Window
from kivy.core.text import LabelBase
from kivy.metrics import dp
from kivy.properties import StringProperty, BooleanProperty, ListProperty
from kivy.lang import Builder
from kivy.utils import platform

# ==================== 中文字体支持 ====================
def _register_chinese_font():
    """注册中文字体，解决 Android 上中文显示为方块的问题"""
    import glob
    font_candidates = []

    # 优先查找本地打包的字体 (APK内)
    app_dir = os.path.dirname(os.path.abspath(__file__))
    for name in ["font.ttf", "font.otf", "NotoSansSC-Regular.ttf", "chinese.ttf"]:
        font_candidates.append(os.path.join(app_dir, name))

    # Android 系统中文字体路径
    font_candidates.extend([
        "/system/fonts/NotoSansSC-Regular.otf",
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/DroidSansFallback.ttf",
        "/system/fonts/NotoSansHans-Regular.otf",
        "/system/fonts/DroidSansChinese.ttf",
    ])
    for pattern in ["/system/fonts/NotoSans*SC*.otf", "/system/fonts/NotoSans*CJK*.ttc",
                    "/system/fonts/DroidSans*.ttf"]:
        font_candidates.extend(glob.glob(pattern))

    # Windows 字体 (开发用)
    font_candidates.extend([
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/simhei.ttf",
    ])

    for font_path in font_candidates:
        if os.path.exists(font_path):
            LabelBase.register(name="Roboto", fn_regular=font_path)
            print(f"[字体] 已注册: {font_path}")
            return font_path
    print("[字体] 警告: 未找到中文字体!")
    return None

_register_chinese_font()

# ==================== UI 定义 ====================
KV = """
#:import dp kivy.metrics.dp

<RoundedButton@ButtonBehavior+Label>:
    size_hint_y: None
    height: dp(48)
    font_size: dp(15)
    bold: True
    canvas.before:
        Color:
            rgba: self.bg_color if hasattr(self, 'bg_color') else (0.39, 0.4, 0.95, 1)
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(12)]

<DeviceCard@BoxLayout>:
    size_hint_y: None
    height: dp(64)
    padding: dp(12)
    canvas.before:
        Color:
            rgba: 0.08, 0.11, 0.18, 0.8
        RoundedRectangle:
            pos: self.pos
            size: self.size
            radius: [dp(10)]

ScreenManager:
    id: sm
    Screen:
        name: 'main'
        BoxLayout:
            orientation: 'vertical'
            canvas.before:
                Color:
                    rgba: 0.06, 0.09, 0.16, 1
                Rectangle:
                    pos: self.pos
                    size: self.size

            # 顶部状态栏
            BoxLayout:
                size_hint_y: None
                height: dp(80)
                padding: dp(16), dp(12)
                orientation: 'vertical'
                Label:
                    text: '⚡ 校园网登录'
                    font_size: dp(22)
                    bold: True
                    color: 0.51, 0.55, 0.97, 1
                    size_hint_y: None
                    height: dp(30)
                Label:
                    id: ip_label
                    text: app.campus_ip_text
                    font_size: dp(12)
                    color: 0.4, 0.45, 0.58, 1
                    size_hint_y: None
                    height: dp(18)

            # 网络状态条
            BoxLayout:
                size_hint_y: None
                height: dp(52)
                padding: dp(16), dp(6)
                spacing: dp(10)
                canvas.before:
                    Color:
                        rgba: 0.05, 0.07, 0.14, 0.9
                    RoundedRectangle:
                        pos: self.x + dp(12), self.y + dp(4)
                        size: self.width - dp(24), self.height - dp(8)
                        radius: [dp(12)]
                Widget:
                    size_hint: None, None
                    size: dp(12), dp(12)
                    pos_hint: {'center_y': 0.5}
                    canvas:
                        Color:
                            rgba: app.status_color
                        Ellipse:
                            pos: self.pos
                            size: self.size
                BoxLayout:
                    orientation: 'vertical'
                    Label:
                        text: app.status_text
                        font_size: dp(14)
                        color: 0.88, 0.91, 0.94, 1
                        halign: 'left'
                        text_size: self.size
                        size_hint_y: None
                        height: dp(20)
                    Label:
                        text: app.status_detail
                        font_size: dp(11)
                        color: 0.58, 0.64, 0.72, 1
                        halign: 'left'
                        text_size: self.size
                        size_hint_y: None
                        height: dp(16)

            # 标签页切换
            BoxLayout:
                size_hint_y: None
                height: dp(44)
                padding: dp(12), dp(6)
                spacing: dp(4)
                canvas.before:
                    Color:
                        rgba: 0.05, 0.07, 0.14, 0.6
                    RoundedRectangle:
                        pos: self.x + dp(12), self.y + dp(4)
                        size: self.width - dp(24), self.height - dp(8)
                        radius: [dp(10)]
                Button:
                    text: '登录'
                    font_size: dp(13)
                    bold: True
                    background_color: (0.39, 0.4, 0.95, 0.3) if app.current_tab == 'login' else (0, 0, 0, 0)
                    color: (0.51, 0.55, 0.97, 1) if app.current_tab == 'login' else (0.58, 0.64, 0.72, 1)
                    background_normal: ''
                    on_release: app.switch_tab('login')
                Button:
                    text: '安全管理'
                    font_size: dp(13)
                    bold: True
                    background_color: (0.39, 0.4, 0.95, 0.3) if app.current_tab == 'security' else (0, 0, 0, 0)
                    color: (0.51, 0.55, 0.97, 1) if app.current_tab == 'security' else (0.58, 0.64, 0.72, 1)
                    background_normal: ''
                    on_release: app.switch_tab('security')
                Button:
                    text: '日志'
                    font_size: dp(13)
                    bold: True
                    background_color: (0.39, 0.4, 0.95, 0.3) if app.current_tab == 'log' else (0, 0, 0, 0)
                    color: (0.51, 0.55, 0.97, 1) if app.current_tab == 'log' else (0.58, 0.64, 0.72, 1)
                    background_normal: ''
                    on_release: app.switch_tab('log')

            # 内容区 - ScreenManager
            ScreenManager:
                id: content_sm

                # ===== 登录页 =====
                Screen:
                    name: 'login'
                    ScrollView:
                        BoxLayout:
                            orientation: 'vertical'
                            size_hint_y: None
                            height: self.minimum_height
                            padding: dp(16)
                            spacing: dp(10)

                            # 登录表单
                            BoxLayout:
                                orientation: 'vertical'
                                size_hint_y: None
                                height: dp(320)
                                padding: dp(16)
                                spacing: dp(10)
                                canvas.before:
                                    Color:
                                        rgba: 0.12, 0.16, 0.23, 0.85
                                    RoundedRectangle:
                                        pos: self.pos
                                        size: self.size
                                        radius: [dp(16)]
                                Label:
                                    text: '🔐 账号登录'
                                    font_size: dp(15)
                                    bold: True
                                    color: 0.51, 0.55, 0.97, 1
                                    size_hint_y: None
                                    height: dp(28)
                                    halign: 'left'
                                    text_size: self.size
                                TextInput:
                                    id: username_input
                                    hint_text: '学号/工号'
                                    text: app.saved_username
                                    font_size: dp(15)
                                    size_hint_y: None
                                    height: dp(44)
                                    multiline: False
                                    background_color: 0.06, 0.09, 0.16, 0.8
                                    foreground_color: 0.88, 0.91, 0.94, 1
                                    hint_text_color: 0.4, 0.45, 0.55, 1
                                    cursor_color: 0.51, 0.55, 0.97, 1
                                    padding: dp(12), dp(10)
                                TextInput:
                                    id: password_input
                                    hint_text: '密码'
                                    password: True
                                    font_size: dp(15)
                                    size_hint_y: None
                                    height: dp(44)
                                    multiline: False
                                    background_color: 0.06, 0.09, 0.16, 0.8
                                    foreground_color: 0.88, 0.91, 0.94, 1
                                    hint_text_color: 0.4, 0.45, 0.55, 1
                                    cursor_color: 0.51, 0.55, 0.97, 1
                                    padding: dp(12), dp(10)
                                Button:
                                    text: '⚡ 一键登录'
                                    font_size: dp(16)
                                    bold: True
                                    size_hint_y: None
                                    height: dp(48)
                                    background_normal: ''
                                    background_color: 0.39, 0.4, 0.95, 1
                                    color: 1, 1, 1, 1
                                    on_release: app.do_login()
                                Button:
                                    text: '🔌 注销下线'
                                    font_size: dp(14)
                                    bold: True
                                    size_hint_y: None
                                    height: dp(44)
                                    background_normal: ''
                                    background_color: 0.94, 0.27, 0.27, 1
                                    color: 1, 1, 1, 1
                                    on_release: app.do_logout()
                                Button:
                                    text: '🔄 刷新状态'
                                    font_size: dp(13)
                                    size_hint_y: None
                                    height: dp(40)
                                    background_normal: ''
                                    background_color: 0.2, 0.25, 0.33, 0.8
                                    color: 0.58, 0.64, 0.72, 1
                                    on_release: app.check_network()

                # ===== 安全管理页 =====
                Screen:
                    name: 'security'
                    ScrollView:
                        BoxLayout:
                            orientation: 'vertical'
                            size_hint_y: None
                            height: self.minimum_height
                            padding: dp(16)
                            spacing: dp(10)

                            # 安全状态卡片
                            BoxLayout:
                                orientation: 'vertical'
                                size_hint_y: None
                                height: dp(380)
                                padding: dp(16)
                                spacing: dp(6)
                                canvas.before:
                                    Color:
                                        rgba: 0.12, 0.16, 0.23, 0.85
                                    RoundedRectangle:
                                        pos: self.pos
                                        size: self.size
                                        radius: [dp(16)]
                                Label:
                                    text: '安全状态'
                                    font_size: dp(15)
                                    bold: True
                                    color: 0.51, 0.55, 0.97, 1
                                    size_hint_y: None
                                    height: dp(28)
                                    halign: 'left'
                                    text_size: self.size
                                Label:
                                    text: app.sec_mab_text
                                    font_size: dp(13)
                                    color: 0.88, 0.91, 0.94, 1
                                    halign: 'left'
                                    text_size: self.size
                                    size_hint_y: None
                                    height: dp(22)
                                Label:
                                    text: app.sec_device_text
                                    font_size: dp(13)
                                    color: 0.88, 0.91, 0.94, 1
                                    halign: 'left'
                                    text_size: self.size
                                    size_hint_y: None
                                    height: dp(22)
                                Label:
                                    text: app.sec_mac_text
                                    font_size: dp(13)
                                    color: 0.88, 0.91, 0.94, 1
                                    halign: 'left'
                                    text_size: self.size
                                    size_hint_y: None
                                    height: dp(22)
                                Label:
                                    text: app.sec_user_text
                                    font_size: dp(13)
                                    color: 0.88, 0.91, 0.94, 1
                                    halign: 'left'
                                    text_size: self.size
                                    size_hint_y: None
                                    height: dp(22)
                                Button:
                                    text: '关闭本机无感认证'
                                    font_size: dp(13)
                                    bold: True
                                    size_hint_y: None
                                    height: dp(42)
                                    background_normal: ''
                                    background_color: 0.94, 0.27, 0.27, 1
                                    color: 1, 1, 1, 1
                                    on_release: app.disable_mab()
                                Button:
                                    text: '刷新安全状态'
                                    font_size: dp(13)
                                    size_hint_y: None
                                    height: dp(40)
                                    background_normal: ''
                                    background_color: 0.2, 0.25, 0.33, 0.8
                                    color: 0.58, 0.64, 0.72, 1
                                    on_release: app.refresh_security()
                                Button:
                                    text: '本机下线 (注销网络)'
                                    font_size: dp(13)
                                    bold: True
                                    size_hint_y: None
                                    height: dp(42)
                                    background_normal: ''
                                    background_color: 0.8, 0.2, 0.2, 1
                                    color: 1, 1, 1, 1
                                    on_release: app.do_logout()

                            # 设备列表
                            BoxLayout:
                                orientation: 'vertical'
                                size_hint_y: None
                                height: self.minimum_height
                                padding: dp(16)
                                spacing: dp(8)
                                canvas.before:
                                    Color:
                                        rgba: 0.12, 0.16, 0.23, 0.85
                                    RoundedRectangle:
                                        pos: self.pos
                                        size: self.size
                                        radius: [dp(16)]
                                BoxLayout:
                                    size_hint_y: None
                                    height: dp(36)
                                    Label:
                                        text: '已绑定设备'
                                        font_size: dp(15)
                                        bold: True
                                        color: 0.51, 0.55, 0.97, 1
                                        halign: 'left'
                                        text_size: self.size
                                    Button:
                                        text: '全部解绑'
                                        size_hint_x: None
                                        width: dp(80)
                                        font_size: dp(12)
                                        bold: True
                                        background_normal: ''
                                        background_color: 0.94, 0.27, 0.27, 1
                                        color: 1, 1, 1, 1
                                        on_release: app.cancel_all()
                                BoxLayout:
                                    id: device_list
                                    orientation: 'vertical'
                                    size_hint_y: None
                                    height: self.minimum_height
                                    spacing: dp(6)

                # ===== 日志页 =====
                Screen:
                    name: 'log'
                    BoxLayout:
                        orientation: 'vertical'
                        padding: dp(16)
                        spacing: dp(8)
                        BoxLayout:
                            size_hint_y: None
                            height: dp(36)
                            Label:
                                text: '📋 运行日志'
                                font_size: dp(15)
                                bold: True
                                color: 0.51, 0.55, 0.97, 1
                                halign: 'left'
                                text_size: self.size
                            Button:
                                text: '清空'
                                size_hint_x: None
                                width: dp(60)
                                font_size: dp(12)
                                background_normal: ''
                                background_color: 0.2, 0.25, 0.33, 0.8
                                color: 0.58, 0.64, 0.72, 1
                                on_release: app.clear_log()
                        ScrollView:
                            id: log_scroll
                            canvas.before:
                                Color:
                                    rgba: 0.04, 0.06, 0.1, 0.8
                                RoundedRectangle:
                                    pos: self.pos
                                    size: self.size
                                    radius: [dp(10)]
                            Label:
                                id: log_label
                                text: app.log_text
                                font_size: dp(11)
                                color: 0.58, 0.64, 0.72, 1
                                markup: True
                                size_hint_y: None
                                height: self.texture_size[1] + dp(20)
                                text_size: self.width - dp(20), None
                                padding: dp(10), dp(10)
                                halign: 'left'
                                valign: 'top'
"""


# ==================== ePortal API ====================

class EPortalAPI:
    def __init__(self, portal_ip="10.228.9.7"):
        self.portal_ip = portal_ip
        self.base_url = f"http://{portal_ip}"

    def _request(self, url, data=None, timeout=8):
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) CampusNet/1.0",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        body = urllib.parse.urlencode(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            return {"status": resp.status, "body": resp.read().decode("utf-8", errors="replace")}
        except urllib.error.HTTPError as e:
            return {"status": e.code, "body": e.read().decode("utf-8", errors="replace")}
        except Exception as e:
            return {"status": 0, "body": "", "error": str(e)}

    def get_campus_ip(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            sock.connect((self.portal_ip, 80))
            ip = sock.getsockname()[0]
            sock.close()
            return ip
        except Exception:
            pass
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith("10."):
                    return ip
        except Exception:
            pass
        return ""

    def check_online(self):
        try:
            r = self._request("http://www.msftconnecttest.com/connecttest.txt", timeout=5)
            return r["status"] == 200 and "Microsoft" in r.get("body", "")
        except Exception:
            return False

    def login(self, username, password, service=""):
        campus_ip = self.get_campus_ip()
        if not campus_ip:
            return {"result": "fail", "message": "未检测到校园网IP"}

        qs = f"wlanuserip={campus_ip}&wlanacip=&wlanacname=&ssid=&nasip={self.portal_ip}&wlanparameter="
        url = f"{self.base_url}/eportal/InterFace.do?method=login"
        data = {
            "userId": username, "password": password, "service": service,
            "queryString": qs, "operatorPwd": "", "operatorUserId": "",
            "validcode": "", "passwordEncrypt": "false",
        }
        r = self._request(url, data)
        if r.get("error"):
            return {"result": "fail", "message": r["error"]}
        try:
            result = json.loads(r["body"])
            msg = result.get("message", "")
            if msg:
                try:
                    msg = msg.encode("latin-1").decode("utf-8")
                except Exception:
                    pass
                result["message"] = msg
            return result
        except json.JSONDecodeError:
            return {"result": "fail", "message": f"解析失败: {r['body'][:80]}"}

    def logout(self, user_index):
        url = f"{self.base_url}/eportal/InterFace.do?method=logout"
        r = self._request(url, {"userIndex": user_index})
        try:
            return json.loads(r["body"])
        except Exception:
            return {"result": "unknown"}

    def get_user_info(self, user_index):
        url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
        r = self._request(url, {"userIndex": user_index})
        if r.get("error"):
            return {"error": r["error"]}
        try:
            return json.loads(r["body"])
        except Exception:
            return {"error": "JSON解析失败"}

    def get_security(self, user_index):
        info = self.get_user_info(user_index)
        if "error" in info or info.get("result") == "fail":
            msg = info.get("error", info.get("message", "获取失败"))
            try:
                msg = msg.encode("latin-1").decode("utf-8")
            except Exception:
                pass
            return {"error": msg}
        devices = []
        mab = info.get("mabInfo", "")
        if mab and isinstance(mab, str):
            try:
                for m in json.loads(mab):
                    devices.append({
                        "mac": m.get("userMac", ""),
                        "name": m.get("deviceName", m.get("client_hostname", "")),
                        "userId": m.get("userId", ""),
                        "time": m.get("createTime", ""),
                    })
            except Exception:
                pass
        return {
            "userName": info.get("userName", ""),
            "userMac": info.get("userMac") or "",
            "hasMab": bool(info.get("hasMabInfo")),
            "devices": devices,
        }

    def cancel_mac(self, user_id, user_mac):
        url = f"{self.base_url}/eportal/InterFace.do?method=cancelMacWithUserNameAndMac"
        r = self._request(url, {"userId": user_id, "userMac": user_mac})
        try:
            return json.loads(r["body"])
        except Exception:
            return {"result": "fail"}

    def disable_mab(self, user_index):
        """关闭本机无感认证"""
        url = f"{self.base_url}/eportal/InterFace.do?method=cancelMab"
        r = self._request(url, {"userIndex": user_index})
        try:
            return json.loads(r["body"])
        except Exception:
            return {"result": "fail"}

    def cancel_all_mab(self, user_index):
        """全部取消绑定"""
        url = f"{self.base_url}/eportal/InterFace.do?method=cancelAllMab"
        r = self._request(url, {"userIndex": user_index})
        try:
            return json.loads(r["body"])
        except Exception:
            return {"result": "fail"}


# ==================== 主应用 ====================

class CampusNetApp(App):
    # Properties
    status_text = StringProperty("检测中...")
    status_detail = StringProperty("")
    status_color = ListProperty([0.4, 0.45, 0.58, 1])  # gray
    campus_ip_text = StringProperty("检测IP中...")
    current_tab = StringProperty("login")
    log_text = StringProperty("")
    saved_username = StringProperty("")

    # Security
    sec_mab_text = StringProperty("无感认证: --")
    sec_device_text = StringProperty("绑定设备: --")
    sec_mac_text = StringProperty("本机 MAC: --")
    sec_user_text = StringProperty("用户名: --")

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.api = EPortalAPI()
        self.user_index = ""
        self.devices = []
        self._config_path = ""

    def build(self):
        self.title = "校园网登录"
        Window.clearcolor = (0.06, 0.09, 0.16, 1)
        if platform != "android":
            Window.size = (380, 700)
        return Builder.load_string(KV)

    def on_start(self):
        # 配置文件路径
        if platform == "android":
            from android.storage import app_storage_path
            self._config_path = os.path.join(app_storage_path(), "config.json")
        else:
            self._config_path = os.path.join(
                os.path.dirname(os.path.abspath(__file__)), "mobile_app_config.json"
            )

        # 加载配置
        self._load_config()
        self.log("[启动] 校园网登录工具")
        self.log(f"[启动] Portal: {self.api.portal_ip}")

        # 异步检测网络
        Clock.schedule_once(lambda dt: self.check_network(), 0.5)

    def _load_config(self):
        try:
            with open(self._config_path, "r") as f:
                cfg = json.load(f)
            self.saved_username = cfg.get("username", "")
            self.user_index = cfg.get("userIndex", "")
            if self.user_index and len(self.user_index) < 20:
                self.user_index = ""
        except Exception:
            pass

    def _save_config(self, **extra):
        try:
            cfg = {"userIndex": self.user_index}
            try:
                with open(self._config_path, "r") as f:
                    cfg = json.load(f)
            except Exception:
                pass
            cfg.update(extra)
            cfg["userIndex"] = self.user_index
            with open(self._config_path, "w") as f:
                json.dump(cfg, f, ensure_ascii=False)
        except Exception:
            pass

    # ===== 日志 =====
    def log(self, msg, level="info"):
        t = time.strftime("%H:%M:%S")
        color_map = {
            "ok": "[color=22c55e]",
            "err": "[color=ef4444]",
            "warn": "[color=f59e0b]",
            "info": "[color=94a3b8]",
        }
        c = color_map.get(level, color_map["info"])
        self.log_text += f"{c}[{t}] {msg}[/color]\n"

    def clear_log(self):
        self.log_text = ""

    # ===== 标签切换 =====
    def switch_tab(self, tab):
        self.current_tab = tab
        self.root.ids.content_sm.current = tab

    # ===== 状态更新 =====
    def _set_status(self, state, text, detail=""):
        colors = {
            "online": [0.13, 0.77, 0.37, 1],
            "offline": [0.94, 0.27, 0.27, 1],
            "checking": [0.96, 0.62, 0.04, 1],
        }
        self.status_color = colors.get(state, [0.4, 0.45, 0.58, 1])
        self.status_text = text
        self.status_detail = detail

    # ===== 网络检测 =====
    def check_network(self):
        self._set_status("checking", "检测中...")
        self.log("检测网络状态...")
        threading.Thread(target=self._check_worker, daemon=True).start()

    def _check_worker(self):
        ip = self.api.get_campus_ip()
        online = self.api.check_online()
        Clock.schedule_once(lambda dt: self._on_check(ip, online))

    def _on_check(self, ip, online):
        self.campus_ip_text = f"IP: {ip}" if ip else "未检测到校园网IP"
        if online:
            self._set_status("online", "✅ 已在线", ip)
            self.log(f"网络在线 - {ip}", "ok")
        else:
            self._set_status("offline", "需要登录", ip)
            self.log(f"未在线 - {ip}", "warn")

    # ===== 登录 =====
    def do_login(self):
        username = self.root.ids.content_sm.get_screen('login').children[0].children[0].children[0].ids.get('username_input')
        # 更简单的方式: 遍历找到 TextInput
        inputs = self._find_inputs()
        if not inputs or len(inputs) < 2:
            self.log("无法获取输入框", "err")
            return

        username = inputs[0].text.strip()
        password = inputs[1].text.strip()
        if not username or not password:
            self.log("请输入账号和密码", "warn")
            return

        self._set_status("checking", "登录中...")
        self.log(f"发起登录: {username}")
        threading.Thread(target=self._login_worker, args=(username, password), daemon=True).start()

    def _find_inputs(self):
        """查找所有 TextInput"""
        from kivy.uix.textinput import TextInput
        results = []
        def walk(widget):
            if isinstance(widget, TextInput):
                results.append(widget)
            for child in widget.children:
                walk(child)
        walk(self.root)
        return results

    def _login_worker(self, username, password):
        result = self.api.login(username, password)
        Clock.schedule_once(lambda dt: self._on_login(result, username))

    def _on_login(self, result, username):
        if result.get("result") == "success":
            ui = result.get("userIndex", "")
            if ui and len(ui) > 20:
                self.user_index = ui
                self._save_config(username=username)
            self._set_status("online", "✅ 登录成功!", result.get("message", ""))
            self.log(f"登录成功! {result.get('message', '')}", "ok")
        else:
            msg = result.get("message", "未知错误")
            self._set_status("offline", "登录失败", msg)
            self.log(f"登录失败: {msg}", "err")

    # ===== 注销 =====
    def do_logout(self):
        if not self.user_index:
            self.log("无有效会话", "warn")
            return
        self._set_status("checking", "注销中...")
        self.log("正在注销...")
        threading.Thread(target=self._logout_worker, daemon=True).start()

    def _logout_worker(self):
        result = self.api.logout(self.user_index)
        Clock.schedule_once(lambda dt: self._on_logout(result))

    def _on_logout(self, result):
        if result.get("result") == "success":
            self._set_status("offline", "已注销", "网络已断开")
            self.log("注销成功!", "ok")
            self.user_index = ""
            self._save_config()
        else:
            self.log("注销结果: " + json.dumps(result, ensure_ascii=False)[:80], "warn")
            self._set_status("offline", "已注销(待确认)")

    # ===== 安全管理 =====
    def refresh_security(self):
        if not self.user_index:
            self.log("请先登录", "warn")
            return
        self.log("获取安全状态...")
        threading.Thread(target=self._sec_worker, daemon=True).start()

    def _sec_worker(self):
        result = self.api.get_security(self.user_index)
        Clock.schedule_once(lambda dt: self._on_security(result))

    def _on_security(self, sec):
        if "error" in sec:
            self.log(f"获取失败: {sec['error']}", "err")
            return

        has_mab = sec.get("hasMab", False)
        devices = sec.get("devices", [])
        mac = sec.get("userMac", "未知")

        self.sec_mab_text = f"无感认证: {'🟢 开启' if has_mab else '🔴 关闭'}"
        self.sec_device_text = f"绑定设备: {len(devices)} 台"
        self.sec_mac_text = f"本机 MAC: {mac}"
        self.sec_user_text = f"用户名: {sec.get('userName', '--')}"
        self.devices = devices

        # 更新设备列表
        self._update_device_list(devices)
        self.log(f"安全状态: 无感={'开' if has_mab else '关'}, 设备={len(devices)}台", "ok")

    def _update_device_list(self, devices):
        from kivy.uix.boxlayout import BoxLayout
        from kivy.uix.label import Label
        from kivy.uix.button import Button

        device_list = self.root.ids.content_sm.get_screen('security')
        # 找到 device_list 容器
        dl = self._find_widget_by_id(self.root, 'device_list')
        if not dl:
            return

        dl.clear_widgets()
        if not devices:
            lbl = Label(
                text="无绑定设备",
                font_size=dp(13),
                color=(0.4, 0.45, 0.58, 1),
                size_hint_y=None,
                height=dp(40),
            )
            dl.add_widget(lbl)
            return

        for d in devices:
            row = BoxLayout(
                orientation='horizontal',
                size_hint_y=None,
                height=dp(56),
                padding=(dp(10), dp(6)),
                spacing=dp(8),
            )
            # 用 canvas 画背景
            from kivy.graphics import Color, RoundedRectangle as RR
            with row.canvas.before:
                Color(0.08, 0.11, 0.18, 0.8)
                bg = RR(pos=row.pos, size=row.size, radius=[dp(10)])
            row.bind(pos=lambda w, p, bg=bg: setattr(bg, 'pos', p))
            row.bind(size=lambda w, s, bg=bg: setattr(bg, 'size', s))

            info = BoxLayout(orientation='vertical', size_hint_x=0.65)
            info.add_widget(Label(
                text=d.get("mac", "").upper(),
                font_size=dp(13), bold=True,
                color=(0.51, 0.55, 0.97, 1),
                halign='left', text_size=(None, None),
                size_hint_y=None, height=dp(22),
            ))
            info.add_widget(Label(
                text=f"{d.get('name', '设备')} · {d.get('time', '')}",
                font_size=dp(11),
                color=(0.58, 0.64, 0.72, 1),
                halign='left', text_size=(None, None),
                size_hint_y=None, height=dp(18),
            ))
            row.add_widget(info)

            uid = d.get("userId", "")
            umac = d.get("mac", "")

            btn_kick = Button(
                text="下线",
                font_size=dp(11),
                bold=True,
                size_hint=(None, None),
                size=(dp(50), dp(30)),
                pos_hint={'center_y': 0.5},
                background_normal='',
                background_color=(0.94, 0.27, 0.27, 1),
                color=(1, 1, 1, 1),
            )
            btn_kick.bind(on_release=lambda _, u=uid, m=umac: self.kick_device(u, m))

            btn_unbind = Button(
                text="解绑",
                font_size=dp(11),
                bold=True,
                size_hint=(None, None),
                size=(dp(50), dp(30)),
                pos_hint={'center_y': 0.5},
                background_normal='',
                background_color=(0.6, 0.15, 0.15, 1),
                color=(1, 1, 1, 1),
            )
            btn_unbind.bind(on_release=lambda _, u=uid, m=umac: self.kick_device(u, m))

            btn_box = BoxLayout(orientation='vertical', size_hint_x=None, width=dp(55), spacing=dp(2))
            btn_box.add_widget(btn_kick)
            btn_box.add_widget(btn_unbind)
            row.add_widget(btn_box)

            dl.add_widget(row)

    def _find_widget_by_id(self, widget, target_id):
        """递归查找 widget by id"""
        if hasattr(widget, 'id') and widget.id == target_id:
            return widget
        for child in widget.children:
            result = self._find_widget_by_id(child, target_id)
            if result:
                return result
        return None

    def disable_mab(self):
        """关闭本机无感认证"""
        if not self.user_index:
            self.log("请先登录", "warn")
            return
        self.log("关闭本机无感认证...")
        threading.Thread(
            target=self._disable_mab_worker,
            daemon=True
        ).start()

    def _disable_mab_worker(self):
        result = self.api.disable_mab(self.user_index)
        Clock.schedule_once(lambda dt: self._on_disable_mab(result))

    def _on_disable_mab(self, result):
        if result.get("result") == "success":
            self.log("无感认证已关闭", "ok")
            self.refresh_security()
        else:
            self.log(f"操作结果: {json.dumps(result, ensure_ascii=False)[:60]}", "warn")

    def cancel_all(self):
        """全部取消绑定并下线"""
        if not self.user_index:
            self.log("请先登录", "warn")
            return
        self.log("全部取消绑定...")
        threading.Thread(
            target=self._cancel_all_worker,
            daemon=True
        ).start()

    def _cancel_all_worker(self):
        result = self.api.cancel_all_mab(self.user_index)
        Clock.schedule_once(lambda dt: self._on_cancel_all(result))

    def _on_cancel_all(self, result):
        if result.get("result") == "success":
            self.log("已取消全部设备绑定", "ok")
            self.refresh_security()
        else:
            self.log(f"操作结果: {json.dumps(result, ensure_ascii=False)[:60]}", "warn")

    def kick_device(self, user_id, mac):
        self.log(f"取消设备绑定: {mac}")
        threading.Thread(
            target=self._kick_worker,
            args=(user_id, mac),
            daemon=True
        ).start()

    def _kick_worker(self, user_id, mac):
        result = self.api.cancel_mac(user_id, mac)
        Clock.schedule_once(lambda dt: self._on_kick(result, mac))

    def _on_kick(self, result, mac):
        if result.get("result") == "success":
            self.log(f"设备 {mac} 已下线", "ok")
            self.refresh_security()
        else:
            self.log(f"操作结果: {json.dumps(result, ensure_ascii=False)[:60]}", "warn")


if __name__ == "__main__":
    CampusNetApp().run()
