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
    brand TEXT DEFAULT NULL,
    store TEXT DEFAULT NULL,
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
    user_id VARCHAR(128) DEFAULT NULL,
    rating FLOAT DEFAULT NULL,
    title TEXT,
    content LONGTEXT,
    verified_purchase TINYINT(1) DEFAULT 0,
    helpful_vote INT DEFAULT 0,
    review_time BIGINT DEFAULT NULL,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_platform_parent_asin (platform, parent_asin),
    KEY idx_review_time (review_time),
    KEY idx_parent_review_time (parent_asin, review_time),
    KEY idx_parent_rating_helpful (parent_asin, rating, helpful_vote)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

# 历史遗留列宽：早期 DDL 把 brand/store 设成 VARCHAR(255)，而 Amazon 的 store
# 字段有超长值（Electronics 导入直接报 1406 Data too long）。这里在启动时按需
# 加宽，避免已建库的部署必须手工 ALTER。
_LEGACY_WIDENINGS: tuple[tuple[str, str, str], ...] = (
    ("product", "brand", "TEXT"),
    ("product", "store", "TEXT"),
    ("review", "user_id", "VARCHAR(128)"),
)


async def _widen_legacy_columns(conn) -> None:
    """把历史遗留的窄列加宽到当前 DDL 的定义（幂等，已是宽列则跳过）。"""
    for table, column, target_ddl in _LEGACY_WIDENINGS:
        result = await conn.execute(
            text(
                "SELECT DATA_TYPE, CHARACTER_MAXIMUM_LENGTH FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {"t": table, "c": column},
        )
        row = result.first()
        if not row:
            continue
        data_type, max_len = (row[0] or "").lower(), row[1]
        if data_type in {"text", "mediumtext", "longtext"}:
            continue
        if target_ddl.startswith("TEXT") or (max_len or 0) < 128:
            await conn.execute(text(f"ALTER TABLE {table} MODIFY {column} {target_ddl}"))
        elif target_ddl.startswith("VARCHAR") and (max_len or 0) < int(target_ddl.split("(")[1].rstrip(")")):
            await conn.execute(text(f"ALTER TABLE {table} MODIFY {column} {target_ddl}"))


# 评论查询所需的索引（幂等补齐，见 _ensure_review_indexes）。
#
# 为什么需要：所有评论查询都按 ``parent_asin`` 过滤，而原有的
# ``idx_platform_parent_asin (platform, parent_asin)`` 最左列是 platform ——
# 该列只有 'amazon' 一个取值（cardinality = 1），全库没有任何查询按 platform
# 过滤，因此这个索引对 ``WHERE parent_asin IN (...)`` 完全不可用，390 万行的
# review 表只能全表扫描（实测 `ORDER BY review_time DESC LIMIT 300` 单条 32.2s，
# 建索引后 0.03s；EXPLAIN 由 `Table scan, cost=498486` 变为 index range scan）。
#
# 只建两个索引即可覆盖全部六种查询形状（``(parent_asin)`` 与
# ``(parent_asin, rating)`` 分别是它们的前缀，另建属冗余）：
#
#   idx_parent_review_time (parent_asin, review_time)
#     - WHERE parent_asin IN (...) ORDER BY review_time DESC LIMIT n
#     - WHERE parent_asin IN (...) AND review_time >= ? AND review_time < ?
#     - SELECT MIN/MAX(review_time) WHERE parent_asin IN (...)
#     - WHERE parent_asin = ? （前缀）
#   idx_parent_rating_helpful (parent_asin, rating, helpful_vote)
#     - WHERE parent_asin IN (...) AND rating <= 2
#       ORDER BY helpful_vote DESC, review_time DESC LIMIT n
#     - WHERE parent_asin IN (...) GROUP BY rating （前缀）
_REVIEW_INDEXES: tuple[tuple[str, str], ...] = (
    ("idx_parent_review_time", "(parent_asin, review_time)"),
    ("idx_parent_rating_helpful", "(parent_asin, rating, helpful_vote)"),
)


async def _ensure_review_indexes(conn) -> None:
    """按需补齐 _REVIEW_INDEXES（幂等：已存在则跳过）。

    MySQL 不支持 ``CREATE INDEX IF NOT EXISTS``，因此先查
    information_schema.STATISTICS 再决定是否 ALTER。
    MySQL 8+ 加二级索引默认走 ONLINE DDL（INPLACE / LOCK=NONE），
    大表上不会阻塞导入与查询；首次迁移耗时与表规模成正比。
    """
    result = await conn.execute(
        text(
            "SELECT DISTINCT INDEX_NAME FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t"
        ),
        {"t": REVIEW_TABLE},
    )
    existing = {row[0] for row in result}
    for name, columns in _REVIEW_INDEXES:
        if name in existing:
            continue
        await conn.execute(text(f"ALTER TABLE {REVIEW_TABLE} ADD INDEX {name} {columns}"))


async def init_ecommerce_tables() -> None:
    """创建电商表（幂等，可重复执行）。"""
    from engines.ReviewEngine.utils.db import get_async_engine

    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.execute(text(PRODUCT_TABLE_DDL))
        await conn.execute(text(REVIEW_TABLE_DDL))
        await _widen_legacy_columns(conn)
        await _ensure_review_indexes(conn)


if __name__ == "__main__":
    # 供 docker-entrypoint.sh 调用（在仓库根目录执行）：
    #     python3 -m tools.ecommerce.schema
    import asyncio

    from engines.ReviewEngine.utils.db import get_async_engine

    async def _main() -> None:
        await init_ecommerce_tables()
        # 在事件循环关闭前显式释放连接池。否则 aiomysql 连接的 __del__
        # 会在循环关闭之后才调用 close()，打印 "Event loop is closed" 噪音
        # （退出码仍为 0，但日志会误导排查）。
        await get_async_engine().dispose()

    asyncio.run(_main())
    print("==> 电商表已就绪：product / review")

