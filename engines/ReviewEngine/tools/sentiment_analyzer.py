"""
多语言情感分析工具
基于WeiboMultilingualSentiment模型为ReviewEngine提供情感分析功能
"""

import importlib.util
import os
import re
import sys
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, replace
from typing import List, Dict, Any, Optional, Union

from loguru import logger

# 依赖可用性标记。初值为 False，只有在真正需要推理（initialize()/enable()）时才探测，
# 绝不在 import 本模块时探测 —— 否则任何 import 本模块的调用方（例如只想要纯词典
# ABSA 的 ReportEngine 可视化）都会被连带拉起 torch + transformers（约 1100 个模块、
# 数秒导入、约 1GB 内存）。
#
# 探测必须可重试且线程安全：
# 三个引擎在 threading.Thread 中并发懒加载各自的包，而 transformers 首次导入很慢；
# 当本模块导入 transformers 的同时另一线程也在导入它，本线程会读到"部分初始化"的
# transformers 模块并抛出：
#     ImportError: cannot import name 'AutoTokenizer' from 'transformers'
# 这是瞬时竞态（CPython 的 from X import Y 不会为属性解析等待 X 的初始化完成），
# 因此这里用模块级锁把导入串行化，并做有限次重试。
torch = None  # type: ignore
TORCH_AVAILABLE = False
AutoTokenizer = None  # type: ignore
AutoModelForSequenceClassification = None  # type: ignore
TRANSFORMERS_AVAILABLE = False

# 串行化 torch/transformers 的首次导入，消除并发导入竞态
_IMPORT_LOCK = threading.RLock()
_IMPORT_RETRIES = 3
_IMPORT_RETRY_DELAY = 0.5  # 秒；重试间隔按次数线性退避


def _module_installed(name: str) -> bool:
    """包是否已安装（不触发导入）。用于区分"没装"和"导入竞态失败"。"""
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def _load_torch() -> bool:
    """探测（或重新探测）torch 是否可用；线程安全 + 有限重试。"""
    global torch, TORCH_AVAILABLE
    if TORCH_AVAILABLE:
        return True
    if not _module_installed("torch"):
        return False
    with _IMPORT_LOCK:
        if TORCH_AVAILABLE:
            return True
        for attempt in range(_IMPORT_RETRIES):
            try:
                import torch as _torch

                _torch.classes.__path__ = []
            except ImportError:
                # 通常是并发导入导致的瞬时失败，退避后重试
                time.sleep(_IMPORT_RETRY_DELAY * (attempt + 1))
                continue
            torch = _torch
            TORCH_AVAILABLE = True
            return True
    return False


def _load_transformers() -> bool:
    """探测（或重新探测）transformers 是否可用；线程安全 + 有限重试。

    失败时返回 False，但**不锁定**结果：调用方可在稍后重试，
    以越过并发导入造成的瞬时失败（见文件顶部说明）。
    """
    global AutoTokenizer, AutoModelForSequenceClassification, TRANSFORMERS_AVAILABLE
    if TRANSFORMERS_AVAILABLE:
        return True
    if not _module_installed("transformers"):
        return False
    with _IMPORT_LOCK:
        if TRANSFORMERS_AVAILABLE:
            return True
        for attempt in range(_IMPORT_RETRIES):
            try:
                from transformers import (
                    AutoTokenizer as _AutoTokenizer,
                    AutoModelForSequenceClassification as _AutoModelForSequenceClassification,
                )
            except ImportError:
                time.sleep(_IMPORT_RETRY_DELAY * (attempt + 1))
                continue
            AutoTokenizer = _AutoTokenizer
            AutoModelForSequenceClassification = _AutoModelForSequenceClassification
            TRANSFORMERS_AVAILABLE = True
            return True
    return False


def probe_sentiment_dependencies() -> str:
    """探测情感分析依赖；返回空字符串表示可用，否则返回缺失描述。

    供 /api/config 或启动自检使用，不会加载模型权重。
    """
    ok_torch = _load_torch()
    ok_tf = _load_transformers()
    if ok_torch and ok_tf:
        return ""
    missing = []
    if not ok_torch:
        missing.append("PyTorch")
    if not ok_tf:
        missing.append("Transformers")
    return " / ".join(missing)


# 情感分析全局开关从配置读取
try:
    from app.config import settings as _app_settings
    SENTIMENT_ANALYSIS_ENABLED = _app_settings.SENTIMENT_ANALYSIS_ENABLED
except Exception:
    SENTIMENT_ANALYSIS_ENABLED = True


def _settings():
    """动态获取当前配置对象（reload_settings() 会替换 config.settings）。"""
    try:
        from app import config as _config

        return _config.settings
    except Exception:
        return _app_settings


def _cfg(name: str, default):
    """读取配置项，读不到时退回默认值。"""
    try:
        return getattr(_settings(), name, default)
    except Exception:
        return default


def _sentiment_globally_enabled() -> bool:
    return bool(_cfg("SENTIMENT_ANALYSIS_ENABLED", SENTIMENT_ANALYSIS_ENABLED))


def _describe_missing_dependencies() -> str:
    missing = []
    if not TORCH_AVAILABLE:
        missing.append("PyTorch")
    if not TRANSFORMERS_AVAILABLE:
        missing.append("Transformers")
    return " / ".join(missing)


@dataclass
class SentimentResult:
    """情感分析结果数据类"""

    text: str
    sentiment_label: str
    confidence: float
    probability_distribution: Dict[str, float]
    success: bool = True
    error_message: Optional[str] = None
    analysis_performed: bool = True


@dataclass
class BatchSentimentResult:
    """批量情感分析结果数据类"""

    results: List[SentimentResult]
    total_processed: int
    success_count: int
    failed_count: int
    average_confidence: float
    analysis_performed: bool = True


class WeiboMultilingualSentimentAnalyzer:
    """
    多语言情感分析器
    封装WeiboMultilingualSentiment模型，为AI Agent提供情感分析功能
    """

    def __init__(self):
        """初始化情感分析器（**不加载模型、不导入 torch/transformers**）"""
        self.model = None
        self.tokenizer = None
        self.device = None
        self.is_initialized = False
        self.is_disabled = False
        self.disable_reason: Optional[str] = None
        # 是否因依赖探测失败被禁用（区别于配置主动关闭）；这类禁用可重试恢复。
        self.disabled_by_missing_deps = False
        # 依赖是否已探测过（探测延迟到 initialize()/enable()）
        self.deps_probed = False
        # 已加载的模型名，用于缓存键
        self.loaded_model_name: Optional[str] = None

        # 情感标签映射（5级分类）
        self.sentiment_map = {
            0: "非常负面",
            1: "负面",
            2: "中性",
            3: "正面",
            4: "非常正面",
        }

        # 结果缓存：同一进程内同一条评论常被多次搜索/反思重复分析
        self._cache: "OrderedDict[str, SentimentResult]" = OrderedDict()
        self._cache_hits = 0
        self._cache_misses = 0

        if not _sentiment_globally_enabled():
            self.disable("情感分析功能已在配置中关闭。")

    # ── 依赖探测 ──────────────────────────────────────────────────────

    def _probe_dependencies(self) -> bool:
        """探测 torch/transformers（线程安全 + 重试）。仅在需要推理时调用。"""
        if TORCH_AVAILABLE and TRANSFORMERS_AVAILABLE:
            self.deps_probed = True
            return True
        ok = _load_torch() and _load_transformers()
        self.deps_probed = True
        return ok

    def disable(self, reason: Optional[str] = None, drop_state: bool = False,
                by_missing_deps: bool = False) -> None:
        """Disable sentiment analysis, optionally clearing loaded resources.

        by_missing_deps 标记本次禁用是否源于依赖探测失败。依赖失败可能是
        并发导入造成的瞬时结果，因此只有这种情况允许 initialize() 稍后重试。
        """
        self.is_disabled = True
        self.disable_reason = reason or "Sentiment analysis disabled."
        self.disabled_by_missing_deps = by_missing_deps
        if drop_state:
            self.model = None
            self.tokenizer = None
            self.device = None
            self.is_initialized = False
            self.loaded_model_name = None
            self._cache.clear()

    def enable(self) -> bool:
        """Attempt to enable sentiment analysis; returns True if enabled."""
        if not _sentiment_globally_enabled():
            self.disable("情感分析功能已在配置中关闭。")
            return False
        if not self._probe_dependencies():
            missing = _describe_missing_dependencies() or "未知依赖"
            self.disable(f"缺少依赖: {missing}，情感分析已禁用。", by_missing_deps=True)
            return False
        self.is_disabled = False
        self.disable_reason = None
        self.disabled_by_missing_deps = False
        return True

    # ── 配置读取（动态，支持 reload_settings） ────────────────────────

    def _model_name(self) -> str:
        return str(_cfg("SENTIMENT_MODEL_NAME", "tabularisai/multilingual-sentiment-analysis")) or "tabularisai/multilingual-sentiment-analysis"

    def _batch_size(self) -> int:
        try:
            return max(1, int(_cfg("SENTIMENT_BATCH_SIZE", 32)))
        except (TypeError, ValueError):
            return 32

    def _max_length(self) -> int:
        try:
            return max(16, int(_cfg("SENTIMENT_MAX_LENGTH", 512)))
        except (TypeError, ValueError):
            return 512

    def _cache_size(self) -> int:
        try:
            return int(_cfg("SENTIMENT_CACHE_SIZE", 5000))
        except (TypeError, ValueError):
            return 5000

    def _enabled_per_search(self) -> bool:
        return bool(_cfg("ENABLE_SENTIMENT_PER_SEARCH", True))

    # ── 结果缓存 ──────────────────────────────────────────────────────

    def _cache_key(self, processed_text: str) -> str:
        return f"{self.loaded_model_name or self._model_name()}|{processed_text}"

    def _cache_lookup(self, processed_text: str) -> Optional[SentimentResult]:
        """命中则返回副本（调用方可能改写 text 字段）。"""
        if self._cache_size() <= 0 or not processed_text:
            return None
        key = self._cache_key(processed_text)
        cached = self._cache.get(key)
        if cached is None:
            self._cache_misses += 1
            return None
        self._cache.move_to_end(key)
        self._cache_hits += 1
        return replace(cached)

    def _cache_store(self, processed_text: str, result: SentimentResult) -> None:
        limit = self._cache_size()
        if limit <= 0 or not processed_text or not result.success:
            return
        key = self._cache_key(processed_text)
        self._cache[key] = replace(result)
        self._cache.move_to_end(key)
        while len(self._cache) > limit:
            self._cache.popitem(last=False)

    def cache_stats(self) -> Dict[str, Any]:
        """缓存命中统计，便于排查"算了但没省下时间"的问题。"""
        total = self._cache_hits + self._cache_misses
        return {
            "size": len(self._cache),
            "limit": self._cache_size(),
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "hit_rate": round(self._cache_hits / total, 4) if total else 0.0,
        }

    def clear_cache(self) -> None:
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    def _select_device(self):
        """Select the best available torch device."""
        if not TORCH_AVAILABLE:
            return None
        assert torch is not None
        if torch.cuda.is_available():
            return torch.device("cuda")
        mps_backend = getattr(torch.backends, "mps", None)
        if (
            mps_backend
            and getattr(mps_backend, "is_available", lambda: False)()
            and getattr(mps_backend, "is_built", lambda: False)()
        ):
            return torch.device("mps")
        return torch.device("cpu")

    def initialize(self) -> bool:
        """
        初始化模型和分词器

        Returns:
            是否初始化成功
        """
        # 若当初是因依赖探测失败被禁用，先重新探测一次：并发的首次 transformers
        # 导入可能瞬时失败，稍后重试通常即可成功（见文件顶部说明）。
        if self.is_disabled and self.disabled_by_missing_deps:
            if self._probe_dependencies():
                logger.info(
                    f"  依赖重新探测成功，已恢复情感分析（此前: {self.disable_reason}）"
                )
                self.enable()

        if self.is_disabled:
            reason = self.disable_reason or "情感分析功能已禁用"
            logger.debug(f"情感分析功能已禁用，跳过模型加载：{reason}")
            return False

        if not self._probe_dependencies():
            missing = _describe_missing_dependencies() or "未知依赖"
            self.disable(f"缺少依赖: {missing}，情感分析已禁用。", drop_state=True,
                         by_missing_deps=True)
            logger.warning(f"缺少依赖: {missing}，无法加载情感分析模型。")
            return False

        if self.is_initialized:
            logger.debug("模型已经初始化，无需重复加载")
            return True

        try:
            assert AutoTokenizer is not None
            assert AutoModelForSequenceClassification is not None

            model_name = self._model_name()
            t0 = time.time()
            logger.info(f"正在加载情感分析模型: {model_name}")
            self.tokenizer = AutoTokenizer.from_pretrained(model_name)
            self.model = AutoModelForSequenceClassification.from_pretrained(model_name)

            # 设置设备
            device = self._select_device()
            if device is None:
                raise RuntimeError("未检测到可用的计算设备")

            self.device = device
            self.model.to(self.device)
            self.model.eval()
            self.loaded_model_name = model_name
            self.is_initialized = True
            self.enable()

            device_type = getattr(self.device, "type", str(self.device))
            if device_type == "cuda":
                device_desc = "CUDA GPU"
            elif device_type == "mps":
                device_desc = "Apple MPS"
            else:
                device_desc = "CPU"

            logger.info(
                f"情感分析模型加载成功: {model_name} | 设备: {device_desc} | "
                f"耗时 {time.time() - t0:.2f}s | 支持 22 种语言、5 级情感"
            )

            return True

        except Exception as e:
            error_message = f"模型加载失败: {e}"
            logger.warning(f"{error_message}（请检查网络连接或模型文件）")
            self.disable(error_message, drop_state=True)
            return False

    def _preprocess_text(self, text: str) -> str:
        """
        文本预处理

        Args:
            text: 输入文本

        Returns:
            处理后的文本
        """
        # 基本文本清理
        if not text or not text.strip():
            return ""

        # 去除多余空格
        text = re.sub(r"\s+", " ", text.strip())

        return text

    def _handled_result(self, text: str, processed_text: str) -> Optional[SentimentResult]:
        """短路结果：禁用 / 未初始化 / 空文本；返回 None 表示可以真正推理。"""
        if self.is_disabled:
            return SentimentResult(
                text=text,
                sentiment_label="情感分析未执行",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message=self.disable_reason or "情感分析功能已禁用",
                analysis_performed=False,
            )
        if not self.is_initialized:
            return SentimentResult(
                text=text,
                sentiment_label="未初始化",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message="模型未初始化，请先调用initialize() 方法",
                analysis_performed=False,
            )
        if not processed_text:
            return SentimentResult(
                text=text,
                sentiment_label="输入错误",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message="输入文本为空或无效内容",
                analysis_performed=False,
            )
        return None

    def _infer_chunk(self, chunk: List[str]) -> List[SentimentResult]:
        """对一小批文本做**一次**前向推理。

        相比逐条推理，批处理能把 padding/调用开销摊薄（实测 bs=32 约快 4~5 倍）。
        批内完全相同（预处理后）的文本只算一次；整批失败时回退到逐条，
        避免单条脏数据拖垮整批。
        """
        processed = [self._preprocess_text(t) for t in chunk]
        results: List[Optional[SentimentResult]] = [None] * len(chunk)
        # 唯一文本下标 -> 该文本在批内的所有位置（批内去重）
        key_to_indices: Dict[str, List[int]] = {}
        unique_idx: List[int] = []

        for i, (raw, proc) in enumerate(zip(chunk, processed)):
            if not proc:
                results[i] = self._handled_result(raw, proc)
                continue
            cached = self._cache_lookup(proc)
            if cached is not None:
                results[i] = replace(cached, text=raw)
                continue
            key = self._cache_key(proc)
            if key in key_to_indices:
                key_to_indices[key].append(i)
            else:
                key_to_indices[key] = [i]
                unique_idx.append(i)

        if not unique_idx:
            return results  # type: ignore[return-value]

        assert self.tokenizer is not None
        assert self.model is not None
        assert torch is not None

        def _fill(indices: List[int], template: SentimentResult) -> None:
            for i in indices:
                results[i] = replace(template, text=chunk[i])

        try:
            inputs = self.tokenizer(
                [processed[i] for i in unique_idx],
                max_length=self._max_length(),
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.inference_mode():
                logits = self.model(**inputs).logits
                probabilities = torch.softmax(logits, dim=1)
            prob_rows = probabilities.detach().to("cpu").tolist()
            labels = list(self.sentiment_map.values())

            for row_idx, i in enumerate(unique_idx):
                probs = prob_rows[row_idx]
                prediction = max(range(len(probs)), key=lambda j: probs[j])
                prob_dist = {
                    name: float(probs[j]) for j, name in enumerate(labels) if j < len(probs)
                }
                result = SentimentResult(
                    text=chunk[i],
                    sentiment_label=self.sentiment_map.get(prediction, "未知"),
                    confidence=float(probs[prediction]),
                    probability_distribution=prob_dist,
                    success=True,
                )
                self._cache_store(processed[i], result)
                _fill(key_to_indices[self._cache_key(processed[i])], result)
        except Exception as exc:
            logger.warning(f"批量情感推理失败，回退逐条处理: {exc}")
            for i in unique_idx:
                result = self._infer_single_uncached(chunk[i], processed[i])
                _fill(key_to_indices[self._cache_key(processed[i])], result)

        # 长度必须与入参严格对齐（调用方按位置与原始数据 zip），故不用过滤式返回
        return [
            r if r is not None else SentimentResult(
                text=chunk[i],
                sentiment_label="分析失败",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message="情感分析未产出结果",
                analysis_performed=False,
            )
            for i, r in enumerate(results)
        ]

    def _infer_single_uncached(self, raw: str, processed: str) -> SentimentResult:
        """单条推理（回退路径）。"""
        assert self.tokenizer is not None
        assert self.model is not None
        assert torch is not None
        try:
            inputs = self.tokenizer(
                processed,
                max_length=self._max_length(),
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            inputs = {k: v.to(self.device) for k, v in inputs.items()}
            with torch.inference_mode():
                logits = self.model(**inputs).logits
                probabilities = torch.softmax(logits, dim=1)
            probs = probabilities[0].detach().to("cpu").tolist()
            prediction = max(range(len(probs)), key=lambda j: probs[j])
            result = SentimentResult(
                text=raw,
                sentiment_label=self.sentiment_map.get(prediction, "未知"),
                confidence=float(probs[prediction]),
                probability_distribution={
                    name: float(probs[j])
                    for j, name in enumerate(self.sentiment_map.values())
                    if j < len(probs)
                },
                success=True,
            )
            self._cache_store(processed, result)
            return result
        except Exception as e:
            return SentimentResult(
                text=raw,
                sentiment_label="分析失败",
                confidence=0.0,
                probability_distribution={},
                success=False,
                error_message=f"预测时发生错误: {str(e)}",
                analysis_performed=False,
            )

    def analyze_single_text(self, text: str) -> SentimentResult:
        """
        对单个文本进行情感分析

        Args:
            text: 要分析的文本

        Returns:
            SentimentResult对象
        """
        processed_text = self._preprocess_text(text)
        handled = self._handled_result(text, processed_text)
        if handled is not None:
            return handled
        return self._infer_chunk([text])[0]

    def analyze_batch(
        self, texts: List[str], show_progress: bool = True, batch_size: Optional[int] = None
    ) -> BatchSentimentResult:
        """
        批量情感分析（真批处理：每个 batch 只做一次前向推理）

        Args:
            texts: 文本列表
            show_progress: 是否显示进度
            batch_size: 批大小，默认取配置 SENTIMENT_BATCH_SIZE

        Returns:
            BatchSentimentResult对象
        """
        if not texts:
            return BatchSentimentResult(
                results=[],
                total_processed=0,
                success_count=0,
                failed_count=0,
                average_confidence=0.0,
                analysis_performed=not self.is_disabled and self.is_initialized,
            )

        if self.is_disabled or not self.is_initialized:
            passthrough_results = [
                SentimentResult(
                    text=text,
                    sentiment_label="情感分析未执行",
                    confidence=0.0,
                    probability_distribution={},
                    success=False,
                    error_message=self.disable_reason or "情感分析功能不可用",
                    analysis_performed=False,
                )
                for text in texts
            ]
            return BatchSentimentResult(
                results=passthrough_results,
                total_processed=len(texts),
                success_count=0,
                failed_count=len(texts),
                average_confidence=0.0,
                analysis_performed=False,
            )

        size = batch_size or self._batch_size()
        results: List[SentimentResult] = []
        for start in range(0, len(texts), size):
            chunk = texts[start:start + size]
            results.extend(self._infer_chunk(chunk))
            if show_progress and len(texts) > size:
                logger.info(f"    情感分析进度: {min(start + size, len(texts))}/{len(texts)}")

        success_count = sum(1 for r in results if r.success)
        total_confidence = sum(r.confidence for r in results if r.success)
        average_confidence = total_confidence / success_count if success_count > 0 else 0.0

        return BatchSentimentResult(
            results=results,
            total_processed=len(texts),
            success_count=success_count,
            failed_count=len(texts) - success_count,
            average_confidence=average_confidence,
            analysis_performed=True,
        )

    def _build_passthrough_analysis(
        self,
        original_data: List[Dict[str, Any]],
        reason: str,
        texts: Optional[List[str]] = None,
        results: Optional[List[SentimentResult]] = None,
    ) -> Dict[str, Any]:
        """
        构建在情感分析不可用时的透传结果。

        注意：这里**不再回传原始文本全文**。该结构会被 metadata 原样序列化进
        每次段落总结的 LLM prompt，回传全文会让 prompt 体积翻数倍而没有任何
        分析价值（文本本身已经在 search_results 里）。
        """
        total_items = len(texts) if texts is not None else len(original_data)
        response: Dict[str, Any] = {
            "sentiment_analysis": {
                "available": False,
                "reason": reason,
                "total_analyzed": 0,
                "success_rate": f"0/{total_items}",
                "average_confidence": 0.0,
                "sentiment_distribution": {},
                "high_confidence_results": [],
                "summary": f"情感分析未执行：{reason}",
                "original_count": total_items,
            }
        }

        return response

    def _max_high_confidence(self) -> int:
        """写入 prompt 的高置信度明细上限（0 或负数 = 不限制）。"""
        try:
            return int(_cfg("MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS", 10))
        except (TypeError, ValueError):
            return 10

    def analyze_query_results(
        self,
        query_results: List[Dict[str, Any]],
        text_field: str = "content",
        min_confidence: float = 0.5,
    ) -> Dict[str, Any]:
        """
        对查询结果进行情感分析
        专门用于分析从ProductReviewDB返回的查询结果

        Args:
            query_results: 查询结果列表，每个元素包含文本内容
            text_field: 文本内容字段名，默认为"content"
            min_confidence: 最小置信度阈值

        Returns:
            包含情感分析结果的字典
        """
        if not query_results:
            return {
                "sentiment_analysis": {
                    "total_analyzed": 0,
                    "sentiment_distribution": {},
                    "high_confidence_results": [],
                    "summary": "没有内容需要分析",
                }
            }

        # 提取文本内容
        texts_to_analyze = []
        original_data = []

        for item in query_results:
            # 尝试多个可能的文本字段
            text_content = ""
            for field in [text_field, "title_or_content", "content", "title", "text"]:
                if field in item and item[field]:
                    text_content = str(item[field])
                    break

            if text_content.strip():
                texts_to_analyze.append(text_content)
                original_data.append(item)

        if not texts_to_analyze:
            return {
                "sentiment_analysis": {
                    "total_analyzed": 0,
                    "sentiment_distribution": {},
                    "high_confidence_results": [],
                    "summary": "查询结果中没有找到可分析的文本内容",
                }
            }

        if self.is_disabled:
            return self._build_passthrough_analysis(
                original_data=original_data,
                reason=self.disable_reason or "情感分析模型不可用",
                texts=texts_to_analyze,
            )

        # 执行批量情感分析（真批处理 + 结果缓存）
        logger.info(f"正在对 {len(texts_to_analyze)} 条内容进行情感分析...")
        batch_result = self.analyze_batch(texts_to_analyze, show_progress=True)

        if not batch_result.analysis_performed:
            reason = self.disable_reason or "情感分析功能不可用"
            if batch_result.results:
                candidate_error = next(
                    (r.error_message for r in batch_result.results if r.error_message),
                    None,
                )
                if candidate_error:
                    reason = candidate_error
            return self._build_passthrough_analysis(
                original_data=original_data,
                reason=reason,
                texts=texts_to_analyze,
                results=batch_result.results,
            )

        # 统计情感分布
        sentiment_distribution: Dict[str, int] = {}
        high_confidence_results: List[Dict[str, Any]] = []
        max_high = self._max_high_confidence()

        for result, original_item in zip(batch_result.results, original_data):
            if result.success:
                # 统计情感分布
                sentiment = result.sentiment_label
                sentiment_distribution[sentiment] = sentiment_distribution.get(sentiment, 0) + 1

                # 收集高置信度结果（限量 + 只带轻量字段，避免 prompt 膨胀）
                if result.confidence >= min_confidence and (max_high <= 0 or len(high_confidence_results) < max_high):
                    high_confidence_results.append(
                        {
                            "sentiment": result.sentiment_label,
                            "confidence": round(result.confidence, 4),
                            "text_preview": result.text[:100] + "..."
                            if len(result.text) > 100
                            else result.text,
                            "platform": original_item.get("platform"),
                            "url": original_item.get("url"),
                            "publish_time": original_item.get("publish_time"),
                        }
                    )

        # 生成情感分析摘要
        total_analyzed = batch_result.success_count
        if total_analyzed > 0:
            dominant_sentiment = max(sentiment_distribution.items(), key=lambda x: x[1])
            sentiment_summary = f"共分析{total_analyzed}条内容，主要情感倾向为'{dominant_sentiment[0]}'({dominant_sentiment[1]}条，占{dominant_sentiment[1] / total_analyzed * 100:.1f}%)"
        else:
            sentiment_summary = "情感分析失败"

        return {
            "sentiment_analysis": {
                "available": True,
                "model": self.loaded_model_name or self._model_name(),
                "total_analyzed": total_analyzed,
                "success_rate": f"{batch_result.success_count}/{batch_result.total_processed}",
                "average_confidence": round(batch_result.average_confidence, 4),
                "sentiment_distribution": sentiment_distribution,
                # 明细限量由 MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS 控制
                "high_confidence_results": high_confidence_results,
                "summary": sentiment_summary,
            }
        }

    def get_model_info(self) -> Dict[str, Any]:
        """
        获取模型信息

        Returns:
            模型信息字典
        """
        return {
            "model_name": "tabularisai/multilingual-sentiment-analysis",
            "supported_languages": [
                "中文",
                "英文",
                "西班牙文",
                "阿拉伯文",
                "日文",
                "韩文",
                "德文",
                "法文",
                "意大利文",
                "葡萄牙文",
                "俄文",
                "荷兰文",
                "波兰文",
                "土耳其文",
                "丹麦文",
                "希腊文",
                "芬兰文",
                "瑞典文",
                "挪威文",
                "匈牙利文",
                "捷克文",
                "保加利亚文",
            ],
            "sentiment_levels": list(self.sentiment_map.values()),
            "is_initialized": self.is_initialized,
            "device": str(self.device) if self.device else "未设置",
        }


# 创建全局实例（**不加载模型**；模型在首次真正使用或显式 warmup 时加载）
multilingual_sentiment_analyzer = WeiboMultilingualSentimentAnalyzer()


def warmup_sentiment_analyzer() -> bool:
    """串行预热情感模型，返回是否可用。

    建议在应用启动时由**单线程**调用一次：既能把 ~数秒的加载耗时从首次
    搜索里挪到启动阶段，也能彻底避开并发首次导入 transformers 的竞态。
    是否自动调用由配置 SENTIMENT_WARMUP_ON_STARTUP 控制（默认关闭）。
    """
    import time as _time

    t0 = _time.time()
    ok = multilingual_sentiment_analyzer.initialize()
    if ok:
        logger.info(f"情感分析模型预热完成（{_time.time() - t0:.2f}s）")
    else:
        logger.warning(
            "情感分析模型预热失败: "
            f"{multilingual_sentiment_analyzer.disable_reason or '未知原因'}"
        )
    return ok


def get_sentiment_analyzer() -> WeiboMultilingualSentimentAnalyzer:
    """获取全局情感分析器（不触发模型加载）。"""
    return multilingual_sentiment_analyzer


def analyze_sentiment(
    text_or_texts: Union[str, List[str]], initialize_if_needed: bool = True
) -> Union[SentimentResult, BatchSentimentResult]:
    """
    便捷的情感分析函数

    Args:
        text_or_texts: 单个文本或文本列表
        initialize_if_needed: 如果模型未初始化，是否自动初始化

    Returns:
        SentimentResult或BatchSentimentResult
    """
    # 同样不复用 is_disabled 短路：允许 initialize() 重新探测依赖后恢复。
    if initialize_if_needed and not multilingual_sentiment_analyzer.is_initialized:
        multilingual_sentiment_analyzer.initialize()

    if isinstance(text_or_texts, str):
        return multilingual_sentiment_analyzer.analyze_single_text(text_or_texts)
    else:
        texts_list = list(text_or_texts)
        return multilingual_sentiment_analyzer.analyze_batch(texts_list)


if __name__ == "__main__":
    # 测试代码
    import os
    os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
    analyzer = WeiboMultilingualSentimentAnalyzer()

    if analyzer.initialize():
        # 测试单个文本
        result = analyzer.analyze_single_text("今天天气真好，心情特别棒！")
        print(
            f"单个文本分析: {result.sentiment_label} (置信度: {result.confidence:.4f})"
        )

        # 测试批量文本
        test_texts = [
            "这家餐厅的菜味道非常棒！",
            "服务态度太差了，很失望",
            "I absolutely love this product!",
            "The customer service was disappointing.",
        ]

        batch_result = analyzer.analyze_batch(test_texts)
        print(
            f"\n批量分析: 成功 {batch_result.success_count}/{batch_result.total_processed}"
        )

        for result in batch_result.results:
            print(
                f"'{result.text[:30]}...' -> {result.sentiment_label} ({result.confidence:.4f})"
            )
    else:
        print("模型初始化失败，无法进行测试")
