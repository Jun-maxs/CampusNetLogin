#!/usr/bin/env python3
"""
校园网登录工具 - 移动端 Web 版
在 Termux / 任意 Python 环境运行，通过浏览器操作

使用方法:
  1. 安装 Termux (Android)
  2. pkg install python
  3. python mobile_server.py
  4. 浏览器打开 http://localhost:8080

零外部依赖，仅使用 Python 标准库
"""

import http.server
import json
import re
import socket
import ssl
import urllib.request
import urllib.parse
import urllib.error
import threading
import time
import os
import sys

# 尝试导入桌面版 API (有 requests.Session, cookie 管理更好)
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from eportal_api import EPortalAPI as DesktopAPI
    HAS_DESKTOP_API = True
except ImportError:
    HAS_DESKTOP_API = False

# ==================== 配置 ====================
PORTAL_IP = "10.228.9.7"
PORTAL_PORT = 80
LISTEN_PORT = 8080
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mobile_config.json")

# ==================== ePortal API ====================

class EPortalMobile:
    """轻量 ePortal API (纯标准库)"""

    def __init__(self, portal_ip=PORTAL_IP):
        self.portal_ip = portal_ip
        self.base_url = f"http://{portal_ip}"
        self.cookies = {}

    def _request(self, url, data=None, timeout=8):
        """发送 HTTP 请求"""
        headers = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36",
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        # 附加 cookies
        if self.cookies:
            headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in self.cookies.items())

        if data:
            body = urllib.parse.urlencode(data).encode("utf-8")
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        else:
            req = urllib.request.Request(url, headers=headers)

        try:
            resp = urllib.request.urlopen(req, timeout=timeout)
            # 保存 cookies
            for header in resp.getheaders():
                if header[0].lower() == "set-cookie":
                    parts = header[1].split(";")[0].split("=", 1)
                    if len(parts) == 2:
                        self.cookies[parts[0].strip()] = parts[1].strip()
            return {
                "status": resp.status,
                "body": resp.read().decode("utf-8", errors="replace"),
                "url": resp.url,
            }
        except urllib.error.HTTPError as e:
            return {"status": e.code, "body": e.read().decode("utf-8", errors="replace"), "url": url}
        except Exception as e:
            return {"status": 0, "body": "", "error": str(e)}

    def get_campus_ip(self):
        """获取本机校园网 IP"""
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

    def check_network(self):
        """检测网络状态"""
        campus_ip = self.get_campus_ip()
        result = {"campus_ip": campus_ip, "online": False, "message": "", "userIndex": ""}
        try:
            resp = self._request("http://www.msftconnecttest.com/connecttest.txt", timeout=5)
            if resp["status"] == 200 and "Microsoft" in resp["body"]:
                result["online"] = True
                result["message"] = "已在线"
                # 已在线则用桌面版 API 提取当前 userIndex
                if HAS_DESKTOP_API:
                    try:
                        api = DesktopAPI(portal_ip=self.portal_ip)
                        ui = api._fetch_user_index([])
                        if ui:
                            result["userIndex"] = ui
                            print(f"[DEBUG] auto-fetched userIndex: {ui[:30]}...")
                    except Exception as e:
                        print(f"[DEBUG] fetch userIndex error: {e}")
            else:
                result["message"] = "需要认证"
        except Exception:
            result["message"] = "网络不通"
        return result

    def login(self, username, password, service=""):
        """登录"""
        campus_ip = self.get_campus_ip()
        if not campus_ip:
            return {"result": "fail", "message": "未检测到校园网IP"}

        query_string = f"wlanuserip={campus_ip}&wlanacip=&wlanacname=&ssid=&nasip={self.portal_ip}&wlanparameter="

        url = f"{self.base_url}/eportal/InterFace.do?method=login"
        data = {
            "userId": username,
            "password": password,
            "service": service,
            "queryString": query_string,
            "operatorPwd": "",
            "operatorUserId": "",
            "validcode": "",
            "passwordEncrypt": "false",
        }
        resp = self._request(url, data)
        if resp.get("error"):
            return {"result": "fail", "message": resp["error"]}

        try:
            result = json.loads(resp["body"])
            # 修复编码
            msg = result.get("message", "")
            if msg:
                try:
                    msg = msg.encode("latin-1").decode("utf-8")
                except Exception:
                    pass
                result["message"] = msg
            return result
        except json.JSONDecodeError:
            return {"result": "fail", "message": f"响应解析失败: {resp['body'][:100]}"}

    def logout(self, user_index):
        """注销"""
        url = f"{self.base_url}/eportal/InterFace.do?method=logout"
        resp = self._request(url, {"userIndex": user_index})
        if resp.get("error"):
            return {"result": "fail", "message": resp["error"]}
        try:
            return json.loads(resp["body"])
        except Exception:
            return {"result": "unknown", "raw": resp["body"][:200]}

    def get_user_info(self, user_index):
        """获取用户信息"""
        url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
        resp = self._request(url, {"userIndex": user_index})
        if resp.get("error"):
            return {"error": resp["error"]}
        try:
            if resp["body"].startswith("{"):
                data = json.loads(resp["body"])
                return data
            return {"error": f"非JSON响应: {resp['body'][:100]}"}
        except json.JSONDecodeError:
            return {"error": "JSON解析失败"}

    def get_security_status(self, user_index):
        """获取安全状态"""
        info = self.get_user_info(user_index)
        if "error" in info or info.get("result") == "fail":
            msg = info.get("error", info.get("message", "获取失败"))
            try:
                msg = msg.encode("latin-1").decode("utf-8")
            except Exception:
                pass
            return {"error": msg}

        devices = []
        mab_info = info.get("mabInfo", "")
        if mab_info and isinstance(mab_info, str):
            try:
                mab_list = json.loads(mab_info)
                for m in mab_list:
                    devices.append({
                        "mac": m.get("userMac", ""),
                        "name": m.get("deviceName", m.get("client_hostname", "")),
                        "userId": m.get("userId", ""),
                        "createTime": m.get("createTime", ""),
                    })
            except Exception:
                pass

        return {
            "userName": info.get("userName", ""),
            "userMac": info.get("userMac") or "",
            "hasMabInfo": bool(info.get("hasMabInfo")),
            "devices": devices,
            "deviceCount": len(devices),
            "mabInfoMaxCount": info.get("mabInfoMaxCount", ""),
        }

    def cancel_mac_device(self, user_id, user_mac, user_index=""):
        """取消设备无感认证绑定 (ePortal)"""
        global desktop_session_api
        if desktop_session_api is None:
            return {"result": "fail", "message": "requests 库不可用"}
        try:
            api = desktop_session_api
            url = f"{api.base_url}/eportal/InterFace.do?method=cancelMacWithUserNameAndMac"
            r1 = api.session.post(url, data={"userId": user_id, "usermac": user_mac}, timeout=8)
            print(f"[DEBUG] cancelMac: {r1.text[:200]}")
            try:
                j = json.loads(r1.text)
                try: j["message"] = j.get("message","").encode("latin-1").decode("utf-8")
                except: pass
                return j
            except:
                return {"result": "fail", "message": r1.text[:80]}
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    # ============ 自助服务系统 API (10.224.5.5:8080) ============
    def _selfservice_login(self, user_id, password):
        """登录自助服务门户, 返回 session"""
        import requests as req_lib
        ss = req_lib.Session()
        # 先访问首页获取 cookie/token
        base = "http://10.224.5.5:8080/selfservice"
        ss.get(f"{base}/module/userself/web/index_self.jsf", timeout=8)
        # 提交登录
        login_url = f"{base}/module/userself/web/index_self.jsf"
        resp = ss.post(login_url, data={
            "loginForm:userId": user_id,
            "loginForm:userPwd": password,
            "loginForm:loginBtn": "",
            "loginForm": "loginForm",
        }, timeout=8, allow_redirects=True)
        print(f"[DEBUG] selfservice login: status={resp.status_code}, url={resp.url[:80]}, cookies={list(ss.cookies.keys())}")
        return ss

    def selfservice_kick(self, user_id, password, device_ip):
        """通过自助服务系统踢设备下线"""
        try:
            ss = self._selfservice_login(user_id, password)
            url = "http://10.224.5.5:8080/selfservice/module/userself/web/userself_ajax.jsf?methodName=indexBean.kickUserBySelfForAjax"
            resp = ss.post(url, data={"key": f"{user_id}:{device_ip}"}, timeout=8)
            text = resp.text.strip()
            print(f"[DEBUG] kickUser: {text[:200]}")
            if text.startswith("false"):
                parts = text.split(":", 1)
                return {"result": "fail", "message": parts[1] if len(parts)>1 else "下线失败"}
            return {"result": "success", "message": f"设备 {device_ip} 已下线"}
        except Exception as e:
            print(f"[DEBUG] selfservice kick error: {e}")
            return {"result": "fail", "message": str(e)}

    def selfservice_cancel_nosense(self, user_id, password, nosense_uuid):
        """通过自助服务系统取消无感认证"""
        try:
            ss = self._selfservice_login(user_id, password)
            url = "http://10.224.5.5:8080/selfservice/module/userself/web/userself_ajax.jsf?methodName=indexBean.cancelUserMabInfoforAjax"
            resp = ss.post(url, data={"key": f"{user_id}:{nosense_uuid}"}, timeout=8)
            text = resp.text.strip()
            print(f"[DEBUG] cancelNoSense: {text[:200]}")
            if text.startswith("false"):
                parts = text.split(":", 1)
                return {"result": "fail", "message": parts[1] if len(parts)>1 else "关闭失败"}
            return {"result": "success", "message": "已关闭无感认证"}
        except Exception as e:
            return {"result": "fail", "message": str(e)}

    def selfservice_get_devices(self, user_id, password):
        """从自助服务系统获取在线设备列表(含IP)"""
        try:
            import re
            ss = self._selfservice_login(user_id, password)
            url = "http://10.224.5.5:8080/selfservice/module/webcontent/web/onlinedevice_list.jsf"
            resp = ss.get(url, timeout=8)
            html = resp.text
            # 提取设备: userIp4{uuid}, usermac{uuid}
            devices = []
            for m in re.finditer(r'id="userIp4([^"]+)"\s+type="hidden"\s+value="([^"]*)"', html):
                uuid = m.group(1)
                ip = m.group(2)
                mac_m = re.search(rf'id="usermac{re.escape(uuid)}"\s+type="hidden"\s+value="([^"]*)"', html)
                name_m = re.search(rf'id="inputId{re.escape(uuid)}"\s+type="hidden"\s+value="([^"]*)"', html)
                mac = mac_m.group(1) if mac_m else ""
                name = name_m.group(1) if name_m else ""
                devices.append({"uuid": uuid, "ip": ip, "mac": mac, "name": name})
            print(f"[DEBUG] selfservice devices: {len(devices)} found")
            return devices
        except Exception as e:
            print(f"[DEBUG] selfservice get_devices error: {e}")
            return []

    def disable_mab(self, user_index):
        """关闭无感认证 - 用持久化 session"""
        global desktop_session_api
        if desktop_session_api:
            try:
                r = desktop_session_api.cancel_mac(user_index)
                print(f"[DEBUG] disable_mab: {r}")
                return r
            except Exception as e:
                print(f"[DEBUG] disable_mab err: {e}")
        url = f"{self.base_url}/eportal/InterFace.do?method=cancelMab"
        resp = self._request(url, {"userIndex": user_index})
        try:
            return json.loads(resp["body"])
        except Exception:
            return {"result": "fail"}

    def cancel_all_mab(self, user_index):
        """全部取消绑定 - 用持久化 session"""
        global desktop_session_api
        if desktop_session_api:
            try:
                url = f"{desktop_session_api.base_url}/eportal/InterFace.do?method=cancelAllMab"
                resp = desktop_session_api.session.post(url, data={"userIndex": user_index}, timeout=8)
                print(f"[DEBUG] cancelAll raw: {resp.text[:200]}")
                try:
                    return json.loads(resp.text)
                except:
                    return {"result": "fail", "message": resp.text[:100]}
            except Exception as e:
                print(f"[DEBUG] cancelAll err: {e}")
        url = f"{self.base_url}/eportal/InterFace.do?method=cancelAllMab"
        resp = self._request(url, {"userIndex": user_index})
        try:
            return json.loads(resp["body"])
        except Exception:
            return {"result": "fail"}


# ==================== 配置管理 ====================

def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_config(cfg):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ==================== HTML 页面 ====================

MOBILE_HTML = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="theme-color" content="#f5f5f5">
<title>校园网助手</title>
<style>
*{margin:0;padding:0;box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{
  font-family:-apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",
    "Microsoft YaHei","Helvetica Neue",sans-serif;
  background:#f2f3f5;min-height:100vh;color:#333;
}
/* 顶栏 */
.topbar{
  background:#fff;padding:14px 20px;
  display:flex;align-items:center;justify-content:space-between;
  border-bottom:1px solid #eee;position:sticky;top:0;z-index:10;
}
.topbar h1{font-size:17px;font-weight:600;color:#333}
.topbar .ip{font-size:12px;color:#999}

/* 状态卡片 */
.net-status{
  margin:12px 16px;background:#fff;border-radius:12px;
  padding:16px;display:flex;align-items:center;gap:12px;
  box-shadow:0 1px 4px rgba(0,0,0,0.04);
}
.dot{width:10px;height:10px;border-radius:50%;background:#ccc;flex-shrink:0}
.dot.on{background:#07c160}
.dot.off{background:#ee3b3b}
.dot.wait{background:#ffb300;animation:blink 1s infinite}
@keyframes blink{50%{opacity:.3}}
.net-status .txt{font-size:14px;color:#333}
.net-status .sub{font-size:12px;color:#999;margin-top:2px}

/* 标签 */
.tabs{
  display:flex;margin:0 16px 12px;background:#fff;border-radius:10px;
  overflow:hidden;box-shadow:0 1px 4px rgba(0,0,0,0.04);
}
.tabs span{
  flex:1;text-align:center;padding:10px 0;font-size:14px;color:#666;
  cursor:pointer;position:relative;transition:color .2s;
}
.tabs span.on{color:#1677ff;font-weight:600}
.tabs span.on::after{
  content:'';position:absolute;bottom:0;left:25%;width:50%;height:2px;
  background:#1677ff;border-radius:2px;
}

/* 内容面板 */
.panel{display:none;padding:0 16px 24px}
.panel.on{display:block}

/* 通用卡片 */
.card{background:#fff;border-radius:12px;padding:16px;margin-bottom:12px;
  box-shadow:0 1px 4px rgba(0,0,0,0.04)}
.card-hd{font-size:14px;font-weight:600;color:#333;margin-bottom:12px;
  display:flex;align-items:center;justify-content:space-between}

/* 输入框 */
.ipt{
  width:100%;height:44px;padding:0 14px;border:1px solid #e8e8e8;
  border-radius:8px;font-size:15px;color:#333;background:#fafafa;
  outline:none;margin-bottom:10px;transition:border .2s;
}
.ipt:focus{border-color:#1677ff;background:#fff}
.ipt::placeholder{color:#bbb}

/* 按钮 */
.btn{
  width:100%;height:44px;border:none;border-radius:8px;font-size:15px;
  font-weight:600;cursor:pointer;transition:opacity .2s;
  display:flex;align-items:center;justify-content:center;
}
.btn:active{opacity:.7}
.btn:disabled{opacity:.4;cursor:default}
.btn+.btn{margin-top:8px}
.btn-blue{background:#1677ff;color:#fff}
.btn-red{background:#ee3b3b;color:#fff}
.btn-gray{background:#f5f5f5;color:#666;border:1px solid #e8e8e8}
.btn-orange{background:#ff8f1f;color:#fff}
.btn-green{background:#07c160;color:#fff}

/* 快捷按钮 */
.quick-row{display:flex;gap:8px;margin-bottom:12px}
.quick-row .qbtn{
  flex:1;height:36px;border:1px solid #e8e8e8;border-radius:8px;
  background:#fafbfc;font-size:13px;color:#1677ff;cursor:pointer;
  display:flex;align-items:center;justify-content:center;font-weight:500;
}
.quick-row .qbtn:active{background:#e8f0fe}

/* 一键清除按钮(登录后显示) */
.nuke-wrap{display:none;margin:12px 16px}
.nuke-wrap.show{display:block}
.nuke-btn{
  width:100%;height:48px;border:none;border-radius:12px;
  background:linear-gradient(90deg,#ee3b3b,#ff6b35);
  color:#fff;font-size:15px;font-weight:700;cursor:pointer;
  box-shadow:0 3px 12px rgba(238,59,59,0.25);
}
.nuke-btn:active{opacity:.8}

/* 信息行 */
.info-row{display:flex;justify-content:space-between;padding:10px 0;
  border-bottom:1px solid #f5f5f5;font-size:13px}
.info-row:last-child{border-bottom:none}
.info-row .k{color:#999}
.info-row .v{color:#333;font-weight:500}

/* 设备条 */
.dev{
  display:flex;align-items:center;padding:12px;margin-bottom:8px;
  background:#fafbfc;border-radius:8px;border:1px solid #f0f0f0;
}
.dev .left{flex:1;overflow:hidden}
.dev .mac{font-size:13px;color:#333;font-weight:600;font-family:monospace}
.dev .extra{font-size:11px;color:#999;margin-top:2px}
.dev .acts{display:flex;gap:6px;flex-shrink:0}
.dev .acts button{
  height:28px;padding:0 10px;border-radius:6px;border:none;
  font-size:12px;font-weight:500;cursor:pointer;
}
.dev .acts .k1{background:#fee;color:#ee3b3b}
.dev .acts .k1:active{background:#fcc}
.dev .acts .k2{background:#f0f0f0;color:#666}
.dev .acts .k2:active{background:#e0e0e0}

/* 日志 */
.log-box{
  background:#fafafa;border-radius:8px;padding:10px 12px;
  font-family:"SF Mono","Menlo",monospace;font-size:11px;line-height:1.7;
  max-height:260px;overflow-y:auto;color:#999;word-break:break-all;
}
.log-box .ok{color:#07c160}
.log-box .er{color:#ee3b3b}
.log-box .wr{color:#ff8f1f}
</style>
</head>
<body>

<div class="topbar">
  <h1>校园网助手</h1>
  <span class="ip" id="ipInfo">--</span>
</div>

<div class="net-status">
  <div class="dot wait" id="dot"></div>
  <div>
    <div class="txt" id="stTxt">检测中...</div>
    <div class="sub" id="stSub"></div>
  </div>
</div>

<div class="nuke-wrap" id="nukeWrap">
  <button class="nuke-btn" onclick="nukeAll()">本机下线</button>
</div>

<div class="tabs" id="tabs">
  <span class="on" onclick="go('login')">登录</span>
  <span onclick="go('security')">安全管理</span>
  <span onclick="go('log')">日志</span>
</div>

<!-- 登录 -->
<div class="panel on" id="p-login">
  <div class="card">
    <div class="card-hd">账号登录</div>
    <div class="quick-row">
      <button class="qbtn" onclick="fillJLB()">一键添加健力宝</button>
      <button class="qbtn" onclick="checkNetwork()">刷新网络</button>
    </div>
    <input class="ipt" type="text" id="username" placeholder="学号/工号">
    <input class="ipt" type="password" id="password" placeholder="密码">
    <button class="btn btn-blue" id="loginBtn" onclick="doLogin()">登 录</button>
    <button class="btn btn-red" onclick="doLogout()">注销下线</button>
  </div>
</div>

<!-- 安全管理 -->
<div class="panel" id="p-security">
  <div class="card">
    <div class="card-hd">
      <span>安全状态</span>
      <button class="qbtn" style="flex:none;width:auto;padding:0 12px;height:30px;font-size:12px"
        onclick="refreshSecurity()">刷新</button>
    </div>
    <div id="secInfo">
      <div class="info-row"><span class="k">无感认证</span><span class="v" id="mabSt">--</span></div>
      <div class="info-row"><span class="k">绑定设备</span><span class="v" id="devCnt">--</span></div>
      <div class="info-row"><span class="k">本机 MAC</span><span class="v" id="myMac">--</span></div>
      <div class="info-row"><span class="k">用户名</span><span class="v" id="secUser">--</span></div>
      <div class="info-row"><span class="k">userIndex</span><span class="v" id="secUI" style="font-size:11px;word-break:break-all;max-width:60%">--</span></div>
    </div>
    <button class="btn btn-red" style="margin-top:12px" onclick="disableMab()">关闭本机无感认证</button>
    <button class="btn btn-gray" onclick="doLogout()">本机下线（注销网络）</button>
  </div>
  <div class="card">
    <div class="card-hd">
      <span>绑定设备</span>
      <button class="qbtn" style="flex:none;width:auto;padding:0 12px;height:30px;font-size:12px;color:#ee3b3b;border-color:#fcc"
        onclick="cancelAll()">全部解绑</button>
    </div>
    <div id="devList">
      <div style="color:#bbb;text-align:center;padding:20px;font-size:13px">点击刷新查看设备</div>
    </div>
  </div>
</div>

<!-- 日志 -->
<div class="panel" id="p-log">
  <div class="card">
    <div class="card-hd">
      <span>运行日志</span>
      <button class="qbtn" style="flex:none;width:auto;padding:0 12px;height:30px;font-size:12px"
        onclick="clrLog()">清空</button>
    </div>
    <div class="log-box" id="logBox"></div>
  </div>
</div>

<script>
let UI="",PWD="",tab="login";

async function f(a,p={}){
  try{const r=await fetch("/api",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({action:a,...p})});return await r.json()}catch(e){return{error:e.message}}
}

let secTimer=null;
function go(t){
  tab=t;
  document.querySelectorAll('#tabs span').forEach((s,i)=>{
    s.className=["login","security","log"][i]===t?"on":"";
  });
  document.querySelectorAll('.panel').forEach(p=>p.className='panel');
  document.getElementById('p-'+t).className='panel on';
  if(secTimer){clearInterval(secTimer);secTimer=null}
  if(t==="security"){
    refreshSecurity();
    secTimer=setInterval(refreshSecurity,5000);
  }
}

function lg(m,t=""){
  const b=document.getElementById("logBox");
  const ts=new Date().toLocaleTimeString("zh-CN",{hour12:false});
  const c=t==="ok"?"ok":t==="err"?"er":t==="warn"?"wr":"";
  b.innerHTML+=`<div class="${c}">[${ts}] ${m}</div>`;
  b.scrollTop=b.scrollHeight;
}
function clrLog(){document.getElementById("logBox").innerHTML=""}

function st(s,t,d=""){
  const dot=document.getElementById("dot");
  dot.className="dot "+(s==="online"?"on":s==="offline"?"off":"wait");
  document.getElementById("stTxt").textContent=t;
  document.getElementById("stSub").textContent=d;
}

function showNuke(v){document.getElementById("nukeWrap").className="nuke-wrap"+(v?" show":"")}

function fillJLB(){
  document.getElementById("username").value="202510007842";
  document.getElementById("password").value="08252152";
  lg("已填充健力宝账号","ok");
}

async function checkNetwork(){
  st("wait","检测中...");lg("检测网络...");
  const r=await f("check_network");
  if(r.error){st("offline","检测失败",r.error);lg("失败: "+r.error,"err");return}
  document.getElementById("ipInfo").textContent=r.campus_ip||"--";
  if(r.online){
    st("online","已在线",r.campus_ip);lg("已在线 "+r.campus_ip,"ok");
    if(r.userIndex){
      UI=r.userIndex;showNuke(true);
      lg("自动识别 userIndex ("+UI.substring(0,20)+"...)","ok");
      f("save_config",{userIndex:UI});
    }
  }
  else{st("offline","未连接",r.message);lg(r.message||"需要登录","warn")}
}

async function doLogin(){
  const u=document.getElementById("username").value.trim();
  const p=document.getElementById("password").value.trim();
  if(!u||!p){lg("请填写账号密码","warn");return}
  const btn=document.getElementById("loginBtn");
  btn.disabled=true;btn.textContent="登录中...";
  st("wait","登录中...");lg("登录: "+u);
  const r=await f("login",{username:u,password:p,service:""});
  btn.disabled=false;btn.textContent="登 录";
  if(r.error){st("offline","登录失败",r.error);lg(r.error,"err");return}
  if(r.result==="success"){
    UI=r.userIndex||"";PWD=p;
    st("online","已连接",r.message||"");lg("登录成功 "+(r.message||""),"ok");
    f("save_config",{username:u,service:""});
    showNuke(true);
  }else{
    st("offline","登录失败",r.message||"");lg(r.message||JSON.stringify(r),"err");
  }
}

async function doLogout(){
  st("wait","注销中...");lg("注销...");
  const r=await f("logout",{userIndex:UI});
  if(r.result==="success"){
    st("offline","已断开","");lg("注销成功","ok");UI="";showNuke(false);
  }else{lg(r.message||JSON.stringify(r),"warn");st("offline","已断开(待确认)")}
}

async function refreshSecurity(){
  if(!UI){lg("请先登录","warn");return}
  const u=document.getElementById("username").value.trim();
  const p=document.getElementById("password").value.trim()||PWD;
  lg("获取安全状态...");
  // ePortal 安全状态
  const r=await f("security_status",{userIndex:UI});
  if(r.error){lg(r.error,"err");return}
  document.getElementById("mabSt").textContent=r.hasMabInfo?"已开启":"已关闭";
  document.getElementById("mabSt").style.color=r.hasMabInfo?"#07c160":"#ee3b3b";
  document.getElementById("myMac").textContent=r.userMac||"--";
  document.getElementById("secUser").textContent=r.userName||"--";
  document.getElementById("secUI").textContent=UI||"--";
  // 自助服务系统获取在线设备(含IP)
  const dl=document.getElementById("devList");
  if(u&&p){
    const sd=await f("selfservice_devices",{userId:u,password:p});
    const devs=sd.devices||[];
    document.getElementById("devCnt").textContent=devs.length+" 台";
    if(!devs.length){
      dl.innerHTML='<div style="color:#bbb;text-align:center;padding:20px;font-size:13px">无在线设备</div>';
    }else{
      dl.innerHTML=devs.map((d,i)=>`
        <div class="dev">
          <div class="left">
            <div class="mac">${(d.mac||"").toUpperCase()}</div>
            <div class="extra">${d.name||"设备"+(i+1)} · IP: ${d.ip||""}</div>
          </div>
          <div class="acts">
            <button class="k1" onclick="kickDev('${d.ip}')">下线</button>
            <button class="k2" onclick="unbindDev('${d.uuid}')">解绑</button>
          </div>
        </div>`).join("");
    }
  }else{
    document.getElementById("devCnt").textContent=(r.deviceCount||0)+" 台";
    dl.innerHTML='<div style="color:#f90;text-align:center;padding:20px;font-size:13px">填写账号密码后可管理在线设备</div>';
  }
  lg("安全状态已刷新","ok");
}

async function kickDev(ip){
  const u=document.getElementById("username").value.trim();
  const p=document.getElementById("password").value.trim()||PWD;
  if(!u||!p){lg("需要账号密码","warn");return}
  lg("下线设备: "+ip+"...");
  const r=await f("selfservice_kick",{userId:u,password:p,deviceIp:ip});
  if(r.result==="success"){lg(ip+" 已下线","ok");refreshSecurity()}
  else{lg(r.message||JSON.stringify(r),"warn")}
}

async function unbindDev(uuid){
  const u=document.getElementById("username").value.trim();
  const p=document.getElementById("password").value.trim()||PWD;
  if(!u||!p){lg("需要账号密码","warn");return}
  lg("解绑设备...");
  const r=await f("selfservice_cancel_nosense",{userId:u,password:p,uuid:uuid});
  if(r.result==="success"){lg("已解绑","ok");refreshSecurity()}
  else{lg(r.message||JSON.stringify(r),"warn")}
}

async function disableMab(){
  if(!UI){lg("请先登录","warn");return}
  lg("关闭无感认证...");
  const r=await f("disable_mab",{userIndex:UI});
  if(r.result==="success"){lg("无感认证已关闭","ok");refreshSecurity()}
  else{lg(r.message||JSON.stringify(r),"warn")}
}

async function cancelAll(){
  if(!UI){lg("请先登录","warn");return}
  lg("全部解绑...");
  const r=await f("cancel_all",{userIndex:UI});
  if(r.result==="success"){lg("已全部解绑","ok");refreshSecurity()}
  else{lg(r.message||JSON.stringify(r),"warn")}
}

async function nukeAll(){
  if(!UI){lg("请先登录","warn");return}
  st("wait","注销中...");lg("本机下线...");
  const r=await f("logout",{userIndex:UI});
  if(r.result==="success"){
    st("offline","已断开","");lg("本机已下线","ok");
    UI="";showNuke(false);
  }else{lg("下线: "+(r.message||JSON.stringify(r)),"warn")}
}

(async()=>{
  const cfg=await f("load_config");
  if(cfg.username)document.getElementById("username").value=cfg.username;
  if(cfg.userIndex){UI=cfg.userIndex;showNuke(true)}
  checkNetwork();
})();
</script>
</body>
</html>
"""

# ==================== HTTP 服务器 ====================

api_instance = EPortalMobile()
# 持久化的桌面版 API 实例 (保持 session cookies)
desktop_session_api = DesktopAPI(portal_ip=PORTAL_IP) if HAS_DESKTOP_API else None

class MobileHandler(http.server.BaseHTTPRequestHandler):
    """处理 HTTP 请求"""

    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{self.log_date_time_string()}] {args[0] if args else ''}")

    def do_GET(self):
        """返回 HTML 页面"""
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(MOBILE_HTML.encode("utf-8"))
        else:
            self.send_error(404)

    def do_POST(self):
        """处理 API 请求"""
        if self.path != "/api":
            self.send_error(404)
            return

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length).decode("utf-8")
        try:
            req = json.loads(body)
        except json.JSONDecodeError:
            self._json_response({"error": "无效请求"})
            return

        action = req.get("action", "")
        result = self._handle_action(action, req)
        self._json_response(result)

    def _json_response(self, data):
        """发送 JSON 响应"""
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_action(self, action, req):
        """处理不同 API 动作"""
        global api_instance

        if action == "check_network":
            return api_instance.check_network()

        elif action == "login":
            # 用桌面版 API 登录，保持 session
            if desktop_session_api is not None:
                try:
                    username = req.get("username", "")
                    password = req.get("password", "")
                    service = req.get("service", "")
                    login_result = desktop_session_api.login(username, password, service)
                    result = {
                        "result": "success" if login_result.success else "fail",
                        "message": login_result.message,
                        "userIndex": login_result.user_index,
                    }
                    if login_result.success and login_result.user_index:
                        cfg = load_config()
                        cfg["userIndex"] = login_result.user_index
                        cfg["username"] = username
                        save_config(cfg)
                    print(f"[DEBUG] login via desktop API: ui={login_result.user_index[:30] if login_result.user_index else 'EMPTY'}, session_cookies={list(desktop_session_api.session.cookies.keys())}")
                    return result
                except Exception as e:
                    print(f"[DEBUG] desktop login error: {e}")
            # 回退到 urllib
            result = api_instance.login(
                req.get("username", ""),
                req.get("password", ""),
                req.get("service", ""),
            )
            if result.get("result") == "success":
                ui = result.get("userIndex", "")
                cfg = load_config()
                cfg["userIndex"] = ui
                save_config(cfg)
            return result

        elif action == "logout":
            ui = req.get("userIndex", "")
            if ui:
                return api_instance.logout(ui)
            # 尝试用保存的 userIndex
            cfg = load_config()
            ui = cfg.get("userIndex", "")
            if ui:
                result = api_instance.logout(ui)
                cfg["userIndex"] = ""
                save_config(cfg)
                return result
            return {"result": "fail", "message": "无有效会话"}

        elif action == "security_status":
            ui = req.get("userIndex", "")
            if not ui:
                cfg = load_config()
                ui = cfg.get("userIndex", "")
            if not ui:
                return {"error": "请先登录"}
            return api_instance.get_security_status(ui)

        elif action == "kick_device":
            ui = req.get("userIndex", "")
            if not ui:
                cfg = load_config()
                ui = cfg.get("userIndex", "")
            return api_instance.cancel_mac_device(
                req.get("userId", ""),
                req.get("userMac", ""),
                ui,
            )

        elif action == "disable_mab":
            ui = req.get("userIndex", "")
            if not ui:
                return {"result": "fail", "message": "无会话"}
            return api_instance.disable_mab(ui)

        elif action == "cancel_all":
            ui = req.get("userIndex", "")
            if not ui:
                return {"result": "fail", "message": "无会话"}
            return api_instance.cancel_all_mab(ui)

        elif action == "selfservice_kick":
            return api_instance.selfservice_kick(
                req.get("userId", ""),
                req.get("password", ""),
                req.get("deviceIp", ""),
            )

        elif action == "selfservice_devices":
            devs = api_instance.selfservice_get_devices(
                req.get("userId", ""),
                req.get("password", ""),
            )
            return {"result": "success", "devices": devs}

        elif action == "selfservice_cancel_nosense":
            return api_instance.selfservice_cancel_nosense(
                req.get("userId", ""),
                req.get("password", ""),
                req.get("uuid", ""),
            )

        elif action == "save_config":
            cfg = load_config()
            if "username" in req:
                cfg["username"] = req.get("username", "")
            if "service" in req:
                cfg["service"] = req.get("service", "")
            if "userIndex" in req:
                cfg["userIndex"] = req.get("userIndex", "")
            save_config(cfg)
            return {"result": "ok"}

        elif action == "load_config":
            return load_config()

        return {"error": f"未知操作: {action}"}


def main():
    print("=" * 50)
    print("  校园网登录工具 - 移动端 Web 版")
    print("=" * 50)

    # 获取本机 IP
    campus_ip = ""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.settimeout(3)
        sock.connect((PORTAL_IP, 80))
        campus_ip = sock.getsockname()[0]
        sock.close()
    except Exception:
        try:
            campus_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            campus_ip = "127.0.0.1"

    print(f"\n  Portal: {PORTAL_IP}")
    print(f"  本机 IP: {campus_ip}")
    print(f"\n  请在浏览器打开:")
    print(f"  ➜ http://localhost:{LISTEN_PORT}")
    if campus_ip != "127.0.0.1":
        print(f"  ➜ http://{campus_ip}:{LISTEN_PORT}  (局域网)")
    print(f"\n  按 Ctrl+C 停止服务")
    print("=" * 50)

    server = http.server.HTTPServer(("0.0.0.0", LISTEN_PORT), MobileHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止")
        server.server_close()


if __name__ == "__main__":
    main()
