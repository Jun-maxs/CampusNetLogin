"""
锐捷 ePortal 校园网认证 API 模块
逆向自 AuthInterFace.js + success.jsp 的认证流程
"""

import requests
import time
import json
import re
import logging
import socket
from urllib.parse import urlencode, quote
from typing import Optional, Callable
from dataclasses import dataclass, field

logger = logging.getLogger("eportal")


@dataclass
class LoginResult:
    success: bool = False
    message: str = ""
    user_index: str = ""
    keepalive_interval: int = 0
    raw: dict = field(default_factory=dict)


@dataclass
class NetworkStatus:
    online: bool = False
    need_login: bool = False
    portal_ip: str = ""
    redirect_url: str = ""
    user_index: str = ""
    message: str = ""
    debug_log: list = field(default_factory=list)


class EPortalAPI:
    """锐捷 ePortal 认证接口封装"""

    def __init__(self, portal_ip: str = "10.228.9.7", portal_port: int = 80, timeout: int = 8):
        self.portal_ip = portal_ip
        self.portal_port = portal_port
        self.timeout = timeout
        self.base_url = f"http://{portal_ip}:{portal_port}" if portal_port != 80 else f"http://{portal_ip}"
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })

    def detect_network_status(self) -> NetworkStatus:
        """
        检测当前网络状态:
        1. 尝试访问外网 -> 如果被重定向到Portal -> 需要登录
        2. 尝试访问Portal success页面 -> 如果有userIndex -> 已登录
        """
        status = NetworkStatus()

        # 方法1: 尝试访问一个稳定的HTTP地址，看是否被重定向到Portal
        test_urls = [
            "http://www.msftconnecttest.com/connecttest.txt",
            "http://captive.apple.com/hotspot-detect.html",
            "http://www.gstatic.com/generate_204",
        ]

        for test_url in test_urls:
            try:
                status.debug_log.append(f"[探测] GET {test_url}")
                resp = self.session.get(test_url, timeout=self.timeout, allow_redirects=False)
                status.debug_log.append(f"  → HTTP {resp.status_code}, len={len(resp.text)}")

                if resp.status_code in (301, 302):
                    redirect_url = resp.headers.get("Location", "")
                    status.debug_log.append(f"  → 重定向到: {redirect_url[:120]}")
                    if self.portal_ip in redirect_url or "eportal" in redirect_url.lower():
                        status.need_login = True
                        status.redirect_url = redirect_url
                        status.portal_ip = self.portal_ip
                        status.message = "未登录 - 检测到Portal重定向"
                        return status

                if resp.status_code == 200:
                    status.online = True
                    status.message = "已在线"
                    campus_ip = self._get_campus_ip()
                    status.debug_log.append(f"[在线] 外网可访问, 校园网IP={campus_ip}")
                    status.user_index = self._fetch_user_index(status.debug_log)
                    return status

                if resp.status_code == 200 and self.portal_ip in resp.text:
                    status.need_login = True
                    status.message = "未登录 - 被Portal劫持"
                    return status

            except requests.exceptions.ConnectionError as e:
                status.debug_log.append(f"  → 连接失败: {e}")
                continue
            except requests.exceptions.Timeout:
                status.debug_log.append(f"  → 超时")
                continue
            except Exception as e:
                status.debug_log.append(f"  → 异常: {e}")
                continue

        # 方法2: 直接尝试访问Portal
        portal_url = f"{self.base_url}/eportal/"
        status.debug_log.append(f"[方法2] GET {portal_url}")
        try:
            resp = self.session.get(portal_url, timeout=self.timeout, allow_redirects=True)
            status.debug_log.append(f"  → HTTP {resp.status_code}")
            status.debug_log.append(f"  → 最终URL: {resp.url[:150]}")
            status.debug_log.append(f"  → 内容前200字: {resp.text[:200]}")

            if resp.status_code == 200:
                if "success" in resp.url:
                    status.online = True
                    match = re.search(r'userIndex=([a-f0-9_.]+)', resp.url)
                    if match:
                        status.user_index = match.group(1)
                        status.debug_log.append(f"  → 从URL提取: {status.user_index}")
                    if not status.user_index:
                        match = re.search(r'userIndex\s*[=:]\s*["\']?([a-f0-9_.]+)', resp.text)
                        if match:
                            status.user_index = match.group(1)
                            status.debug_log.append(f"  → 从内容提取: {status.user_index}")
                    if not status.user_index:
                        status.debug_log.append(f"  → 仍未提取到userIndex!")
                    status.message = "已在线 (从Portal确认)"
                else:
                    status.need_login = True
                    status.message = "未登录 - Portal登录页可访问"
            return status
        except Exception as e:
            status.debug_log.append(f"  → 异常: {e}")
            status.message = f"网络异常: {e}"
            return status

    def _fetch_user_index(self, debug_log: list = None) -> str:
        """
        已在线时，访问Portal获取当前会话的userIndex
        Portal在已登录状态下会重定向到 success.jsp?userIndex=xxx
        """
        if debug_log is None:
            debug_log = []

        # 尝试多个可能的Portal入口
        urls_to_try = [
            f"{self.base_url}/eportal/index.jsp",
            f"{self.base_url}/eportal/",
            f"{self.base_url}/eportal/success.jsp",
        ]

        for url in urls_to_try:
            try:
                debug_log.append(f"[获取userIndex] GET {url}")
                resp = self.session.get(url, timeout=self.timeout, allow_redirects=True)
                debug_log.append(f"  → HTTP {resp.status_code}, URL: {resp.url[:120]}")

                # 检测 gzip 压缩内容并解压
                content = resp.text
                if resp.content[:2] == b'\x1f\x8b':  # gzip magic bytes
                    import gzip
                    try:
                        content = gzip.decompress(resp.content).decode('utf-8', errors='replace')
                        debug_log.append(f"  → gzip解压成功, len={len(content)}")
                    except Exception:
                        debug_log.append(f"  → gzip解压失败, 跳过")
                        continue
                else:
                    debug_log.append(f"  → 内容长度: {len(content)}")

                # 只显示可读文本的前150字
                preview = content[:150].replace('\r', '').replace('\n', ' ').strip()
                if preview:
                    debug_log.append(f"  → 预览: {preview[:100]}")

                if resp.status_code == 200:
                    # 从最终URL提取
                    match = re.search(r'userIndex=([a-f0-9_.]{20,})', resp.url, re.IGNORECASE)
                    if match:
                        ui = match.group(1)
                        debug_log.append(f"  → 从URL匹配到: {ui[:40]}...")
                        return ui

                    # 从页面内容提取 - 只接受真实的userIndex值(长hex串)
                    patterns = [
                        r'var\s+userIndex\s*=\s*["\']([a-f0-9_.]{20,})["\']',
                        r'"userIndex"\s*:\s*"([a-f0-9_.]{20,})"',
                        r'userIndex\s*=\s*["\']([a-f0-9_.]{20,})["\']',
                        r'userIndex=([a-f0-9_.]{20,})(?:[&"\s<>]|$)',
                    ]
                    for pat in patterns:
                        match = re.search(pat, content, re.IGNORECASE)
                        if match:
                            ui = match.group(1)
                            # 二次验证: 必须是长hex串,不能包含JS语法字符
                            if len(ui) > 20 and '+' not in ui and '(' not in ui:
                                debug_log.append(f"  → 匹配到: {ui[:40]}...")
                                return ui
                            else:
                                debug_log.append(f"  → 疑似假匹配,跳过: {ui[:40]}")

                    debug_log.append(f"  → 未匹配")

            except Exception as e:
                debug_log.append(f"  → 异常: {e}")
                continue

        debug_log.append("[获取userIndex] 所有URL均失败")
        return ""

    def _get_local_mac(self) -> str:
        """获取本机连接校园网的网卡MAC地址"""
        import subprocess
        try:
            campus_ip = self._get_campus_ip()
            if not campus_ip:
                return ""
            # 用 arp -a 查自己不行，用 getmac 或 ipconfig
            result = subprocess.run(
                ["getmac", "/v", "/fo", "csv", "/nh"],
                capture_output=True, text=True, timeout=5, encoding="gbk", errors="replace"
            )
            for line in result.stdout.strip().split("\n"):
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 3:
                    mac = parts[1].replace("-", "").upper()
                    if len(mac) == 12 and mac != "N/A" and "Media" not in parts[2]:
                        return mac
        except Exception:
            pass
        # 备用: uuid方式
        try:
            import uuid
            mac_int = uuid.getnode()
            return ":".join(f"{(mac_int >> (8 * (5 - i))) & 0xFF:02X}" for i in range(6))
        except Exception:
            return ""

    def _get_campus_ip(self) -> str:
        """
        获取本机在校园网中的IP (通常是10.x.x.x网段)
        通过连接Portal服务器来确定正确的出口IP
        """
        # 方法1: 通过连接Portal确定路由出口IP (最可靠)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.settimeout(3)
            sock.connect((self.portal_ip, 80))
            ip = sock.getsockname()[0]
            sock.close()
            if ip and not ip.startswith("127."):
                return ip
        except Exception:
            pass

        # 方法2: 遍历所有IP，优选10.x.x.x网段
        try:
            for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
                ip = info[4][0]
                if ip.startswith("10."):
                    return ip
        except Exception:
            pass

        return ""

    def _get_query_string(self, debug_log: list = None) -> str:
        """
        获取Portal登录所需的queryString
        多种策略:
        1. 通过外网重定向获取
        2. 从Portal登录页HTML中提取
        3. 从Portal的302重定向中获取
        """
        if debug_log is None:
            debug_log = []

        # 策略1: 通过外网重定向获取
        try:
            debug_log.append("[queryString] 策略1: 外网重定向")
            resp = self.session.get(
                "http://www.msftconnecttest.com/connecttest.txt",
                timeout=self.timeout, allow_redirects=False
            )
            debug_log.append(f"  → HTTP {resp.status_code}")
            if resp.status_code in (301, 302):
                redirect_url = resp.headers.get("Location", "")
                debug_log.append(f"  → 重定向: {redirect_url[:200]}")
                if "?" in redirect_url:
                    qs = redirect_url.split("?", 1)[1]
                    debug_log.append(f"  → 提取到queryString: {qs[:100]}...")
                    return qs
            else:
                debug_log.append(f"  → 已在线(无重定向)")
        except Exception as e:
            debug_log.append(f"  → 异常: {e}")

        # 策略2: 从Portal登录页HTML中提取queryString
        try:
            debug_log.append("[queryString] 策略2: 从登录页HTML提取")
            resp = self.session.get(
                f"{self.base_url}/eportal/",
                timeout=self.timeout, allow_redirects=True
            )
            debug_log.append(f"  → HTTP {resp.status_code}, len={len(resp.text)}")

            # 从登录页中提取 (通常在form action或JS变量中)
            patterns = [
                r'queryString\s*[=:]\s*["\']([^"\']+)',
                r'name=["\']queryString["\'][^>]*value=["\']([^"\']*)',
                r'value=["\']([^"\']*)["\'][^>]*name=["\']queryString',
                r'InterFace\.do\?method=login[^"\']*queryString=([^&"\']+)',
                r'wlanuserip=([^&"\'<>\s]+[^"\'<>\s]*)',
            ]
            for pat in patterns:
                match = re.search(pat, resp.text, re.IGNORECASE)
                if match:
                    qs = match.group(1)
                    debug_log.append(f"  → 模式匹配: {qs[:100]}")
                    return qs

            # 检查URL是否有queryString
            if "?" in resp.url:
                qs = resp.url.split("?", 1)[1]
                if qs:
                    debug_log.append(f"  → 从URL提取: {qs[:100]}")
                    return qs

            debug_log.append(f"  → 未从HTML提取到")
        except Exception as e:
            debug_log.append(f"  → 异常: {e}")

        # 策略3: 直接不带重定向访问 index.jsp
        try:
            debug_log.append("[queryString] 策略3: index.jsp 不跟随重定向")
            resp = self.session.get(
                f"{self.base_url}/eportal/index.jsp",
                timeout=self.timeout, allow_redirects=False
            )
            debug_log.append(f"  → HTTP {resp.status_code}")
            if resp.status_code in (301, 302):
                redirect_url = resp.headers.get("Location", "")
                debug_log.append(f"  → 重定向: {redirect_url[:200]}")
                if "?" in redirect_url:
                    return redirect_url.split("?", 1)[1]
        except Exception as e:
            debug_log.append(f"  → 异常: {e}")

        # 策略4: 用本机校园网IP直接构造queryString
        campus_ip = self._get_campus_ip()
        if campus_ip:
            debug_log.append(f"[queryString] 策略4: 用校园网IP构造 ({campus_ip})")
            qs = f"wlanuserip={campus_ip}&wlanacip=&wlanacname=&ssid=&nasip={self.portal_ip}&wlanparameter="
            debug_log.append(f"  → 构造: {qs}")
            return qs

        debug_log.append("[queryString] 所有策略均失败，未找到校园网IP")
        return ""

    def logout_by_ip(self) -> bool:
        """
        通过IP注销当前在线状态 (无需userIndex)
        用于：已通过浏览器登录，需要先注销再通过工具重新登录
        """
        try:
            # 方法1: 访问Portal的logout接口
            resp = self.session.get(
                f"{self.base_url}/eportal/InterFace.do?method=logout",
                timeout=self.timeout
            )
            if resp.status_code == 200 and "success" in resp.text.lower():
                return True
        except Exception:
            pass

        try:
            # 方法2: POST空userIndex
            resp = self.session.post(
                f"{self.base_url}/eportal/InterFace.do?method=logout",
                data={"userIndex": ""},
                timeout=self.timeout
            )
        except Exception:
            pass

        # 检查是否真的下线了
        try:
            resp = self.session.get(
                "http://www.msftconnecttest.com/connecttest.txt",
                timeout=self.timeout, allow_redirects=False
            )
            # 如果被重定向 → 已下线
            return resp.status_code in (301, 302)
        except Exception:
            return False

    def login(self, username: str, password: str, service: str = "",
              force_relogin: bool = False) -> LoginResult:
        """
        执行ePortal登录

        锐捷ePortal标准登录流程:
        1. (可选)先注销已有会话
        2. 获取queryString
        3. POST /eportal/InterFace.do?method=login
        """
        result = LoginResult()
        debug_log = []

        try:
            # 步骤0: 如果需要强制重登，先注销
            if force_relogin:
                debug_log.append("[登录] 强制重登: 先注销现有会话...")
                self.logout_by_ip()
                time.sleep(1)  # 等待Portal释放会话

            # 步骤1: 获取queryString
            query_string = self._get_query_string(debug_log)
            debug_log.append(f"[登录] queryString={'有' if query_string else '空'}: {query_string[:80] if query_string else 'N/A'}")

            if not query_string:
                debug_log.append("[登录] queryString为空，尝试强制注销后重试...")
                self.logout_by_ip()
                time.sleep(1)
                query_string = self._get_query_string(debug_log)
                debug_log.append(f"[登录] 重试queryString: {query_string[:80] if query_string else '仍为空'}")

            # 步骤2: 构建登录请求
            login_url = f"{self.base_url}/eportal/InterFace.do?method=login"

            login_data = {
                "userId": username,
                "password": password,
                "service": service,
                "queryString": query_string,
                "operatorPwd": "",
                "operatorUserId": "",
                "validcode": "",
                "passwordEncrypt": "false",
            }

            self.session.headers.update({
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": f"{self.base_url}/eportal/index.jsp",
                "Origin": self.base_url,
            })

            debug_log.append(f"[登录] POST {login_url}")
            resp = self.session.post(login_url, data=login_data, timeout=self.timeout)
            debug_log.append(f"[登录] 响应: HTTP {resp.status_code}")
            debug_log.append(f"[登录] 响应内容: {resp.text[:300]}")

            if resp.status_code == 200:
                self._parse_login_response(resp.text, result)
            else:
                result.message = f"HTTP错误: {resp.status_code}"

        except requests.exceptions.ConnectionError:
            result.message = "连接失败 - 无法访问Portal服务器"
        except requests.exceptions.Timeout:
            result.message = "连接超时"
        except Exception as e:
            result.message = f"登录异常: {e}"

        result.raw["_debug_log"] = debug_log
        return result

    def _parse_login_response(self, response_text: str, result: LoginResult):
        """解析登录响应 - 锐捷ePortal返回的是特殊格式"""
        try:
            # 尝试JSON解析
            data = json.loads(response_text)
            result.raw = data

            if data.get("result") == "success":
                result.success = True
                result.user_index = data.get("userIndex", "")
                result.keepalive_interval = int(data.get("keepaliveInterval", 0))
                result.message = "登录成功"
            else:
                result.message = data.get("message", "登录失败")
            return
        except json.JSONDecodeError:
            pass

        # 锐捷某些版本返回的是类JS对象格式
        try:
            # 匹配 result:"success" 或 result:"fail"
            result_match = re.search(r'"result"\s*:\s*"(\w+)"', response_text)
            if result_match:
                if result_match.group(1) == "success":
                    result.success = True
                    result.message = "登录成功"

                    ui_match = re.search(r'"userIndex"\s*:\s*"([^"]+)"', response_text)
                    if ui_match:
                        result.user_index = ui_match.group(1)

                    ka_match = re.search(r'"keepaliveInterval"\s*:\s*"?(\d+)"?', response_text)
                    if ka_match:
                        result.keepalive_interval = int(ka_match.group(1))
                else:
                    msg_match = re.search(r'"message"\s*:\s*"([^"]*)"', response_text)
                    result.message = msg_match.group(1) if msg_match else "登录失败"
                return
        except Exception:
            pass

        # 如果响应中包含success关键字
        if "success" in response_text.lower():
            result.success = True
            result.message = "登录成功(推测)"
        else:
            result.message = f"未知响应格式: {response_text[:200]}"

    def logout(self, user_index: str) -> bool:
        """注销登录"""
        try:
            logout_url = f"{self.base_url}/eportal/InterFace.do?method=logout"
            resp = self.session.post(
                logout_url,
                data={"userIndex": user_index},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    data = json.loads(resp.text)
                    return data.get("result") == "success"
                except Exception:
                    return "success" in resp.text.lower()
        except Exception:
            pass
        return False

    def keepalive(self, user_index: str) -> bool:
        """发送保活心跳"""
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=keepalive"
            resp = self.session.post(
                url,
                data={"userIndex": user_index},
                timeout=self.timeout
            )
            return resp.status_code == 200
        except Exception:
            return False

    def get_user_info(self, user_index: str) -> dict:
        """获取在线用户信息(含MAC绑定、设备列表等)"""
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=getOnlineUserInfo"
            resp = self.session.post(
                url,
                data={"userIndex": user_index},
                timeout=self.timeout
            )
            # 修复编码: ePortal可能返回GBK
            if resp.encoding and resp.encoding.lower() in ('iso-8859-1', 'latin-1'):
                resp.encoding = 'utf-8'
            text = resp.text
            if resp.status_code == 200:
                try:
                    data = json.loads(text)
                    # 附加调试信息
                    data["_debug_keys"] = list(data.keys())
                    data["_debug_mabInfo_raw"] = str(data.get("mabInfo", ""))[:200]
                    data["_debug_hasMab"] = data.get("hasMabInfo")
                    return data
                except json.JSONDecodeError:
                    return {"error": f"JSON解析失败", "raw": text[:500]}
            return {"error": f"HTTP {resp.status_code}"}
        except Exception as e:
            return {"error": str(e)}

    # ==================== 无感认证(MAC)管理 ====================

    def cancel_mac(self, user_index: str) -> dict:
        """
        取消本机无感认证
        对应源码: AuthInterFace.cancelMac('', userIndex)
        """
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=cancelMac"
            resp = self.session.post(
                url,
                data={"mac": "", "userIndex": user_index},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    return json.loads(resp.text)
                except json.JSONDecodeError:
                    return {"result": "unknown", "raw": resp.text}
        except Exception as e:
            return {"result": "fail", "message": str(e)}
        return {"result": "fail", "message": "请求失败"}

    def register_mac(self, user_index: str) -> dict:
        """
        注册本机无感认证 (开启MAC绑定)
        对应源码: AuthInterFace.registerMac('', userIndex)
        """
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=registerMac"
            resp = self.session.post(
                url,
                data={"mac": "", "userIndex": user_index},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    return json.loads(resp.text)
                except json.JSONDecodeError:
                    return {"result": "unknown", "raw": resp.text}
        except Exception as e:
            return {"result": "fail", "message": str(e)}
        return {"result": "fail", "message": "请求失败"}

    def cancel_mac_for_device(self, user_id: str, user_mac: str) -> dict:
        """
        取消指定设备的无感认证 (踢掉其他设备的MAC绑定)
        对应源码: AuthInterFace.cancelMacWithUserNameAndMac(userId, usermac)
        """
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=cancelMacWithUserNameAndMac"
            resp = self.session.post(
                url,
                data={"userId": user_id, "usermac": user_mac},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    return json.loads(resp.text)
                except json.JSONDecodeError:
                    return {"result": "unknown", "raw": resp.text}
        except Exception as e:
            return {"result": "fail", "message": str(e)}
        return {"result": "fail", "message": "请求失败"}

    def force_offline_device(self, user_id: str, user_mac: str) -> dict:
        """
        强制指定设备下线: 先取消MAC绑定, 再尝试按MAC踢下线
        尝试多种ePortal接口实现
        """
        results = []

        # 方法1: cancelMacWithUserNameAndMac (取消MAC绑定, 设备将无法自动重连)
        r1 = self.cancel_mac_for_device(user_id, user_mac)
        results.append(f"cancelMac: {r1.get('result', '?')}")

        # 方法2: 尝试 logoutByUserIdAndMac (部分锐捷版本支持)
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=logoutByUserIdAndMac"
            resp = self.session.post(
                url,
                data={"userId": user_id, "userMac": user_mac},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    r2 = json.loads(resp.text)
                    results.append(f"logoutByMac: {r2.get('result', '?')}")
                    if r2.get("result") == "success":
                        return {"result": "success", "message": f"设备 {user_mac} 已强制下线", "details": results}
                except json.JSONDecodeError:
                    results.append(f"logoutByMac: 非JSON响应")
        except Exception as e:
            results.append(f"logoutByMac: {e}")

        # 方法3: 尝试 logoutByMac (另一种接口)
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=logoutByMac"
            resp = self.session.post(
                url,
                data={"userMac": user_mac},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    r3 = json.loads(resp.text)
                    results.append(f"logoutByMac2: {r3.get('result', '?')}")
                except json.JSONDecodeError:
                    results.append(f"logoutByMac2: 非JSON")
        except Exception:
            pass

        # 只要MAC绑定取消成功就算成功
        if r1.get("result") == "success":
            return {"result": "success", "message": f"设备 {user_mac} MAC绑定已取消 (无法自动重连)", "details": results}

        return {"result": "partial", "message": f"操作结果: {'; '.join(results)}", "details": results}

    def fresh_online_user_info(self, user_index: str) -> dict:
        """
        刷新在线用户信息 (获取最新MAC/设备列表)
        对应源码: AuthInterFace.freshOnlineUserInfo(userIndex)
        """
        try:
            url = f"{self.base_url}/eportal/InterFace.do?method=freshOnlineUserInfo"
            resp = self.session.post(
                url,
                data={"userIndex": user_index},
                timeout=self.timeout
            )
            if resp.status_code == 200:
                try:
                    return json.loads(resp.text)
                except json.JSONDecodeError:
                    return {"raw": resp.text}
        except Exception as e:
            return {"error": str(e)}
        return {}

    def parse_mab_info(self, user_info: dict) -> list:
        """
        从用户信息中解析MAC绑定设备列表

        返回: [{"userMac": "AA:BB:CC:DD:EE:FF", "userId": "xxx",
                "deviceName": "xxx", "createTime": "xxx", "macExpireTime": "xxx"}, ...]
        """
        devices = []
        mab_info_str = user_info.get("mabInfo", "")
        if not mab_info_str or mab_info_str == "[]":
            return devices
        try:
            mab_list = json.loads(mab_info_str)
            for mab in mab_list:
                devices.append({
                    "userMac": mab.get("userMac", ""),
                    "userId": mab.get("userId", ""),
                    "deviceName": mab.get("deviceName", ""),
                    "createTime": mab.get("createTime", ""),
                    "macExpireTime": mab.get("macExpireTime", "永不过期"),
                })
            return devices
        except (json.JSONDecodeError, TypeError):
            return devices

    def get_security_status(self, user_index: str) -> dict:
        """
        获取安全相关状态汇总:
        - 本机MAC地址 / 是否开启无感认证
        - 绑定的设备列表
        - 是否允许无感认证
        - 自动登录Cookie状态
        """
        info = self.get_user_info(user_index)
        if "error" in info or info.get("result") == "fail":
            msg = info.get("error", info.get("message", "获取失败"))
            # 修复可能的编码问题
            if msg and isinstance(msg, str):
                try:
                    msg = msg.encode('latin-1').decode('utf-8')
                except (UnicodeDecodeError, UnicodeEncodeError):
                    pass
            return {"error": msg}

        devices = self.parse_mab_info(info)
        user_mac = info.get("userMac") or ""
        # API返回null时，从本机获取MAC
        if not user_mac:
            user_mac = self._get_local_mac()
        has_mab = bool(info.get("hasMabInfo", False))
        is_allow_mab = info.get("isAlowMab", "")
        is_auto_login = info.get("isAutoLogin", "")

        return {
            "userName": info.get("userName", ""),
            "userMac": user_mac,
            "hasMabInfo": has_mab,           # 本机是否已开启无感认证
            "isAlowMab": is_allow_mab,       # 系统是否允许无感认证
            "isAutoLogin": is_auto_login,    # 是否支持自动登录
            "devices": devices,              # 所有绑定的设备
            "deviceCount": len(devices),
            "mabInfoMaxCount": info.get("mabInfoMaxCount", ""),
            "service": info.get("service", ""),
            "realServiceName": info.get("realServiceName", ""),
            # 调试信息
            "_debug_hasMab_raw": info.get("hasMabInfo"),
            "_debug_mabInfo_raw": str(info.get("mabInfo", ""))[:200],
            "_debug_ballInfo_raw": str(info.get("ballInfo", ""))[:200],
        }
