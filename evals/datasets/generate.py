"""从本地 MySQL 采样生成 v1 数据集候选（DESIGN 4.2）。

自动化的只有 S1（评论充足的单通道商品）与 S5（证据缺失）；S2/S3/S4 需要人工
甄别「评论与网页信息是否一致/冲突」，本脚本只打印候选与 verify SQL，供人工筛选。

用法（需 DB 就绪）：
    ./project_venv/bin/python -m evals.datasets.generate --min-reviews 400 --max-reviews 840 --limit 20
"""

from __future__ import annotations

import argparse
import asyncio
import json

from engines.ReviewEngine.utils.db import fetch_all


async def _review_counts(min_reviews: int, max_reviews: int, limit: int) -> list:
    sql = (
        "SELECT p.parent_asin, p.title, p.main_category, p.average_rating, p.rating_number, "
        "       COUNT(r.id) AS review_count "
        "FROM product p LEFT JOIN review r ON r.parent_asin = p.parent_asin "
        "GROUP BY p.parent_asin, p.title, p.main_category, p.average_rating, p.rating_number "
        "HAVING review_count BETWEEN %s AND %s "
        "ORDER BY review_count DESC LIMIT %s"
    )
    return await fetch_all(sql, (min_reviews, max_reviews, limit))


def _s1_item(row: dict) -> dict:
    asin = row["parent_asin"]
    return {
        "id": f"s1-{asin.lower()}",
        "stratum": "S1",
        "query": row["title"] or asin,
        "entity": {"type": "product", "asin": asin},
        "required_findings": [
            {
                "id": "f1",
                "statement": f"平均评分落在 {row['average_rating']} 附近",
                "channel_policy": "only-review",
                "verify": {
                    "kind": "db_query",
                    "sql": f"SELECT AVG(rating) FROM review WHERE parent_asin='{asin}'",
                    "tolerance": 0.3,
                },
            },
        ],
        "conflicts": [],
        "expect": "report",
    }


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--min-reviews", type=int, default=400)
    p.add_argument("--max-reviews", type=int, default=840)
    p.add_argument("--limit", type=int, default=20)
    args = p.parse_args(argv)

    rows = asyncio.run(_review_counts(args.min_reviews, args.max_reviews, args.limit))
    items = [_s1_item(r) for r in rows]
    print(json.dumps(items, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
