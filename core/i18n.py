"""
core/i18n.py
Lightweight internationalization support.
Usage:
    from core.i18n import t
    t("task.completed")  # returns localized string

Locale detection: SWARM_LANG env var → system locale → "en"
"""

from __future__ import annotations
import os
import locale

# ── Locale detection ─────────────────────────────────────────────────────────

def _detect_locale() -> str:
    """Detect locale from env or system. Returns 'en' or 'zh'."""
    lang = os.environ.get("SWARM_LANG", "")
    if lang:
        return "zh" if lang.startswith("zh") else "en"
    try:
        sys_locale = locale.getdefaultlocale()[0] or ""
        if sys_locale.startswith("zh"):
            return "zh"
    except Exception:
        pass
    return "en"


_locale = _detect_locale()

# ── Translation tables ───────────────────────────────────────────────────────

_STRINGS = {
    # Task statuses
    "status.pending":     {"en": "Waiting…",      "zh": "等待中…"},
    "status.working":     {"en": "Working…",       "zh": "处理中…"},
    "status.review":      {"en": "Reviewing…",     "zh": "审查中…"},
    "status.done":        {"en": "Done",           "zh": "完成"},
    "status.failed":      {"en": "Failed",         "zh": "失败"},
    "status.cancelled":   {"en": "Cancelled",      "zh": "已取消"},
    "status.paused":      {"en": "Paused",         "zh": "已暂停"},

    # Summary
    "summary.done":       {"en": "Done",           "zh": "完成"},
    "summary.finished":   {"en": "Finished",       "zh": "结束"},
    "summary.working":    {"en": "working",        "zh": "进行中"},
    "summary.failed":     {"en": "failed",         "zh": "失败"},
    "summary.cancelled":  {"en": "cancelled",      "zh": "已取消"},
    "summary.elapsed":    {"en": "elapsed",        "zh": "耗时"},

    # Errors
    "error.api_key":      {"en": "Invalid API Key (401)", "zh": "API Key 无效 (401)"},
    "error.forbidden":    {"en": "Forbidden (403)",       "zh": "权限不足 (403)"},
    "error.rate_limit":   {"en": "Rate limited (429)",    "zh": "请求过多 (429)"},
    "error.timeout":      {"en": "Request timed out",     "zh": "请求超时"},
    "error.connect":      {"en": "Cannot connect to API", "zh": "无法连接 API"},
    "error.exec_failed":  {"en": "Execution failed",      "zh": "执行失败"},

    # Commands
    "cmd.cleared":        {"en": "Cleared",        "zh": "已清除"},
    "cmd.cancelled":      {"en": "Cancelled",      "zh": "已取消"},
    "cmd.bye":            {"en": "Bye!",           "zh": "再见！"},
    "cmd.no_tasks":       {"en": "No tasks yet.",  "zh": "暂无任务。"},
    "cmd.active_exist":   {"en": "Active tasks exist. Force clear?",
                           "zh": "有进行中的任务，确认清除？"},

    # Budget
    "budget.not_set":     {"en": "Budget: not configured",
                           "zh": "预算：未配置"},
    "budget.warning":     {"en": "Budget warning",
                           "zh": "预算预警"},
    "budget.exceeded":    {"en": "Budget exceeded",
                           "zh": "预算已超出"},
}


def t(key: str, **kwargs) -> str:
    """Translate a key to the current locale."""
    entry = _STRINGS.get(key)
    if not entry:
        return key
    text = entry.get(_locale, entry.get("en", key))
    if kwargs:
        text = text.format(**kwargs)
    return text


def set_locale(lang: str):
    """Override locale at runtime."""
    global _locale
    _locale = "zh" if lang.startswith("zh") else "en"


def get_locale() -> str:
    """Get current locale."""
    return _locale
