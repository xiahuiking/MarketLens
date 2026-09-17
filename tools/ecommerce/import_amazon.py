"""
Amazon Reviews 2023 数据导入脚本（MarketLens）。

将 McAuley lab 的 Amazon Reviews 2023 JSONL(.gz) 导入本地 MySQL。

下载地址（Electronics 品类）：
- 评论: https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/review_categories/Electronics.jsonl.gz
- 元数据: https://mcauleylab.ucsd.edu/public_datasets/data/amazon_2023/raw/meta_categories/meta_Electronics.jsonl.gz
- HuggingFace 镜像: https://huggingface.co/datasets/McAuley-Lab/Amazon-Reviews-2023

数据格式（每行一个 JSON 对象，支持 .jsonl 与 .jsonl.gz）：
- meta 文件：main_category, title, average_rating, rating_number, price, store, parent_asin, ...
- review 文件：rating, title, text, asin, parent_asin, user_id, timestamp(ms), helpful_vote, verified_purchase, ...

用法示例：
    python tools/ecommerce/import_amazon.py \
        --meta data/amazon/meta_Electronics.jsonl.gz \
        --reviews data/amazon/Electronics.jsonl.gz \
        --limit 200000   # 可选：每个文件最多导入行数（demo 建议限流，避免全量 43.9M 评论）

说明：
- product 表以 parent_asin 为商品标识（ON DUPLICATE KEY UPDATE 幂等可重跑）
- review 表为 append-only
"""

from __future__ import annotations

import argparse
import gzip
import json
import sys
from pathlib import Path
from typing import Optional

import pymysql

# 保证直接执行本脚本时能找到 app 包
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.config import settings  # noqa: E402

PLATFORM = "amazon"


# ── 归一化辅助 ───────────────────────────────────────────────────────────────

def _safe_str(v) -> Optional[str]:
    if v is None:
        return None
    s = str(v).strip()
    return s if s else None


def _norm_price(v) -> Optional[str]:
    """price 字段可能是 'None' 字符串 / None / '19.99' / '$19.99'。"""
    if v is None:
        return None
    s = str(v).strip()
    if not s or s.lower() == "none":
        return None
    return s


def _norm_float(v) -> Optional[float]:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _norm_int(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


# ── 数据库连接 ───────────────────────────────────────────────────────────────

def _connect() -> pymysql.Connection:
    return pymysql.connect(
        host=settings.DB_HOST,
        port=int(settings.DB_PORT or 3306),
        user=settings.DB_USER,
        password=settings.DB_PASSWORD,
        database=settings.DB_NAME,
        charset="utf8mb4",
        autocommit=False,
    )


# ── 导入逻辑 ─────────────────────────────────────────────────────────────────

PRODUCT_INSERT_SQL = """
    INSERT INTO product
        (platform, parent_asin, title, brand, store, price, main_category, average_rating, rating_number)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON DUPLICATE KEY UPDATE
        title=VALUES(title), brand=VALUES(brand), store=VALUES(store),
        price=VALUES(price), main_category=VALUES(main_category),
        average_rating=VALUES(average_rating), rating_number=VALUES(rating_number)
"""

REVIEW_INSERT_SQL = """
    INSERT INTO review
        (platform, asin, parent_asin, user_id, rating, title, content, verified_purchase, helpful_vote, review_time)
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""


def _read_jsonl(path: Path, limit: Optional[int] = None):
    """逐行读取 JSONL，自动识别 .gz 压缩。"""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt", encoding="utf-8", errors="ignore") as f:
        for i, line in enumerate(f):
            if limit is not None and i >= limit:
                break
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def import_meta(conn: pymysql.Connection, meta_path: Path, limit: Optional[int] = None) -> int:
    """导入商品元数据，返回写入条数。"""
    rows = []
    count = 0
    with conn.cursor() as cur:
        for obj in _read_jsonl(meta_path, limit):
            parent_asin = _safe_str(obj.get("parent_asin"))
            if not parent_asin:
                continue
            store = _safe_str(obj.get("store"))
            rows.append((
                PLATFORM,
                parent_asin,
                _safe_str(obj.get("title")),
                store,  # Amazon meta 无独立 brand 字段，用 store 作为品牌近似
                store,
                _norm_price(obj.get("price")),
                _safe_str(obj.get("main_category")),
                _norm_float(obj.get("average_rating")),
                _norm_int(obj.get("rating_number")) or 0,
            ))
            if len(rows) >= 500:
                cur.executemany(PRODUCT_INSERT_SQL, rows)
                count += len(rows)
                rows = []
        if rows:
            cur.executemany(PRODUCT_INSERT_SQL, rows)
            count += len(rows)
    conn.commit()
    return count


def import_reviews(conn: pymysql.Connection, reviews_path: Path, limit: Optional[int] = None) -> int:
    """导入评论，返回写入条数。"""
    rows = []
    count = 0
    with conn.cursor() as cur:
        for obj in _read_jsonl(reviews_path, limit):
            parent_asin = _safe_str(obj.get("parent_asin"))
            if not parent_asin:
                continue
            rows.append((
                PLATFORM,
                _safe_str(obj.get("asin")),
                parent_asin,
                _safe_str(obj.get("user_id")),
                _norm_float(obj.get("rating")),
                _safe_str(obj.get("title")),
                _safe_str(obj.get("text")),
                1 if obj.get("verified_purchase") else 0,
                _norm_int(obj.get("helpful_vote", obj.get("helpful_votes"))) or 0,
                _norm_int(obj.get("timestamp", obj.get("sort_timestamp"))),
            ))
            if len(rows) >= 500:
                cur.executemany(REVIEW_INSERT_SQL, rows)
                count += len(rows)
                rows = []
        if rows:
            cur.executemany(REVIEW_INSERT_SQL, rows)
            count += len(rows)
    conn.commit()
    return count


# ── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="导入 Amazon Reviews 2023 数据")
    parser.add_argument("--meta", type=str, help="meta_*.jsonl 路径")
    parser.add_argument("--reviews", type=str, help="*_5.jsonl 路径")
    parser.add_argument("--limit", type=int, default=None, help="每个文件最多导入行数（调试用）")
    args = parser.parse_args()

    if not args.meta and not args.reviews:
        parser.error("至少指定 --meta 或 --reviews 之一")

    conn = _connect()
    try:
        if args.meta:
            path = Path(args.meta)
            print(f"导入商品元数据: {path}")
            n = import_meta(conn, path, args.limit)
            print(f"  完成，写入 {n} 条商品")
        if args.reviews:
            path = Path(args.reviews)
            print(f"导入评论: {path}")
            n = import_reviews(conn, path, args.limit)
            print(f"  完成，写入 {n} 条评论")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
