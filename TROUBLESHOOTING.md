# 抖音模块问题诊断与修复

## 问题汇总

### 1. ✅ 扫码登录获取二维码问题 - 已修复
**问题**：获取到的是抖音 icon 而不是二维码
**解决方案**：改用原生浏览器窗口方式

### 2. ✅ 取消登录功能 - 已添加
**问题**：没有取消登录的功能
**解决方案**：添加取消按钮和 API 端点

### 3. ✅ API 参数对齐 - 已完成
**问题**：API 请求参数不完整
**解决方案**：补充 6 个缺失参数

### 4. ⚠️ 登录窗口不弹出 - 需要检查
**现象**：点击"开始扫码登录"后没有浏览器窗口弹出
**可能原因**：
- Playwright 浏览器未正确安装
- macOS 权限问题
- 应用在后台运行，窗口被隐藏

### 5. ⚠️ 取消登录返回 HTML - 路由问题
**现象**：调用取消 API 返回 405 Method Not Allowed HTML
**原因**：可能是旧的应用进程仍在运行，使用了旧代码

### 6. ✅ 搜索功能需要登录 - 符合预期
**问题**：搜索用户需要登录 Cookie
**结论**：参考库 `DY_video_downloader` 的搜索功能也需要 Cookie，这是正常的

## 修复步骤

### 步骤 1: 确保 Playwright 浏览器已安装

```bash
cd /Users/apple/Downloads/wechat-mp-tools
playwright install chromium
```

### 步骤 2: 停止所有旧进程

```bash
pkill -f "python.*app.py"
```

### 步骤 3: 重新启动应用

```bash
python3 app.py --port 5200
```

### 步骤 4: 测试登录功能

1. 访问 http://localhost:5200
2. 点击左侧菜单"抖音扫码登录"
3. 点击"开始扫码登录"按钮
4. **应该会弹出浏览器窗口**

## 诊断命令

### 检查 Playwright 安装

```bash
python3 -c "
from playwright.sync_api import sync_playwright
with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    print('✅ 浏览器可以启动')
    browser.close()
"
```

### 检查应用进程

```bash
ps aux | grep "python.*app.py"
```

### 测试 API 端点

```bash
# 测试启动登录
curl -X POST http://localhost:5200/api/douyin/auth/start

# 测试取消登录
curl -X POST http://localhost:5200/api/douyin/auth/cancel

# 测试状态查询
curl http://localhost:5200/api/douyin/auth/status
```

## 已知问题与解决方案

### 问题 1: macOS 权限问题

**现象**：浏览器无法启动或窗口不显示

**解决方案**：
```bash
# 给予终端完全磁盘访问权限
# 系统偏好设置 → 安全性与隐私 → 隐私 → 完全磁盘访问权限
# 添加 Terminal.app 或 iTerm.app
```

### 问题 2: 浏览器被阻止

**现象**：macOS 阻止 Chromium 启动

**解决方案**：
```bash
# 移除隔离属性
xattr -cr ~/.cache/ms-playwright/
```

### 问题 3: 端口被占用

**现象**：应用启动失败，提示端口被占用

**解决方案**：
```bash
# 查找占用端口的进程
lsof -i :5200

# 杀死进程
kill -9 <PID>
```

## 代码修改总结

### backend/douyin_auth.py
- 完全重写登录逻辑
- 使用 Playwright 非 headless 模式
- 添加取消登录功能
- 轮询检查 Cookie

### backend/douyin.py
- 更新 `_get_common_params()` 方法
- 添加 6 个缺失参数

### frontend/js/components/douyin/dy_login.js
- 移除二维码显示逻辑
- 添加取消按钮
- 添加登录提示
- 改进状态显示

### frontend/js/api.js
- 添加 `cancel` API 调用

## 测试清单

- [x] 签名算法测试
- [x] 客户端初始化测试
- [x] URL 解析测试
- [ ] 登录窗口弹出测试
- [ ] 扫码登录完整流程测试
- [ ] 取消登录测试
- [ ] 单条下载测试
- [ ] 批量下载测试

## 下一步行动

1. **确保 Playwright 浏览器已安装**
   ```bash
   playwright install chromium
   ```

2. **重启应用**
   ```bash
   pkill -f "python.*app.py"
   python3 app.py --port 5200
   ```

3. **测试登录功能**
   - 访问 http://localhost:5200
   - 点击"抖音扫码登录"
   - 观察是否弹出浏览器窗口

4. **如果窗口不弹出**
   - 检查终端是否有错误输出
   - 运行 `test_browser.py` 诊断脚本
   - 检查 macOS 权限设置

5. **完成登录后测试下载**
   - 测试单条链接下载
   - 测试用户主页批量下载
   - 验证视频是否无水印

## 参考资料

- [DY_video_downloader](https://github.com/anYuJia/DY_video_downloader) - 参考库
- [Playwright Python](https://playwright.dev/python/) - Playwright 文档
- `DOUYIN_FIX_SUMMARY.md` - 详细修复说明

## 联系与支持

如果遇到问题：
1. 查看终端错误输出
2. 运行诊断脚本
3. 检查 `test_douyin_fixes.py` 测试结果
4. 查看应用日志
