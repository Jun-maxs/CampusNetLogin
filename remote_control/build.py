#!/usr/bin/env python3
"""
打包脚本 - 将 agent.py 编译为独立 .exe
使用 PyInstaller，生成单文件可执行程序，无需安装 Python
"""
import subprocess, sys, os, shutil

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
AGENT_PY = os.path.join(SCRIPT_DIR, "agent.py")
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "release")

def check_pyinstaller():
    try:
        import PyInstaller
        print(f"  [OK] PyInstaller {PyInstaller.__version__}")
        return True
    except ImportError:
        print("  [..] PyInstaller 未安装，正在安装...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])
        print("  [OK] PyInstaller 已安装")
        return True

def build():
    print("=" * 50)
    print("  校园网 Agent - 打包工具")
    print("=" * 50)

    check_pyinstaller()

    # 清理旧构建
    for d in ["build"]:
        p = os.path.join(SCRIPT_DIR, d)
        if os.path.exists(p):
            shutil.rmtree(p)
    spec = os.path.join(SCRIPT_DIR, "agent.spec")
    if os.path.exists(spec):
        os.remove(spec)

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--onefile",
        "--name", "CampusNetAgent",
        "--distpath", OUTPUT_DIR,
        "--workpath", os.path.join(SCRIPT_DIR, "build"),
        "--specpath", SCRIPT_DIR,
        "--noconsole",
        "--hidden-import", "tkinter",
        "--hidden-import", "tkinter.ttk",
        "--hidden-import", "tkinter.messagebox",
        "--uac-admin",
        AGENT_PY,
    ]

    print(f"\n  开始打包...\n")
    result = subprocess.run(cmd, cwd=SCRIPT_DIR)

    if result.returncode == 0:
        exe_path = os.path.join(OUTPUT_DIR, "CampusNetAgent.exe")
        if os.path.exists(exe_path):
            size_mb = os.path.getsize(exe_path) / 1024 / 1024
            print(f"\n{'=' * 50}")
            print(f"  打包成功!")
            print(f"  文件: {exe_path}")
            print(f"  大小: {size_mb:.1f} MB")
            print(f"{'=' * 50}")
            print(f"\n  分发方式:")
            print(f"  1. 直接复制 CampusNetAgent.exe 到目标机器")
            print(f"  2. 双击运行 → 弹出配置向导 → 自动连接服务器")
            print(f"  3. 或上传到: https://yuanai.best/gyk/download/")
            return True
    print(f"\n  打包失败! (code={result.returncode})")
    return False

if __name__ == "__main__":
    build()
