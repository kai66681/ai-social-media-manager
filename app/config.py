import os
from dotenv import load_dotenv

# 加载当前目录或父目录中的 .env 文件
load_dotenv()

# Web 服务配置
PORT = int(os.getenv("PORT", "8000"))
HOST = os.getenv("HOST", "127.0.0.1")

# 数据库配置
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./social_media.db")

# 大模型配置 (默认兼容 OpenAI 格式，以支持各种中转、DeepSeek、Ollama 等)
LLM_API_KEY = os.getenv("LLM_API_KEY", "")
LLM_API_BASE = os.getenv("LLM_API_BASE", "https://api.openai.com/v1")
LLM_MODEL = os.getenv("LLM_MODEL", "gpt-4o-mini")

# 图像生成配置
# 支持 "mock" (生成随机美化占位图), "huggingface" (免费 HF 接口), "openai" (DALL-E)
IMAGE_PROVIDER = os.getenv("IMAGE_PROVIDER", "mock")
HF_TOKEN = os.getenv("HF_TOKEN", "")

# 真实发布配置 (当启用真实发布时的 Webhook 或 Token)
REAL_PUBLISH_ENABLED = os.getenv("REAL_PUBLISH_ENABLED", "false").lower() == "true"
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "")
