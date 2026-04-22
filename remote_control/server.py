#!/usr/bin/env python3
"""
校园网远程控制面板 - Server
功能: 查看所有 Agent 状态、远程下线/登录、查看 token
"""
import json, time, uuid, os, threading
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

# ============ 数据存储 (内存) ============
agents = {}      # agent_id -> {状态信息}
commands = {}    # agent_id -> [待执行命令列表]
cmd_results = {} # cmd_id -> {执行结果}
lock = threading.Lock()

# ============ API: Agent 端 ============

@app.route("/api/heartbeat", methods=["POST"])
def heartbeat():
    """Agent 定时心跳上报"""
    data = request.json or {}
    aid = data.get("agent_id")
    if not aid:
        return jsonify({"error": "no agent_id"}), 400
    with lock:
        agents[aid] = {**data, "last_seen": time.time()}
        pending = commands.pop(aid, [])
    return jsonify({"commands": pending})

@app.route("/api/report", methods=["POST"])
def report():
    """Agent 回报命令执行结果"""
    data = request.json or {}
    cid = data.get("cmd_id")
    if cid:
        with lock:
            cmd_results[cid] = {
                "agent_id": data.get("agent_id"),
                "cmd_id": cid,
                "success": data.get("success", False),
                "message": data.get("message", ""),
                "time": time.time(),
            }
    return jsonify({"ok": True})

# ============ API: Web 面板 ============

@app.route("/api/agents")
def get_agents():
    now = time.time()
    result = []
    with lock:
        for aid, d in agents.items():
            item = dict(d)
            item["alive"] = (now - d.get("last_seen", 0)) < 30
            result.append(item)
    result.sort(key=lambda x: x.get("last_seen", 0), reverse=True)
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
    return jsonify({"ok": True, "cmd_id": cid})

@app.route("/api/results")
def get_results():
    with lock:
        items = list(cmd_results.values())
    items.sort(key=lambda x: x.get("time", 0), reverse=True)
    return jsonify(items[:50])

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
    <h2>📋 操作日志</h2>
    <div class="log-box" id="logBox"><div style="color:#9ca3af">等待操作...</div></div>
  </div>
</div>

<script>
const API = "";

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
async function confirmAction(){
  if(!pendingAction)return;
  const{agentId,action}=pendingAction;
  closeModal();
  await sendCmd(agentId,action);
}

async function setReconnect(agentId,delay){
  await sendCmd(agentId,'set_reconnect',{delay:parseInt(delay)});
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
      const now=Date.now()/1000;
      const dt=now-(a.last_seen||0);
      if(a.alive && a.net_online)online++;
      else if(a.alive)offline++;
      else away++;
    });
    
    document.getElementById("totalCnt").textContent=agents.length;
    document.getElementById("onlineCnt").textContent=online;
    document.getElementById("offlineCnt").textContent=offline;
    document.getElementById("awayCnt").textContent=away;
    
    if(!agents.length){
      grid.innerHTML='<div class="empty">暂无 Agent 连接，请在客户端启动 agent.py</div>';
      return;
    }
    
    grid.innerHTML=agents.map(a=>{
      const st=!a.alive?"away":a.net_online?"online":"offline";
      const stText=!a.alive?"失联":a.net_online?"在线":"离线";
      const badgeCls=!a.alive?"aw":a.net_online?"on":"off";
      return `
      <div class="agent ${st}">
        <div class="agent-head">
          <span class="agent-name">💻 ${a.hostname||a.agent_id}</span>
          <span class="badge ${badgeCls}">${stText}</span>
        </div>
        <div class="info-row"><span class="k">Agent ID</span><span class="v">${a.agent_id||"--"}</span></div>
        <div class="info-row"><span class="k">局域网 IP</span><span class="v">${a.local_ip||"--"}</span></div>
        <div class="info-row"><span class="k">校园网 IP</span><span class="v">${a.campus_ip||"--"}</span></div>
        <div class="info-row"><span class="k">MAC</span><span class="v">${a.mac||"--"}</span></div>
        <div class="info-row"><span class="k">用户名</span><span class="v">${a.username||"--"}</span></div>
        <div class="info-row"><span class="k">网络状态</span><span class="v">${a.net_online?"✅ 已认证":"❌ 未认证"}</span></div>
        <div class="info-row"><span class="k">最后心跳</span><span class="v">${ago(a.last_seen)}</span></div>
        <div class="info-row"><span class="k">运行时间</span><span class="v">${a.uptime||"--"}</span></div>
        <div class="info-row"><span class="k">自动重连</span><span class="v">${a.reconnect_status||"禁用"}</span></div>
        <div class="autostart-row">
          <span class="label">🚀 开机自启</span>
          <label class="toggle" onclick="toggleAutostart('${a.agent_id}','${a.hostname||a.agent_id}',${!!a.autostart})">
            <input type="checkbox" ${a.autostart?"checked":""} onclick="event.preventDefault()">
            <span class="slider"></span>
          </label>
        </div>
        <div class="actions">
          <button class="btn btn-red" onclick="sendCmd('${a.agent_id}','logout')">⏏ 下线+取消无感</button>
          <button class="btn btn-blue" onclick="sendCmd('${a.agent_id}','refresh')">� 刷新</button>
          <button class="btn btn-orange" onclick="sendCmd('${a.agent_id}','cancel_mab')">� 取消无感</button>
          <button class="btn" style="background:#f1f5f9;color:#64748b" onclick="toggleToken('${a.agent_id}')">� Token</button>
        </div>
        <div class="actions" style="margin-top:6px">
          <span style="font-size:12px;color:#6b7280;line-height:30px">⏰ 重连延迟:</span>
          <select id="rc-${a.agent_id}" style="padding:4px 8px;border-radius:6px;border:1px solid #e5e7eb;font-size:12px" onchange="setReconnect('${a.agent_id}',this.value)">
            <option value="0" ${a.reconnect_delay==0?"selected":""}>禁用</option>
            <option value="30" ${a.reconnect_delay==30?"selected":""}>30秒</option>
            <option value="60" ${a.reconnect_delay==60?"selected":""}>1分钟</option>
            <option value="180" ${a.reconnect_delay==180?"selected":""}>3分钟</option>
            <option value="300" ${a.reconnect_delay==300?"selected":""}>5分钟</option>
            <option value="600" ${a.reconnect_delay==600?"selected":""}>10分钟</option>
          </select>
        </div>
        <div class="token-box" id="tok-${a.agent_id}">${a.user_index||"无 token"}</div>
      </div>`;
    }).join("");
    
    // 拉取操作结果
    const rr=await fetch(API+"/api/results");
    const results=await rr.json();
    results.slice(0,5).forEach(r=>{
      // 只显示最近10秒内的新结果
      if(Date.now()/1000-r.time<10){
        addLog(`[${r.agent_id?.substring(0,8)}] ${r.message}`, r.success?"ok":"err");
      }
    });
    
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
    print(f"  ➜ http://localhost:{args.port}")
    print(f"  ➜ http://0.0.0.0:{args.port}  (局域网)")
    print("=" * 50)
    
    app.run(host=args.host, port=args.port, debug=False)
