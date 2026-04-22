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
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
            body = json.dumps({}).encode()
            req = urllib.request.Request(url, data=b"userIndex=", method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            
            # 先获取重定向页面检测
            test_url = f"{self.base_url}/eportal/redirectortos498.portal"
            try:
                resp_text = http_get(test_url, timeout=5)
                if resp_text and "Portal" in resp_text:
                    # 尝试从页面提取 userIndex
                    import re
                    m = re.search(r'userIndex=([a-f0-9]+)', resp_text)
                    if m:
                        self.user_index = m.group(1)
                        return True, get_local_ip(), self.user_index, "已在线"
            except:
                pass
            
            # 尝试检测网络连通性
            try:
                urllib.request.urlopen("http://www.baidu.com", timeout=3)
                return True, get_local_ip(), self.user_index, "网络可用"
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
                r = self.net.logout()
                success = r.get("result") == "success"
                message = r.get("message", "已下线" if success else "下线失败")
                
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

    def run(self):
        """主循环"""
        print("=" * 50)
        print("  校园网远程控制 - Agent")
        print("=" * 50)
        print(f"  Agent ID : {self.agent_id}")
        print(f"  主机名   : {self.hostname}")
        print(f"  MAC      : {self.mac}")
        print(f"  本机 IP  : {get_local_ip()}")
        print(f"  服务器   : {self.server_url}")
        print(f"  用户名   : {self.net.username or '(未设置)'}")
        print("=" * 50)
        print("  Agent 运行中... (Ctrl+C 停止)\n")
        
        while True:
            try:
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
