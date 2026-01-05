"""
Structured logging configuration for SalesWhisper Crosspost.

This module provides:
- JSON structured logging with structlog
- Context enrichment (request_id, user_id, etc.)
- Performance and audit logging
- Integration with FastAPI and Celery
- Log filtering and formatting
"""

import logging
import logging.config
import sys
import uuid
from contextvars import ContextVar
from datetime import datetime

import structlog
from loguru import logger
from structlog.types import FilteringBoundLogger

from .config import settings

# Context variables for request tracking
request_id_ctx: ContextVar[str | None] = ContextVar("request_id", default=None)
user_id_ctx: ContextVar[str | None] = ContextVar("user_id", default=None)
task_id_ctx: ContextVar[str | None] = ContextVar("task_id", default=None)


class StructlogFormatter(logging.Formatter):
    """Custom formatter to bridge between stdlib logging and structlog."""

    def __init__(self, processor):
        super().__init__()
        self.processor = processor

    def format(self, record: logging.LogRecord) -> str:
        """Format log record using structlog processor."""
        # Convert LogRecord to structlog event
        event_dict = {
            "event": record.getMessage(),
            "level": record.levelname.lower(),
            "logger": record.name,
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "module": record.module,
            "function": record.funcName,
            "line": record.lineno,
        }

        # Add context variables
        if request_id := request_id_ctx.get():
            event_dict["request_id"] = request_id
        if user_id := user_id_ctx.get():
            event_dict["user_id"] = user_id
        if task_id := task_id_ctx.get():
            event_dict["task_id"] = task_id

        # Add exception info if present
        if record.exc_info:
            event_dict["exception"] = self.formatException(record.exc_info)

        # Add extra fields
        if hasattr(record, "__dict__"):
            for key, value in record.__dict__.items():
                if key not in {
                    "name",
                    "msg",
                    "args",
                    "levelname",
                    "levelno",
                    "pathname",
                    "filename",
                    "module",
                    "lineno",
                    "funcName",
                    "created",
                    "msecs",
                    "relativeCreated",
                    "thread",
                    "threadName",
                    "processName",
                    "process",
                    "getMessage",
                    "exc_info",
                    "exc_text",
                    "stack_info",
                }:
                    event_dict[key] = value

        return self.processor(None, None, event_dict)


def add_context_fields(logger, method_name, event_dict):
    """Add context fields to every log entry."""
    # Add request context
    if request_id := request_id_ctx.get():
        event_dict["request_id"] = request_id
    if user_id := user_id_ctx.get():
        event_dict["user_id"] = user_id
    if task_id := task_id_ctx.get():
        event_dict["task_id"] = task_id

    # Add application context
    event_dict["app"] = settings.app.app_name
    event_dict["version"] = settings.app.version
    event_dict["environment"] = settings.app.environment

    return event_dict


def add_timestamps(logger, method_name, event_dict):
    """Add timestamp in ISO format."""
    event_dict["timestamp"] = datetime.utcnow().isoformat() + "Z"
    return event_dict


def filter_sensitive_data(logger, method_name, event_dict):
    """Filter sensitive data from logs."""
    sensitive_fields = {"password", "secret", "token", "key", "api_key", "access_token", "refresh_token", "jwt", "auth"}

    def _filter_dict(obj, path=""):
        if isinstance(obj, dict):
            return {
                key: (
                    "[REDACTED]"
                    if any(field in key.lower() for field in sensitive_fields)
                    else _filter_dict(value, f"{path}.{key}" if path else key)
                )
                for key, value in obj.items()
            }
        elif isinstance(obj, list):
            return [_filter_dict(item, f"{path}[{i}]") for i, item in enumerate(obj)]
        else:
            return obj

    return _filter_dict(event_dict)


def setup_structlog():
    """Configure structlog with JSON output."""
    processors = [
        add_context_fields,
        add_timestamps,
        filter_sensitive_data,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
        structlog.processors.UnicodeDecoder(),
    ]

    if settings.app.log_format == "json":
        processors.append(structlog.processors.JSONRenderer())
    else:
        processors.append(structlog.dev.ConsoleRenderer(colors=True))

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.stdlib.BoundLogger,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def setup_stdlib_logging():
    """Configure standard library logging to work with structlog."""
    formatter = StructlogFormatter(
        structlog.processors.JSONRenderer()
        if settings.app.log_format == "json"
        else structlog.dev.ConsoleRenderer(colors=True)
    )

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, settings.app.log_level))

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(getattr(logging, settings.app.log_level))
    root_logger.addHandler(console_handler)

    # Configure specific loggers
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = True

    # Reduce noise from third-party libraries
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)


def setup_loguru():
    """Configure Loguru for additional logging features."""
    # Remove default handler
    logger.remove()

    # Add structured JSON handler
    log_format = (
        "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
        "<level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "<level>{message}</level>"
    )

    if settings.app.log_format == "json":
        logger.add(sys.stdout, level=settings.app.log_level, serialize=True, backtrace=True, diagnose=True)
    else:
        logger.add(
            sys.stdout, level=settings.app.log_level, format=log_format, backtrace=True, diagnose=True, colorize=True
        )

    # Add file handler for errors (production)
    if settings.app.is_production:
        logger.add(
            "logs/error.log",
            level="ERROR",
            rotation="10 MB",
            retention="30 days",
            compression="gz",
            serialize=True,
            backtrace=True,
            diagnose=True,
        )


class LoggingContextManager:
    """Context manager for setting logging context."""

    def __init__(self, request_id: str | None = None, user_id: str | None = None, task_id: str | None = None):
        self.request_id = request_id or str(uuid.uuid4())
        self.user_id = user_id
        self.task_id = task_id
        self._tokens = []

    def __enter__(self):
        self._tokens.append(request_id_ctx.set(self.request_id))
        if self.user_id:
            self._tokens.append(user_id_ctx.set(self.user_id))
        if self.task_id:
            self._tokens.append(task_id_ctx.set(self.task_id))
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        for token in reversed(self._tokens):
            if hasattr(token, "var"):
                token.var.set(token.old_value)


class AuditLogger:
    """Structured audit logging for business events."""

    def __init__(self):
        self.logger = structlog.get_logger("audit")

    def log_post_created(self, post_id: str, platform: str, user_id: str, product_id: str, **kwargs):
        """Log post creation event."""
        self.logger.info(
            "post_created",
            post_id=post_id,
            platform=platform,
            user_id=user_id,
            product_id=product_id,
            action="create_post",
            **kwargs,
        )

    def log_post_published(self, post_id: str, platform: str, platform_post_id: str, platform_url: str, **kwargs):
        """Log successful post publication."""
        self.logger.info(
            "post_published",
            post_id=post_id,
            platform=platform,
            platform_post_id=platform_post_id,
            platform_url=platform_url,
            action="publish_post",
            **kwargs,
        )

    def log_post_failed(self, post_id: str, platform: str, error: str, **kwargs):
        """Log failed post publication."""
        self.logger.error(
            "post_failed",
            post_id=post_id,
            platform=platform,
            error=error,
            action="publish_post",
            status="failed",
            **kwargs,
        )

    def log_media_processed(self, media_id: str, rendition_id: str, platform: str, processing_time: float, **kwargs):
        """Log media processing completion."""
        self.logger.info(
            "media_processed",
            media_id=media_id,
            rendition_id=rendition_id,
            platform=platform,
            processing_time_seconds=processing_time,
            action="process_media",
            **kwargs,
        )

    def log_api_access(
        self, method: str, path: str, status_code: int, response_time: float, user_id: str | None = None, **kwargs
    ):
        """Log API access."""
        self.logger.info(
            "api_access",
            http_method=method,
            path=path,
            status_code=status_code,
            response_time_seconds=response_time,
            user_id=user_id,
            action="api_request",
            **kwargs,
        )


class PerformanceLogger:
    """Performance monitoring and metrics logging."""

    def __init__(self):
        self.logger = structlog.get_logger("performance")

    def log_task_performance(self, task_name: str, execution_time: float, queue: str, success: bool, **kwargs):
        """Log Celery task performance."""
        self.logger.info(
            "task_performance",
            task_name=task_name,
            execution_time_seconds=execution_time,
            queue=queue,
            success=success,
            metric_type="task_performance",
            **kwargs,
        )

    def log_database_query(
        self, query_type: str, execution_time: float, table: str, rows_affected: int | None = None, **kwargs
    ):
        """Log database query performance."""
        self.logger.info(
            "database_query",
            query_type=query_type,
            execution_time_seconds=execution_time,
            table=table,
            rows_affected=rows_affected,
            metric_type="database_performance",
            **kwargs,
        )

    def log_external_api_call(self, service: str, endpoint: str, response_time: float, status_code: int, **kwargs):
        """Log external API call performance."""
        self.logger.info(
            "external_api_call",
            service=service,
            endpoint=endpoint,
            response_time_seconds=response_time,
            status_code=status_code,
            metric_type="api_performance",
            **kwargs,
        )


def setup_logging():
    """Initialize all logging systems."""
    setup_structlog()
    setup_stdlib_logging()
    setup_loguru()


# Global logger instances
audit_logger = AuditLogger()
performance_logger = PerformanceLogger()


# Convenience functions
def get_logger(name: str = None) -> FilteringBoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)


def with_logging_context(request_id: str = None, user_id: str = None, task_id: str = None) -> LoggingContextManager:
    """Create logging context manager."""
    return LoggingContextManager(request_id, user_id, task_id)


# FastAPI middleware integration
def create_request_id() -> str:
    """Generate unique request ID."""
    return str(uuid.uuid4())


# Example usage and testing
if __name__ == "__main__":
    """Example usage of logging system."""

    # Initialize logging
    setup_logging()

    # Get loggers
    app_logger = get_logger("app")

    print("= Testing SalesWhisper Logging System")

    # Test basic logging
    app_logger.info("Application starting", version=settings.app.version)
    app_logger.warning("This is a warning", component="test")

    # Test context logging
    with with_logging_context(request_id="req_123", user_id="user_456") as ctx:
        app_logger.info("Processing request", action="test_action")
        app_logger.error("Simulated error", error_code="TEST_ERROR")

    # Test audit logging
    audit_logger.log_post_created(
        post_id="post_123", platform="instagram", user_id="user_456", product_id="prod_789", title="Test Post"
    )

    # Test performance logging
    performance_logger.log_task_performance(
        task_name="transcode_video",
        execution_time=15.5,
        queue="transcode",
        success=True,
        input_format="mov",
        output_format="mp4",
    )

    # Test Loguru
    logger.info("Loguru test message", extra_field="test_value")

    print(" Logging system test completed")


def get_test_logger() -> FilteringBoundLogger:
    """Get logger configured for testing."""
    # Set test log level
    import logging

    logging.getLogger().setLevel(logging.DEBUG)
    return get_logger("test")
