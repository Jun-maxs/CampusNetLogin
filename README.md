# 🌐 校园网一键登录工具

基于锐捷 ePortal 认证系统的自动登录工具。

## 功能

- **一键登录** — 点一下按钮即可完成校园网认证
- **自动检测** — 启动时自动检测网络状态（在线/离线/需要登录）
- **自动保活** — 定时发送心跳，防止会话超时掉线
- **断线重连** — 检测到掉线后自动重新登录
- **加密存储** — 账号密码使用 PBKDF2 + XOR 加密保存在本地
- **GUI界面** — 简洁直观的图形界面

## 安装

```bash
# 安装依赖 (仅需 requests)
pip install -r requirements.txt
```

## 使用

```bash
# 启动 GUI
python app.py
```

### 首次使用
1. 输入你的校园网账号和密码
2. 点击「保存账号」
3. 点击「⚡ 一键登录」

### 日常使用
- 打开程序 → 点击「⚡ 一键登录」→ 完成
- 勾选「断线自动重连」可以保持持续在线

## 配置

配置文件保存在: `~/.campus_net_login/config.json`
密码经过加密，不会明文存储。

### Portal IP
默认为 `10.228.9.7`，可在界面底部「高级选项」中修改。

## 项目结构

```
CampusNetLogin/
├── app.py              # GUI 主程序
├── eportal_api.py      # 锐捷 ePortal API 模块
├── config_manager.py   # 加密配置管理
├── requirements.txt    # Python 依赖
└── README.md           # 使用说明
```

## 技术原理

本工具模拟浏览器向锐捷 ePortal 发送 HTTP 请求：

```
1. 检测网络 → 访问外网测试URL，看是否被重定向到Portal
2. 获取queryString → Portal登录所需的会话参数
3. POST登录 → /eportal/InterFace.do?method=login
4. 保活心跳 → /eportal/InterFace.do?method=keepalive
5. 注销 → /eportal/InterFace.do?method=logout
```

## 注意事项

- 本工具仅在校园内网环境下有效
- 首次使用需确保能访问 Portal 服务器 (10.228.9.7)
- 如果你的学校有多个服务/套餐，在「服务」栏填写对应的服务名
