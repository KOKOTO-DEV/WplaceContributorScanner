from __future__ import annotations

import locale
import os
from typing import Any

SUPPORTED_CONSOLE_LANGUAGES = {"ko", "en", "ja", "zh-CN"}


def normalize_console_language(value: str | None) -> str:
    text = str(value or "").strip().replace("_", "-").lower()
    if text.startswith("ko"):
        return "ko"
    if text.startswith("ja"):
        return "ja"
    if text.startswith("zh"):
        return "zh-CN"
    return "en"


def detect_console_language(explicit: str | None = None) -> str:
    if explicit:
        return normalize_console_language(explicit)
    env_value = os.environ.get("WPCS_LANG")
    if env_value:
        return normalize_console_language(env_value)
    for key in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(key)
        if value:
            return normalize_console_language(value)
    try:
        value = locale.getlocale()[0] or locale.getdefaultlocale()[0]  # type: ignore[attr-defined]
    except Exception:
        value = None
    return normalize_console_language(value)


_TEXT = {
    "ko": {
        "description": "Wplace 작업 픽셀 통계 도구",
        "listening": "Wplace Contributor Scanner 실행 주소: {url}",
        "access": "접속 주소: http://<이 PC의 IP>:{port}/ (로컬: {local})",
        "warning": "주의: 로그인 기능이 없으므로 인터넷에 직접 공개하지 마세요.",
        "stop": "종료하려면 이 창에서 Ctrl+C를 누르세요.",
        "shutdown_pause": "프로그램 종료로 일시정지했습니다.",
    },
    "en": {
        "description": "Wplace work pixel statistics tool",
        "listening": "Wplace Contributor Scanner listening at: {url}",
        "access": "Access: http://<this PC's IP>:{port}/ (local: {local})",
        "warning": "Warning: There is no authentication. Do not expose this server directly to the internet.",
        "stop": "Press Ctrl+C in this window to stop.",
        "shutdown_pause": "Paused because the program is shutting down.",
    },
    "ja": {
        "description": "Wplace 作業ピクセル統計ツール",
        "listening": "Wplace Contributor Scanner の起動アドレス: {url}",
        "access": "接続先: http://<このPCのIP>:{port}/（ローカル: {local}）",
        "warning": "注意: 認証機能はありません。インターネットへ直接公開しないでください。",
        "stop": "終了するには、このウィンドウで Ctrl+C を押してください。",
        "shutdown_pause": "プログラム終了のため一時停止しました。",
    },
    "zh-CN": {
        "description": "Wplace 作业像素统计工具",
        "listening": "Wplace Contributor Scanner 启动地址：{url}",
        "access": "访问地址：http://<本机IP>:{port}/（本机：{local}）",
        "warning": "注意：此程序没有登录验证，请勿直接暴露到互联网。",
        "stop": "要停止程序，请在此窗口按 Ctrl+C。",
        "shutdown_pause": "程序正在退出，扫描已暂停。",
    },
}


def console_text(key: str, language: str | None = None, **values: Any) -> str:
    lang = normalize_console_language(language) if language else detect_console_language()
    text = _TEXT.get(lang, _TEXT["en"]).get(key, _TEXT["en"].get(key, key))
    return text.format(**values)
