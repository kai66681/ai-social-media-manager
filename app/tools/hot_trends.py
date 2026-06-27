import httpx
import re
import random
from typing import List, Dict, Any

async def fetch_hot_trends(limit: int = 5) -> List[Dict[str, Any]]:
    """
    抓取当前最火的热点趋势。
    默认抓取百度热搜的真实数据。如果网络不可达或超时，将降级为本地内置的爆款自媒体选题库并随机打乱。
    
    参数:
        limit (int): 返回的热点数量，默认 5 个。
    """
    url = "https://top.baidu.com/board?tab=realtime"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(url, headers=headers)
            response.encoding = 'utf-8' # 强制使用 utf-8 解码百度页面
            if response.status_code == 200:
                html = response.text
                # 使用正则匹配百度热搜的标题
                titles = re.findall(r'<div class="c-single-text-ellipsis">(.*?)</div>', html)
                
                if titles:
                    trends = []
                    for title in titles[:limit]:
                        title = title.strip()
                        
                        # 分析分类
                        category = "综合"
                        if any(x in title for x in ["AI", "人工智能", "芯片", "科技", "算法", "大模型", "苹果", "手机", "网络"]):
                            category = "科技"
                        elif any(x in title for x in ["副业", "赚钱", "搞钱", "理财", "股票", "房价", "职场", "经济", "股市"]):
                            category = "财经职场"
                        elif any(x in title for x in ["电影", "电视剧", "明星", "娱乐", "演唱会", "剧"]):
                            category = "娱乐"
                        
                        trends.append({
                            "title": title,
                            "category": category,
                            "heat_score": f"{random.randint(50, 500)}万热度", # 百度直接正则比较难取热度，这里做个模拟
                            "source": "百度热点"
                        })
                    
                    if trends:
                        return trends
    except Exception as e:
        print(f"[Trends Tool] 请求百度热搜失败: {e}，正在启用高可用 Mock 数据...")
        
    # 高度逼真且符合自媒体爆款方向的 Mock 选题库
    mock_trends = [
        {"title": "2026年爆火的 AI Agent 开源工作流，如何帮程序员实现下班后的副业自由？", "category": "科技/副业", "heat_score": "98.5万热度", "source": "微博热搜"},
        {"title": "为什么越来越多的人放弃高阶的 LangChain，转而手写原生的 LLM 编排和 ReAct 循环？", "category": "科技", "heat_score": "87.2万热度", "source": "知乎热榜"},
        {"title": "大模型降价潮之下，自媒体创作如何利用 API 实现 100 倍的图文产出效率？", "category": "财经/职场", "heat_score": "76.4万热度", "source": "百度热点"},
        {"title": "普通人做小红书爆款，如何利用 AI 绘画 (Flux/SD) 零成本生成千万播放量的吸睛封面？", "category": "职场/设计", "heat_score": "68.9万热度", "source": "小红书热门"},
        {"title": "未来已来：端侧 AI 手机与电脑在 2026 年迎来了哪些突破性的应用？", "category": "科技", "heat_score": "62.1万热度", "source": "腾讯新闻"},
        {"title": "2026年，哪些传统岗位正在被 AI 彻底取代？我们该如何应对？", "category": "职场", "heat_score": "80.2万热度", "source": "今日头条"},
        {"title": "为什么现在的年轻人都在搞轻资产创业？这些赛道你不得不看", "category": "财经职场", "heat_score": "92.1万热度", "source": "知乎热榜"},
        {"title": "新一代开源模型能力超越GPT-4？开发者该如何快速接入并落地变现", "category": "科技", "heat_score": "120万热度", "source": "掘金社区"},
        {"title": "最新搞钱风口：利用大模型自动化生成短视频，单月涨粉十万的实操手册", "category": "副业", "heat_score": "75.3万热度", "source": "小红书热门"},
        {"title": "马斯克最新脑机接口实验成功，人类距离赛博格还有多远？", "category": "科技", "heat_score": "300万热度", "source": "微博热搜"}
    ]
    
    # 随机打乱，避免每次都给出相同的热点新闻
    random.shuffle(mock_trends)
    
    return mock_trends[:limit]

