"""查询词归一化 —— 让英文 Amazon 库兼容中文 / 中英混合查询。

背景
----
本地库（由 ``tools/ecommerce/import_amazon.py`` 导入）中 ``product`` 表的
title / brand / store 全部是英文，而用户与 LLM 给出的查询串经常是中文或
中英混合，例如::

    "Sony WF-1000XM5 无线降噪耳机 口碑与竞品分析"
    "headphones 耳机"
    "索尼 蓝牙降噪耳机 无线 入耳式"

整串 ``LIKE %查询%`` 做子串匹配时这些查询几乎必然 0 命中，于是引擎会
误判成"库里没有数据"。

职责
----
检索层负责把任意语言的查询串归一化成一组可用于 LIKE 的**英文候选词**，
调用方（LLM 提示词、上层业务）无需关心底层库的语言：

1. 英文词与型号原样保留（``Sony``、``WF-1000XM5``、``wireless earbuds``）；
2. 中文部分翻译成英文电商关键词，两级策略：
   a. 先查内置词典（零成本、确定性强，覆盖常见类目 / 属性 / 品牌）；
   b. 词典覆盖不到的中文再交给 LLM 翻译，结果按查询串缓存；
3. 两种手段都拿不到英文词时，退回原始查询串——行为与改动前一致，不会更差。

LLM 不可用或调用失败时只记一条 warning 并降级，绝不影响检索链路可用性。
"""

from __future__ import annotations

import re
from functools import lru_cache
from typing import List, Sequence

from loguru import logger

__all__ = ["build_match_terms", "has_cjk", "like_escape"]


# --- 基础工具 -----------------------------------------------------------

_TERM_SPLIT_RE = re.compile(r"[\s,，、;；/|()（）\[\]【】\"'“”‘’]+")
_LATIN_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-\.]*")
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\uac00-\ud7af]")

MIN_TERM_LEN = 2
MAX_TERMS = 8


def has_cjk(text: str) -> bool:
    """是否含有中日韩字符。"""
    return bool(_CJK_RE.search(text or ""))


def like_escape(term: str) -> str:
    """转义 LIKE 通配符，避免查询词里的 % / _ 被当成通配符。"""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# --- 内置词典：常见类目 / 属性 / 品牌 ------------------------------------
#
# 词典先行的理由：这些是高频词，命中后无需任何 LLM 调用即可检索，
# 既省延迟又不受 LLM 可用性影响。词典未覆盖时再走 LLM。
_CATEGORY_DICT: dict[str, Sequence[str]] = {
    # 音频
    "耳机": ("headphones", "earbuds"),
    "蓝牙耳机": ("bluetooth", "headphones"),
    "降噪耳机": ("noise", "cancelling", "headphones"),
    "头戴式": ("headphones",),
    "入耳式": ("earbuds", "in ear"),
    "耳塞": ("earbuds",),
    "音箱": ("speaker",),
    "音响": ("speaker",),
    "麦克风": ("microphone",),
    "蓝牙": ("bluetooth",),
    "无线": ("wireless",),
    "有线": ("wired",),
    "降噪": ("noise", "cancelling"),
    "运动": ("sport",),
    "防水": ("waterproof",),
    # 数码
    "手机": ("phone",),
    "手机壳": ("phone case",),
    "平板": ("tablet",),
    "电脑": ("laptop",),
    "笔记本": ("laptop",),
    "显示器": ("monitor",),
    "键盘": ("keyboard",),
    "鼠标": ("mouse",),
    "相机": ("camera",),
    "摄像头": ("camera",),
    "手表": ("watch",),
    "智能手表": ("smart watch",),
    "充电器": ("charger",),
    "充电宝": ("power bank",),
    "移动电源": ("power bank",),
    "数据线": ("cable",),
    "硬盘": ("hard drive",),
    "内存卡": ("memory card",),
    "投影仪": ("projector",),
    "打印机": ("printer",),
    "路由器": ("router",),
    # 美妆个护
    "面膜": ("mask",),
    "口红": ("lipstick",),
    "唇膏": ("lip balm",),
    "洗发水": ("shampoo",),
    "护发素": ("conditioner",),
    "洗面奶": ("cleanser",),
    "精华": ("serum",),
    "防晒": ("sunscreen",),
    "眼影": ("eyeshadow",),
    "睫毛膏": ("mascara",),
    "指甲油": ("nail polish",),
    "剃须刀": ("razor",),
    "牙刷": ("toothbrush",),
    "牙膏": ("toothpaste",),
    # 家居 / 其他
    "咖啡机": ("coffee maker",),
    "吸尘器": ("vacuum",),
    "空气炸锅": ("air fryer",),
    "电饭煲": ("rice cooker",),
    "水杯": ("water bottle",),
    "背包": ("backpack",),
    "眼镜": ("glasses",),
    "玩具": ("toy",),
    "宠物": ("pet",),
    # 常见品牌（中文名 -> 英文品牌）
    "索尼": ("sony",),
    "苹果": ("apple",),
    "三星": ("samsung",),
    "华为": ("huawei",),
    "小米": ("xiaomi",),
    "博士": ("bose",),
    "森海塞尔": ("sennheiser",),
    "飞利浦": ("philips",),
    "佳能": ("canon",),
    "尼康": ("nikon",),
    "罗技": ("logitech",),
    "戴森": ("dyson",),
    "松下": ("panasonic",),
    "铁三角": ("audio technica",),
    "拜亚动力": ("beyerdynamic",),
    "捷波朗": ("jabra",),
    "漫步者": ("edifier",),
    "安克": ("anker",),
    "倍思": ("baseus",),
    "绿联": ("ugreen",),
}

# 词典按 key 长度降序匹配，保证"蓝牙耳机"优先于"耳机"、
# "智能手表"优先于"手表"。
_DICT_KEYS_BY_LEN: List[str] = sorted(_CATEGORY_DICT, key=len, reverse=True)


def _dict_terms(query: str) -> List[str]:
    """从查询串中抽取词典能覆盖的英文关键词。"""
    terms: List[str] = []
    consumed = query
    for key in _DICT_KEYS_BY_LEN:
        if key in consumed:
            terms.extend(_CATEGORY_DICT[key])
            # 命中后从待匹配串里去掉，避免"耳机"重复命中"蓝牙耳机"
            consumed = consumed.replace(key, " ")
    return terms


# --- LLM 兜底翻译 -------------------------------------------------------

_TRANSLATE_SYSTEM_PROMPT = (
    "你是电商检索查询翻译器。把用户给出的商品查询翻译成适合在英文商品库中"
    "做子串检索的英文关键词。只输出英文关键词，用英文逗号分隔，不要输出中文、"
    "不要解释、不要编号。品牌名保留官方英文写法（如 索尼->Sony）。"
    "最多输出 5 个关键词。"
)


def _split_keywords(text: str) -> List[str]:
    """从 LLM 回复中提取英文关键词。"""
    found: List[str] = []
    seen = set()
    for raw in re.split(r"[\s,，、;；/|\n]+", text or ""):
        word = raw.strip(" \t\r\n\"'`-•*[]()（）")
        if len(word) < MIN_TERM_LEN or has_cjk(word):
            continue
        if not _LATIN_RE.match(word):
            continue
        key = word.lower()
        if key not in seen:
            seen.add(key)
            found.append(word)
    return found


@lru_cache(maxsize=256)
def _llm_translate(query: str) -> tuple[str, ...]:
    """用 LLM 把中文查询翻译成英文关键词（结果按查询串缓存）。

    任何失败都只返回空元组，由调用方退回词典 / 原串，不影响检索可用性。
    """
    try:
        from app import config
        from engines.common.llm_client import LLMClient

        settings = config.settings
        api_key = (
            getattr(settings, "KEYWORD_OPTIMIZER_API_KEY", None)
            or getattr(settings, "REVIEW_ENGINE_API_KEY", None)
        )
        if not api_key:
            logger.warning("查询词翻译跳过：未配置可用的 LLM API Key")
            return ()

        client = LLMClient(
            api_key=api_key,
            model_name=(
                getattr(settings, "KEYWORD_OPTIMIZER_MODEL_NAME", None)
                or getattr(settings, "REVIEW_ENGINE_MODEL_NAME", None)
            ),
            base_url=(
                getattr(settings, "KEYWORD_OPTIMIZER_BASE_URL", None)
                or getattr(settings, "REVIEW_ENGINE_BASE_URL", None)
            ),
            # 由 ReviewEngine 发起，归入口碑 Agent 的成本
            engine_name="ReviewEngine",
        )
        raw = client.invoke(_TRANSLATE_SYSTEM_PROMPT, f"查询：{query}", timeout=30)
        terms = _split_keywords(raw)
        if terms:
            logger.info(f"查询词翻译: {query!r} -> {terms}")
        return tuple(terms)
    except Exception as exc:  # pragma: no cover - 依赖外部服务
        logger.warning(f"查询词翻译失败，回退到词典/原串: {exc}")
        return ()


def translate_cjk_terms(query: str) -> List[str]:
    """中文查询 -> 英文关键词：词典优先，词典无覆盖时再调用 LLM。"""
    dict_terms = _dict_terms(query)
    if dict_terms:
        return dict_terms
    return list(_llm_translate(query))


# --- 对外主入口 ---------------------------------------------------------

def build_match_terms(query: str) -> List[str]:
    """把任意语言的查询串归一化成英文 LIKE 候选词（顺序即优先级）。"""
    q = (query or "").strip()
    if not q:
        return []

    terms: List[str] = []
    seen = set()

    def add(term: str) -> None:
        term = (term or "").strip()
        if len(term) < MIN_TERM_LEN or has_cjk(term):
            return
        key = term.lower()
        if key in seen:
            return
        seen.add(key)
        terms.append(term)

    # 1) 英文词与型号：纯英文查询额外保留整串，命中即最相关
    if not has_cjk(q):
        add(q)
    for piece in _TERM_SPLIT_RE.split(q):
        for word in _LATIN_RE.findall(piece):
            add(word)

    # 2) 中文部分：词典 / LLM 翻译成英文关键词
    if has_cjk(q):
        for term in translate_cjk_terms(q):
            add(term)

    # 3) 完全拿不到英文词（如词典与 LLM 都未覆盖的冷门中文词）：
    #    退回原串，保持与改动前一致的行为，不做无意义的全表扫描。
    return terms[:MAX_TERMS] if terms else [q]
