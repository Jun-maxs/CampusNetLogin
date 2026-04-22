"""
快速诊断脚本 - 抓取Portal各页面完整内容
"""
import requests
import time

PORTAL = "http://10.228.9.7"
s = requests.Session()
s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0"})

print("=" * 60)
print("1. 测试外网连通性")
print("=" * 60)
try:
    r = s.get("http://www.msftconnecttest.com/connecttest.txt", timeout=5, allow_redirects=False)
    print(f"   HTTP {r.status_code}")
    print(f"   Headers: {dict(r.headers)}")
    print(f"   Body: {repr(r.text[:200])}")
    if r.status_code in (301, 302):
        print(f"   → 重定向到: {r.headers.get('Location', '')}")
except Exception as e:
    print(f"   异常: {e}")

print()
print("=" * 60)
print("2. Portal 登录页完整内容 (/eportal/)")
print("=" * 60)
try:
    r = s.get(f"{PORTAL}/eportal/", timeout=5, allow_redirects=True)
    print(f"   HTTP {r.status_code}")
    print(f"   URL: {r.url}")
    print(f"   长度: {len(r.text)}")
    print("--- 完整内容 ---")
    print(r.text)
    print("--- 结束 ---")
except Exception as e:
    print(f"   异常: {e}")

print()
print("=" * 60)
print("3. index.jsp")
print("=" * 60)
try:
    r = s.get(f"{PORTAL}/eportal/index.jsp", timeout=5, allow_redirects=False)
    print(f"   HTTP {r.status_code}")
    print(f"   Headers: {dict(r.headers)}")
    print(f"   Body: {repr(r.text)}")
except Exception as e:
    print(f"   异常: {e}")

print()
print("=" * 60)
print("4. 尝试注销 (logout)")
print("=" * 60)
try:
    r = s.post(f"{PORTAL}/eportal/InterFace.do?method=logout", data={"userIndex": ""}, timeout=5)
    print(f"   HTTP {r.status_code}, Body: {repr(r.text[:300])}")
except Exception as e:
    print(f"   异常: {e}")

print()
print("等待2秒...")
time.sleep(2)

print()
print("=" * 60)
print("5. 注销后再测外网 (是否被重定向)")
print("=" * 60)
try:
    r = s.get("http://www.msftconnecttest.com/connecttest.txt", timeout=5, allow_redirects=False)
    print(f"   HTTP {r.status_code}")
    if r.status_code in (301, 302):
        loc = r.headers.get("Location", "")
        print(f"   → 重定向到: {loc}")
        print(f"   → queryString: {loc.split('?', 1)[1] if '?' in loc else 'N/A'}")
    else:
        print(f"   Body: {repr(r.text[:200])}")
except Exception as e:
    print(f"   异常: {e}")

print()
print("=" * 60)
print("6. 注销后 Portal 登录页")
print("=" * 60)
try:
    r = s.get(f"{PORTAL}/eportal/", timeout=5, allow_redirects=True)
    print(f"   HTTP {r.status_code}")
    print(f"   URL: {r.url}")
    print("--- 完整内容 ---")
    print(r.text)
    print("--- 结束 ---")
except Exception as e:
    print(f"   异常: {e}")
