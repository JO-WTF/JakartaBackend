#!/bin/bash
# setup.sh - Jakarta Backend项目环境设置脚本

set -e  # 遇到错误时退出

echo "🚀 Jakarta Backend 环境设置开始..."

# 检查 Python 版本
echo "📋 检查 Python 版本..."
PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
echo "检测到 Python 版本: $PYTHON_VERSION"

if [[ "$PYTHON_VERSION" == "3.13" ]]; then
    echo "⚠️  警告: 检测到 Python 3.13，某些包可能存在兼容性问题"
    echo "💡 建议使用 Python 3.11 或 3.12 以获得最佳兼容性"
fi

# 检查是否在虚拟环境中
if [[ "$VIRTUAL_ENV" != "" ]]; then
    echo "✅ 检测到虚拟环境: $VIRTUAL_ENV"
else
    echo "⚠️  建议在虚拟环境中安装依赖"
    echo "💡 运行以下命令创建虚拟环境:"
    echo "   python3 -m venv venv"
    echo "   source venv/bin/activate"
    echo ""
    read -p "是否继续安装到系统环境? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        echo "❌ 安装已取消"
        exit 1
    fi
fi

# macOS 特殊处理
if [[ "$OSTYPE" == "darwin"* ]]; then
    echo "🍎 检测到 macOS 系统"
    
    # 检查 PostgreSQL
    if ! command -v pg_config &> /dev/null; then
        echo "📦 PostgreSQL 工具未找到，正在安装..."
        if command -v brew &> /dev/null; then
            brew install postgresql@14
            echo "✅ PostgreSQL 已安装"
        else
            echo "❌ 需要 Homebrew 来安装 PostgreSQL"
            echo "请先安装 Homebrew: https://brew.sh/"
            exit 1
        fi
    else
        echo "✅ PostgreSQL 工具已可用"
    fi
    
    # 设置 PostgreSQL PATH
    export PATH="/opt/homebrew/opt/postgresql@14/bin:$PATH"
fi

# 选择安装类型
echo ""
echo "📦 选择安装类型:"
echo "1) 生产环境 (requirements.txt)"
echo "2) 开发环境 (requirements-dev.txt) - 包含代码质量工具"
echo "3) Python 3.13 兼容版本 (requirements-python313.txt)"
echo ""
read -p "请选择 (1-3): " -n 1 -r choice
echo

case $choice in
    1)
        REQ_FILE="requirements.txt"
        echo "🎯 安装生产环境依赖..."
        ;;
    2)
        REQ_FILE="requirements-dev.txt"
        echo "🛠️  安装开发环境依赖..."
        ;;
    3)
        REQ_FILE="requirements-python313.txt"
        echo "🐍 安装 Python 3.13 兼容依赖..."
        ;;
    *)
        echo "❌ 无效选择，使用默认的生产环境依赖"
        REQ_FILE="requirements.txt"
        ;;
esac

# 安装依赖
echo "📦 正在安装依赖从 $REQ_FILE..."

if [[ "$VIRTUAL_ENV" != "" ]]; then
    # 在虚拟环境中
    pip install -r "$REQ_FILE"
else
    # 系统环境，需要 --break-system-packages
    pip install -r "$REQ_FILE" --break-system-packages
fi

echo ""
echo "✅ 依赖安装完成！"
echo ""
echo "🎉 接下来的步骤:"
echo "1. 配置环境变量 (.env 文件)"
echo "2. 设置数据库连接"
echo "3. 运行应用: uvicorn app.main:app --reload"
echo ""
echo "📚 查看 README.md 获取详细配置说明"