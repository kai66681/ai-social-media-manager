import os
import json
import re
import inspect
from typing import List, Dict, Any, Callable, Generator, AsyncGenerator
import httpx
from app.config import LLM_API_KEY, LLM_API_BASE, LLM_MODEL

class Tool:
    """Agent 工具封装类"""
    def __init__(self, name: Callable, description: str):
        self.func = name
        self.name = name.__name__
        self.description = description
        # 自动提取参数签名
        self.signature = inspect.signature(name)
        
    def get_schema(self) -> Dict[str, Any]:
        """生成工具的描述 Schema"""
        params = {}
        for param_name, param in self.signature.parameters.items():
            if param_name == "self" or param_name == "db": # 过滤特殊依赖注入参数
                continue
            params[param_name] = {
                "type": str(param.annotation.__name__) if param.annotation != inspect.Parameter.empty else "str",
                "default": param.default if param.default != inspect.Parameter.empty else None,
                "required": param.default == inspect.Parameter.empty
            }
        return {
            "name": self.name,
            "description": self.description,
            "parameters": params
        }

    async def execute(self, *args, **kwargs) -> Any:
        """执行工具函数 (支持异步与同步函数)"""
        cleaned_kwargs = {}
        for param_name, param in self.signature.parameters.items():
            if param_name == "self" or param_name == "db":
                continue
            if param_name in kwargs:
                val = kwargs[param_name]
                # 检查大模型是否误传了包含参数定义的 dict
                # 例如: {"limit": {"type": "int", "default": 5, "required": false}}
                if isinstance(val, dict) and any(k in val for k in ["type", "default", "required"]):
                    if "default" in val:
                        val = val["default"]
                    elif param.default != inspect.Parameter.empty:
                        val = param.default
                    else:
                        val = None
                
                # 如果定义了类型且 val 不是该类型，尝试进行强制类型转换
                if param.annotation != inspect.Parameter.empty and val is not None:
                    try:
                        if param.annotation == int:
                            val = int(val)
                        elif param.annotation == float:
                            val = float(val)
                        elif param.annotation == str:
                            val = str(val)
                    except Exception:
                        if param.default != inspect.Parameter.empty:
                            val = param.default
                
                cleaned_kwargs[param_name] = val
            elif param.default != inspect.Parameter.empty:
                cleaned_kwargs[param_name] = param.default

        if inspect.iscoroutinefunction(self.func):
            return await self.func(*args, **cleaned_kwargs)
        return self.func(*args, **cleaned_kwargs)


class BaseAgent:
    """手写轻量级 Agent 基础类，支持 ReAct 循环和手写 Tool-Calling 解析"""
    def __init__(
        self,
        name: str,
        role: str,
        goal: str,
        backstory: str,
        tools: List[Tool] = None,
        llm_config: Dict[str, Any] = None
    ):
        self.name = name
        self.role = role
        self.goal = goal
        self.backstory = backstory
        self.tools = tools or []
        self.tools_map = {tool.name: tool for tool in self.tools}
        
        # 默认 LLM 配置
        self.api_key = (llm_config or {}).get("api_key", LLM_API_KEY)
        self.api_base = (llm_config or {}).get("api_base", LLM_API_BASE)
        self.model = (llm_config or {}).get("model", LLM_MODEL)
        
    async def _trigger_callback(self, callback: Callable[[Dict[str, Any]], None], data: Dict[str, Any]):
        """安全地触发同步或异步回调函数"""
        if not callback:
            return
        if inspect.iscoroutinefunction(callback):
            await callback(data)
        else:
            callback(data)
        
    def _get_system_prompt(self) -> str:
        """生成智能体的系统提示词，注入角色背景和可用的工具描述"""
        tools_desc = ""
        for tool in self.tools:
            schema = tool.get_schema()
            tools_desc += f"- **{schema['name']}**: {schema['description']}\n"
            if schema['parameters']:
                tools_desc += "  参数说明:\n"
                for param_name, info in schema['parameters'].items():
                    req_str = "必填" if info['required'] else "可选"
                    default_str = f", 默认值: {info['default']}" if info['default'] is not None else ""
                    tools_desc += f"    - **{param_name}** ({info['type']}, {req_str}{default_str})\n"
            else:
                tools_desc += "  参数说明: 无\n"
            tools_desc += "\n"
            
        system_prompt = f"""你扮演智能体角色：{self.name}
角色定位：{self.role}
核心目标：{self.goal}
背景设定：{self.backstory}

"""
        if self.tools:
            system_prompt += f"""你可以调用以下工具来辅助你完成任务：
{tools_desc}
若要调用工具，你必须严格使用以下 JSON 格式进行输出（一次只能调用一个工具，且不能包含其他额外内容）：
Action: {{
  "name": "工具函数名",
  "args": {{
    "参数名": "参数值"
  }}
}}

当你执行完工具，系统会给你返回工具的执行结果 Observation。
如果你已经拥有完成任务所需的全部信息，请严格使用以下格式输出你的最终答案（不要包含 Action）：
Final Answer: 你的最终回答内容

请时刻保持思考，在每次调用工具前或给出最终答案前，都以 `Thought: 你的思考过程` 开头。
记住：必须且只能在 Action 或 Final Answer 之中选择一个输出！
"""
        else:
            system_prompt += "请直接给出你的回答。以 `Thought: 你的思考过程` 开头，并以 `Final Answer: 你的最终回答内容` 结束。"
            
        return system_prompt

    async def _call_llm(self, messages: List[Dict[str, str]]) -> str:
        """调用大模型 API 获取回复"""
        # 如果没有配置 API Key，启动优雅的 Mock 模式以供本地无密钥调试
        if not self.api_key or self.api_key.startswith("your-"):
            return self._mock_response(messages)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        data = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.7
        }
        
        async with httpx.AsyncClient(timeout=180.0) as client:
            try:
                response = await client.post(
                    f"{self.api_base.rstrip('/')}/chat/completions",
                    headers=headers,
                    json=data
                )
                response.raise_for_status()
                res_json = response.json()
                return res_json["choices"][0]["message"]["content"]
            except Exception as e:
                # 异常时降级为 Mock，保证系统高可用，同时记录错误
                print(f"[LLM Error] 调用 API 失败: {e}，将自动降级为沙箱模拟数据...")
                return self._mock_response(messages)

    def _mock_response(self, messages: List[Dict[str, str]]) -> str:
        """Mock 回复生成器，专为无 API Key 演示或离线演示而设"""
        last_user_message = messages[-1]["content"] if messages else ""
        
        # 针对不同智能体生成高度真实的拟真日志，展现完美的 CoT 思考过程
        if "Trend Hunter" in self.name or "热点" in last_user_message:
            # 检查是否要求调用工具
            if "Action" not in "".join([m.get("content", "") for m in messages if m["role"] == "assistant"]):
                return """Thought: 我需要发现当前的自媒体热点趋势。我应该调用 `fetch_hot_trends` 工具来获取最新的热门榜单。
Action: {
  "name": "fetch_hot_trends",
  "args": {
    "limit": 5
  }
}"""
            else:
                return """Thought: 我已经获取到了热门微博和知乎热榜的数据。其中最热门的是“生成式人工智能在自媒体领域的全面落地”和“端侧AI助手的日常应用”。我需要对这些热点进行精简总结，输出热点标题和热度值。
Final Answer: [
  {"title": "生成式 AI 在自媒体的爆发式应用", "category": "科技", "heat_score": "98.5万", "source": "知乎热榜"},
  {"title": "普通人如何用 AI 工具副业变现", "category": "财经", "heat_score": "89.2万", "source": "微博热搜"},
  {"title": "2026年端侧智能设备发展白皮书", "category": "科技", "heat_score": "75.1万", "source": "百度热点"}
]"""
                
        elif "Content Planner" in self.name or "策划" in last_user_message:
            return f"""Thought: 针对热点“生成式 AI 在自媒体的爆发式应用”，我需要为小红书和 Twitter 策划大纲。小红书需要突出实用性与视觉冲击，Twitter 突出简练的科技感。
Final Answer: {{
  "concept": "AI 正在掀起自媒体革命。以前写稿、配图、排版需要数小时，现在利用 AI Agent 可以在5分钟内流水线作业。本文将深度剖析这个效率奇迹。",
  "target_platforms": ["小红书", "Twitter"],
  "outlines": {{
    "小红书": "【标题】吹爆这个AI工作流！5分钟搞定一篇爆款自媒体图文！\n【大纲】\n1. 痛点引入：做自媒体卡在选题和配图？\n2. 核心揭秘：多智能体协作（Trend -> Copywriter -> Designer）的闭环。\n3. 保姆级教程：从热搜到自动排版发布的实操步骤。\n4. 总结呼吁：不要等，先用起来！",
    "Twitter": "【推文线索】AI Agents 正在重塑内容创作的生产力曲线。下面用 5 个 Steps 拆解如何用 Multi-Agent 搭建一条全自动自媒体生产线..."
  }}
}}"""

        elif "Copywriter" in self.name:
            return """Thought: 我收到了策划案。现在我需要为小红书平台撰写一篇充满吸引力、多用 Emoji 的爆款文案。
Final Answer: 💡 吹爆这个 AI 工作流！5分钟搞定一篇爆款自媒体图文！

做自媒体的小伙伴，你是不是每天都在为【写什么选题】和【配什么图】想破脑袋？🤯
今天给大家分享一个惊天效率神器 —— **多智能体自媒体运营管家**！✨

原来需要一下午的创作，现在只需3步：
1️⃣ **Trend Hunter** 实时捕获知乎、微博热搜话题 📈
2️⃣ **Content Planner** 深度策划大纲，拟定爆款切入点 📝
3️⃣ **Copywriter** 自动撰写针对平台特性优化的文案，连 Emoji 和标签都帮你加好了！
4️⃣ **Visual Designer** 自动生成高画质配图提示词并绘图 🎨

告别创作焦虑，用 AI 赋能你的第二曲线！🚀

#AI变现 #自媒体运营 #自媒体干货 #AI工作流 #生产力工具 #搞钱副业"""

        elif "Visual Designer" in self.name:
            if "Action" not in "".join([m.get("content", "") for m in messages if m["role"] == "assistant"]):
                return """Thought: 我需要为“自媒体 AI 工作流”的文章生成一张精美的配图。我应该先拟定一个表现“科技、效率、未来感”的 Prompt，然后调用 `generate_image` 工具生成图片。
Action: {
  "name": "generate_image",
  "args": {
    "prompt": "An artistic cyberpunk studio setup, a floating glowing brain made of binary code, multiple holographic screens showing graphs and charts, warm neon ambient lighting, ultra-detailed, 8k resolution, modern tech style"
  }
}"""
            else:
                # 从对话历史中提取上一步工具返回的真实图片 URL
                actual_image_url = "https://images.unsplash.com/photo-1607604276583-eef5d076aa5f?w=800&auto=format&fit=crop&q=80"
                for m in reversed(messages):
                    content = m.get("content", "")
                    if "Observation:" in content and "/static/generated/" in content:
                        import re as _re
                        url_match = _re.search(r"(/static/generated/[a-zA-Z0-9_]+\.png)", content)
                        if url_match:
                            actual_image_url = url_match.group(1)
                            break
                    elif "Observation:" in content and "unsplash.com" in content:
                        actual_image_url = content.replace("Observation:", "").strip()
                        break
                return f"""Thought: 图像已经生成完毕。我需要将最终生成的图片 URL 和我的 Prompt 返回。
Final Answer: {{
  "prompt": "An artistic cyberpunk studio setup, a floating glowing brain made of binary code, multiple holographic screens showing graphs and charts, warm neon ambient lighting, ultra-detailed, 8k resolution, modern tech style",
  "image_url": "{actual_image_url}"
}}"""

        elif "Engagement Agent" in self.name:
            return """Thought: 粉丝留言问：“这个工作流能支持本地大模型吗？API Key 太贵了”。我需要以运营者的身份，给出既专业又亲切的回复。
Final Answer: {
  "comments": [
    {
      "username": "掘金打工人",
      "comment_text": "这个真的强！手写 Agent 引擎比直接调 LangChain 还要丝滑，支持本地 Llama 3 运行吗？",
      "reply_text": "必须支持！项目内置了 Ollama 适配，不需要 API Key 也能完全本地离线跑起来，安全又省钱！点个 Star 持续关注哟～🚀"
    },
    {
      "username": "小红书种草酱",
      "comment_text": "哇！这个图是用什么模型生成的？质感也太棒了吧！",
      "reply_text": "默认集成了免费免密钥的 Flux 图像接口，效果非常能打！可以在配置文件里一键切换成 OpenAI DALL-E 3 哦～❤️"
    }
  ]
}"""

        return "Thought: 处理请求。\nFinal Answer: 执行完毕。"

    async def run(self, task_input: str, chat_history: List[Dict[str, str]] = None, step_callback: Callable[[Dict[str, Any]], None] = None) -> str:
        """
        运行 Agent，执行 ReAct 思考与工具调用循环。
        step_callback 用于实时将 Agent 每一个步骤（Thought, Tool Call, Output）回调推送。
        """
        system_prompt = self._get_system_prompt()
        messages = [
            {"role": "system", "content": system_prompt}
        ]
        if chat_history:
            messages.extend(chat_history)
        
        messages.append({"role": "user", "content": task_input})
        
        max_iterations = 6
        iteration = 0
        
        # 默认返回
        final_result = ""
        
        while iteration < max_iterations:
            iteration += 1
            
            # 1. 询问大模型
            llm_output = await self._call_llm(messages)
            
            # 2. 提取 Thought
            thought_match = re.search(r"Thought:\s*(.*?)(?=(Action:|Final Answer:|$))", llm_output, re.DOTALL)
            thought = thought_match.group(1).strip() if thought_match else "正在分析当前任务并制定策略..."
            
            # 将 Thought 推送给前端
            await self._trigger_callback(step_callback, {
                "agent": self.name,
                "type": "thought",
                "content": thought
            })
                
            # 3. 检查是否为 Tool Action
            action_match = re.search(r"Action:\s*(.*)", llm_output, re.DOTALL)
            if action_match:
                action_str = action_match.group(1).strip()
                # 寻找最外层的 '{' 和 '}' 进行贪婪提取，完美支持嵌套 JSON 以及 Markdown 代码块包裹
                start_idx = action_str.find('{')
                end_idx = action_str.rfind('}')
                if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
                    action_str = action_str[start_idx:end_idx+1]
                try:
                    action_json = json.loads(action_str)
                    tool_name = action_json.get("name")
                    tool_args = action_json.get("args", {})
                    
                    await self._trigger_callback(step_callback, {
                        "agent": self.name,
                        "type": "tool_call",
                        "content": f"调用工具 [{tool_name}]，参数: {json.dumps(tool_args, ensure_ascii=False)}"
                    })
                        
                    # 执行工具
                    if tool_name in self.tools_map:
                        tool = self.tools_map[tool_name]
                        observation = await tool.execute(**tool_args)
                    else:
                        observation = f"错误: 找不到名为 [{tool_name}] 的工具。"
                        
                    observation_str = str(observation)
                    
                    await self._trigger_callback(step_callback, {
                        "agent": self.name,
                        "type": "tool_response",
                        "content": observation_str
                    })
                        
                    # 将本次交互加入上下文，继续 ReAct 循环
                    messages.append({"role": "assistant", "content": llm_output})
                    messages.append({"role": "user", "content": f"Observation: {observation_str}"})
                    
                except Exception as e:
                    error_msg = f"解析或执行工具出错: {e}。请确保 Action 使用正确的 JSON 格式。"
                    await self._trigger_callback(step_callback, {
                        "agent": self.name,
                        "type": "tool_response",
                        "content": error_msg
                    })
                    messages.append({"role": "assistant", "content": llm_output})
                    messages.append({"role": "user", "content": f"Observation: {error_msg}"})
            
            # 4. 检查是否为 Final Answer
            elif "Final Answer:" in llm_output:
                final_answer_parts = llm_output.split("Final Answer:", 1)
                final_result = final_answer_parts[1].strip()
                await self._trigger_callback(step_callback, {
                    "agent": self.name,
                    "type": "final_answer",
                    "content": final_result
                })
                break
            else:
                # 如果都没有提取到，可能是大模型随意生成的，直接当作最终回复
                final_result = llm_output.strip()
                await self._trigger_callback(step_callback, {
                    "agent": self.name,
                    "type": "final_answer",
                    "content": final_result
                })
                break
                
        return final_result
