#!/bin/bash

# ==============================================================================
#  📱 微信公众号/抖音批量下载工具箱 — GitHub 快速上传脚本
#  主要目的：安全、极速地将项目推送到 GitHub，自动触发云端打包 Windows .exe / macOS .app
# ==============================================================================

# 设置颜色输出
GREEN='\033[0;32m'
BLUE='\033[0;34m'
RED='\033[0;31m'
NC='\033[0m' # 无颜色

echo -e "${BLUE}=========================================================${NC}"
echo -e "${BLUE}   📱 微信公众号/抖音批量下载工具箱 — 自动上传 GitHub 助手${NC}"
echo -e "${BLUE}=========================================================${NC}"
echo ""

# 1. 检查本地是否安装 git
if ! command -v git &> /dev/null; then
    echo -e "${RED}❌ 错误：您的 Mac 系统未检测到 Git 命令行工具。${NC}"
    echo -e "请在终端中运行：${GREEN}xcode-select --install${GREEN} 安装开发工具，然后再试。${NC}"
    exit 1
fi

# 2. 如果没有 git 仓库，进行初始化
if [ ! -d ".git" ]; then
    echo -e "${BLUE}ℹ️  正在初始化本地 Git 仓库...${NC}"
    git init
    echo -e "${GREEN}✅ 初始化完成。${NC}"
    echo ""
fi

# 3. 询问用户输入 GitHub 仓库链接
echo -e "${BLUE}请输入您的 GitHub 仓库 URL 链接。${NC}"
echo -e "例如：${GREEN}https://github.com/您的用户名/您的仓库名.git${NC}"
echo -n "👉 仓库 URL: "
read repo_url

if [ -z "$repo_url" ]; then
    echo -e "${RED}❌ 错误：输入不能为空。${NC}"
    exit 1
fi

# 4. 清理可能已存在的 remote，并添加新 remote
git remote remove origin &> /dev/null
git remote add origin "$repo_url"

# 5. 添加并提交代码
echo ""
echo -e "${BLUE}📦 正在添加并提交源码文件...${NC}"
echo -e "（我们已经为您配置了 .gitignore，您的凭证 data/ 和临时打包产物不会上传，请放心）${NC}"

git add .
git commit -m "feat: integrate PyInstaller Spec and GitHub Actions CI/CD workflows"

# 6. 推送到 GitHub
echo ""
echo -e "${BLUE}🚀 正在将代码推送到 GitHub (main 分支)...${NC}"
echo -e "${RED}提示：首次推送可能会要求您输入 GitHub 账号凭证（用户名与 Token 密码）。${NC}"
echo ""

git branch -M main
git push -u origin main

if [ $? -eq 0 ]; then
    echo ""
    echo -e "${GREEN}=========================================================${NC}"
    echo -e "${GREEN}🎉 恭喜您！代码已成功上传到 GitHub！${NC}"
    echo -e "${GREEN}=========================================================${NC}"
    echo -e "1. 现在，请打开您的网页端 GitHub 仓库页面。"
    echo -e "2. 点击页面上方的 ${BLUE}Actions${GREEN} 选项卡。"
    echo -e "3. 您会看到构建流水线已经自动启动！"
    echo -e "4. 构建成功后（约2-3分钟），滚动到页面底部在 ${BLUE}Artifacts${GREEN} 下载打包好的 Windows .exe。"
    echo ""
else
    echo ""
    echo -e "${RED}❌ 推送失败，请检查您的网络连接、GitHub 仓库链接及账号推送权限。${NC}"
    echo ""
fi
