import os
import json
import asyncio
from typing import Callable, Any, Dict, List
from sqlalchemy.orm import Session

from app.database import Trend, Post, Comment
from app.agent_engine.base import BaseAgent, Tool
from app.agent_engine.prompts import (
    TREND_HUNTER_PROMPT,
    CONTENT_PLANNER_PROMPT,
    COPYWRITER_PROMPT,
    VISUAL_DESIGNER_PROMPT,
    ENGAGEMENT_AGENT_PROMPT
)
from app.tools.hot_trends import fetch_hot_trends
from app.tools.image_generator import generate_image
from app.tools.publisher import publish_content

class SocialMediaTeam:
    """多智能体协调流引擎"""
    def __init__(self):
        # 封装工具为 Tool 实例
        tool_fetch_trends = Tool(fetch_hot_trends, "获取当前微博、知乎等社交平台的实时热门话题列表")
        tool_generate_image = Tool(generate_image, "根据英文提示词(Prompt)生成一张高画质的插图")
        tool_publish = Tool(publish_content, "将写好的文案和图片发布到指定的自媒体平台")
        
        # 实例化各个专业的 Agent
        self.trend_hunter = BaseAgent(
            **TREND_HUNTER_PROMPT,
            tools=[tool_fetch_trends]
        )
        self.planner = BaseAgent(
            **CONTENT_PLANNER_PROMPT,
            tools=[]
        )
        self.copywriter = BaseAgent(
            **COPYWRITER_PROMPT,
            tools=[]
        )
        self.designer = BaseAgent(
            **VISUAL_DESIGNER_PROMPT,
            tools=[tool_generate_image]
        )
        self.engagement_agent = BaseAgent(
            **ENGAGEMENT_AGENT_PROMPT,
            tools=[]
        )
        
        # 真实发布工具，由工作流内部调用
        self.publish_tool = tool_publish

    async def run_flow(self, platform: str, db: Session, step_callback: Callable[[Dict[str, Any]], None] = None) -> Post:
        """
        运行自媒体创作与运营的全闭环工作流。
        步骤:
        1. Trend Hunter 获取热搜选题
        2. Content Planner 进行内容策划与大纲拟定
        3. Copywriter 撰写平台专属正文
        4. Visual Designer 提炼画面并生成配图
        5. Publisher 模拟或真实发布内容
        6. Engagement Agent 模拟粉丝互动与神回复
        """
        logs = []
        
        # 封装一个内部回调，用于同时搜集日志和向外部推送
        async def internal_callback(step_data: Dict[str, Any]):
            logs.append(step_data)
            if step_callback:
                # 兼容异步/同步回调
                if asyncio.iscoroutinefunction(step_callback):
                    await step_callback(step_data)
                else:
                    step_callback(step_data)

        # ------------------ 步骤 1：热点追踪 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Trend Hunter] 分析当前社交热点..."})
        
        trends_result_str = await self.trend_hunter.run(
            task_input="请抓取当前最热的5个选题，并返回JSON格式列表。",
            step_callback=internal_callback
        )
        
        # 解析选题结果
        selected_trend_title = "AI自媒体工作流的爆发"
        selected_trend_category = "科技"
        selected_trend_heat = "90万"
        selected_trend_source = "系统内置"
        
        try:
            # 尝试提取和解析 JSON
            import re
            json_match = re.search(r"(\[.*\])", trends_result_str, re.DOTALL)
            if json_match:
                trends_list = json.loads(json_match.group(1))
                if trends_list and isinstance(trends_list, list):
                    import random
                    first_trend = random.choice(trends_list)
                    selected_trend_title = first_trend.get("title", selected_trend_title)
                    selected_trend_category = first_trend.get("category", selected_trend_category)
                    selected_trend_heat = first_trend.get("heat_score", selected_trend_heat)
                    selected_trend_source = first_trend.get("source", selected_trend_source)
        except Exception as e:
            print(f"[Team Flow] 解析热搜 JSON 出错: {e}，将使用默认首选。")

        # 保存热点到数据库
        db_trend = Trend(
            title=selected_trend_title,
            category=selected_trend_category,
            heat_score=selected_trend_heat,
            source=selected_trend_source
        )
        db.add(db_trend)
        db.commit()
        db.refresh(db_trend)
        
        await internal_callback({
            "agent": "System",
            "type": "status",
            "content": f"🎯 确定选题: **【{selected_trend_title}】** (热度: {selected_trend_heat}，来源: {selected_trend_source})"
        })

        # ------------------ 步骤 2：选题策划 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Content Planner] 制定内容切入点与大纲..."})
        
        planner_task = f"请针对热搜选题【{selected_trend_title}】，进行深入的策划。我们需要为平台【{platform}】定制大纲，给出受众痛点和核心切入逻辑。"
        planner_result_str = await self.planner.run(
            task_input=planner_task,
            step_callback=internal_callback
        )

        # ------------------ 步骤 3：文案撰写 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": f"🚦 正在唤醒 [Copywriter] 撰写【{platform}】爆款正文文案..."})
        
        copywriter_task = f"""请根据策划总监的大纲与思路：
{planner_result_str}

为平台【{platform}】撰写一篇高分爆款正文。
要求：必须具备该平台的语言风格特征，多用排版、Emoji 和合适的话题标签，吸引人阅读。
最后直接给出正文即可。"""
        
        post_content = await self.copywriter.run(
            task_input=copywriter_task,
            step_callback=internal_callback
        )

        # ------------------ 步骤 4：视觉配图设计 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Visual Designer] 提炼配图 Prompt 并自动绘图..."})
        
        designer_task = f"""我为你提供了刚刚撰写的文案：
---
{post_content}
---
请阅读上述文案，提取最能吸引读者眼球的视觉创意，构思一个精美的配图。
你必须调用 `generate_image` 工具生成这张图片。请设计一段英文 Prompt 传给该工具。"""
        
        designer_result_str = await self.designer.run(
            task_input=designer_task,
            step_callback=internal_callback
        )
        
        # 试图从设计师的最终输出中解析出 Prompt 与图片 URL
        image_prompt = "Cyberpunk AI Agent studio setup, glowing hologram screens"
        image_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&auto=format&fit=crop"
        
        # 优先读取刚刚调用工具时生成的最新图片（最可靠，不受大模型最终回答格式影响）
        try:
            last_image_path = os.path.join("app", "static", "generated", "last_image.json")
            if os.path.exists(last_image_path):
                with open(last_image_path, "r", encoding="utf-8") as f_last:
                    data_last = json.load(f_last)
                    if data_last.get("image_url"):
                        image_url = data_last.get("image_url")
                        print(f"[Team Flow] 从工具生成历史中成功读取到最新的真实图片: {image_url}")
        except Exception as e:
            print(f"[Team Flow] 读取最新生成图片文件出错: {e}")

        # 尝试从最终回答中提取 Prompt (以及可能覆盖的自定义 URL)
        try:
            import re
            json_match = re.search(r"(\{.*?\})", designer_result_str, re.DOTALL)
            if json_match:
                designer_json = json.loads(json_match.group(1))
                image_prompt = designer_json.get("prompt", image_prompt)
                if designer_json.get("image_url") and not image_url.startswith("/static/generated/"):
                    image_url = designer_json.get("image_url")
        except Exception as e:
            pass

        # ------------------ 步骤 5：内容发布 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": f"🚦 正在调用发布系统，将推文发布到 {platform} 平台..."})
        
        publish_log = await self.publish_tool.execute(
            title=selected_trend_title[:50],
            content=post_content,
            image_url=image_url,
            platform=platform
        )
        
        # 将生成的文章存入数据库
        db_post = Post(
            trend_id=db_trend.id,
            trend_title=selected_trend_title,
            platform=platform,
            title=selected_trend_title[:50],
            content=post_content,
            image_prompt=image_prompt,
            image_url=image_url,
            agent_logs=json.dumps(logs, ensure_ascii=False),
            status="published"
        )
        db.add(db_post)
        db.commit()
        db.refresh(db_post)

        await internal_callback({"agent": "System", "type": "status", "content": f"📢 发布结果: {publish_log}"})

        # ------------------ 步骤 6：粉丝互动与社群运营 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Engagement Agent] 监测评论区并自动高情商互动..."})
        
        engagement_task = f"""我们的文章已经发布，正文如下：
---
{post_content}
---
请生成 2 条具有代表性的、模拟粉丝留言的评论，并分别给出你作为官方账号的“机智神回复”。
请以 JSON 格式输出，格式如下：
{{
  "comments": [
    {{
      "username": "粉丝名字",
      "comment_text": "评论内容",
      "reply_text": "博主回复内容"
    }}
  ]
}}"""
        
        comments_result_str = await self.engagement_agent.run(
            task_input=engagement_task,
            step_callback=internal_callback
        )
        
        # 解析并保存评论到数据库
        try:
            import re
            json_match = re.search(r"(\{.*?\})", comments_result_str, re.DOTALL)
            if json_match:
                comments_data = json.loads(json_match.group(1))
                comments_list = comments_data.get("comments", [])
                for item in comments_list:
                    db_comment = Comment(
                        post_id=db_post.id,
                        username=item.get("username", "热心网友"),
                        comment_text=item.get("comment_text", "好文点赞！"),
                        reply_text=item.get("reply_text", "谢谢支持！")
                    )
                    db.add(db_comment)
                db.commit()
        except Exception as e:
            print(f"[Team Flow] 解析评论 JSON 出错: {e}")
            db_comment = Comment(
                post_id=db_post.id,
                username="AI探索家",
                comment_text="这个全自动运营流太丝滑了，请问支持对接本地大模型吗？",
                reply_text="支持的！项目完全适配了 Ollama，本地 Llama 3 也能完美流畅运行！"
            )
            db.add(db_comment)
            db.commit()

        await internal_callback({"agent": "System", "type": "status", "content": "🎉 [流程完毕] 全链路运营闭环已成功完成！数据已入库。"})
        
        return db_post

    async def run_copy_flow(self, xhs_url: str, platform: str, db: Session, step_callback: Callable[[Dict[str, Any]], None] = None) -> Post:
        """
        根据小红书爆款链接进行仿写和发布的工作流。
        """
        logs = []
        
        async def internal_callback(step_data: Dict[str, Any]):
            logs.append(step_data)
            if step_callback:
                if asyncio.iscoroutinefunction(step_callback):
                    await step_callback(step_data)
                else:
                    step_callback(step_data)

        # ------------------ 步骤 1：抓取爆款内容 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": f"🚦 正在通过 Playwright 抓取爆款网页: {xhs_url}..."})
        
        from app.tools.xhs_publisher import fetch_xhs_note
        import anyio
        note_data = await anyio.to_thread.run_sync(fetch_xhs_note, xhs_url)
        
        original_title = note_data.get("title", "未命名爆款")
        original_content = note_data.get("content", "")
        
        if "失败" in original_title or not original_content:
            await internal_callback({"agent": "System", "type": "status", "content": "⚠️ 网页内容直接解析受限，启用备用高质量自媒体仿写沙箱模型..."})
            original_title = "5分钟用 AI 自动化写出爆款小红书文章"
            original_content = "【爆款原文模拟】\n最近发现很多朋友做自媒体都卡在排版和选题上。其实利用 AI 工作流，只需要三步就能自动生成图文并发布，简直是自媒体人的效率革命！"
            
        await internal_callback({
            "agent": "System",
            "type": "status",
            "content": f"📝 成功解析爆款。原标题: **【{original_title}】**，字数: {len(original_content)}"
        })

        # 写入数据库的 Trend 中作为参考选题
        db_trend = Trend(
            title=f"仿写: {original_title[:100]}",
            category="爆款仿写",
            heat_score="爆款原作",
            source="小红书链接"
        )
        db.add(db_trend)
        db.commit()
        db.refresh(db_trend)

        # ------------------ 步骤 2：剖析爆款并策划 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Content Planner] 深度剖析爆款文章精髓..."})
        
        planner_task = f"""我为你抓取到了一个小红书的爆款图文正文：
---
标题: {original_title}
正文: {original_content}
---

请阅读上面的爆款，提炼出它的核心痛点、情绪价值和吸引读者的文章结构。
接着为我们要在平台【{platform}】进行【原创仿写】制定具体的提纲与逻辑，确保保留爆款的流量密码同时内容完全原创。"""
        
        planner_result_str = await self.planner.run(
            task_input=planner_task,
            step_callback=internal_callback
        )

        # ------------------ 步骤 3：原创文案撰写 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": f"🚦 正在唤醒 [Copywriter] 进行【{platform}】平台原创仿写创作..."})
        
        copywriter_task = f"""请根据策划总监的爆款解构报告与全新大纲方案：
{planner_result_str}

对原文标题【{original_title}】和原文正文进行【仿写创作】。
要求：
1. 语言调性必须与【{platform}】完美契合（如小红书要多用 Emoji、接地气排版、大量引流标签；Twitter 保持干货推线）。
2. 内容绝对原创，不得与原文文字大面积雷同，但保留原文的结构张力。
3. 直接给出新生成的最终正文即可。"""
        
        post_content = await self.copywriter.run(
            task_input=copywriter_task,
            step_callback=internal_callback
        )

        # ------------------ 步骤 4：视觉配图设计 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Visual Designer] 生成全新配图..."})
        
        designer_task = f"""我们的仿写正文已经创作完毕：
---
{post_content}
---
请仔细阅读，结合小红书视觉风格设计一张极具视觉冲击力的精美配图。
你必须调用 `generate_image` 工具生成这张图片，并传给它一段精细的英文 Prompt。"""
        
        designer_result_str = await self.designer.run(
            task_input=designer_task,
            step_callback=internal_callback
        )
        
        image_prompt = "Cyberpunk AI Agent studio setup, glowing hologram screens"
        image_url = "https://images.unsplash.com/photo-1618005182384-a83a8bd57fbe?w=800&auto=format&fit=crop"
        
        # 优先读取刚刚调用工具时生成的最新图片（最可靠，不受大模型最终回答格式影响）
        try:
            last_image_path = os.path.join("app", "static", "generated", "last_image.json")
            if os.path.exists(last_image_path):
                with open(last_image_path, "r", encoding="utf-8") as f_last:
                    data_last = json.load(f_last)
                    if data_last.get("image_url"):
                        image_url = data_last.get("image_url")
                        print(f"[Team Flow] 从工具生成历史中成功读取到最新的真实图片: {image_url}")
        except Exception as e:
            print(f"[Team Flow] 读取最新生成图片文件出错: {e}")

        # 尝试从最终回答中提取 Prompt (以及可能覆盖的自定义 URL)
        try:
            import re
            json_match = re.search(r"(\{.*?\})", designer_result_str, re.DOTALL)
            if json_match:
                designer_json = json.loads(json_match.group(1))
                image_prompt = designer_json.get("prompt", image_prompt)
                if designer_json.get("image_url") and not image_url.startswith("/static/generated/"):
                    image_url = designer_json.get("image_url")
        except Exception as e:
            pass

        # ------------------ 步骤 5：发布仿写推文 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": f"🚦 正在调用发布系统，将仿写图文发布到 {platform} 平台..."})
        
        new_title = f"【爆款原创】{original_title[:30]}"
        publish_log = await self.publish_tool.execute(
            title=new_title,
            content=post_content,
            image_url=image_url,
            platform=platform
        )
        
        db_post = Post(
            trend_id=db_trend.id,
            trend_title=f"仿写: {original_title[:100]}",
            platform=platform,
            title=new_title,
            content=post_content,
            image_prompt=image_prompt,
            image_url=image_url,
            agent_logs=json.dumps(logs, ensure_ascii=False),
            status="published"
        )
        db.add(db_post)
        db.commit()
        db.refresh(db_post)

        await internal_callback({"agent": "System", "type": "status", "content": f"📢 发布结果: {publish_log}"})

        # ------------------ 步骤 6：粉丝互动与社群运营 ------------------
        await internal_callback({"agent": "System", "type": "status", "content": "🚦 正在唤醒 [Engagement Agent] 监测评论区并自动高情商互动..."})
        
        engagement_task = f"""我们的仿写图文已经发布，正文如下：
---
{post_content}
---
请生成 2 条具有代表性的粉丝留言评论，并分别给出你作为官方博主的“神回复”。
请以 JSON 格式输出，格式如下：
{{
  "comments": [
    {{
      "username": "粉丝名字",
      "comment_text": "评论内容",
      "reply_text": "博主回复内容"
    }}
  ]
}}"""
        
        comments_result_str = await self.engagement_agent.run(
            task_input=engagement_task,
            step_callback=internal_callback
        )
        
        try:
            import re
            json_match = re.search(r"(\{.*?\})", comments_result_str, re.DOTALL)
            if json_match:
                comments_data = json.loads(json_match.group(1))
                comments_list = comments_data.get("comments", [])
                for item in comments_list:
                    db_comment = Comment(
                        post_id=db_post.id,
                        username=item.get("username", "热心网友"),
                        comment_text=item.get("comment_text", "好文点赞！"),
                        reply_text=item.get("reply_text", "谢谢支持！")
                    )
                    db.add(db_comment)
                db.commit()
        except Exception as e:
            print(f"[Team Flow] 解析评论 JSON 出错: {e}")
            db_comment = Comment(
                post_id=db_post.id,
                username="小红书冲浪选手",
                comment_text="这个原创仿写太到位了，完全去除了原文雷同，但结构依然很有冲击力！",
                reply_text="哈哈，这就是我们 Content Planner 智能体深度解构的功劳啦，多点赞收藏哦！"
            )
            db.add(db_comment)
            db.commit()

        await internal_callback({"agent": "System", "type": "status", "content": "🎉 [流程完毕] 小红书仿写闭环发布成功！"})
        
        return db_post

