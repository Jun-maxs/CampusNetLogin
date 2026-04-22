# 校园网远程控制系统

## 架构

```
┌──────────────┐     HTTP/心跳      ┌──────────────────┐
│   Agent      │ ◄──────────────► │   Server (面板)   │
│  (本地电脑)   │    每5秒上报状态    │  (服务器/云端)    │
│              │    接收远程命令     │  Web UI :9090     │
└──────────────┘                   └──────────────────┘
      ↕                                    ↕
  校园网 Portal                        管理员浏览器
  (10.228.9.7)                    http://server:9090
```

## 快速开始

### 1. 启动控制面板 (Server)

```bash
# 安装依赖
pip install flask

# 启动 (默认端口 9090)
python server.py
# 或指定端口
python server.py --port 8888
```

打开浏览器访问 `http://localhost:9090`

### 2. 启动本地 Agent

```bash
# 基本启动 (连接本地 server)
python agent.py

# 指定服务器地址
python agent.py --server http://你的服务器IP:9090

# 带账号密码启动
python agent.py --server http://server:9090 --username 202510007842 --password 08252152
```

Agent 启动后会自动:
- 每 5 秒向 Server 上报状态 (IP、MAC、在线状态、token)
- 接收并执行 Server 下发的命令 (下线/上线/刷新)

### 3. 远程操控

在 Server 面板上可以:
- 📊 查看所有 Agent 的实时状态
- 🔑 查看每台设备的 userIndex (token)
- ⏏ 远程下线任意设备
- 🔌 远程上线
- 🔄 强制刷新状态

## 命令说明

| 命令 | 说明 |
|------|------|
| `logout` | 下线 (调用 ePortal logout) |
| `login` | 上线 (需要 Agent 已保存账号密码) |
| `refresh` | 刷新网络状态 |
| `set_credentials` | 远程设置账号密码 |

## 配置文件

Agent 会在运行目录生成 `agent_config.json`:

```json
{
  "server": "http://server:9090",
  "username": "202510007842",
  "password": "08252152",
  "portal_ip": "10.228.9.7"
}
```

## 注意事项

- Agent 无需额外依赖，仅用 Python 标准库
- Server 需要安装 Flask (`pip install flask`)
- Agent 掉线超过 30 秒会显示为「失联」
- 密码仅保存在本地 agent_config.json 中
