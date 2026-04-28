#!/bin/bash
# 启动后端服务

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PYTHON_EXEC="$PROJECT_DIR/.venv/bin/python"

if [ ! -x "$PYTHON_EXEC" ]; then
    echo "❌ 未找到项目虚拟环境 Python: $PYTHON_EXEC"
    echo "请先在项目根目录创建虚拟环境并安装依赖。"
    exit 1
fi

# 进入后端目录
cd "$SCRIPT_DIR"

# 检查.env文件
if [ ! -f .env ]; then
    echo "⚠️  未找到.env文件，使用默认配置"
fi

echo "使用 Python: $PYTHON_EXEC"

# 使用 python -m uvicorn 确保使用正确的 Python 环境
# 并且设置环境变量确保子进程也使用正确的 Python
export PYTHON_EXECUTABLE="$PYTHON_EXEC"
$PYTHON_EXEC -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
