"""
在 Google Colab 上一键编译 APK

使用步骤:
1. 打开 https://colab.research.google.com
2. 新建笔记本
3. 把下面的代码块分段粘贴到 Colab cell 中运行
4. 最后一步会自动下载 APK 到你的电脑
"""

# ============================================
# Cell 1: 安装编译工具 (约3分钟)
# ============================================
CELL_1 = """
!pip install buildozer cython==3.0.11
!sudo apt-get update -qq
!sudo apt-get install -y -qq python3-pip build-essential git python3-dev \\
    ffmpeg libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \\
    libsdl2-ttf-dev libportmidi-dev libswscale-dev libavformat-dev \\
    libavcodec-dev zlib1g-dev libgstreamer1.0-dev gstreamer1.0-plugins-base \\
    autoconf automake libtool pkg-config cmake libffi-dev libssl-dev \\
    zip unzip openjdk-17-jdk 2>/dev/null
print("✅ 工具安装完成!")
"""

# ============================================
# Cell 2: 创建项目文件 (直接粘贴)
# ============================================
CELL_2 = """
import os
os.makedirs('/content/campus_app', exist_ok=True)
%cd /content/campus_app
# 上传 main.py 和 buildozer.spec
from google.colab import files
print("请上传 main.py 和 buildozer.spec 两个文件:")
uploaded = files.upload()
print(f"✅ 已上传 {len(uploaded)} 个文件")
"""

# ============================================
# Cell 3: 编译 APK (约15-25分钟，首次较慢)
# ============================================
CELL_3 = """
%cd /content/campus_app
!buildozer android debug 2>&1 | tail -20
print("\\n✅ 编译完成!")
"""

# ============================================
# Cell 4: 下载 APK
# ============================================
CELL_4 = """
import glob
from google.colab import files
apks = glob.glob('/content/campus_app/bin/*.apk')
if apks:
    print(f"找到 APK: {apks[0]}")
    files.download(apks[0])
else:
    print("❌ 未找到 APK，请检查编译日志")
"""

if __name__ == "__main__":
    print("=" * 50)
    print("  Google Colab 编译 APK 指南")
    print("=" * 50)
    print()
    print("请按以下步骤操作:")
    print()
    print("1. 打开 https://colab.research.google.com")
    print("2. 新建笔记本")
    print("3. 依次创建 4 个代码块，复制以下内容:")
    print()

    for i, (name, code) in enumerate([
        ("安装编译工具", CELL_1),
        ("上传项目文件", CELL_2),
        ("编译 APK", CELL_3),
        ("下载 APK", CELL_4),
    ], 1):
        print(f"{'='*40}")
        print(f"  Cell {i}: {name}")
        print(f"{'='*40}")
        print(code.strip())
        print()

    print("完成后 APK 会自动下载到你的电脑!")
    print("传到手机上安装即可使用。")
