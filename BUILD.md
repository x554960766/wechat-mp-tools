# 📱 微信公众号/抖音批量下载工具箱 — 打包构建指南

为了方便您使用和分发，本项目已经集成了 **PyInstaller** 跨平台打包规约。我们对项目代码进行了**智能路径适配**：
1. **数据外置**：打包后，所有的 Cookie、历史记录、下载的文章数据均会自动保存在**可执行文件旁边**的 `data/` 目录中，完全不影响迁移和备份。
2. **资源内置**：网页前端的 `frontend` 静态文件已完美配置打包进可执行文件中，无需手动拷贝。

---

## ☁️ 方案一：使用 GitHub Actions 自动打包（⭐️ 强烈推荐，最省心）

由于您使用的是 macOS，本地无法编译 Windows 专用的 `.exe` 文件。**为此我们为您编写了云端自动构建脚本，您不需要 Windows 电脑也能拿到打包好的 `.exe`！**

### 🎯 使用步骤：
1. 在 GitHub 上新建一个代码仓库（私有/公有均可）。
2. 将本项目代码推送到您的 GitHub 仓库。
3. 推送完成后，GitHub Actions 会**自动拉起 Windows 和 macOS 的云端虚拟机**开始为您打包编译！
4. **如何下载**：
   - 打开您的 GitHub 仓库页面，点击顶部的 **Actions** 标签。
   - 看到名为 `Build WeChat MP Tools Executables` 的工作流，点击进入最新的一条记录。
   - 滚动到页面底部，即可在 **Artifacts (产物)** 栏直接下载已经打包压缩好的：
     - 🎁 `WeChat-MP-Tools-Windows-Executable` (内含 `.exe` 绿色程序包，Windows 用户解压即用)
     - 🎁 `WeChat-MP-Tools-macOS-Executable` (内含 `.app` 双击程序包)

---

## 🍎 方案二：在 macOS 本地打包（生成 Mac 专用的 `.app`）

我们已在您的 Mac 上完成了打包测试，编译输出文件为双击运行的 **Mac 应用程序 (`.app`)**。

### 1. 打包成果位置
打包生成的文件位于项目根目录的：
📁 `dist/WeChat MP Tools.app`

### 2. 如何运行
您可以直接在 Finder（访达）中双击运行 `WeChat MP Tools.app`：
- **首次运行提示安全未知**：由于没有苹果官方的付费签名，首次双击可能会提示“无法打开”或“未知开发者”。请在 `dist/WeChat MP Tools.app` 上**右键 -> 打开**，然后在弹出的确认框中选择 **“打开”** 即可永久信任运行！
- 启动后，它会自动在后台拉起 Flask 服务，并启动您的浏览器打开管理网页。

---

## 💻 方案三：在物理 Windows 电脑上本地打包（生成 `.exe`）

如果您有 Windows 电脑，也可以非常方便地进行本地打包：

### 1. 准备工作（在一台 Windows 电脑上）
1. 将本项目整个目录（`wechat-mp-tools` 文件夹）复制到 Windows 电脑上。
2. 在 Windows 上安装 Python（推荐使用 [Python 3.10 / 3.11 / 3.12](https://www.python.org/downloads/)，安装时务必勾选 **"Add Python to PATH"** 选项）。

### 2. 一键安装依赖
打开 Windows 的 **PowerShell** 或 **CMD 命令行**，切换到项目所在的目录，然后运行：
```powershell
# 切换到项目根目录
cd C:\path\to\wechat-mp-tools

# 安装项目运行所需依赖
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 安装打包工具 PyInstaller
pip install pyinstaller -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. 一键编译打包
使用我们已经写好的通用 spec 规约文件进行打包：
```powershell
pyinstaller wechat_mp_tools.spec
```

### 4. 提取打包成果
编译完成后，打开项目根目录下的 `dist` 文件夹，您将看到：
📁 `dist\WeChat MP Tools\` 文件夹。

这个文件夹就是**绿色免安装版的完整程序包**！
- 里面包含一个 **`WeChat MP Tools.exe`** 文件。
- 双击运行这个 `.exe`，它将自动在浏览器中打开下载管理工具网页。
- **打包分发**：您可以直接将 `WeChat MP Tools` 整个文件夹压缩为 `.zip` 发送给 Windows 用户，解压缩即可直接双击运行！

---

## ⚠️ 常见问题说明

### 1. 启动扫码登录时提示“Playwright 缺失浏览器依赖”？
由于我们将 Playwright 的 Chromium 浏览器保持外置以缩减程序体积，如果首次运行时系统没有安装该浏览器，程序会报错。
- **解决方法**：只需在命令行中运行一次以下命令，Playwright 就会自动下载 Chromium 浏览器到您系统的公共缓存区：
  ```bash
  playwright install chromium
  ```
  执行完毕后，再次双击运行 `.exe` 或 `.app` 即可完美扫码登录！
