"""查询词归一化与商品匹配排序的单元测试。

这些测试不依赖 MySQL 与 LLM：
- ``build_match_terms`` 的英文/中文/混合分支可通过内置词典确定性覆盖；
- LLM 兜底分支通过 monkeypatch ``_llm_translate`` 验证；
- ``_product_filter_sql`` / ``_term_score`` 是纯函数，可直接断言。
"""

import pytest

from engines.ReviewEngine.tools import query_normalizer as qn
from engines.ReviewEngine.tools.search import (
    _candidate_pool_size,
    _product_filter_sql,
    _rank_rows,
    _term_score,
)


class TestBuildMatchTermsEnglish:
    """纯英文查询保持原有行为。"""

    def test_single_word(self):
        assert qn.build_match_terms("headphones") == ["headphones"]

    def test_phrase_keeps_full_string_first(self):
        terms = qn.build_match_terms("wireless earbuds")
        assert terms[0] == "wireless earbuds"
        assert "wireless" in terms and "earbuds" in terms

    def test_brand_and_model_preserved(self):
        terms = qn.build_match_terms("Sony WF-1000XM5")
        assert "Sony" in terms
        assert "WF-1000XM5" in terms

    def test_empty_query(self):
        assert qn.build_match_terms("") == []
        assert qn.build_match_terms("   ") == []


class TestBuildMatchTermsChinese:
    """中文查询必须被归一化成英文关键词（这是本次修复的核心）。"""

    def test_pure_chinese_category(self):
        terms = qn.build_match_terms("降噪耳机")
        assert "headphones" in terms
        assert not any(qn.has_cjk(t) for t in terms), "结果中不应残留中文词"

    def test_chinese_brand_translated(self):
        terms = qn.build_match_terms("索尼 蓝牙降噪耳机 无线 入耳式")
        assert "sony" in terms, "中文品牌名应翻译成英文品牌"
        assert "headphones" in terms
        assert not any(qn.has_cjk(t) for t in terms)

    def test_mixed_query_keeps_english_and_adds_chinese_terms(self):
        terms = qn.build_match_terms("headphones 耳机")
        assert "headphones" in terms
        assert not any(qn.has_cjk(t) for t in terms)

    def test_offline_dict_hit_avoids_llm(self, monkeypatch):
        """词典能覆盖时不应触发 LLM 调用。"""
        called = []

        def boom(query):
            called.append(query)
            raise AssertionError("词典命中时不应调用 LLM")

        monkeypatch.setattr(qn, "_llm_translate", boom)
        terms = qn.build_match_terms("空气炸锅")
        assert terms == ["air fryer"]
        assert called == []

    def test_llm_fallback_used_when_dict_misses(self, monkeypatch):
        monkeypatch.setattr(qn, "_llm_translate", lambda q: ("quantum", "computer"))
        terms = qn.build_match_terms("量子计算机")
        assert terms == ["quantum", "computer"]

    def test_falls_back_to_raw_query_when_no_english_available(self, monkeypatch):
        """词典与 LLM 都拿不到英文词时退回原串，行为不会比改动前更差。"""
        monkeypatch.setattr(qn, "_llm_translate", lambda q: ())
        terms = qn.build_match_terms("祖传秘方")
        assert terms == ["祖传秘方"]

    def test_cjk_terms_are_dropped_from_results(self):
        terms = qn.build_match_terms("Sony 耳机")
        assert "Sony" in terms
        assert not any(qn.has_cjk(t) for t in terms)


class TestLikeEscape:
    def test_wildcards_escaped(self):
        assert qn.like_escape("50%_off") == "50\\%\\_off"

    def test_backslash_escaped(self):
        assert qn.like_escape("a\\b") == "a\\\\b"


class TestProductFilterSql:
    def test_params_cover_score_then_where(self):
        score_sql, where_sql, params = _product_filter_sql(["a", "b"])
        # 每个词 4 个 score 占位符 + 4 个 where 占位符
        assert score_sql.count("%s") == 8
        assert where_sql.count("%s") == 8
        assert len(params) == 16

    def test_empty_terms_matches_nothing(self):
        score_sql, where_sql, params = _product_filter_sql([])
        assert score_sql == "0"
        assert where_sql == "1 = 0"
        assert params == []


class TestTermScore:
    """相关性打分：整词/品牌精确应高于子串。"""

    def _row(self, title="", brand="", store="", asin="B0", rating_number=0):
        return {
            "title": title, "brand": brand, "store": store,
            "parent_asin": asin, "rating_number": rating_number,
        }

    def test_word_boundary_beats_substring(self):
        """'Sony' 不应把 'Sonya' 当成整词命中。"""
        real = self._row(title="Sony WH-1000XM5 Wireless Headphones")
        coincidence = self._row(title="Eco by Sonya Driver Super Acai Exfoliator")
        assert _term_score(real, ["Sony"]) > _term_score(coincidence, ["Sony"])

    def test_exact_brand_scores_highest(self):
        brand_row = self._row(title="Whatever", brand="Sony")
        title_row = self._row(title="Sony headphones")
        assert _term_score(brand_row, ["Sony"]) > _term_score(title_row, ["Sony"])

    def test_brand_state_reset_between_terms(self):
        row = self._row(title="Wireless Earbuds", brand="Anker")
        assert _term_score(row, ["Anker", "Earbuds"]) > _term_score(row, ["Anker"])

    def test_no_match_scores_zero(self):
        assert _term_score(self._row(title="Coffee Maker"), ["headphones"]) == 0


class TestRankRows:
    def test_relevance_outranks_popularity(self):
        rows = [
            {"title": "Eco by Sonya", "brand": "", "store": "", "parent_asin": "A", "rating_number": 99999},
            {"title": "Sony Headphones", "brand": "Sony", "store": "", "parent_asin": "B", "rating_number": 5},
        ]
        ranked = _rank_rows(rows, ["Sony"], limit=2)
        assert ranked[0]["parent_asin"] == "B", "整词命中应排在子串巧合之前"

    def test_zero_score_rows_dropped(self):
        rows = [{"title": "Coffee Maker", "brand": "", "store": "", "parent_asin": "A", "rating_number": 1}]
        assert _rank_rows(rows, ["headphones"], limit=5) == []

    def test_limit_applied(self):
        rows = [
            {"title": "headphones one", "brand": "", "store": "", "parent_asin": "A", "rating_number": 3},
            {"title": "headphones two", "brand": "", "store": "", "parent_asin": "B", "rating_number": 2},
        ]
        assert len(_rank_rows(rows, ["headphones"], limit=1)) == 1

    def test_empty_rows(self):
        assert _rank_rows([], ["headphones"], limit=5) == []


class TestCandidatePool:
    def test_pool_is_bounded(self):
        assert _candidate_pool_size(1) == 100
        assert _candidate_pool_size(5) == 100
        assert _candidate_pool_size(50) == 500
        assert _candidate_pool_size(1000) == 500
