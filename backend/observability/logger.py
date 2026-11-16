"""
구조화 로깅 (REQ-004)
structlog 기반 run_id 바인딩 로거.
ENV=dev  → ConsoleRenderer (컬러 출력)
ENV=prod → JSONRenderer (단일 라인 JSON)
"""

from __future__ import annotations

import functools
import os
import logging
from pathlib import Path
from datetime import datetime

try:
    import structlog
    from rich.logging import RichHandler
    from rich.console import Console
    from rich.theme import Theme

    # 전용 테마 설정
    custom_theme = Theme({
        "logging.level.info": "cyan",
        "logging.level.warning": "yellow",
        "logging.level.error": "bold red",
        "node.name": "bold magenta",
        "run.id": "dim white",
    })
    console = Console(theme=custom_theme)

    @functools.lru_cache(maxsize=None)
    def _ensure_configured() -> None:
        """프로세스 내 최초 1회 structlog 및 Rich 설정."""
        env = os.environ.get("ENV", "dev").lower()
        
        # 기본 로깅 핸들러 설정 (Rich)
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(console=console, rich_tracebacks=True, show_path=False)]
        )

        processors = [
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.processors.TimeStamper(fmt="%Y-%m-%d %H:%M:%S", utc=False),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
        ]

        if env == "dev":
            processors.append(structlog.dev.ConsoleRenderer(colors=True))
        else:
            processors.append(structlog.processors.JSONRenderer())
        
        structlog.configure(
            processors=processors,
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )

    def configure_logging() -> None:
        _ensure_configured()

    def get_logger(run_id: str = "", node_name: str = ""):
        """run_id 및 node_name이 자동 바인딩된 로거 반환."""
        _ensure_configured()
        logger = structlog.get_logger()
        if run_id:
            logger = logger.bind(run_id=run_id)
        if node_name:
            logger = logger.bind(node_name=node_name)
        return logger

    def setup_session_logger(run_id: str):
        """세션별 로그 파일을 생성하여 storage/logs에 기록."""
        if not run_id: return
        
        from pipeline.core.utils import get_storage_path
        log_dir = Path(get_storage_path("logs"))
        log_file = log_dir / f"{run_id}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"
        
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s: %(message)s'
        ))
        logging.getLogger().addHandler(file_handler)
        structlog.get_logger().info(f"Session logging started: {log_file}")

except ImportError:
    # structlog 미설치 시 표준 logging으로 폴백
    import logging as _logging

    @functools.lru_cache(maxsize=None)
    def _ensure_configured() -> None:
        _logging.basicConfig(
            level=_logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
        )

    def configure_logging() -> None:  # type: ignore[misc]
        _ensure_configured()

    class _FallbackLogger:
        def __init__(self, run_id: str = ""):
            self._log = _logging.getLogger("pipeline")
            self._run_id = run_id

        def _fmt(self, msg: str, **kw) -> str:
            parts = [msg]
            if self._run_id:
                parts.insert(0, f"run_id={self._run_id}")
            parts += [f"{k}={v}" for k, v in kw.items()]
            return " | ".join(parts)

        def info(self, msg: str, **kw):
            self._log.info(self._fmt(msg, **kw))

        def warning(self, msg: str, **kw):
            self._log.warning(self._fmt(msg, **kw))

        def error(self, msg: str, **kw):
            self._log.error(self._fmt(msg, **kw))

        def exception(self, msg: str, **kw):
            self._log.exception(self._fmt(msg, **kw))

        def bind(self, **kw):
            return self

    def get_logger(run_id: str = "", node_name: str = ""):  # type: ignore[misc]
        _ensure_configured()
        return _FallbackLogger(run_id=run_id)

    def setup_session_logger(run_id: str):
        # shortcut: optional rich 미설치 시 파일 로그 없이 콘솔 로그만 남긴다, 파일 로그가 필요해지면 공통 핸들러로 통합한다.
        return None
