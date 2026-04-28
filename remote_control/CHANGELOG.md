# CampusNetAgent 更新日志

## v1.65 (2026-04-28)

### 🔧 修复
- **完全隐藏 PowerShell 窗口**: 修复客户端运行时偶尔唤起 PowerShell 窗口的问题
  - 为所有 `subprocess.run()` 和 `subprocess.Popen()` 调用添加 `STARTUPINFO` 配置
  - 添加 `-WindowStyle Hidden` 参数到所有 PowerShell 命令
  - 使用 `SW_HIDE` 标志完全隐藏窗口

### ✨ 优化
- **自检逻辑优化**: 将需要管理员权限的测试项改为信息提示而非失败
  - Defender 排除检查：非管理员时显示为信息而非错误
  - 限速测试：权限不足时显示为信息而非失败
  - 减少误报，提升用户体验

### 📝 技术细节
- 新增 `_get_startupinfo()` 辅助函数统一管理窗口隐藏配置
- 所有 subprocess 调用现在都使用 `startupinfo=_get_startupinfo()`
- PowerShell 命令统一添加 `-WindowStyle Hidden` 参数

## v1.64 (2026-04-28)

### 🔧 修复
- 初步添加窗口隐藏支持

## v1.63 及更早版本

详见 Git 提交历史
