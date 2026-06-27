import httpx
from app.config import REAL_PUBLISH_ENABLED, WEBHOOK_URL

async def publish_content(title: str, content: str, image_url: str, platform: str) -> str:
    """
    发布生成的内容至目标社交平台。
    如果启用了真实发布模式，将通过 Webhook 推送。否则将作为沙箱模拟发布。
    
    参数:
        title (str): 文章或推文的标题。
        content (str): 文章或推文的正文内容。
        image_url (str): 配图的 URL。
        platform (str): 目标发布平台。
    """
    if REAL_PUBLISH_ENABLED:
        if platform == "小红书":
            try:
                print(f"[Publish Tool] 检测到真实发布模式，正在调用 Playwright 真实小红书发布工具...")
                from app.tools.xhs_publisher import xhs_publish_post
                import anyio
                res = await anyio.to_thread.run_sync(
                    lambda: xhs_publish_post(title=title, content=content, image_relative_path=image_url)
                )
                if res.get("status") == "success":
                    return f"【真实发布成功】{res.get('message')}"
                else:
                    return f"【真实发布失败】{res.get('message')}"
            except Exception as e:
                return f"【真实发布异常】小红书自动化发布发生错误: {e}"
        
        elif WEBHOOK_URL:
            # 其它平台走 Webhook
            payload = {
                "title": title,
                "content": content,
                "image_url": image_url,
                "platform": platform,
                "event": "social_media_publish"
            }
            try:
                print(f"[Publish Tool] 正在将内容发布至 Webhook ({WEBHOOK_URL})...")
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.post(WEBHOOK_URL, json=payload)
                    if response.status_code in [200, 201]:
                        return f"【成功】内容已真实推送到 Webhook。HTTP {response.status_code}"
                    else:
                        return f"【失败】真实发布失败，Webhook 返回状态码 {response.status_code}: {response.text}"
            except Exception as e:
                return f"【错误】真实发布发生异常: {e}"
            
    # 沙箱模拟发布
    print(f"[Publish Tool] [沙箱模式] 已成功在后台将内容发布到模拟的社交账号({platform})！")
    return f"【成功】[沙箱模式] 内容已成功发布到虚拟的 {platform} 平台！配图已渲染完毕。"
