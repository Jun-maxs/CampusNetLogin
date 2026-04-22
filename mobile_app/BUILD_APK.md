# 编译 APK 教程

## 方法 1: Google Colab（最简单，免费）

1. 打开 https://colab.research.google.com
2. 新建笔记本，运行以下代码：

```python
# 安装 buildozer
!pip install buildozer cython
!sudo apt-get install -y python3-pip build-essential git python3 \
    python3-dev ffmpeg libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
    libsdl2-ttf-dev libportmidi-dev libswscale-dev libavformat-dev \
    libavcodec-dev zlib1g-dev libgstreamer1.0 gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good autoconf automake libtool pkg-config \
    libncurses5-dev libncursesw5-dev libtinfo5 cmake libffi-dev libssl-dev \
    zip unzip openjdk-17-jdk

# 上传 main.py 和 buildozer.spec
from google.colab import files
# files.upload()  # 手动上传

# 或者直接从 GitHub clone
# !git clone https://github.com/你的用户名/CampusNetLogin.git
# %cd CampusNetLogin/mobile_app

# 编译 APK
!buildozer android debug

# 下载 APK
files.download('bin/campusnetlogin-1.0.0-arm64-v8a_armeabi-v7a-debug.apk')
```

## 方法 2: WSL (Windows)

```bash
# 1. 安装 WSL
wsl --install

# 2. 在 WSL 中安装依赖
sudo apt update && sudo apt install -y python3-pip build-essential git \
    python3-dev ffmpeg libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
    libsdl2-ttf-dev libportmidi-dev libswscale-dev libavformat-dev \
    libavcodec-dev zlib1g-dev openjdk-17-jdk autoconf automake libtool \
    cmake libffi-dev libssl-dev zip unzip

pip3 install buildozer cython

# 3. 进入项目目录
cd /mnt/f/code/临时ai文件/CampusNetLogin/mobile_app

# 4. 编译
buildozer android debug

# 5. APK 在 bin/ 目录下
```

## 方法 3: GitHub Actions（自动化）

将项目推送到 GitHub，添加 `.github/workflows/build.yml`：

```yaml
name: Build APK
on:
  push:
    branches: [main]
  workflow_dispatch:

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-java@v4
        with:
          distribution: temurin
          java-version: 17
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
      - name: Install deps
        run: |
          pip install buildozer cython
          sudo apt-get install -y build-essential git python3-dev \
            libsdl2-dev libsdl2-image-dev libsdl2-mixer-dev \
            libsdl2-ttf-dev libportmidi-dev libswscale-dev \
            libavformat-dev libavcodec-dev zlib1g-dev \
            autoconf automake libtool cmake libffi-dev libssl-dev \
            zip unzip
      - name: Build APK
        working-directory: mobile_app
        run: buildozer android debug
      - uses: actions/upload-artifact@v4
        with:
          name: apk
          path: mobile_app/bin/*.apk
```

## 安装 APK

1. 手机设置 → 安全 → 允许安装未知来源应用
2. 传输 APK 到手机
3. 点击安装
4. 打开「校园网登录」app
5. 输入学号密码，一键登录！

## 注意事项

- 首次编译约需 20-30 分钟（下载 Android SDK/NDK）
- APK 大小约 15-20MB
- 需要手机连接校园网 WiFi 才能使用
- 配置会保存在 app 本地存储中
