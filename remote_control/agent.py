#!/usr/bin/env python3
"""
校园网远程控制 - Agent (本地客户端)
功能: 自动上报状态、接收远程命令(下线/登录/刷新)
安装后自动运行，保持与控制面板的连接
"""
import json, time, os, sys, socket, uuid, hashlib, platform, traceback
import urllib.request, urllib.parse, urllib.error

# ============ 配置 ============
DEFAULT_SERVER = "http://localhost:9090"  # 控制面板地址
PORTAL_IP = "10.228.9.7"
HEARTBEAT_INTERVAL = 5  # 心跳间隔(秒)
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "agent_config.json")
AGENT_SCRIPT = os.path.abspath(__file__)
REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
REG_NAME = "CampusNetAgent"

# ============ 工具函数 ============

def get_mac():
    """获取本机 MAC 地址"""
    mac_int = uuid.getnode()
    mac_str = ":".join(f"{(mac_int >> (8 * i)) & 0xFF:02X}" for i in reversed(range(6)))
    return mac_str

def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def get_agent_id():
    """生成唯一 Agent ID (基于主机名+MAC)"""
    hostname = socket.gethostname()
    mac = get_mac()
    return hashlib.md5(f"{hostname}-{mac}".encode()).hexdigest()[:12]

def http_post(url, data, timeout=8):
    """发送 POST 请求 (仅用 urllib, 无需 requests)"""
    body = json.dumps(data).encode("utf-8")
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"error": str(e)}

def http_get(url, timeout=8):
    """发送 GET 请求"""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return resp.read().decode("utf-8")
    except:
        return None

# ============ 开机自启管理 (Windows) ============

def is_autostart_enabled():
    """检查是否已设置开机自启"""
    if platform.system() != "Windows":
        return False
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ)
        try:
            val, _ = winreg.QueryValueEx(key, REG_NAME)
            winreg.CloseKey(key)
            return bool(val)
        except FileNotFoundError:
            winreg.CloseKey(key)
            return False
    except Exception:
        return False

def enable_autostart():
    """启用开机自启 (写注册表 + 创建静默启动 VBS)"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    try:
        import winreg
        # 创建 VBS 静默启动脚本 (无黑窗)
        vbs_path = os.path.join(os.path.dirname(AGENT_SCRIPT), "start_agent.vbs")
        python_exe = sys.executable
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(f'Set ws = CreateObject("WScript.Shell")\n')
            f.write(f'ws.Run """{python_exe}"" ""{AGENT_SCRIPT}""", 0, False\n')
        # 写注册表
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, f'wscript.exe "{vbs_path}"')
        winreg.CloseKey(key)
        return True, "已启用开机自启"
    except Exception as e:
        return False, f"启用失败: {e}"

def disable_autostart():
    """禁用开机自启 (删注册表项)"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, REG_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        # 清理 VBS
        vbs_path = os.path.join(os.path.dirname(AGENT_SCRIPT), "start_agent.vbs")
        if os.path.exists(vbs_path):
            os.remove(vbs_path)
        return True, "已禁用开机自启"
    except Exception as e:
        return False, f"禁用失败: {e}"

# ============ 配置管理 ============

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[WARN] 保存配置失败: {e}")

# ============ 校园网操作 ============

class CampusNet:
    def __init__(self, portal_ip=PORTAL_IP):
        self.portal_ip = portal_ip
        self.base_url = f"http://{portal_ip}"
        self.user_index = ""
        self.username = ""
        self.password = ""

    def check_online(self):
        """检测是否在线, 返回 (online, campus_ip, user_index, message)"""
        import re
        try:
            # 1. 多种方式提取 userIndex
            if not self.user_index:
                for url in [
                    f"{self.base_url}/eportal/redirectortos498.portal",
                    f"{self.base_url}/eportal/",
                    f"{self.base_url}/eportal/success.jsp",
                ]:
                    try:
                        resp_text = http_get(url, timeout=5) or ""
                        # 在页面内容、JS跳转、URL参数中搜索 userIndex
                        m = re.search(r'userIndex[=:]\s*["\']?([a-fA-F0-9]{16,})', resp_text)
                        if m:
                            self.user_index = m.group(1)
                            print(f"  [NET] 从 {url} 提取到 userIndex")
                            break
                    except:
                        pass

            # 2. 用 userIndex 获取完整用户信息
            if self.user_index:
                try:
                    info_url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
                    data = urllib.parse.urlencode({"userIndex": self.user_index}).encode()
                    req = urllib.request.Request(info_url, data=data, method="POST")
                    req.add_header("Content-Type", "application/x-www-form-urlencoded")
                    with urllib.request.urlopen(req, timeout=5) as resp:
                        text = resp.read().decode("utf-8")
                        info = json.loads(text)
                        ui = info.get("userIndex", "")
                        name = info.get("userName", "")
                        uid = info.get("userId", "")
                        if ui or info.get("result") == "success":
                            if ui:
                                self.user_index = ui
                            if name and not self.username:
                                self.username = name
                            if uid and not self.username:
                                self.username = uid
                            return True, get_local_ip(), self.user_index, f"已认证: {name or uid}"
                        else:
                            print(f"  [NET] getOnlineUserInfo 失败: {info.get('message','')}")
                            self.user_index = ""
                except Exception as e:
                    print(f"  [NET] getOnlineUserInfo 异常: {e}")

            # 3. 直接调 getOnlineUserInfo (空 userIndex, Portal 按 IP 返回)
            try:
                info_url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
                req = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
                req.add_header("Content-Type", "application/x-www-form-urlencoded")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    text = resp.read().decode("utf-8")
                    info = json.loads(text)
                    # Portal 可能返回 result=success 或 result=wait, 只要有 userIndex 就算在线
                    ui = info.get("userIndex", "")
                    name = info.get("userName", "")
                    uid = info.get("userId", "")
                    if ui:
                        self.user_index = ui
                        if name and not self.username:
                            self.username = name
                        if uid and not self.username:
                            self.username = uid
                        return True, get_local_ip(), self.user_index, f"已认证: {name or uid}"
            except:
                pass

            # 4. 检测网络连通性
            try:
                urllib.request.urlopen("http://www.baidu.com", timeout=3)
                return True, get_local_ip(), self.user_index, "网络可用(未获取token)"
            except:
                return False, get_local_ip(), "", "网络不可用"

        except Exception as e:
            return False, get_local_ip(), "", str(e)

    def login(self, username, password, service=""):
        """登录校园网"""
        try:
            self.username = username
            self.password = password
            url = f"{self.base_url}/eportal/InterFace.do?method=login"
            data = urllib.parse.urlencode({
                "userId": username,
                "password": password,
                "service": service,
                "queryString": "",
                "operatorPwd": "",
                "operatorUserId": "",
                "validcode": "",
                "passwordEncrypt": "false",
            }).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                result = json.loads(text)
                if result.get("result") == "success":
                    self.user_index = result.get("userIndex", "")
                return result
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def cancel_mab(self):
        """取消本机无感认证 (cancelMab)"""
        if not self.user_index:
            return {"result": "fail", "message": "无 userIndex"}
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=cancelMab"
            data = urllib.parse.urlencode({"userIndex": self.user_index}).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                print(f"  [NET] cancelMab: {text[:120]}")
                return json.loads(text)
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def cancel_mac_by_name(self):
        """用账号+MAC取消无感认证绑定 (cancelMacWithUserNameAndMac)"""
        if not self.username:
            return {"result": "fail", "message": "无用户名"}
        try:
            mac = get_mac().replace(":", "")
            url = f"{self.base_url}/eportal/InterFace.do?method=cancelMacWithUserNameAndMac"
            data = urllib.parse.urlencode({"userId": self.username, "usermac": mac}).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                print(f"  [NET] cancelMacByName: {text[:120]}")
                return json.loads(text)
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def logout(self):
        """注销"""
        if not self.user_index:
            return {"result": "fail", "message": "无 userIndex"}
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=logout"
            data = urllib.parse.urlencode({"userIndex": self.user_index}).encode()
            req = urllib.request.Request(url, data=data, method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with urllib.request.urlopen(req, timeout=10) as resp:
                text = resp.read().decode("utf-8")
                result = json.loads(text)
                if result.get("result") == "success":
                    self.user_index = ""
                return result
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def full_logout(self):
        """完整下线: 取消无感认证 + 注销"""
        results = []
        # 1. 取消无感认证 (两种方式都试)
        r1 = self.cancel_mab()
        results.append(f"cancelMab: {r1.get('result','')}")
        r2 = self.cancel_mac_by_name()
        results.append(f"cancelMac: {r2.get('result','')}")
        # 2. 注销
        r3 = self.logout()
        results.append(f"logout: {r3.get('result','')}")
        ok = r3.get("result") == "success"
        return {"result": "success" if ok else "fail", "message": " | ".join(results)}

# ============ Agent 主类 ============

class Agent:
    def __init__(self, server_url=None):
        self.cfg = load_config()
        self.server_url = (server_url or self.cfg.get("server") or DEFAULT_SERVER).rstrip("/")
        self.agent_id = get_agent_id()
        self.hostname = socket.gethostname()
        self.mac = get_mac()
        self.start_time = time.time()
        self.net = CampusNet(self.cfg.get("portal_ip", PORTAL_IP))
        
        # 恢复保存的凭据
        self.net.username = self.cfg.get("username", "")
        self.net.password = self.cfg.get("password", "")
        self.net.user_index = self.cfg.get("user_index", "")
        
        # 自动重连设置
        self.reconnect_delay = self.cfg.get("reconnect_delay", 0)  # 0=禁用, >0=秒数
        self.reconnect_at = 0      # 计划重连的时间戳
        self.was_online = False     # 上一次检测是否在线

    def get_uptime(self):
        s = int(time.time() - self.start_time)
        if s < 60: return f"{s}秒"
        if s < 3600: return f"{s//60}分{s%60}秒"
        return f"{s//3600}时{(s%3600)//60}分"

    def build_status(self):
        """构建上报状态"""
        online, campus_ip, ui, msg = self.net.check_online()
        if ui:
            self.net.user_index = ui
        
        # 自动重连状态描述
        rc_status = "禁用"
        if self.reconnect_delay > 0:
            if self.reconnect_at > 0:
                left = int(self.reconnect_at - time.time())
                rc_status = f"{left}秒后重连" if left > 0 else "重连中..."
            else:
                rc_status = f"已启用 ({self.reconnect_delay}秒)"
        
        return {
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "platform": platform.system(),
            "local_ip": get_local_ip(),
            "campus_ip": campus_ip,
            "mac": self.mac,
            "username": self.net.username,
            "user_index": self.net.user_index,
            "net_online": online,
            "net_message": msg,
            "uptime": self.get_uptime(),
            "autostart": is_autostart_enabled(),
            "reconnect_delay": self.reconnect_delay,
            "reconnect_status": rc_status,
            "version": "1.0",
        }

    def heartbeat(self):
        """发送心跳并接收命令"""
        status = self.build_status()
        result = http_post(f"{self.server_url}/api/heartbeat", status)
        if "error" in result:
            print(f"  [!] 心跳失败: {result['error']}")
            return
        # 处理命令
        for cmd in result.get("commands", []):
            self.execute(cmd)

    def execute(self, cmd):
        """执行远程命令"""
        command = cmd.get("command", "")
        params = cmd.get("params", {})
        cmd_id = cmd.get("id", "")
        print(f"  [CMD] 执行: {command} (id={cmd_id})")
        
        success = False
        message = ""
        
        try:
            if command == "logout":
                r = self.net.full_logout()
                success = r.get("result") == "success"
                message = r.get("message", "已下线" if success else "下线失败")
                # 如果有自动重连，设置重连时间
                if success and self.reconnect_delay > 0:
                    self.reconnect_at = time.time() + self.reconnect_delay
                    message += f" | {self.reconnect_delay}秒后自动重连"

            elif command == "cancel_mab":
                r1 = self.net.cancel_mab()
                r2 = self.net.cancel_mac_by_name()
                success = r1.get("result")=="success" or r2.get("result")=="success"
                message = f"cancelMab:{r1.get('result','')} cancelMac:{r2.get('result','')}"

            elif command == "set_reconnect":
                delay = int(params.get("delay", 0))
                self.reconnect_delay = delay
                self.cfg["reconnect_delay"] = delay
                save_config(self.cfg)
                self.reconnect_at = 0
                success = True
                if delay > 0:
                    message = f"自动重连已启用: 下线后 {delay}秒重连"
                else:
                    message = "自动重连已禁用"

            elif command == "login_now":
                # 立即登录 (本地执行，不需要网络到服务器)
                if self.net.username and self.net.password:
                    r = self.net.login(self.net.username, self.net.password)
                    success = r.get("result") == "success"
                    message = r.get("message", "已登录" if success else "登录失败")
                else:
                    message = "无保存的账号密码"
                
            elif command == "login":
                username = params.get("username", self.net.username)
                password = params.get("password", self.net.password)
                if not username or not password:
                    message = "无账号密码，无法登录"
                else:
                    r = self.net.login(username, password, params.get("service", ""))
                    success = r.get("result") == "success"
                    message = r.get("message", "已登录" if success else "登录失败")
                    if success:
                        self.net.username = username
                        self.net.password = password
                        self._save_credentials()
                        
            elif command == "refresh":
                online, ip, ui, msg = self.net.check_online()
                success = True
                message = f"{'在线' if online else '离线'} - {ip} - {msg}"
                
            elif command == "set_credentials":
                self.net.username = params.get("username", "")
                self.net.password = params.get("password", "")
                self._save_credentials()
                success = True
                message = f"已设置凭据: {self.net.username}"

            elif command == "enable_autostart":
                success, message = enable_autostart()

            elif command == "disable_autostart":
                success, message = disable_autostart()
                
            else:
                message = f"未知命令: {command}"
                
        except Exception as e:
            message = f"执行异常: {e}"
            traceback.print_exc()
        
        # 回报结果
        print(f"  [RES] {command}: {'✓' if success else '✗'} {message}")
        http_post(f"{self.server_url}/api/report", {
            "agent_id": self.agent_id,
            "cmd_id": cmd_id,
            "success": success,
            "message": message,
        })

    def _save_credentials(self):
        self.cfg["username"] = self.net.username
        self.cfg["password"] = self.net.password
        self.cfg["user_index"] = self.net.user_index
        self.cfg["server"] = self.server_url
        save_config(self.cfg)

    def _check_auto_reconnect(self):
        """检查是否需要自动重连"""
        if self.reconnect_at <= 0:
            return
        if time.time() < self.reconnect_at:
            return
        # 到时间了，自动登录
        self.reconnect_at = 0
        if not self.net.username or not self.net.password:
            print("  [重连] 无保存的账号密码，跳过")
            return
        print(f"  [重连] 自动登录: {self.net.username}")
        r = self.net.login(self.net.username, self.net.password)
        ok = r.get("result") == "success"
        print(f"  [重连] {'\u2713 成功' if ok else '\u2717 失败'}: {r.get('message','')}")
        if not ok and self.reconnect_delay > 0:
            # 失败了，再试
            self.reconnect_at = time.time() + self.reconnect_delay
            print(f"  [重连] {self.reconnect_delay}秒后重试")

    def run(self):
        """主循环"""
        rc_info = f"已启用({self.reconnect_delay}秒)" if self.reconnect_delay > 0 else "禁用"
        print("=" * 50)
        print("  校园网远程控制 - Agent")
        print("=" * 50)
        print(f"  Agent ID : {self.agent_id}")
        print(f"  主机名   : {self.hostname}")
        print(f"  MAC      : {self.mac}")
        print(f"  本机 IP  : {get_local_ip()}")
        print(f"  服务器   : {self.server_url}")
        print(f"  用户名   : {self.net.username or '(未设置)'}")
        print(f"  自动重连 : {rc_info}")
        print("=" * 50)
        print("  Agent 运行中... (Ctrl+C 停止)\n")
        
        while True:
            try:
                self._check_auto_reconnect()
                self.heartbeat()
            except KeyboardInterrupt:
                print("\n  Agent 已停止")
                break
            except Exception as e:
                print(f"  [ERR] {e}")
            time.sleep(HEARTBEAT_INTERVAL)


# ============ 入口 ============

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="校园网远程控制 Agent")
    parser.add_argument("--server", default=None, help="控制面板地址 (如 http://your-server:9090)")
    parser.add_argument("--username", default=None, help="校园网账号")
    parser.add_argument("--password", default=None, help="校园网密码")
    parser.add_argument("--portal", default=None, help="Portal IP (默认 10.228.9.7)")
    parser.add_argument("--autostart", action="store_true", help="启用开机自启")
    parser.add_argument("--no-autostart", action="store_true", help="禁用开机自启")
    args = parser.parse_args()

    if args.autostart:
        ok, msg = enable_autostart()
        print(f"  {'✓' if ok else '✗'} {msg}")
    if args.no_autostart:
        ok, msg = disable_autostart()
        print(f"  {'✓' if ok else '✗'} {msg}")
    
    agent = Agent(server_url=args.server)
    
    if args.username:
        agent.net.username = args.username
    if args.password:
        agent.net.password = args.password
    if args.portal:
        agent.net.portal_ip = args.portal
        agent.net.base_url = f"http://{args.portal}"
    
    # 保存配置
    if args.username or args.password:
        agent._save_credentials()
    
    agent.run()
