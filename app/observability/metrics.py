"""
Prometheus metrics collection for SalesWhisper Crosspost.

This module provides:
- Application metrics (requests, errors, response times)
- Business metrics (posts, publications, media processing)
- Infrastructure metrics (database, Redis, S3 operations)
- Celery task metrics (execution time, success/failure rates)
- Custom gauge, counter, histogram metrics
"""

import time
from contextlib import contextmanager
from functools import wraps

from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Enum,
    Gauge,
    Histogram,
    Info,
    generate_latest,
)

from ..core.config import settings


class MetricsCollector:
    """Central metrics collector for the application."""

    def __init__(self, registry: CollectorRegistry | None = None):
        """Initialize metrics collector."""
        self.registry = registry or CollectorRegistry()
        self._setup_metrics()

    def _setup_metrics(self):
        """Initialize all application metrics."""

        # Application info
        self.app_info = Info(
            'saleswhisper_crosspost_info',
            'Application information',
            registry=self.registry
        )
        self.app_info.info({
            'version': settings.app.version,
            'environment': settings.app.environment,
            'name': settings.app.app_name
        })

        # HTTP request metrics
        self.http_requests_total = Counter(
            'saleswhisper_http_requests_total',
            'Total HTTP requests',
            ['method', 'endpoint', 'status_code'],
            registry=self.registry
        )

        self.http_request_duration = Histogram(
            'saleswhisper_http_request_duration_seconds',
            'HTTP request duration in seconds',
            ['method', 'endpoint'],
            buckets=[0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
            registry=self.registry
        )

        self.http_request_size = Histogram(
            'saleswhisper_http_request_size_bytes',
            'HTTP request size in bytes',
            ['method', 'endpoint'],
            buckets=[64, 256, 1024, 4096, 16384, 65536, 262144],
            registry=self.registry
        )

        self.http_response_size = Histogram(
            'saleswhisper_http_response_size_bytes',
            'HTTP response size in bytes',
            ['method', 'endpoint'],
            buckets=[64, 256, 1024, 4096, 16384, 65536, 262144],
            registry=self.registry
        )

        # Business metrics - Posts
        self.posts_created_total = Counter(
            'saleswhisper_posts_created_total',
            'Total posts created',
            ['platform', 'source_type'],
            registry=self.registry
        )

        self.posts_published_total = Counter(
            'saleswhisper_posts_published_total',
            'Total posts successfully published',
            ['platform'],
            registry=self.registry
        )

        self.posts_failed_total = Counter(
            'saleswhisper_posts_failed_total',
            'Total posts that failed to publish',
            ['platform', 'error_type'],
            registry=self.registry
        )

        self.active_posts = Gauge(
            'saleswhisper_active_posts',
            'Number of active posts by status',
            ['status', 'platform'],
            registry=self.registry
        )

        # Media processing metrics
        self.media_processed_total = Counter(
            'saleswhisper_media_processed_total',
            'Total media files processed',
            ['media_type', 'platform', 'success'],
            registry=self.registry
        )

        self.media_processing_duration = Histogram(
            'saleswhisper_media_processing_duration_seconds',
            'Media processing duration in seconds',
            ['media_type', 'platform', 'operation'],
            buckets=[1, 5, 10, 30, 60, 180, 300, 600],
            registry=self.registry
        )

        self.media_file_size = Histogram(
            'saleswhisper_media_file_size_bytes',
            'Media file sizes in bytes',
            ['media_type', 'platform'],
            buckets=[1024, 10240, 102400, 1048576, 10485760, 104857600, 524288000],
            registry=self.registry
        )

        # Celery task metrics
        self.celery_tasks_total = Counter(
            'saleswhisper_celery_tasks_total',
            'Total Celery tasks executed',
            ['task_name', 'queue', 'status'],
            registry=self.registry
        )

        self.celery_task_duration = Histogram(
            'saleswhisper_celery_task_duration_seconds',
            'Celery task execution duration',
            ['task_name', 'queue'],
            buckets=[0.1, 0.5, 1, 5, 10, 30, 60, 180, 300],
            registry=self.registry
        )

        self.celery_active_tasks = Gauge(
            'saleswhisper_celery_active_tasks',
            'Number of active Celery tasks',
            ['queue'],
            registry=self.registry
        )

        self.celery_queue_size = Gauge(
            'saleswhisper_celery_queue_size',
            'Number of tasks in queue',
            ['queue'],
            registry=self.registry
        )

        # Database metrics
        self.database_queries_total = Counter(
            'saleswhisper_database_queries_total',
            'Total database queries',
            ['operation', 'table', 'status'],
            registry=self.registry
        )

        self.database_query_duration = Histogram(
            'saleswhisper_database_query_duration_seconds',
            'Database query duration',
            ['operation', 'table'],
            buckets=[0.001, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0],
            registry=self.registry
        )

        self.database_connections = Gauge(
            'saleswhisper_database_connections',
            'Number of database connections',
            ['pool', 'state'],
            registry=self.registry
        )

        # External API metrics
        self.external_api_calls_total = Counter(
            'saleswhisper_external_api_calls_total',
            'Total external API calls',
            ['service', 'endpoint', 'status_code'],
            registry=self.registry
        )

        self.external_api_duration = Histogram(
            'saleswhisper_external_api_duration_seconds',
            'External API call duration',
            ['service', 'endpoint'],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            registry=self.registry
        )

        # Circuit breaker metrics
        self.circuit_breaker_state = Enum(
            'saleswhisper_circuit_breaker_state',
            'Circuit breaker state',
            ['service'],
            states=['closed', 'open', 'half_open'],
            registry=self.registry
        )

        self.circuit_breaker_failures = Counter(
            'saleswhisper_circuit_breaker_failures_total',
            'Circuit breaker failures',
            ['service'],
            registry=self.registry
        )

        # Storage metrics
        self.storage_operations_total = Counter(
            'saleswhisper_storage_operations_total',
            'Total storage operations',
            ['operation', 'bucket', 'status'],
            registry=self.registry
        )

        self.storage_operation_duration = Histogram(
            'saleswhisper_storage_operation_duration_seconds',
            'Storage operation duration',
            ['operation', 'bucket'],
            buckets=[0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0],
            registry=self.registry
        )

        self.storage_file_size = Histogram(
            'saleswhisper_storage_file_size_bytes',
            'Storage file sizes',
            ['bucket', 'file_type'],
            buckets=[1024, 10240, 102400, 1048576, 10485760, 104857600, 524288000],
            registry=self.registry
        )

        # Cache metrics (Redis)
        self.cache_operations_total = Counter(
            'saleswhisper_cache_operations_total',
            'Total cache operations',
            ['operation', 'status'],
            registry=self.registry
        )

        self.cache_hit_ratio = Gauge(
            'saleswhisper_cache_hit_ratio',
            'Cache hit ratio',
            registry=self.registry
        )

    # HTTP request tracking methods
    def track_request(self, method: str, endpoint: str, status_code: int,
                     duration: float, request_size: int = 0, response_size: int = 0):
        """Track HTTP request metrics."""
        status_str = str(status_code)

        self.http_requests_total.labels(
            method=method, endpoint=endpoint, status_code=status_str
        ).inc()

        self.http_request_duration.labels(
            method=method, endpoint=endpoint
        ).observe(duration)

        if request_size > 0:
            self.http_request_size.labels(
                method=method, endpoint=endpoint
            ).observe(request_size)

        if response_size > 0:
            self.http_response_size.labels(
                method=method, endpoint=endpoint
            ).observe(response_size)

    # Business metrics methods
    def track_post_created(self, platform: str, source_type: str = "telegram"):
        """Track post creation."""
        self.posts_created_total.labels(
            platform=platform, source_type=source_type
        ).inc()

    def track_post_published(self, platform: str):
        """Track successful post publication."""
        self.posts_published_total.labels(platform=platform).inc()

    def track_post_failed(self, platform: str, error_type: str):
        """Track failed post publication."""
        self.posts_failed_total.labels(
            platform=platform, error_type=error_type
        ).inc()

    def update_active_posts(self, status: str, platform: str, count: int):
        """Update active posts gauge."""
        self.active_posts.labels(status=status, platform=platform).set(count)

    # Media processing metrics
    def track_media_processed(self, media_type: str, platform: str,
                            success: bool, duration: float, file_size: int = 0):
        """Track media processing."""
        success_str = "success" if success else "failure"

        self.media_processed_total.labels(
            media_type=media_type, platform=platform, success=success_str
        ).inc()

        self.media_processing_duration.labels(
            media_type=media_type, platform=platform, operation="transcode"
        ).observe(duration)

        if file_size > 0:
            self.media_file_size.labels(
                media_type=media_type, platform=platform
            ).observe(file_size)

    # Celery task metrics
    def track_celery_task(self, task_name: str, queue: str,
                         status: str, duration: float):
        """Track Celery task execution."""
        self.celery_tasks_total.labels(
            task_name=task_name, queue=queue, status=status
        ).inc()

        self.celery_task_duration.labels(
            task_name=task_name, queue=queue
        ).observe(duration)

    def update_celery_queue_size(self, queue: str, size: int):
        """Update Celery queue size."""
        self.celery_queue_size.labels(queue=queue).set(size)

    def update_active_celery_tasks(self, queue: str, count: int):
        """Update active Celery tasks."""
        self.celery_active_tasks.labels(queue=queue).set(count)

    # Database metrics
    def track_database_query(self, operation: str, table: str,
                           status: str, duration: float):
        """Track database query."""
        self.database_queries_total.labels(
            operation=operation, table=table, status=status
        ).inc()

        self.database_query_duration.labels(
            operation=operation, table=table
        ).observe(duration)

    # External API metrics
    def track_external_api_call(self, service: str, endpoint: str,
                              status_code: int, duration: float):
        """Track external API call."""
        self.external_api_calls_total.labels(
            service=service, endpoint=endpoint, status_code=str(status_code)
        ).inc()

        self.external_api_duration.labels(
            service=service, endpoint=endpoint
        ).observe(duration)

    # Circuit breaker metrics
    def update_circuit_breaker_state(self, service: str, state: str):
        """Update circuit breaker state."""
        self.circuit_breaker_state.labels(service=service).state(state)

    def track_circuit_breaker_failure(self, service: str):
        """Track circuit breaker failure."""
        self.circuit_breaker_failures.labels(service=service).inc()

    # Storage metrics
    def track_storage_operation(self, operation: str, bucket: str,
                              status: str, duration: float, file_size: int = 0):
        """Track storage operation."""
        self.storage_operations_total.labels(
            operation=operation, bucket=bucket, status=status
        ).inc()

        self.storage_operation_duration.labels(
            operation=operation, bucket=bucket
        ).observe(duration)

        if file_size > 0:
            # Determine file type from operation context
            file_type = "media"  # Default, could be enhanced
            self.storage_file_size.labels(
                bucket=bucket, file_type=file_type
            ).observe(file_size)

    # Cache metrics
    def track_cache_operation(self, operation: str, status: str):
        """Track cache operation."""
        self.cache_operations_total.labels(
            operation=operation, status=status
        ).inc()

    def update_cache_hit_ratio(self, ratio: float):
        """Update cache hit ratio."""
        self.cache_hit_ratio.set(ratio)

    def get_metrics(self) -> str:
        """Get metrics in Prometheus format."""
        return generate_latest(self.registry)


# Global metrics collector
metrics = MetricsCollector()


# Decorators for automatic metrics collection
def track_request_metrics(endpoint: str):
    """Decorator to automatically track HTTP request metrics."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                response = await func(*args, **kwargs)
                duration = time.time() - start_time
                status_code = getattr(response, 'status_code', 200)

                # Extract method from request if available
                request = kwargs.get('request') or (args[0] if args else None)
                method = getattr(request, 'method', 'unknown') if request else 'unknown'

                metrics.track_request(method, endpoint, status_code, duration)
                return response
            except Exception:
                duration = time.time() - start_time
                request = kwargs.get('request') or (args[0] if args else None)
                method = getattr(request, 'method', 'unknown') if request else 'unknown'

                metrics.track_request(method, endpoint, 500, duration)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                response = func(*args, **kwargs)
                duration = time.time() - start_time
                status_code = getattr(response, 'status_code', 200)

                request = kwargs.get('request') or (args[0] if args else None)
                method = getattr(request, 'method', 'unknown') if request else 'unknown'

                metrics.track_request(method, endpoint, status_code, duration)
                return response
            except Exception:
                duration = time.time() - start_time
                request = kwargs.get('request') or (args[0] if args else None)
                method = getattr(request, 'method', 'unknown') if request else 'unknown'

                metrics.track_request(method, endpoint, 500, duration)
                raise

        # Return appropriate wrapper based on function type
        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


def track_database_metrics(operation: str, table: str):
    """Decorator to automatically track database operation metrics."""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                metrics.track_database_query(operation, table, "success", duration)
                return result
            except Exception:
                duration = time.time() - start_time
                metrics.track_database_query(operation, table, "error", duration)
                raise
        return wrapper
    return decorator


def track_external_api_metrics(service: str, endpoint: str):
    """Decorator to automatically track external API call metrics."""
    def decorator(func):
        @wraps(func)
        async def async_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                response = await func(*args, **kwargs)
                duration = time.time() - start_time
                status_code = getattr(response, 'status_code', 200)
                metrics.track_external_api_call(service, endpoint, status_code, duration)
                return response
            except Exception:
                duration = time.time() - start_time
                metrics.track_external_api_call(service, endpoint, 0, duration)
                raise

        @wraps(func)
        def sync_wrapper(*args, **kwargs):
            start_time = time.time()
            try:
                response = func(*args, **kwargs)
                duration = time.time() - start_time
                status_code = getattr(response, 'status_code', 200)
                metrics.track_external_api_call(service, endpoint, status_code, duration)
                return response
            except Exception:
                duration = time.time() - start_time
                metrics.track_external_api_call(service, endpoint, 0, duration)
                raise

        import asyncio
        if asyncio.iscoroutinefunction(func):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


@contextmanager
def track_processing_time(metric_name: str, labels: dict[str, str]):
    """Context manager to track processing time for any operation."""
    start_time = time.time()
    try:
        yield
        time.time() - start_time
        # This would need to be connected to specific metrics based on metric_name
        # For now, we'll use a generic approach
    except Exception:
        time.time() - start_time
        # Track error case
        raise


# Helper functions for manual metrics collection
def track_post_lifecycle(post_id: str, platform: str, status: str,
                        error_type: str | None = None):
    """Track complete post lifecycle."""
    if status == "created":
        metrics.track_post_created(platform)
    elif status == "published":
        metrics.track_post_published(platform)
    elif status == "failed" and error_type:
        metrics.track_post_failed(platform, error_type)


def track_celery_task_lifecycle(task_name: str, queue: str,
                              execution_time: float, success: bool):
    """Track Celery task lifecycle."""
    status = "success" if success else "failure"
    metrics.track_celery_task(task_name, queue, status, execution_time)


def update_system_metrics():
    """Update system-level metrics (called periodically)."""
    # This would typically query the database and other systems
    # to get current counts and update gauges
    pass


# Metrics endpoint for Prometheus scraping
def get_metrics_response():
    """Get metrics in format suitable for HTTP response."""
    return metrics.get_metrics(), {"Content-Type": CONTENT_TYPE_LATEST}


# Example usage and testing
if __name__ == "__main__":
    """Example usage of metrics system."""

    print("=== Testing SalesWhisper Metrics System ===")

    # Test HTTP request tracking
    print("\n1. Testing HTTP Request Metrics:")
    metrics.track_request("GET", "/api/posts", 200, 0.15, 1024, 2048)
    metrics.track_request("POST", "/api/posts", 201, 0.45, 4096, 512)
    metrics.track_request("GET", "/api/posts", 500, 2.1, 1024, 0)

    # Test business metrics
    print("\n2. Testing Business Metrics:")
    metrics.track_post_created("instagram", "telegram")
    metrics.track_post_published("instagram")
    metrics.track_post_failed("tiktok", "api_error")
    metrics.update_active_posts("draft", "instagram", 5)

    # Test media processing
    print("\n3. Testing Media Processing Metrics:")
    metrics.track_media_processed("video", "tiktok", True, 15.5, 10485760)
    metrics.track_media_processed("image", "instagram", False, 2.1, 2048000)

    # Test Celery tasks
    print("\n4. Testing Celery Task Metrics:")
    metrics.track_celery_task("transcode_video", "transcode", "success", 25.3)
    metrics.track_celery_task("publish_post", "publish", "failure", 5.1)
    metrics.update_celery_queue_size("ingest", 12)

    # Test external API calls
    print("\n5. Testing External API Metrics:")
    metrics.track_external_api_call("instagram", "/media", 200, 1.2)
    metrics.track_external_api_call("vk", "/wall.post", 403, 0.8)

    # Test circuit breaker
    print("\n6. Testing Circuit Breaker Metrics:")
    metrics.update_circuit_breaker_state("instagram_api", "open")
    metrics.track_circuit_breaker_failure("tiktok_api")

    # Generate metrics output
    print("\n7. Sample Metrics Output:")
    metrics_output = metrics.get_metrics()

    # Show first few lines of metrics
    lines = metrics_output.decode('utf-8').split('\n')[:20]
    for line in lines:
        if line.strip() and not line.startswith('#'):
            print(f"  {line}")

    print(f"\n Generated {len(lines)} metric lines")
    print(" Metrics system test completed")


def get_test_metrics() -> MetricsCollector:
    """Get metrics collector configured for testing."""
    from prometheus_client import CollectorRegistry
    test_registry = CollectorRegistry()
    return MetricsCollector(test_registry)
