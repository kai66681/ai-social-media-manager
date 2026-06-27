from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, ForeignKey
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from app.config import DATABASE_URL

# 创建数据库引擎 (针对 SQLite 允许在多线程中访问)
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 基础模型类
Base = declarative_base()

class Trend(Base):
    """热点趋势数据表"""
    __tablename__ = "trends"

    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    category = Column(String(50), default="综合")
    heat_score = Column(String(50), default="0")
    source = Column(String(50), default="系统模拟")
    created_at = Column(DateTime, default=datetime.utcnow)

    # 关联的文章
    posts = relationship("Post", back_populates="trend", cascade="all, delete-orphan")

class Post(Base):
    """生成的文章/推文数据表"""
    __tablename__ = "posts"

    id = Column(Integer, primary_key=True, index=True)
    trend_id = Column(Integer, ForeignKey("trends.id", ondelete="CASCADE"), nullable=True)
    trend_title = Column(String(255), nullable=True)
    platform = Column(String(50), nullable=False) # 小红书, 微博, Twitter, 微信公众号等
    title = Column(String(255), nullable=True)
    content = Column(Text, nullable=False)
    image_prompt = Column(Text, nullable=True)
    image_url = Column(Text, nullable=True)
    agent_logs = Column(Text, nullable=True) # 保存 Agent 思考轨迹 JSON/Text
    status = Column(String(50), default="draft") # draft, published
    created_at = Column(DateTime, default=datetime.utcnow)

    trend = relationship("Trend", back_populates="posts")
    comments = relationship("Comment", back_populates="post", cascade="all, delete-orphan")

class Comment(Base):
    """模拟互动的评论表"""
    __tablename__ = "comments"

    id = Column(Integer, primary_key=True, index=True)
    post_id = Column(Integer, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(100), nullable=False)
    comment_text = Column(Text, nullable=False)
    reply_text = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    post = relationship("Post", back_populates="comments")

def init_db():
    """初始化数据库，创建所有数据表"""
    Base.metadata.create_all(bind=engine)

def get_db():
    """FastAPI 依赖注入，用于获取数据库会话"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
