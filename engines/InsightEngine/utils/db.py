"""
通用数据库工具（异步）

此模块提供基于 SQLAlchemy 2.x 异步引擎的数据库访问封装，支持 MySQL 与 PostgreSQL。
数据模型定义位置：
- 无（本模块仅提供连接与查询工具，不定义数据模型）
"""

from __future__ import annotations
from urllib.parse import quote_plus
import asyncio
import os
from typing import Any, Dict, Iterable, List, Optional, Union

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy import text

__all__ = [
    "get_async_engine",
    "fetch_all",
    "reset_engine",
]


_engine: Optional[AsyncEngine] = None
_engine_url: Optional[str] = None


def _get_settings():
    """
    运行时动态读取配置。

    不能使用 `from app.config import settings` 按值导入：config.reload_settings()
    会重新赋值 config.settings，按值导入会永远指向启动时的旧对象。
    """
    from app import config
    return config.settings


def reset_engine() -> None:
    """重置缓存的异步引擎，使数据库配置变更后立即生效。"""
    global _engine, _engine_url
    _engine = None
    _engine_url = None


def _build_database_url() -> str:
    settings = _get_settings()
    dialect: str = (settings.DB_DIALECT or "mysql").lower()
    host: str = settings.DB_HOST or ""
    port: str = str(settings.DB_PORT or "")
    user: str = settings.DB_USER or ""
    password: str = settings.DB_PASSWORD or ""
    db_name: str = settings.DB_NAME or ""

    if os.getenv("DATABASE_URL"):
        return os.getenv("DATABASE_URL")  # 直接使用外部提供的完整URL

    password = quote_plus(password)

    if dialect in ("postgresql", "postgres"):
        # PostgreSQL 使用 asyncpg 驱动
        return f"postgresql+asyncpg://{user}:{password}@{host}:{port}/{db_name}"

    # 默认 MySQL 使用 aiomysql 驱动
    return f"mysql+aiomysql://{user}:{password}@{host}:{port}/{db_name}"


def get_async_engine() -> AsyncEngine:
    global _engine, _engine_url
    database_url: str = _build_database_url()

    # 配置变更后重建引擎（旧引擎连接池交给 GC；pool_pre_ping 会规避失效连接）
    if _engine is not None and database_url != _engine_url:
        _engine = None
        _engine_url = None

    if _engine is None:
        _engine = create_async_engine(
            database_url,
            pool_pre_ping=True,
            pool_recycle=1800,
        )
        _engine_url = database_url
    return _engine


async def fetch_all(query: str, params: Optional[Union[Iterable[Any], Dict[str, Any]]] = None) -> List[Dict[str, Any]]:
    """
    执行只读查询并返回字典列表。

    兼容两种参数风格：
    - dict 参数 → 直接传给 SQLAlchemy text()（支持 :name 和 %(name)s 占位符）
    - tuple/list 参数 → 将 %s 占位符依次替换为 :p0, :p1... 再绑定
    """
    engine: AsyncEngine = get_async_engine()
    async with engine.connect() as conn:
        if params is not None and not isinstance(params, dict):
            # tuple → 将 %s 替换为 :pN 命名参数
            param_list = list(params)
            for i in range(len(param_list)):
                query = query.replace("%s", f":p{i}", 1)
            params = {f"p{i}": v for i, v in enumerate(param_list)}

        result = await conn.execute(text(query), params or {})
        rows = result.mappings().all()
        return [dict(row) for row in rows]


