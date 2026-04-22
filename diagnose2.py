"""
诊断2: 测试构造queryString登录 + 获取本机网络信息
"""
import requests
import socket
import re
import json

PORTAL = "http://10.228.9.7"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

# ---- 1. 获取本机IP ----
print("=" * 60)
print("1. 获取本机网络信息")
print("=" * 60)

def get_local_ips():
    """获取所有本机IP"""
    ips = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in ips and not ip.startswith("127."):
                ips.append(ip)
    except:
        pass
    # 也尝试通过连接获取
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("10.228.9.7", 80))
        ip = sock.getsockname()[0]
        if ip not in ips:
            ips.insert(0, ip)
        sock.close()
    except:
        pass
    return ips

ips = get_local_ips()
for ip in ips:
    print(f"   本机IP: {ip}")

# ---- 2. 尝试不同的Portal入口 ----
print()
print("=" * 60)
print("2. 测试各Portal路径")
print("=" * 60)

paths_to_try = [
    "/eportal/InterFace.do?method=getOnlineUserInfo",
    "/eportal/InterFace.do?method=freshOnlineUserInfo",
    "/eportal/redirectortosuccess.jsp",
    "/eportal/gotoSuccess.jsp",
    f"/eportal/success.jsp?wlanuserip={ips[0] if ips else ''}",
    f"/eportal/index.jsp?wlanuserip={ips[0] if ips else ''}",
]

for path in paths_to_try:
    url = f"{PORTAL}{path}"
    try:
        r = s.get(url, timeout=5, allow_redirects=True)
        content = r.text[:300] if len(r.text) > 300 else r.text
        print(f"\n   GET {path}")
        print(f"   → HTTP {r.status_code}, len={len(r.text)}")
        print(f"   → Final URL: {r.url}")
        print(f"   → Content: {repr(content)}")
    except Exception as e:
        print(f"\n   GET {path}")
        print(f"   → Error: {e}")

# ---- 3. 尝试构造queryString登录 ----
print()
print("=" * 60)
print("3. 尝试构造queryString登录 (不输入密码，只测试API响应)")
print("=" * 60)

if ips:
    user_ip = ips[0]
    
    # 各种queryString格式
    qs_variants = [
        f"wlanuserip={user_ip}",
        f"wlanuserip={user_ip}&wlanacip=10.228.9.7",
        f"wlanuserip={user_ip}&wlanacname=&ssid=&nasip=10.228.9.7&wlanacip=",
        f"wlanuserip={user_ip}&wlanacip=&wlanacname=&ssid=&nasip=&wlanparameter=",
    ]
    
    for qs in qs_variants:
        try:
            r = s.post(
                f"{PORTAL}/eportal/InterFace.do?method=login",
                data={
                    "userId": "test_user",
                    "password": "test_pass",
                    "service": "",
                    "queryString": qs,
                    "operatorPwd": "",
                    "operatorUserId": "",
                    "validcode": "",
                    "passwordEncrypt": "false",
                },
                timeout=5
            )
            print(f"\n   queryString: {qs}")
            print(f"   → HTTP {r.status_code}")
            print(f"   → Response: {repr(r.text[:200])}")
        except Exception as e:
            print(f"\n   queryString: {qs}")
            print(f"   → Error: {e}")

# ---- 4. 测试 InterFace.do 的其他方法 ----
print()
print("=" * 60)
print("4. 测试InterFace.do的其他方法")
print("=" * 60)

methods = [
    ("getOnlineUserInfo", {}),
    ("freshOnlineUserInfo", {}),
    ("getUserIndex", {"userId": "test"}),
]

if ips:
    methods.append(("getOnlineUserInfo", {"userip": ips[0]}))

for method_name, data in methods:
    try:
        r = s.post(
            f"{PORTAL}/eportal/InterFace.do?method={method_name}",
            data=data,
            timeout=5
        )
        print(f"\n   method={method_name}, data={data}")
        print(f"   → HTTP {r.status_code}")
        print(f"   → Response: {repr(r.text[:300])}")
    except Exception as e:
        print(f"\n   method={method_name}")
        print(f"   → Error: {e}")

print()
print("=" * 60)
print("诊断完成")
print("=" * 60)
