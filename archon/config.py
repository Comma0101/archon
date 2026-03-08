"""Configuration loading from XDG directories and environment variables."""

import os
import tomllib
from pathlib import Path
from dataclasses import dataclass, field

from archon.calls.models import CallsConfig


def _xdg(env: str, default: str) -> Path:
    return Path(os.environ.get(env, os.path.expanduser(default)))


CONFIG_DIR = _xdg("XDG_CONFIG_HOME", "~/.config") / "archon"
DATA_DIR = _xdg("XDG_DATA_HOME", "~/.local/share") / "archon"
STATE_DIR = _xdg("XDG_STATE_HOME", "~/.local/state") / "archon"
CACHE_DIR = _xdg("XDG_CACHE_HOME", "~/.cache") / "archon"

MEMORY_DIR = DATA_DIR / "memory"
HISTORY_DIR = STATE_DIR / "history"
NEWS_STATE_DIR = STATE_DIR / "news"
NEWS_CACHE_DIR = CACHE_DIR / "news"
CALLS_STATE_DIR = STATE_DIR / "calls"
CALLS_MISSIONS_DIR = CALLS_STATE_DIR / "missions"
CALLS_EVENTS_DIR = CALLS_STATE_DIR / "events"


@dataclass
class LLMConfig:
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    api_key: str = ""
    base_url: str = ""
    fallback_provider: str = "openai"
    fallback_model: str = "gpt-4o"
    fallback_api_key: str = ""
    fallback_base_url: str = ""


@dataclass
class AgentConfig:
    max_iterations: int = 15
    temperature: float = 0.3
    llm_request_timeout_sec: float = 45.0
    llm_retry_attempts: int = 3
    # Prompt-budget guard: cap tool_result payload size before adding to history.
    tool_result_max_chars: int = 3000
    # Stricter cap for verbose worker/delegation tools.
    tool_result_worker_max_chars: int = 1500
    history_max_messages: int = 80
    history_trim_to_messages: int = 60
    # Approximate character budget for history payload (lightweight token proxy).
    history_max_chars: int = 48000
    history_trim_to_chars: int = 36000


@dataclass
class OrchestratorConfig:
    enabled: bool = False
    mode: str = "legacy"  # legacy | hybrid
    shadow_eval: bool = True
    default_profile: str = "default"


@dataclass
class ProfileConfig:
    allowed_tools: list[str] = field(default_factory=lambda: ["*"])
    max_mode: str = "implement"
    execution_backend: str = "host"
    skill: str = ""
    allowed_tools_explicit: bool = field(default=False, repr=False)
    max_mode_explicit: bool = field(default=False, repr=False)

    def __post_init__(self) -> None:
        cleaned_tools = [str(x).strip() for x in self.allowed_tools if str(x).strip()]
        self.allowed_tools = cleaned_tools or ["*"]
        self.max_mode = str(self.max_mode or "implement").strip().lower() or "implement"
        self.execution_backend = (
            str(self.execution_backend or "host").strip().lower() or "host"
        )
        self.skill = str(self.skill or "").strip().lower()

        if self.allowed_tools != ["*"]:
            self.allowed_tools_explicit = True
        if self.max_mode != "implement":
            self.max_mode_explicit = True


@dataclass
class SafetyConfig:
    default_action: str = "confirm"  # "allow" | "confirm" | "deny"
    permission_mode: str = "confirm_all"  # confirm_all | accept_reads | auto


@dataclass
class TelegramConfig:
    enabled: bool = False
    connect_on_chat: bool = False
    token: str = ""
    allowed_user_ids: list[int] = field(default_factory=list)
    poll_timeout_sec: int = 30


@dataclass
class WebConfig:
    enabled: bool = True
    provider: str = "auto"  # auto | duckduckgo_html | searxng | brave
    max_results: int = 5
    timeout_sec: int = 15
    user_agent: str = "Archon-Web/0.1"
    searxng_base_url: str = ""
    brave_api_key: str = ""


@dataclass
class MCPServerConfig:
    enabled: bool = False
    mode: str = "read_only"  # read_only | read_write
    transport: str = "stdio"
    command: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)


@dataclass
class MCPConfig:
    result_max_chars: int = 2000
    servers: dict[str, MCPServerConfig] = field(default_factory=dict)


@dataclass
class GoogleDeepResearchConfig:
    enabled: bool = True
    agent: str = "deep-research-pro-preview-12-2025"
    timeout_minutes: int = 20
    poll_interval_sec: int = 10
    thinking_summaries: str = "auto"


@dataclass
class ResearchConfig:
    google_deep_research: GoogleDeepResearchConfig = field(
        default_factory=GoogleDeepResearchConfig
    )


@dataclass
class NewsScheduleConfig:
    run_after_hour_local: int = 8


@dataclass
class NewsLLMConfig:
    retries: int = 3
    retry_delay_sec: float = 4.0
    timeout_sec: int = 90


@dataclass
class NewsSourcesConfig:
    hacker_news: bool = True
    github: bool = True
    huggingface: bool = True
    reddit_localllama: bool = True


@dataclass
class NewsTelegramConfig:
    send_enabled: bool = False
    chat_ids: list[int] = field(default_factory=list)


@dataclass
class NewsConfig:
    enabled: bool = False
    max_items: int = 12
    prefilter_cap: int = 30
    min_hn_score: int = 10
    min_github_stars: int = 50
    min_reddit_score: int = 10
    keywords: list[str] = field(default_factory=list)
    blocklist: list[str] = field(default_factory=list)
    schedule: NewsScheduleConfig = field(default_factory=NewsScheduleConfig)
    llm: NewsLLMConfig = field(default_factory=NewsLLMConfig)
    sources: NewsSourcesConfig = field(default_factory=NewsSourcesConfig)
    telegram: NewsTelegramConfig = field(default_factory=NewsTelegramConfig)


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    agent: AgentConfig = field(default_factory=AgentConfig)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    profiles: dict[str, ProfileConfig] = field(
        default_factory=lambda: {"default": ProfileConfig()}
    )
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    web: WebConfig = field(default_factory=WebConfig)
    mcp: MCPConfig = field(default_factory=MCPConfig)
    research: ResearchConfig = field(default_factory=ResearchConfig)
    news: NewsConfig = field(default_factory=NewsConfig)
    calls: CallsConfig = field(default_factory=CallsConfig)


def load_config() -> Config:
    """Load config from TOML file and environment variables."""
    cfg = Config()
    config_file = CONFIG_DIR / "config.toml"

    if config_file.exists():
        with open(config_file, "rb") as f:
            data = tomllib.load(f)

        llm = data.get("llm", {})
        cfg.llm.provider = llm.get("provider", cfg.llm.provider)
        cfg.llm.model = llm.get("model", cfg.llm.model)
        cfg.llm.api_key = llm.get("api_key", "")
        cfg.llm.base_url = llm.get("base_url", "")

        fallback = llm.get("fallback", {})
        cfg.llm.fallback_provider = fallback.get("provider", cfg.llm.fallback_provider)
        cfg.llm.fallback_model = fallback.get("model", cfg.llm.fallback_model)
        cfg.llm.fallback_api_key = fallback.get("api_key", "")
        cfg.llm.fallback_base_url = fallback.get("base_url", "")

        agent = data.get("agent", {})
        cfg.agent.max_iterations = max(
            1,
            int(agent.get("max_iterations", cfg.agent.max_iterations)),
        )
        cfg.agent.temperature = agent.get("temperature", cfg.agent.temperature)
        cfg.agent.llm_request_timeout_sec = float(
            agent.get("llm_request_timeout_sec", cfg.agent.llm_request_timeout_sec)
        )
        cfg.agent.llm_retry_attempts = int(
            agent.get("llm_retry_attempts", cfg.agent.llm_retry_attempts)
        )
        cfg.agent.tool_result_max_chars = int(
            agent.get("tool_result_max_chars", cfg.agent.tool_result_max_chars)
        )
        cfg.agent.tool_result_worker_max_chars = int(
            agent.get(
                "tool_result_worker_max_chars",
                cfg.agent.tool_result_worker_max_chars,
            )
        )
        cfg.agent.history_max_messages = int(
            agent.get("history_max_messages", cfg.agent.history_max_messages)
        )
        cfg.agent.history_trim_to_messages = int(
            agent.get("history_trim_to_messages", cfg.agent.history_trim_to_messages)
        )
        cfg.agent.history_max_chars = int(
            agent.get("history_max_chars", cfg.agent.history_max_chars)
        )
        cfg.agent.history_trim_to_chars = int(
            agent.get("history_trim_to_chars", cfg.agent.history_trim_to_chars)
        )

        orchestrator = data.get("orchestrator", {})
        cfg.orchestrator.enabled = bool(
            orchestrator.get("enabled", cfg.orchestrator.enabled)
        )
        mode = str(orchestrator.get("mode", cfg.orchestrator.mode)).strip().lower()
        if mode in {"legacy", "hybrid"}:
            cfg.orchestrator.mode = mode
        cfg.orchestrator.shadow_eval = bool(
            orchestrator.get("shadow_eval", cfg.orchestrator.shadow_eval)
        )
        default_profile = str(
            orchestrator.get("default_profile", cfg.orchestrator.default_profile)
        ).strip()
        cfg.orchestrator.default_profile = default_profile or "default"

        profiles_raw = data.get("profiles", {})
        if isinstance(profiles_raw, dict):
            parsed_profiles: dict[str, ProfileConfig] = {}
            for profile_name, profile_value in profiles_raw.items():
                if not isinstance(profile_name, str) or not isinstance(profile_value, dict):
                    continue
                profile = ProfileConfig()
                allowed_tools = profile_value.get("allowed_tools", profile.allowed_tools)
                if isinstance(allowed_tools, list):
                    cleaned = [str(x).strip() for x in allowed_tools if str(x).strip()]
                    profile.allowed_tools = cleaned or ["*"]
                    profile.allowed_tools_explicit = True
                profile.max_mode = str(profile_value.get("max_mode", profile.max_mode)).strip().lower()
                if "max_mode" in profile_value:
                    profile.max_mode_explicit = True
                execution_backend = str(
                    profile_value.get("execution_backend", profile.execution_backend)
                ).strip().lower()
                if execution_backend != "host":
                    raise ValueError(
                        f"Unsupported profile execution_backend '{execution_backend}'"
                    )
                profile.execution_backend = execution_backend
                profile.skill = str(profile_value.get("skill", profile.skill)).strip().lower()
                parsed_profiles[profile_name.strip()] = profile
            if parsed_profiles:
                if "default" not in parsed_profiles:
                    parsed_profiles["default"] = ProfileConfig()
                cfg.profiles = parsed_profiles

        safety = data.get("safety", {})
        cfg.safety.default_action = safety.get("default_action", cfg.safety.default_action)
        permission_mode = str(
            safety.get("permission_mode", cfg.safety.permission_mode)
        ).strip().lower()
        if permission_mode in {"confirm_all", "accept_reads", "auto"}:
            cfg.safety.permission_mode = permission_mode

        telegram = data.get("telegram", {})
        cfg.telegram.enabled = bool(telegram.get("enabled", cfg.telegram.enabled))
        cfg.telegram.connect_on_chat = bool(
            telegram.get("connect_on_chat", cfg.telegram.connect_on_chat)
        )
        cfg.telegram.token = telegram.get("token", cfg.telegram.token)
        allowed = telegram.get("allowed_user_ids", cfg.telegram.allowed_user_ids)
        if isinstance(allowed, list):
            cfg.telegram.allowed_user_ids = [int(x) for x in allowed]
        cfg.telegram.poll_timeout_sec = int(
            telegram.get("poll_timeout_sec", cfg.telegram.poll_timeout_sec)
        )

        web = data.get("web", {})
        cfg.web.enabled = bool(web.get("enabled", cfg.web.enabled))
        cfg.web.provider = str(web.get("provider", cfg.web.provider))
        cfg.web.max_results = int(web.get("max_results", cfg.web.max_results))
        cfg.web.timeout_sec = int(web.get("timeout_sec", cfg.web.timeout_sec))
        cfg.web.user_agent = str(web.get("user_agent", cfg.web.user_agent))
        cfg.web.searxng_base_url = str(
            web.get("searxng_base_url", cfg.web.searxng_base_url)
        )
        cfg.web.brave_api_key = str(
            web.get("brave_api_key", cfg.web.brave_api_key)
        )

        mcp = data.get("mcp", {})
        cfg.mcp.result_max_chars = int(
            mcp.get("result_max_chars", cfg.mcp.result_max_chars)
        )
        servers_raw = mcp.get("servers", {})
        if isinstance(servers_raw, dict):
            parsed_servers: dict[str, MCPServerConfig] = {}
            for server_name, server_value in servers_raw.items():
                if not isinstance(server_name, str) or not isinstance(server_value, dict):
                    continue
                command = server_value.get("command", [])
                env_map = server_value.get("env", {})
                normalized_name = server_name.strip().lower()
                if not normalized_name:
                    continue
                parsed_servers[normalized_name] = MCPServerConfig(
                    enabled=bool(server_value.get("enabled", False)),
                    mode=str(server_value.get("mode", "read_only")).strip().lower() or "read_only",
                    transport=str(server_value.get("transport", "stdio")).strip().lower() or "stdio",
                    command=[str(item).strip() for item in command if str(item).strip()]
                    if isinstance(command, list)
                    else [],
                    env=_resolve_mcp_env_map(env_map) if isinstance(env_map, dict) else {},
                )
            cfg.mcp.servers = parsed_servers

        research = data.get("research", {})
        deep_research = research.get("google_deep_research", {})
        cfg.research.google_deep_research.enabled = bool(
            deep_research.get(
                "enabled",
                cfg.research.google_deep_research.enabled,
            )
        )
        cfg.research.google_deep_research.agent = (
            str(
                deep_research.get(
                    "agent",
                    cfg.research.google_deep_research.agent,
                )
            ).strip()
            or cfg.research.google_deep_research.agent
        )
        cfg.research.google_deep_research.timeout_minutes = max(
            1,
            int(
                deep_research.get(
                    "timeout_minutes",
                    cfg.research.google_deep_research.timeout_minutes,
                )
            ),
        )
        cfg.research.google_deep_research.poll_interval_sec = max(
            1,
            int(
                deep_research.get(
                    "poll_interval_sec",
                    cfg.research.google_deep_research.poll_interval_sec,
                )
            ),
        )
        cfg.research.google_deep_research.thinking_summaries = (
            str(
                deep_research.get(
                    "thinking_summaries",
                    cfg.research.google_deep_research.thinking_summaries,
                )
            ).strip().lower()
            or cfg.research.google_deep_research.thinking_summaries
        )

        news = data.get("news", {})
        cfg.news.enabled = bool(news.get("enabled", cfg.news.enabled))
        cfg.news.max_items = int(news.get("max_items", cfg.news.max_items))
        cfg.news.prefilter_cap = int(news.get("prefilter_cap", cfg.news.prefilter_cap))
        cfg.news.min_hn_score = int(news.get("min_hn_score", cfg.news.min_hn_score))
        cfg.news.min_github_stars = int(
            news.get("min_github_stars", cfg.news.min_github_stars)
        )
        cfg.news.min_reddit_score = int(
            news.get("min_reddit_score", cfg.news.min_reddit_score)
        )

        keywords = news.get("keywords", cfg.news.keywords)
        if isinstance(keywords, list):
            cfg.news.keywords = [str(x) for x in keywords]
        blocklist = news.get("blocklist", cfg.news.blocklist)
        if isinstance(blocklist, list):
            cfg.news.blocklist = [str(x) for x in blocklist]

        news_schedule = news.get("schedule", {})
        cfg.news.schedule.run_after_hour_local = int(
            news_schedule.get(
                "run_after_hour_local",
                cfg.news.schedule.run_after_hour_local,
            )
        )

        news_llm = news.get("llm", {})
        cfg.news.llm.retries = int(news_llm.get("retries", cfg.news.llm.retries))
        cfg.news.llm.retry_delay_sec = float(
            news_llm.get("retry_delay_sec", cfg.news.llm.retry_delay_sec)
        )
        cfg.news.llm.timeout_sec = int(
            news_llm.get("timeout_sec", cfg.news.llm.timeout_sec)
        )

        news_sources = news.get("sources", {})
        cfg.news.sources.hacker_news = bool(
            news_sources.get("hacker_news", cfg.news.sources.hacker_news)
        )
        cfg.news.sources.github = bool(
            news_sources.get("github", cfg.news.sources.github)
        )
        cfg.news.sources.huggingface = bool(
            news_sources.get("huggingface", cfg.news.sources.huggingface)
        )
        cfg.news.sources.reddit_localllama = bool(
            news_sources.get(
                "reddit_localllama",
                cfg.news.sources.reddit_localllama,
            )
        )

        news_telegram = news.get("telegram", {})
        cfg.news.telegram.send_enabled = bool(
            news_telegram.get("send_enabled", cfg.news.telegram.send_enabled)
        )
        chat_ids = news_telegram.get("chat_ids", cfg.news.telegram.chat_ids)
        if isinstance(chat_ids, list):
            cfg.news.telegram.chat_ids = [int(x) for x in chat_ids]

        calls = data.get("calls", {})
        cfg.calls.enabled = bool(calls.get("enabled", cfg.calls.enabled))

        voice_service = calls.get("voice_service", {})
        cfg.calls.voice_service.mode = str(
            voice_service.get("mode", cfg.calls.voice_service.mode)
        )
        cfg.calls.voice_service.base_url = str(
            voice_service.get("base_url", cfg.calls.voice_service.base_url)
        )
        cfg.calls.voice_service.systemd_unit = str(
            voice_service.get("systemd_unit", cfg.calls.voice_service.systemd_unit)
        )

        realtime = calls.get("realtime", {})
        cfg.calls.realtime.enabled = bool(realtime.get("enabled", cfg.calls.realtime.enabled))
        cfg.calls.realtime.provider = str(
            realtime.get("provider", cfg.calls.realtime.provider)
        )

        twilio_calls = calls.get("twilio", {})
        cfg.calls.twilio.account_sid = str(
            twilio_calls.get("account_sid", cfg.calls.twilio.account_sid)
        )
        cfg.calls.twilio.auth_token = str(
            twilio_calls.get("auth_token", cfg.calls.twilio.auth_token)
        )
        cfg.calls.twilio.from_number = str(
            twilio_calls.get("from_number", cfg.calls.twilio.from_number)
        )
        cfg.calls.twilio.status_callback_url = str(
            twilio_calls.get(
                "status_callback_url",
                cfg.calls.twilio.status_callback_url,
            )
        )

    # Environment variables override file config
    if key := os.environ.get("ANTHROPIC_API_KEY"):
        if cfg.llm.provider == "anthropic":
            cfg.llm.api_key = key
    if key := os.environ.get("OPENAI_API_KEY"):
        cfg.llm.fallback_api_key = key
    if key := os.environ.get("GEMINI_API_KEY"):
        if cfg.llm.provider == "google" or (cfg.llm.provider == "openai" and "googleapis" in cfg.llm.base_url):
            cfg.llm.api_key = key
    if key := os.environ.get("TELEGRAM_BOT_TOKEN"):
        cfg.telegram.token = key
    if users := os.environ.get("TELEGRAM_ALLOWED_USER_IDS"):
        cfg.telegram.allowed_user_ids = [
            int(part.strip()) for part in users.split(",") if part.strip()
        ]
    if provider := os.environ.get("ARCHON_WEB_PROVIDER"):
        cfg.web.provider = provider.strip()
    if searxng := os.environ.get("SEARXNG_BASE_URL"):
        cfg.web.searxng_base_url = searxng.strip()
    if brave_key := os.environ.get("BRAVE_SEARCH_API_KEY"):
        cfg.web.brave_api_key = brave_key.strip()

    return cfg


def _resolve_mcp_env_map(raw_env: dict) -> dict[str, str]:
    resolved: dict[str, str] = {}
    for key, value in raw_env.items():
        env_key = str(key or "").strip()
        if not env_key:
            continue
        resolved[env_key] = _resolve_mcp_env_value(value)
    return resolved


def _resolve_mcp_env_value(value: object) -> str:
    text = str(value or "")
    if text.startswith("${") and text.endswith("}") and len(text) > 3:
        env_name = text[2:-1].strip()
        if not env_name:
            return ""
        return str(os.environ.get(env_name, ""))
    return text


def ensure_dirs():
    """Create all XDG directories if they don't exist."""
    for d in [
        CONFIG_DIR,
        MEMORY_DIR,
        HISTORY_DIR,
        CACHE_DIR,
        NEWS_STATE_DIR,
        NEWS_CACHE_DIR,
        CALLS_MISSIONS_DIR,
        CALLS_EVENTS_DIR,
    ]:
        d.mkdir(parents=True, exist_ok=True)
