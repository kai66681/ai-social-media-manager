import os
import json
import sys
import asyncio

# 解决 Windows 平台下 Playwright 依赖 asyncio 创建子进程的兼容性问题
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, Depends, Query, Request
from fastapi.responses import StreamingResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import init_db, get_db, Post, Trend, Comment
from app.agent_engine.team import SocialMediaTeam
from app.config import HOST, PORT

# 初始化 FastAPI 应用
app = FastAPI(title="AI Social Media Manager", description="全自动自媒体内容创作与运营智能体 Dashboard")

# 确保必要的文件夹存在
os.makedirs(os.path.join("app", "static", "generated"), exist_ok=True)
os.makedirs(os.path.join("app", "templates"), exist_ok=True)

# 挂载静态文件目录 (用于图片和 CSS)
app.mount("/static", StaticFiles(directory=os.path.join("app", "static")), name="static")

# 设置模板引擎
templates = Jinja2Templates(directory=os.path.join("app", "templates"))

@app.on_event("startup")
def startup_event():
    """服务启动时初始化数据库"""
    init_db()
    print("[OK] SQLite 数据库初始化完毕。")
    print(f"[OK] 服务已启动，请访问 http://{HOST}:{PORT}")

@app.get("/", response_class=HTMLResponse)
async def read_root(request: Request):
    """渲染前端 Dashboard 页面"""
    return templates.TemplateResponse(request=request, name="index.html")

@app.get("/api/posts")
async def get_posts(db: Session = Depends(get_db)):
    """获取所有已生成的自媒体文章历史（关联热点与评论）"""
    posts = db.query(Post).order_by(Post.created_at.desc()).all()
    
    results = []
    for post in posts:
        # 获取关联的评论
        comments = db.query(Comment).filter(Comment.post_id == post.id).all()
        comments_list = [
            {
                "id": c.id,
                "username": c.username,
                "comment_text": c.comment_text,
                "reply_text": c.reply_text,
                "created_at": c.created_at.isoformat()
            }
            for c in comments
        ]
        
        # 获取关联的热点选题
        trend = db.query(Trend).filter(Trend.id == post.trend_id).first()
        trend_info = {
            "title": trend.title if trend else post.trend_title,
            "category": trend.category if trend else "科技",
            "heat_score": trend.heat_score if trend else "0",
            "source": trend.source if trend else "系统模拟"
        }
        
        results.append({
            "id": post.id,
            "title": post.title,
            "platform": post.platform,
            "content": post.content,
            "image_prompt": post.image_prompt,
            "image_url": post.image_url,
            "agent_logs": json.loads(post.agent_logs) if post.agent_logs else [],
            "status": post.status,
            "created_at": post.created_at.isoformat(),
            "trend": trend_info,
            "comments": comments_list
        })
        
    return results

@app.get("/api/run-agent")
async def run_agent(platform: str = "小红书", db: Session = Depends(get_db)):
    """
    流式触发并执行 Agent 工作流。
    利用 Server-Sent Events (SSE) 实时推送 Agent 思考与工具调用日志。
    """
    async def event_generator():
        # 创建用于异步事件传输的队列
        queue = asyncio.Queue()
        
        # 回调函数：将 Agent 执行步骤安全地放入队列
        def callback(step_data):
            queue.put_nowait(step_data)
            
        # 在后台异步启动多智能体协作工作流，避免阻塞 SSE HTTP 连接
        async def run_workflow():
            try:
                team = SocialMediaTeam()
                await team.run_flow(platform=platform, db=db, step_callback=callback)
                # 投递流程完成信号
                await queue.put({"agent": "System", "type": "done", "content": "Done"})
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                # 投递异常信息，以便前端展示
                await queue.put({
                    "agent": "System",
                    "type": "error",
                    "content": f"工作流运行出错: {str(e)}",
                    "trace": error_trace
                })
        
        # 启动后台任务
        workflow_task = asyncio.create_task(run_workflow())
        
        # 从队列消费事件并将其流式 yield 给前端
        while True:
            # 等待新事件
            event_data = await queue.get()
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
            
            # 若是流程正常结束或发生错误，终止连接
            if event_data.get("type") in ["done", "error"]:
                break
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/xhs/status")
async def get_xhs_status():
    """获取小红书账号绑定状态"""
    from app.tools.xhs_publisher import COOKIE_PATH
    return {"bound": os.path.exists(COOKIE_PATH)}

@app.get("/api/xhs/login")
async def xhs_login_api():
    """弹出真实浏览器窗口供用户手动登录 (非阻塞后台执行)"""
    from app.tools.xhs_publisher import COOKIE_PATH, xhs_login, _save_login_status, _read_login_status
    import threading

    current_status = _read_login_status()
    if current_status.get("status") in ["starting", "navigating", "waiting", "saving"]:
        return {
            "status": "info",
            "message": "已有登录流程正在进行中，请稍候..."
        }

    if os.path.exists(COOKIE_PATH):
        try:
            os.remove(COOKIE_PATH)
        except Exception:
            pass

    def run_login_in_thread():
        try:
            _save_login_status("starting", "正在启动浏览器窗口...")
            xhs_login()
        except Exception as e:
            print(f"[XHS Login Thread] 启动失败: {e}")
            _save_login_status("error", str(e))

    t = threading.Thread(target=run_login_in_thread, daemon=False)
    t.start()

    return {
        "status": "success",
        "message": "浏览器窗口已弹出，请在弹出的窗口中登录你的小红书账号。"
    }

@app.get("/api/xhs/login-status")
async def xhs_login_status_api():
    """轮询登录的实时状态"""
    from app.tools.xhs_publisher import _read_login_status
    return _read_login_status()

@app.get("/api/xhs/verify-cookie")
async def verify_cookie_api():
    """验证已保存的 Cookie 是否有效"""
    from app.tools.xhs_publisher import verify_saved_cookies
    return await asyncio.to_thread(verify_saved_cookies)


@app.get("/api/xhs/qr-start")
async def xhs_qr_start_api():
    """启动无头浏览器获取小红书登录二维码"""
    from app.tools.xhs_publisher import xhs_qr_login_start
    return await asyncio.to_thread(xhs_qr_login_start)


@app.get("/api/xhs/qr-poll")
async def xhs_qr_poll_api():
    """轮询扫码登录状态"""
    from app.tools.xhs_publisher import xhs_qr_login_poll
    return await asyncio.to_thread(xhs_qr_login_poll)


@app.get("/api/xhs/qr-cancel")
async def xhs_qr_cancel_api():
    """取消扫码登录"""
    from app.tools.xhs_publisher import xhs_qr_login_cancel
    return await asyncio.to_thread(xhs_qr_login_cancel)


@app.get("/api/xhs/qr-refresh")
async def xhs_qr_refresh_api():
    """刷新二维码"""
    from app.tools.xhs_publisher import xhs_qr_refresh
    return await asyncio.to_thread(xhs_qr_refresh)


@app.get("/api/run-agent-copy")
async def run_agent_copy(xhs_url: str = Query(...), platform: str = "小红书", db: Session = Depends(get_db)):
    """
    流式触发小红书链接的仿写流程。
    """
    async def event_generator():
        queue = asyncio.Queue()
        
        def callback(step_data):
            queue.put_nowait(step_data)
            
        async def run_workflow():
            try:
                team = SocialMediaTeam()
                await team.run_copy_flow(xhs_url=xhs_url, platform=platform, db=db, step_callback=callback)
                await queue.put({"agent": "System", "type": "done", "content": "Done"})
            except Exception as e:
                import traceback
                error_trace = traceback.format_exc()
                await queue.put({
                    "agent": "System",
                    "type": "error",
                    "content": f"仿写工作流运行出错: {str(e)}",
                    "trace": error_trace
                })
        
        asyncio.create_task(run_workflow())
        
        while True:
            event_data = await queue.get()
            yield f"data: {json.dumps(event_data, ensure_ascii=False)}\n\n"
            if event_data.get("type") in ["done", "error"]:
                break
                
    return StreamingResponse(event_generator(), media_type="text/event-stream")

from fastapi import Body

@app.post("/api/xhs/cookie_bind")
async def xhs_cookie_bind(cookie: str = Body(embed=True)):
    """手动粘贴 Cookie 绑定小红书账号"""
    from app.tools.xhs_publisher import save_xhs_cookies_from_input
    cookie_input = cookie.strip()
    if not cookie_input:
        return {"status": "error", "message": "Cookie 内容不能为空！"}

    return save_xhs_cookies_from_input(cookie_input)
