"""
专为 AI Agent 设计的电商评论数据库查询工具集 (ProductReviewDB)

MarketLens 电商数据层：封装本地 MySQL 中的 Amazon 商品/评论数据的查询，
供 ReviewEngine（口碑 Agent）调用。提供以下工具：

- search_products: 按标题/品牌/ASIN 检索商品
- get_product_reviews: 获取某商品的评论
- get_rating_distribution: 评分分布（1-5 星）
- compare_products: 多个商品的横向对比
- get_review_trend: 评论时间趋势
- get_top_complaints: 差评归因（1-2 星评论）
"""

import asyncio
from datetime import datetime, timedelta, date
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field

from loguru import logger

from ..utils.db import fetch_all


# --- 数据结构定义 ---

@dataclass
class QueryResult:
    """统一的数据库查询结果数据类"""
    platform: str
    content_type: str
    title_or_content: str
    author_nickname: Optional[str] = None
    url: Optional[str] = None
    publish_time: Optional[datetime] = None
    engagement: Dict[str, int] = field(default_factory=dict)
    source_keyword: Optional[str] = None
    hotness_score: float = 0.0
    source_table: str = ""


@dataclass
class DBResponse:
    """封装工具的完整返回结果"""
    tool_name: str
    parameters: Dict[str, Any]
    results: List[QueryResult] = field(default_factory=list)
    results_count: int = 0
    error_message: Optional[str] = None


# --- 核心客户端 ---

class ProductReviewDB:
    """电商评论数据库查询工具集"""

    PLATFORM = "amazon"

    def __init__(self):
        pass

    def _execute_query(self, query: str, params: tuple = None) -> List[Dict[str, Any]]:
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_closed():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            return loop.run_until_complete(fetch_all(query, params))
        except Exception as e:
            logger.exception(f"数据库查询时发生错误: {e}")
            return []

    @staticmethod
    def _to_datetime(ts: Any) -> Optional[datetime]:
        if not ts:
            return None
        try:
            if isinstance(ts, datetime):
                return ts
            if isinstance(ts, date):
                return datetime.combine(ts, datetime.min.time())
            if isinstance(ts, (int, float)) or str(ts).isdigit():
                val = float(ts)
                return datetime.fromtimestamp(val / 1000 if val > 1_000_000_000_000 else val)
            if isinstance(ts, str):
                return datetime.fromisoformat(ts.split('+')[0].strip())
        except (ValueError, TypeError):
            return None
        return None

    # -- 商品检索 ---------------------------------------------------------

    def search_products(self, query: str, limit: int = 50) -> DBResponse:
        """【工具】按标题/品牌/ASIN 检索商品。"""
        params_for_log = {'query': query, 'limit': limit}
        logger.info(f"--- TOOL: 检索商品 (params: {params_for_log}) ---")

        sql = (
            "SELECT parent_asin, title, brand, store, price, main_category, average_rating, rating_number "
            "FROM product "
            "WHERE title LIKE %s OR parent_asin = %s OR brand LIKE %s OR store LIKE %s "
            "ORDER BY rating_number DESC LIMIT %s"
        )
        like = f"%{query}%"
        rows = self._execute_query(sql, (like, query, like, like, limit))

        results = [
            QueryResult(
                platform=self.PLATFORM,
                content_type="product",
                title_or_content=r.get("title") or "",
                author_nickname=r.get("brand") or r.get("store"),
                engagement={
                    "rating_number": int(r.get("rating_number") or 0),
                    "average_rating": float(r.get("average_rating") or 0),
                },
                hotness_score=float(r.get("average_rating") or 0),
                source_table=r.get("parent_asin") or "",
            )
            for r in rows
        ]
        return DBResponse("search_products", params_for_log, results=results, results_count=len(results))

    def _resolve_parent_asins(self, product_query: str, max_products: int = 5) -> List[str]:
        """根据商品查询串定位 parent_asin 列表。"""
        like = f"%{product_query}%"
        sql = (
            "SELECT parent_asin FROM product "
            "WHERE title LIKE %s OR parent_asin = %s OR brand LIKE %s OR store LIKE %s "
            "ORDER BY rating_number DESC LIMIT %s"
        )
        rows = self._execute_query(sql, (like, product_query, like, like, max_products))
        return [r["parent_asin"] for r in rows]

    # -- 评论查询 ---------------------------------------------------------

    def get_product_reviews(self, product_query: str, limit: int = 100) -> DBResponse:
        """【工具】获取某商品的评论列表。"""
        params_for_log = {'product_query': product_query, 'limit': limit}
        logger.info(f"--- TOOL: 获取商品评论 (params: {params_for_log}) ---")

        asins = self._resolve_parent_asins(product_query)
        if not asins:
            return DBResponse("get_product_reviews", params_for_log, error_message=f"未找到商品: {product_query}")

        placeholders = ",".join(["%s"] * len(asins))
        sql = (
            f"SELECT asin, parent_asin, user_id, rating, title, content, verified_purchase, helpful_vote, review_time "
            f"FROM review WHERE parent_asin IN ({placeholders}) "
            f"ORDER BY review_time DESC LIMIT %s"
        )
        rows = self._execute_query(sql, (*asins, limit))

        results = [
            QueryResult(
                platform=self.PLATFORM,
                content_type="review",
                title_or_content=(r.get("title") or "") + (" " + r["content"] if r.get("content") else ""),
                author_nickname=r.get("user_id"),
                publish_time=self._to_datetime(r.get("review_time")),
                engagement={
                    "rating": int(float(r.get("rating") or 0)),
                    "helpful_vote": int(r.get("helpful_vote") or 0),
                },
                source_table=r.get("parent_asin") or "",
            )
            for r in rows
        ]
        return DBResponse("get_product_reviews", params_for_log, results=results, results_count=len(results))

    def get_rating_distribution(self, product_query: str) -> DBResponse:
        """【工具】获取商品评分分布（1-5 星计数与平均分）。"""
        params_for_log = {'product_query': product_query}
        logger.info(f"--- TOOL: 评分分布 (params: {params_for_log}) ---")

        asins = self._resolve_parent_asins(product_query)
        if not asins:
            return DBResponse("get_rating_distribution", params_for_log, error_message=f"未找到商品: {product_query}")

        placeholders = ",".join(["%s"] * len(asins))
        sql = (
            f"SELECT rating, COUNT(*) AS cnt FROM review "
            f"WHERE parent_asin IN ({placeholders}) GROUP BY rating ORDER BY rating"
        )
        rows = self._execute_query(sql, tuple(asins))

        dist: Dict[int, int] = {}
        total = 0
        rating_sum = 0.0
        for r in rows:
            star = int(float(r["rating"]))
            dist[star] = int(r["cnt"])
            total += int(r["cnt"])
            rating_sum += star * int(r["cnt"])

        avg = round(rating_sum / total, 2) if total else 0.0
        params_for_log["rating_distribution"] = dist
        params_for_log["total_reviews"] = total
        params_for_log["average_rating"] = avg
        return DBResponse("get_rating_distribution", params_for_log, results=[], results_count=0)

    def compare_products(self, product_queries: List[str], limit_per_product: int = 20) -> DBResponse:
        """【工具】多个商品的横向对比（元信息 + 评分统计）。"""
        params_for_log = {'product_queries': product_queries, 'limit_per_product': limit_per_product}
        logger.info(f"--- TOOL: 竞品对比 (params: {params_for_log}) ---")

        comparison: List[Dict[str, Any]] = []
        for q in product_queries:
            asins = self._resolve_parent_asins(q, max_products=1)
            if not asins:
                comparison.append({"query": q, "found": False})
                continue
            parent_asin = asins[0]
            product = self._execute_query(
                "SELECT parent_asin, title, brand, store, price, main_category, average_rating, rating_number "
                "FROM product WHERE parent_asin = %s LIMIT 1", (parent_asin,)
            )
            stats = self._execute_query(
                "SELECT COUNT(*) AS cnt, AVG(rating) AS avg_rating FROM review WHERE parent_asin = %s", (parent_asin,)
            )
            meta = product[0] if product else {}
            st = stats[0] if stats else {}
            comparison.append({
                "query": q,
                "found": True,
                "parent_asin": parent_asin,
                "title": meta.get("title"),
                "brand": meta.get("brand") or meta.get("store"),
                "price": meta.get("price"),
                "main_category": meta.get("main_category"),
                "average_rating": meta.get("average_rating"),
                "rating_number": meta.get("rating_number"),
                "review_count": int(st.get("cnt") or 0),
                "avg_rating_from_reviews": round(float(st.get("avg_rating") or 0), 2),
            })

        params_for_log["comparison"] = comparison
        return DBResponse("compare_products", params_for_log, results=[], results_count=0)

    def get_review_trend(self, product_query: str, start_date: str, end_date: str) -> DBResponse:
        """【工具】按日期查看商品评论趋势（按天聚合评论数与平均评分）。"""
        params_for_log = {'product_query': product_query, 'start_date': start_date, 'end_date': end_date}
        logger.info(f"--- TOOL: 评论趋势 (params: {params_for_log}) ---")

        try:
            start_dt = datetime.strptime(start_date, '%Y-%m-%d')
            end_dt = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
        except ValueError:
            return DBResponse("get_review_trend", params_for_log, error_message="日期格式错误，请使用 'YYYY-MM-DD' 格式。")

        asins = self._resolve_parent_asins(product_query)
        if not asins:
            return DBResponse("get_review_trend", params_for_log, error_message=f"未找到商品: {product_query}")

        start_ms = int(start_dt.timestamp() * 1000)
        end_ms = int(end_dt.timestamp() * 1000)
        placeholders = ",".join(["%s"] * len(asins))
        sql = (
            f"SELECT FROM_UNIXTIME(review_time/1000, '%Y-%m-%d') AS day, COUNT(*) AS cnt, AVG(rating) AS avg_rating "
            f"FROM review WHERE parent_asin IN ({placeholders}) "
            f"AND review_time >= %s AND review_time < %s "
            f"GROUP BY day ORDER BY day"
        )
        rows = self._execute_query(sql, (*asins, start_ms, end_ms))

        trend = [
            {"day": r["day"], "count": int(r["cnt"]), "avg_rating": round(float(r["avg_rating"]), 2)}
            for r in rows
        ]
        params_for_log["trend"] = trend
        return DBResponse("get_review_trend", params_for_log, results=[], results_count=0)

    def get_top_complaints(self, product_query: str, limit: int = 50) -> DBResponse:
        """【工具】差评归因：获取 1-2 星差评（按有用票数排序）。"""
        params_for_log = {'product_query': product_query, 'limit': limit}
        logger.info(f"--- TOOL: 差评归因 (params: {params_for_log}) ---")

        asins = self._resolve_parent_asins(product_query)
        if not asins:
            return DBResponse("get_top_complaints", params_for_log, error_message=f"未找到商品: {product_query}")

        placeholders = ",".join(["%s"] * len(asins))
        sql = (
            f"SELECT asin, parent_asin, user_id, rating, title, content, helpful_vote, review_time "
            f"FROM review WHERE parent_asin IN ({placeholders}) AND rating <= 2 "
            f"ORDER BY helpful_vote DESC, review_time DESC LIMIT %s"
        )
        rows = self._execute_query(sql, (*asins, limit))

        results = [
            QueryResult(
                platform=self.PLATFORM,
                content_type="review",
                title_or_content=(r.get("title") or "") + (" " + r["content"] if r.get("content") else ""),
                author_nickname=r.get("user_id"),
                publish_time=self._to_datetime(r.get("review_time")),
                engagement={
                    "rating": int(float(r.get("rating") or 0)),
                    "helpful_vote": int(r.get("helpful_vote") or 0),
                },
                source_table=r.get("parent_asin") or "",
            )
            for r in rows
        ]
        return DBResponse("get_top_complaints", params_for_log, results=results, results_count=len(results))


# --- 打印预览 ---

def print_response_summary(response: DBResponse):
    """简化的打印函数，用于展示测试结果。"""
    if response.error_message:
        logger.info(f"工具 '{response.tool_name}' 执行出错: {response.error_message}")
        return

    params_str = ", ".join(f"{k}='{v}'" for k, v in response.parameters.items() if k not in ("rating_distribution", "comparison", "trend"))
    logger.info(f"查询: 工具='{response.tool_name}', 参数=[{params_str}]")
    logger.info(f"找到 {response.results_count} 条记录。")

    output_lines = ["==== 查询结果预览（最多前5条） ===="]
    for idx, res in enumerate(response.results[:5], 1):
        content_preview = (res.title_or_content.replace('\n', ' ')[:70] + '...') if res.title_or_content and len(res.title_or_content) > 70 else (res.title_or_content or '')
        author_str = res.author_nickname or "N/A"
        publish_time_str = res.publish_time.strftime('%Y-%m-%d %H:%M') if res.publish_time else "N/A"
        engagement_str = ", ".join(f"{k}: {v}" for k, v in (res.engagement or {}).items() if v)
        output_lines.append(
            f"{idx}. [{res.platform.upper()}/{res.content_type}] {content_preview}\n"
            f"   用户: {author_str} | 时间: {publish_time_str}\n"
            f"   互动数据: {{{engagement_str}}}"
        )
    if not response.results:
        output_lines.append("暂无内容。")
    output_lines.append("=" * 60)
    logger.info('\n'.join(output_lines))


if __name__ == "__main__":
    db = ProductReviewDB()
    logger.info("数据库工具初始化成功，开始执行测试场景...\n")

    response1 = db.search_products(query="headphone", limit=5)
    print_response_summary(response1)

    response2 = db.get_rating_distribution(product_query="headphone")
    logger.info(response2.parameters)
