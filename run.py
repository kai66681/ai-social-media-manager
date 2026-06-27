import os
import shutil
import sys
import subprocess
import asyncio

# 解决 Windows 平台下 Playwright 依赖 asyncio 创建子进程的兼容性问题
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

def setup_env():
    """检查并创建 .env 配置文件"""
    env_file = ".env"
    example_file = ".env.example"
    
    if not os.path.exists(env_file):
        if os.path.exists(example_file):
            print(f"检测到未配置环境，正在从 {example_file} 自动创建 {env_file}...")
            shutil.copy(example_file, env_file)
            print("[OK] 已生成默认配置文件，可在 .env 中填写您的大模型 API 密钥。")
        else:
            print("警告: 未找到 .env.example 模板文件，请手动创建 .env。")
    else:
        print("[OK] 检测到已存在 .env 配置文件。")

def start_server():
    """运行 Web 服务器"""
    setup_env()
    
    print("\n" + "="*50)
    print("      AI Social Media Manager 智能运营管家 启动中")
    print("="*50)
    print("  系统即将启动 FastAPI Web 接口服务，请用浏览器访问:")
    print("  ->  http://127.0.0.1:8000")
    print("="*50 + "\n")
    
    # 动态载入并运行 uvicorn
    try:
        import uvicorn
    except ImportError:
        print("错误: 检测到未安装 uvicorn 或其它相关依赖。")
        print("请先执行以下命令安装依赖：")
        print("  pip install -r requirements.txt")
        sys.exit(1)
        
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)

if __name__ == "__main__":
    start_server()
