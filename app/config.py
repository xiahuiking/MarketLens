# -*- coding: utf-8 -*-
"""
MarketLens 电商商品评论竞品分析平台配置

此模块使用 pydantic-settings 管理全局配置，支持从环境变量和 .env 文件自动加载。
数据模型定义位置：
- 本文件 - 配置模型定义
"""

from pathlib import Path
from pydantic_settings import BaseSettings
from pydantic import Field, ConfigDict
from typing import Optional, Literal
from loguru import logger


# .env 文件固定在项目根目录（与 app.py 同级），不依赖运行时 cwd
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
ENV_FILE: str = str(PROJECT_ROOT / ".env")


class Settings(BaseSettings):
    """
    全局配置；支持 .env 和环境变量自动加载。
    变量名与原 config.py 大写一致，便于平滑过渡。
    """
    # ================== 服务器配置 ====================
    HOST: str = Field("0.0.0.0", description="MarketLens 主机地址，例如 0.0.0.0 或 127.0.0.1")
    PORT: int = Field(5000, description="服务器端口号，默认5000")
    LOG_LEVEL:str = Field("INFO",description="日志等级")

    # ====================== 数据库配置 ======================
    DB_DIALECT: str = Field("postgresql", description="数据库类型，可选 mysql 或 postgresql；请与其他连接信息同时配置")
    DB_HOST: str = Field("your_db_host", description="数据库主机，例如localhost 或 127.0.0.1")
    DB_PORT: int = Field(3306, description="数据库端口号，默认为3306")
    DB_USER: str = Field("your_db_user", description="数据库用户名")
    DB_PASSWORD: str = Field("your_db_password", description="数据库密码")
    DB_NAME: str = Field("your_db_name", description="数据库名称")
    DB_CHARSET: str = Field("utf8mb4", description="数据库字符集，推荐utf8mb4，兼容emoji")
    
    # ======================= LLM 相关 =======================
    
    # 口碑 Agent（ReviewEngine，推荐Kimi，申请地址：https://platform.moonshot.cn/）
    REVIEW_ENGINE_API_KEY: Optional[str] = Field(None, description="口碑 Agent（推荐 kimi-k2）API 密钥，用于主 LLM。")
    REVIEW_ENGINE_BASE_URL: Optional[str] = Field("https://api.moonshot.cn/v1", description="口碑 Agent LLM BaseUrl，可根据厂商自定义")
    REVIEW_ENGINE_MODEL_NAME: str = Field("kimi-k2-0711-preview", description="口碑 Agent LLM 模型名称，例如 kimi-k2-0711-preview")
    
    # 竞品 Agent（CompetitorEngine，推荐Gemini）
    COMPETITOR_ENGINE_API_KEY: Optional[str] = Field(None, description="竞品 Agent（推荐 gemini-2.5-pro）API 密钥")
    COMPETITOR_ENGINE_BASE_URL: Optional[str] = Field("https://aihubmix.com/v1", description="竞品 Agent LLM BaseUrl，可根据中转服务调整")
    COMPETITOR_ENGINE_MODEL_NAME: str = Field("gemini-2.5-pro", description="竞品 Agent LLM 模型名称，如 gemini-2.5-pro")
    
    # 趋势 Agent（TrendEngine，推荐DeepSeek，申请地址：https://www.deepseek.com/）
    TREND_ENGINE_API_KEY: Optional[str] = Field(None, description="趋势 Agent（推荐 deepseek）API 密钥")
    TREND_ENGINE_BASE_URL: Optional[str] = Field("https://api.deepseek.com", description="趋势 Agent LLM BaseUrl")
    TREND_ENGINE_MODEL_NAME: str = Field("deepseek-chat", description="趋势 Agent LLM 模型名称，如 deepseek-reasoner")
    
    # Report Agent（推荐Gemini）
    REPORT_ENGINE_API_KEY: Optional[str] = Field(None, description="Report Agent（推荐 gemini-2.5-pro）API 密钥")
    REPORT_ENGINE_BASE_URL: Optional[str] = Field("https://aihubmix.com/v1", description="Report Agent LLM BaseUrl，可根据中转服务调整")
    REPORT_ENGINE_MODEL_NAME: str = Field("gemini-2.5-pro", description="Report Agent LLM 模型名称，如 gemini-2.5-pro")

    # Forum Host（Qwen3最新模型，这里我使用了硅基流动这个平台，申请地址：https://cloud.siliconflow.cn/）
    FORUM_HOST_API_KEY: Optional[str] = Field(None, description="Forum Host（推荐 qwen-plus）API 密钥")
    FORUM_HOST_BASE_URL: Optional[str] = Field(None, description="Forum Host LLM BaseUrl，可按所选服务配置")
    FORUM_HOST_MODEL_NAME: Optional[str] = Field(None, description="Forum Host LLM 模型名称，例如 qwen-plus")
    
    # SQL keyword Optimizer（小参数Qwen3模型，这里我使用了硅基流动这个平台，申请地址：https://cloud.siliconflow.cn/）
    KEYWORD_OPTIMIZER_API_KEY: Optional[str] = Field(None, description="SQL Keyword Optimizer（推荐 qwen-plus）API 密钥")
    KEYWORD_OPTIMIZER_BASE_URL: Optional[str] = Field(None, description="Keyword Optimizer BaseUrl，可按所选服务配置")
    KEYWORD_OPTIMIZER_MODEL_NAME: Optional[str] = Field(None, description="Keyword Optimizer LLM 模型名称，例如 qwen-plus")
    
    # ================== 网络工具配置 ====================
    # Tavily API（申请地址：https://www.tavily.com/）
    TAVILY_API_KEY: Optional[str] = Field(None, description="Tavily API（申请地址：https://www.tavily.com/）API密钥，用于Tavily网络搜索")

    SEARCH_TOOL_TYPE: Literal["TavilyAPI", "AnspireAPI", "BochaAPI"] = Field("TavilyAPI", description="网络搜索工具类型，支持TavilyAPI/AnspireAPI/BochaAPI，默认为TavilyAPI")
    # Bocha API（可选，TavilyAPI模式下不需要）
    BOCHA_BASE_URL: Optional[str] = Field("https://api.bocha.cn/v1/ai-search", description="Bocha AI 搜索BaseUrl")
    BOCHA_WEB_SEARCH_API_KEY: Optional[str] = Field(None, description="Bocha API密钥（可选）")

    # Anspire AI Search API（可选，TavilyAPI模式下不需要）
    ANSPIRE_BASE_URL: Optional[str] = Field("https://plugin.anspire.cn/api/ntsearch/search", description="Anspire AI 搜索BaseUrl")
    ANSPIRE_API_KEY: Optional[str] = Field(None, description="Anspire AI Search API密钥（可选）")

    
    # ================== 口碑 Engine（ReviewEngine）搜索配置 ====================
    # 聚类
    ENABLE_CLUSTERING: bool = Field(True, description="是否启用搜索结果聚类")
    MAX_CLUSTERED_RESULTS: int = Field(50, description="聚类后最大结果数")
    RESULTS_PER_CLUSTER: int = Field(5, description="每个聚类采样数")
    # 情感分析
    SENTIMENT_ANALYSIS_ENABLED: bool = Field(True, description="是否启用情感分析（全局开关）")
    ENABLE_SENTIMENT_PER_SEARCH: bool = Field(True, description="每次搜索默认是否对结果做情感分析（可由 LLM 参数覆盖）")
    # 情感分析模型（HuggingFace 模型名或本地路径）
    SENTIMENT_MODEL_NAME: str = Field("tabularisai/multilingual-sentiment-analysis", description="情感分析模型名（HuggingFace 或本地路径）")
    # 批量推理：一次前向处理的文本条数（实测 bs=32 比逐条快约 4-5 倍，越大越省时间但显存/内存占用越高）
    SENTIMENT_BATCH_SIZE: int = Field(32, description="情感分析批量推理的批大小")
    # 单条文本最大 token 数（评论通常很短，调小可显著减少 padding 浪费）
    SENTIMENT_MAX_LENGTH: int = Field(512, description="情感分析单条文本最大 token 数")
    # 结果缓存条数（同一次运行内评论会跨搜索/反思重复出现，缓存可直接省掉重复前向；0 = 关闭）
    SENTIMENT_CACHE_SIZE: int = Field(5000, description="情感分析结果 LRU 缓存条数，0 表示关闭缓存")
    # 启动时是否串行预热情感模型（默认关闭以免拖慢启动；开启可彻底避免并发导入竞态）
    SENTIMENT_WARMUP_ON_STARTUP: bool = Field(False, description="服务启动时是否同步预热情感分析模型")
    # 聚类模型（sentence-transformers 模型名）
    CLUSTERING_MODEL_NAME: str = Field("paraphrase-multilingual-MiniLM-L12-v2", description="聚类嵌入模型名（sentence-transformers）")
    # 搜索
    DEFAULT_SEARCH_HOT_CONTENT_LIMIT: int = Field(100, description="热榜内容默认最大数")
    DEFAULT_SEARCH_TOPIC_GLOBALLY_LIMIT_PER_TABLE: int = Field(50, description="按表全局话题最大数")
    DEFAULT_SEARCH_TOPIC_BY_DATE_LIMIT_PER_TABLE: int = Field(100, description="按日期话题最大数")
    DEFAULT_GET_COMMENTS_FOR_TOPIC_LIMIT: int = Field(500, description="单话题评论最大数")
    DEFAULT_SEARCH_TOPIC_ON_PLATFORM_LIMIT: int = Field(200, description="平台搜索话题最大数")
    MAX_SEARCH_RESULTS_FOR_LLM: int = Field(0, description="供LLM用搜索结果最大数")
    MAX_HIGH_CONFIDENCE_SENTIMENT_RESULTS: int = Field(10, description="写入 LLM prompt 的高置信度情感明细最大条数（0 = 不限制，不推荐）")
    MAX_REFLECTIONS: int = Field(3, description="最大反思次数")
    MAX_PARAGRAPHS: int = Field(6, description="最大段落数")
    SEARCH_TIMEOUT: int = Field(240, description="单次搜索请求超时")
    MAX_CONTENT_LENGTH: int = Field(500000, description="搜索最大内容长度（ReviewEngine/ReportEngine 使用）")
    SEARCH_CONTENT_MAX_LENGTH: int = Field(500000, description="搜索最大内容长度（CompetitorEngine/TrendEngine 使用）")
    SAVE_INTERMEDIATE_STATES: bool = Field(True, description="是否保存中间状态")
    MAX_SEARCH_RESULTS: int = Field(20, description="最大搜索结果数（TrendEngine）")
    # Bocha 兼容键（别名）
    BOCHA_API_KEY: Optional[str] = Field(None, description="Bocha 兼容键（别名）")
    
    # 只有配置了这个属性，才能够自动加载.env文件
    model_config = ConfigDict(
        env_file=ENV_FILE,
        env_prefix="",
        case_sensitive=False,
        extra="allow"
    )


# 创建全局配置实例
settings = Settings()


def reload_settings() -> Settings:
    """
    重新加载配置
    
    从 .env 文件和环境变量重新加载配置，更新全局 settings 实例。
    用于在运行时动态更新配置。
    
    Returns:
        Settings: 新创建的配置实例
    """
    import os
    
    global settings
    # 先将 CONFIG_KEYS 对应的环境变量从 os.environ 中清除，
    # 防止 pydantic-settings 优先读旧的 os.environ 值而忽略 .env 文件的修改
    _keys_to_clear = [
        'HOST', 'PORT', 'DB_DIALECT', 'DB_HOST', 'DB_PORT', 'DB_USER',
        'DB_PASSWORD', 'DB_NAME', 'DB_CHARSET',
        'REVIEW_ENGINE_API_KEY', 'REVIEW_ENGINE_BASE_URL', 'REVIEW_ENGINE_MODEL_NAME',
        'COMPETITOR_ENGINE_API_KEY', 'COMPETITOR_ENGINE_BASE_URL', 'COMPETITOR_ENGINE_MODEL_NAME',
        'TREND_ENGINE_API_KEY', 'TREND_ENGINE_BASE_URL', 'TREND_ENGINE_MODEL_NAME',
        'REPORT_ENGINE_API_KEY', 'REPORT_ENGINE_BASE_URL', 'REPORT_ENGINE_MODEL_NAME',
        'FORUM_HOST_API_KEY', 'FORUM_HOST_BASE_URL', 'FORUM_HOST_MODEL_NAME',
        'KEYWORD_OPTIMIZER_API_KEY', 'KEYWORD_OPTIMIZER_BASE_URL', 'KEYWORD_OPTIMIZER_MODEL_NAME',
        'TAVILY_API_KEY', 'SEARCH_TOOL_TYPE', 'BOCHA_WEB_SEARCH_API_KEY', 'ANSPIRE_API_KEY',
    ]
    for k in _keys_to_clear:
        os.environ.pop(k, None)
    
    settings = Settings()
    return settings
