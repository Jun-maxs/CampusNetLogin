[app]
title = 校园网登录
package.name = campusnetlogin
package.domain = org.campus
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,ttc,otf
version = 1.0.0

# Android 配置
requirements = python3,kivy
android.permissions = INTERNET,ACCESS_NETWORK_STATE,ACCESS_WIFI_STATE
android.api = 33
android.minapi = 21
android.archs = arm64-v8a, armeabi-v7a

# UI
orientation = portrait
fullscreen = 0

# 图标和启动画面 (可选)
# icon.filename = icon.png
# presplash.filename = splash.png

# 日志
log_level = 2

[buildozer]
log_level = 2
warn_on_root = 1
