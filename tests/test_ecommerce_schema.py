"""ecommerce schema 的索引迁移。

两个不变量：

1. **幂等**：``_ensure_review_indexes`` 在索引已存在时不得重复 ALTER
   （MySQL 没有 ``CREATE INDEX IF NOT EXISTS``，判断完全靠 information_schema）。
2. **不漂移**：新建库走 ``REVIEW_TABLE_DDL``，已有库走迁移函数，两条路径必须
   得到同一组索引；否则新老部署的查询性能会不一致。

这里用假连接替换 SQLAlchemy 的 AsyncConnection，不访问数据库。
"""

import sys
from pathlib import Path

import pytest

_PROJ_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJ_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJ_ROOT))

from tools.ecommerce import schema as schema_mod  # noqa: E402


class _FakeResult:
    def __init__(self, rows):
        self._rows = rows

    def __iter__(self):
        return iter(self._rows)

    def first(self):
        return self._rows[0] if self._rows else None


class _FakeConn:
    """只实现 _ensure_review_indexes 用到的那部分 execute 行为。"""

    def __init__(self, existing_indexes):
        self.existing = set(existing_indexes)
        self.alters: list[str] = []

    async def execute(self, statement, params=None):
        sql = str(statement)
        if "information_schema.STATISTICS" in sql:
            return _FakeResult([(name,) for name in sorted(self.existing)])
        self.alters.append(sql)
        return _FakeResult([])


@pytest.mark.asyncio
async def test_missing_indexes_are_created():
    conn = _FakeConn(existing_indexes={"PRIMARY", "idx_platform_parent_asin", "idx_review_time"})

    await schema_mod._ensure_review_indexes(conn)

    assert len(conn.alters) == len(schema_mod._REVIEW_INDEXES)
    for name, columns in schema_mod._REVIEW_INDEXES:
        assert any(f"ADD INDEX {name} {columns}" in sql for sql in conn.alters), (
            f"{name} 未被创建：{conn.alters}"
        )


@pytest.mark.asyncio
async def test_existing_indexes_are_skipped():
    """已建过的库重复执行 init，不得再产生任何 ALTER。"""
    all_names = {"PRIMARY", "idx_review_time"} | {name for name, _ in schema_mod._REVIEW_INDEXES}
    conn = _FakeConn(existing_indexes=all_names)

    await schema_mod._ensure_review_indexes(conn)

    assert conn.alters == []


@pytest.mark.asyncio
async def test_partial_indexes_only_fill_the_gap():
    name, columns = schema_mod._REVIEW_INDEXES[0]
    conn = _FakeConn(existing_indexes={name})

    await schema_mod._ensure_review_indexes(conn)

    assert conn.alters == [
        f"ALTER TABLE {schema_mod.REVIEW_TABLE} ADD INDEX {schema_mod._REVIEW_INDEXES[1][0]} "
        f"{schema_mod._REVIEW_INDEXES[1][1]}"
    ], f"只应补缺失的那个：{conn.alters}"


def test_ddl_and_migration_agree():
    """REVIEW_TABLE_DDL 与 _REVIEW_INDEXES 必须描述同一组索引。"""
    ddl = schema_mod.REVIEW_TABLE_DDL
    for name, columns in schema_mod._REVIEW_INDEXES:
        # DDL 里写作：KEY idx_parent_review_time (parent_asin, review_time)
        assert f"KEY {name} {columns}" in ddl, (
            f"新建库的 DDL 缺少 {name}，新老部署的索引会不一致"
        )


def test_indexes_cover_the_real_query_shapes():
    """回归护栏：这组索引是为六种评论查询形状选的，改动前先看 schema.py 的注释。

    - (parent_asin, ...) 前缀服务所有 ``WHERE parent_asin IN (...)``
    - review_time 服务 ORDER BY review_time DESC 与 review_time 范围过滤
    - rating / helpful_vote 服务 rating<=2 + ORDER BY helpful_vote DESC
    """
    cols = {name: columns for name, columns in schema_mod._REVIEW_INDEXES}

    assert cols["idx_parent_review_time"] == "(parent_asin, review_time)"
    assert cols["idx_parent_rating_helpful"] == "(parent_asin, rating, helpful_vote)"

    # 不得再单独建 (parent_asin) 或 (parent_asin, rating)——它们是上面两个的前缀
    prefixes = {"(parent_asin)", "(parent_asin, rating)"}
    assert not (set(cols.values()) & prefixes), "存在前缀冗余索引"
