# 校园网登录工具 - 使用说明

## 架构说明

```
服务器端 (ConfirmServer.exe)
    ↓ 控制权限
客户端 (CampusNetLogin.exe)
    ↓ 网络认证
校园网 Portal
```

## 快速开始

### 1. 打包程序

```bash
# Windows
build.bat

# 或手动打包
pip install pyinstaller
pyinstaller --onefile --windowed --name CampusNetLogin app.py
pyinstaller --onefile --console --name ConfirmServer confirm_server.py
```

### 2. 部署服务器

```bash
# 启动确认服务器（必须先启动）
ConfirmServer.exe
```

服务器会自动创建：
- `server_config.json` - 配置文件
- `server_audit.log` - 审计日志

### 3. 运行客户端

```bash
# 首次运行（会弹出 UAC 请求管理员权限）
CampusNetLogin.exe
```

首次运行后：
- ✅ 自动创建计划任务
- ✅ 开机自启
- ✅ 后续无弹窗

## 配置说明

### 服务器配置 (`server_config.json`)

```json
{
  "port": 9999,
  "allowed_operations": {
    "login": true,              // 允许登录
    "logout": true,             // 允许注销
    "offline": true,            // 允许下线
    "cancel_mac": true,         // 允许关闭无感认证
    "force_offline_device": true,  // 允许强制设备下线
    "kick_device": true,        // 允许取消设备绑定
    "kick_all_devices": true,   // 允许全部取消绑定
    "save_credentials": true    // 允许保存账号
  }
}
```

修改配置后重载：
```bash
curl http://localhost:9999/reload
```

### 客户端配置

客户端会自动连接 `http://127.0.0.1:9999`

如需修改服务器地址，编辑 `app.py` 第 22 行：
```python
CONFIRM_SERVER = "http://127.0.0.1:9999"
```

## 容错机制

### 客户端容错
- ✅ 服务器无响应 → 重试 2 次
- ✅ 重试失败 → **默认允许操作**（降级策略）
- ✅ 超时时间：1 秒
- ✅ 所有异常静默处理，不弹窗

### 服务器容错
- ✅ 配置文件损坏 → 自动重建默认配置
- ✅ 日志写入失败 → 静默跳过
- ✅ 异常请求 → 返回 500 错误

## 审计日志

`server_audit.log` 记录所有操作：

```
[2026-04-30 10:30:15] 192.168.1.100 | login | 允许
[2026-04-30 10:35:20] 192.168.1.100 | logout | 允许
[2026-04-30 10:40:25] 192.168.1.101 | kick_device | 拒绝
```

## 卸载

### 删除计划任务
```bash
schtasks /delete /tn CampusNetLogin /f
```

### 删除程序文件
直接删除 `CampusNetLogin.exe` 和 `ConfirmServer.exe`

## 故障排查

### 问题 1：客户端提示"服务器拒绝"
**原因**：服务器未启动或配置拒绝操作  
**解决**：
1. 启动 `ConfirmServer.exe`
2. 检查 `server_config.json` 配置

### 问题 2：首次运行无 UAC 弹窗
**原因**：已安装计划任务  
**解决**：正常现象，无需处理

### 问题 3：开机不自启
**原因**：计划任务未创建  
**解决**：
```bash
# 检查任务
schtasks /query /tn CampusNetLogin

# 手动运行一次客户端
CampusNetLogin.exe
```

## 架构优势

| 特性 | 说明 |
|------|------|
| **无弹窗** | 首次 UAC 后完全无感 |
| **集中控制** | 服务器统一管理权限 |
| **容错降级** | 服务器挂了仍可用 |
| **审计日志** | 所有操作可追溯 |
| **热重载** | 修改配置无需重启 |

## 技术栈

- **客户端**：Python + Tkinter + urllib
- **服务器**：Python + http.server
- **打包**：PyInstaller
- **部署**：Windows 计划任务

## 开发者

修改源码后重新打包：
```bash
build.bat
```
