# CDP 连接模式使用指南

## 什么是 CDP 模式？

CDP (Chrome DevTools Protocol) 模式允许 Flow2API 连接到一个**已经运行的真实 Chrome 浏览器**，而不是让 Playwright 自己启动浏览器。

### 优势

1. **完全绕过自动化检测** - 使用真实的 Chrome，不是 Playwright 控制的浏览器
2. **使用真实的浏览器指纹** - 你的个人 Chrome 配置、Cookie、插件等
3. **更高的 reCAPTCHA 通过率** - Google 更难检测为机器人
4. **可以手动干预** - 需要时可以在浏览器中手动操作

## 使用步骤

### 1. 启动 Chrome 远程调试模式

根据你的操作系统，在终端执行以下命令：

**macOS:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/Users/你的用户名/chrome-debug-profile"
```

**Linux:**
```bash
google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="/home/你的用户名/chrome-debug-profile"
```

**Windows:**
```cmd
"C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --remote-debugging-port=9222 ^
  --user-data-dir="C:\chrome-debug-profile"
```

**重要提示：**
- 确保 Chrome 没有其他实例在运行（完全关闭所有 Chrome 窗口）
- `--user-data-dir` 可以是任意目录，用于存储这个调试会话的配置
- `9222` 是默认端口，可以改成其他端口，但要记住

### 2. 配置 Flow2API

启动 Chrome 后，在 Flow2API 管理界面配置：

1. 打开 Flow2API 管理页面
2. 进入"验证码配置"
3. 启用"使用 CDP 连接模式"
4. 输入 CDP 端点：`http://localhost:9222`
5. 保存配置

或者直接在数据库中设置（`captcha_config` 表）：
```sql
UPDATE captcha_config
SET browser_use_cdp = 1,
    browser_cdp_endpoint = 'http://localhost:9222';
```

### 3. 首次使用建议

1. **手动登录 Google 账号** - 在调试 Chrome 中访问 https://labs.google 并登录你的账号
2. **完成人机验证** - 如果遇到 reCAPTCHA 挑战，手动完成一次
3. **保持浏览器打开** - 只要 Flow2API 在运行，就保持这个 Chrome 窗口打开

### 4. 验证连接

重启 Flow2API 服务，查看日志：

```
[BrowserCaptcha] 连接到 CDP 端点: http://localhost:9222
[BrowserCaptcha] ✅ 已连接到 CDP (已有 1 个上下文)
```

如果看到这个日志，说明连接成功！

## 故障排查

### 连接失败

如果看到 `CDP 连接失败`，检查：

1. Chrome 是否以远程调试模式启动？
   - 访问 http://localhost:9222/json/version 应该能看到 Chrome 版本信息
2. 端口是否正确？
3. 防火墙是否阻止了连接？

### 降级到普通模式

如果 CDP 连接失败，Flow2API 会自动降级到普通 Playwright 启动模式，不会影响服务运行。

## 高级配置

### 使用不同端口

如果 9222 被占用，可以改用其他端口：

```bash
# 使用 9223 端口
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9223 \
  --user-data-dir="/Users/你的用户名/chrome-debug-profile"
```

然后在 Flow2API 中配置 `http://localhost:9223`

### 远程机器上的 Chrome

如果 Chrome 运行在另一台机器上（比如 `192.168.1.100`）：

```bash
# 在远程机器上启动 Chrome，允许所有 IP 连接
google-chrome \
  --remote-debugging-port=9222 \
  --remote-debugging-address=0.0.0.0 \
  --user-data-dir="/home/user/chrome-debug-profile"
```

然后在 Flow2API 中配置 `http://192.168.1.100:9222`

**安全警告：** 远程调试端口可以完全控制浏览器，不要暴露到公网！

### 使用 systemd 自动启动（Linux）

创建 `/etc/systemd/system/chrome-debug.service`：

```ini
[Unit]
Description=Chrome Remote Debugging
After=network.target

[Service]
Type=simple
User=你的用户名
ExecStart=/usr/bin/google-chrome \
  --remote-debugging-port=9222 \
  --user-data-dir=/home/你的用户名/chrome-debug-profile \
  --no-first-run \
  --no-default-browser-check
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

启用服务：
```bash
sudo systemctl enable chrome-debug
sudo systemctl start chrome-debug
```

## 对比：CDP 模式 vs 普通模式

| 特性 | CDP 模式 | 普通模式 |
|------|---------|---------|
| 反检测能力 | ⭐⭐⭐⭐⭐ 极强 | ⭐⭐⭐ 中等 |
| reCAPTCHA 通过率 | 高 | 中 |
| 配置复杂度 | 中 | 低 |
| 需要手动操作 | 可能需要首次登录 | 全自动 |
| 浏览器指纹 | 真实用户 | Playwright 指纹 |
| 多实例支持 | 需要多个端口 | 支持 |

## 推荐场景

**使用 CDP 模式：**
- reCAPTCHA 验证频繁失败
- 需要最高的通过率
- 可以接受手动登录一次

**使用普通模式：**
- 需要完全自动化
- 部署环境没有图形界面
- 需要运行多个实例
