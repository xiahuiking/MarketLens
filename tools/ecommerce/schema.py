"""
MarketLens 电商数据表 schema。

表结构：
- product：商品元数据（按 parent_asin 唯一，parent_asin 是 Amazon 的"商品"标识，
            asin 则是具体变体，如颜色/容量）
- review：商品评论（append-only，每条评论带 asin 与 parent_asin）

platform 字段预留多平台扩展（后续可加入中文电商数据源，如 Kaggle 中文评论）。
"""

from __future__ import annotations

from sqlalchemy import text

# MySQL 优先（项目默认 DB_DIALECT=mysql）；PostgreSQL 需替换 AUTO_INCREMENT / ENGINE / TINYINT 语法。
PRODUCT_TABLE = "product"
REVIEW_TABLE = "review"

PRODUCT_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS product (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(32) NOT NULL DEFAULT 'amazon',
    parent_asin VARCHAR(32) NOT NULL,
    title TEXT,
    brand VARCHAR(255) DEFAULT NULL,
    store VARCHAR(255) DEFAULT NULL,
    price VARCHAR(64) DEFAULT NULL,
    main_category VARCHAR(255) DEFAULT NULL,
    average_rating FLOAT DEFAULT NULL,
    rating_number INT DEFAULT 0,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uk_platform_parent_asin (platform, parent_asin),
    KEY idx_main_category (main_category)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

REVIEW_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS review (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    platform VARCHAR(32) NOT NULL DEFAULT 'amazon',
    asin VARCHAR(32) DEFAULT NULL,
    parent_asin VARCHAR(32) NOT NULL,
    user_id VARCHAR(64) DEFAULT NULL,
    rating FLOAT DEFAULT NULL,
    title TEXT,
    content LONGTEXT,
    verified_purchase TINYINT(1) DEFAULT 0,
    helpful_vote INT DEFAULT 0,
    review_time BIGINT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_platform_parent_asin (platform, parent_asin),
    KEY idx_review_time (review_time)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""


async def init_ecommerce_tables() -> None:
    """创建电商表（幂等，可重复执行）。"""
    from engines.InsightEngine.utils.db import get_async_engine

    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(PRODUCT_TABLE_DDL))
        await conn.execute(text(REVIEW_TABLE_DDL))
