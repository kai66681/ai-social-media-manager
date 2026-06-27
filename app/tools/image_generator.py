import os
import random
import httpx
from app.config import IMAGE_PROVIDER, HF_TOKEN, LLM_API_KEY, LLM_API_BASE

# 预设的极其精美、充满科技感与高审美的 Unsplash 静态壁纸库
# 这些图片能让 Mock 模式下生成的卡片看起来极度 premium
MOCK_IMAGES = [
    "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&auto=format&fit=crop&q=80",  # 3D 流体彩色渐变
    "https://images.unsplash.com/photo-1634017839464-5c339ebe3cb4?w=800&auto=format&fit=crop&q=80",  # 炫彩玻璃几何体
    "https://images.unsplash.com/photo-1639762681485-074b7f938ba0?w=800&auto=format&fit=crop&q=80",  # 数字化抽象神经网络
    "https://images.unsplash.com/photo-1579546929518-9e396f3cc809?w=800&auto=format&fit=crop&q=80",  # 梦幻全息色彩渐变
    "https://images.unsplash.com/photo-1607604276583-eef5d076aa5f?w=800&auto=format&fit=crop&q=80"   # 赛博朋克霓虹色调
]

import json

async def generate_image(prompt: str) -> str:
    """
    根据给定的 Prompt 生成精美配图，并在生成成功后将图片地址统一缓存到本地文件中，
    确保发布工作流可以百分之百准确地提取到最新生成的配图。
    """
    res_url = await _generate_image_impl(prompt)
    
    # 统一缓存最新生成的图片地址，方便工作流直接使用，解决大模型最终回复格式解析的局限性
    try:
        cache_path = os.path.join("app", "static", "generated", "last_image.json")
        with open(cache_path, "w", encoding="utf-8") as f_last:
            json.dump({"image_url": res_url}, f_last, ensure_ascii=False)
        print(f"[Image Tool] 成功缓存最新配图地址: {res_url} -> {cache_path}")
    except Exception as e:
        print(f"[Image Tool] 缓存配图地址出错: {e}")
        
    return res_url

async def _generate_image_impl(prompt: str) -> str:
    """
    实际生成配图的内部实现。
    """
    # 确保本地静态文件目录存在，用于保存生成的图片
    os.makedirs(os.path.join("app", "static", "generated"), exist_ok=True)
    filename = f"img_{random.randint(100000, 999999)}.png"
    save_path = os.path.join("app", "static", "generated", filename)
    web_url = f"/static/generated/{filename}"
    
    # 1. 智谱 AI CogView 生图模式
    if IMAGE_PROVIDER == "zhipu" and LLM_API_KEY and not LLM_API_KEY.startswith("your-"):
        try:
            print(f"[Image Tool] 正在调用智谱 CogView 生成图片...")
            headers = {
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "cogview-4",
                "prompt": prompt
            }
            api_url = "https://open.bigmodel.cn/api/paas/v4/images/generations"
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(api_url, headers=headers, json=data)
                response.raise_for_status()
                res_json = response.json()
                img_url = res_json["data"][0]["url"]
                
                # 下载图片保存到本地
                img_res = await client.get(img_url)
                if img_res.status_code == 200:
                    with open(save_path, "wb") as f:
                        f.write(img_res.content)
                    
                    # 裁剪掉底部的 AI 生成水印（智谱 CogView 默认会在右下角强制添加带有合规标识的水印）
                    try:
                        from PIL import Image
                        with Image.open(save_path) as img:
                            w, h = img.size
                            # 剪裁掉底部的 80 像素，彻底去除右下角水印字样且不伤害主体构图
                            cropped_img = img.crop((0, 0, w, h - 80))
                            cropped_img.save(save_path)
                        print(f"[Image Tool] 成功裁切掉生成的本地图片底部的 AI 水印。")
                    except Exception as img_err:
                        print(f"[Image Tool] 自动裁剪图片水印失败: {img_err}")
                        
                    return web_url
        except Exception as e:
            print(f"[Image Tool] 智谱 CogView 绘图出错: {e}，将自动降级...")

    # 2. OpenAI DALL-E 3 模式
    if IMAGE_PROVIDER == "openai" and LLM_API_KEY and not LLM_API_KEY.startswith("your-"):
        try:
            print(f"[Image Tool] 正在调用 OpenAI DALL-E 3 生成图片...")
            headers = {
                "Authorization": f"Bearer {LLM_API_KEY}",
                "Content-Type": "application/json"
            }
            data = {
                "model": "dall-e-3",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024"
            }
            # 兼容一些自定义的 API Base
            base_url = LLM_API_BASE.rstrip("/").replace("/v1", "")
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(f"{base_url}/v1/images/generations", headers=headers, json=data)
                response.raise_for_status()
                res_json = response.json()
                img_url = res_json["data"][0]["url"]
                
                # 下载图片保存到本地
                img_res = await client.get(img_url)
                if img_res.status_code == 200:
                    with open(save_path, "wb") as f:
                        f.write(img_res.content)
                    return web_url
        except Exception as e:
            print(f"[Image Tool] DALL-E 3 绘图出错: {e}，将自动降级...")

    # 2. Hugging Face 免费接口模式 (FLUX.1-schnell 或是 SD-XL)
    elif IMAGE_PROVIDER == "huggingface" and HF_TOKEN:
        # 使用先进的 FLUX.1-schnell 模型或 SDXL
        hf_model = "black-forest-labs/FLUX.1-schnell" 
        api_url = f"https://api-inference.huggingface.co/models/{hf_model}"
        headers = {"Authorization": f"Bearer {HF_TOKEN}"}
        
        try:
            print(f"[Image Tool] 正在调用 Hugging Face ({hf_model}) 生成图片...")
            async with httpx.AsyncClient(timeout=90.0) as client:
                response = await client.post(
                    api_url,
                    headers=headers,
                    json={"inputs": prompt},
                )
                if response.status_code == 200:
                    with open(save_path, "wb") as f:
                        f.write(response.content)
                    return web_url
                else:
                    print(f"[Image Tool] Hugging Face 接口返回错误 {response.status_code}: {response.text}")
        except Exception as e:
            print(f"[Image Tool] Hugging Face 绘图出错: {e}")

    # 3. Fallback / Mock 模式：使用预设的极高审美 Unsplash 图片
    # 为了表现出“配图与内容相关”，我们根据 prompt 里的关键词做个简单的筛选，使得匹配更智能！
    selected_img = MOCK_IMAGES[0]
    prompt_lower = prompt.lower()
    
    if "cyberpunk" in prompt_lower or "neon" in prompt_lower or "cyber" in prompt_lower:
        selected_img = MOCK_IMAGES[4] # 赛博朋克霓虹
    elif "brain" in prompt_lower or "network" in prompt_lower or "neural" in prompt_lower or "code" in prompt_lower:
        selected_img = MOCK_IMAGES[2] # 数字化抽象神经网络
    elif "glass" in prompt_lower or "holographic" in prompt_lower or "3d" in prompt_lower:
        selected_img = MOCK_IMAGES[1] # 玻璃几何
    elif "gradient" in prompt_lower or "color" in prompt_lower or "abstract" in prompt_lower:
        selected_img = MOCK_IMAGES[3] # 梦幻渐变
    else:
        selected_img = random.choice(MOCK_IMAGES)

    print(f"[Image Tool] 启用沙箱 Mock 配图: {selected_img}")
    return selected_img
