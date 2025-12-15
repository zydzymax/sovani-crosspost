"""
Celery application configuration for SoVAni Crosspost.

This module provides:
- Celery application instance with proper configuration
- Task discovery and routing
- Rate limiting and priority settings
- Monitoring and logging integration
- Beat scheduler configuration
"""

# Load environment variables BEFORE any other imports
import os
from pathlib import Path
from dotenv import load_dotenv

# Find and load .env file from project root
project_root = Path(__file__).parent.parent.parent
env_file = project_root / ".env"
if env_file.exists():
    load_dotenv(env_file)

from celery import Celery
from celery.signals import (
    after_setup_logger, after_setup_task_logger,
    task_prerun, task_postrun, task_failure, task_success
)
from kombu import Queue, Exchange

from ..core.config import settings
from ..core.logging import setup_logging, get_logger, with_logging_context
from ..observability.metrics import metrics


# Initialize logging
setup_logging()
logger = get_logger("celery")


def make_celery() -> Celery:
    """Create and configure Celery application."""
    
    # Create Celery instance
    celery_app = Celery(
        "sovani_crosspost",
        broker=settings.get_redis_url(db=settings.redis.celery_broker_db),
        backend=settings.get_redis_url(db=settings.redis.celery_backend_db),
        include=[
            'app.workers.tasks.ingest',
            'app.workers.tasks.enrich', 
            'app.workers.tasks.captionize',
            'app.workers.tasks.transcode',
            'app.workers.tasks.preflight',
            'app.workers.tasks.publish',
            'app.workers.tasks.finalize',
            'app.workers.tasks.outbox'
        ]
    )
    
    # Configure Celery
    celery_app.conf.update(
        # Task settings
        task_serializer='json',
        accept_content=['json'],
        result_serializer='json',
        timezone='UTC',
        enable_utc=True,
        
        # Task execution settings
        task_acks_late=settings.celery.task_acks_late,
        task_reject_on_worker_lost=True,
        task_soft_time_limit=settings.celery.task_soft_time_limit,
        task_time_limit=settings.celery.task_time_limit,
        
        # Worker settings
        worker_concurrency=settings.celery.worker_concurrency,
        worker_max_memory_per_child=settings.celery.worker_max_memory_per_child,
        worker_max_tasks_per_child=settings.celery.worker_max_tasks_per_child,
        worker_prefetch_multiplier=1,  # Important for fair task distribution
        
        # Result backend settings
        result_expires=3600,  # 1 hour
        result_persistent=True,
        result_compression='gzip',
        
        # Retry settings
        task_default_max_retries=settings.celery.max_retry_attempts,
        task_default_retry_delay=60,  # 1 minute
        task_default_retry_backoff=settings.celery.retry_backoff_factor,
        task_default_retry_jitter=True,
        
        # Queue routing and priorities
        task_routes={
            # Ingest queue - highest priority
            'app.workers.tasks.ingest.*': {
                'queue': 'ingest',
                'priority': settings.celery.queue_priorities['ingest'],
                'rate_limit': '10/s'  # 10 tasks per second
            },
            
            # Enrichment queue
            'app.workers.tasks.enrich.*': {
                'queue': 'enrich', 
                'priority': settings.celery.queue_priorities['enrich'],
                'rate_limit': '5/s'
            },
            
            # Caption generation queue
            'app.workers.tasks.captionize.*': {
                'queue': 'captionize',
                'priority': settings.celery.queue_priorities['captionize'],
                'rate_limit': '3/s'  # AI API rate limiting
            },
            
            # Media transcoding queue - resource intensive
            'app.workers.tasks.transcode.*': {
                'queue': 'transcode',
                'priority': settings.celery.queue_priorities['transcode'],
                'rate_limit': '2/s'  # Limited by CPU/memory
            },
            
            # Preflight checks
            'app.workers.tasks.preflight.*': {
                'queue': 'preflight',
                'priority': settings.celery.queue_priorities['preflight'],
                'rate_limit': '5/s'
            },
            
            # Publishing queue - external API rate limits
            'app.workers.tasks.publish.*': {
                'queue': 'publish',
                'priority': settings.celery.queue_priorities['publish'],
                'rate_limit': '1/s'  # Conservative for API limits
            },
            
            # Finalization queue
            'app.workers.tasks.finalize.*': {
                'queue': 'finalize',
                'priority': settings.celery.queue_priorities['finalize'],
                'rate_limit': '5/s'
            },
            
            # Outbox processing - system critical
            'app.workers.tasks.outbox.*': {
                'queue': 'outbox',
                'priority': 10,  # Highest priority
                'rate_limit': '20/s'
            }
        },
        
        # Queue definitions with exchanges
        task_queues=[
            Queue('ingest', Exchange('ingest', type='direct'), routing_key='ingest'),
            Queue('enrich', Exchange('enrich', type='direct'), routing_key='enrich'),
            Queue('captionize', Exchange('captionize', type='direct'), routing_key='captionize'),
            Queue('transcode', Exchange('transcode', type='direct'), routing_key='transcode'),
            Queue('preflight', Exchange('preflight', type='direct'), routing_key='preflight'),
            Queue('publish', Exchange('publish', type='direct'), routing_key='publish'),
            Queue('finalize', Exchange('finalize', type='direct'), routing_key='finalize'),
            Queue('outbox', Exchange('outbox', type='direct'), routing_key='outbox'),
        ],
        
        # Default queue
        task_default_queue='ingest',
        task_default_exchange='ingest',
        task_default_routing_key='ingest',
        
        # Beat scheduler settings
        beat_schedule={
            'process-outbox': {
                'task': 'app.workers.tasks.outbox.process_outbox_events',
                'schedule': 10.0,  # Every 10 seconds
                'options': {'queue': 'outbox', 'priority': 10}
            },
            
            'cleanup-completed-tasks': {
                'task': 'app.workers.tasks.finalize.cleanup_completed_tasks',
                'schedule': 300.0,  # Every 5 minutes
                'options': {'queue': 'finalize', 'priority': 1}
            },
            
            'update-queue-metrics': {
                'task': 'app.workers.tasks.outbox.update_queue_metrics', 
                'schedule': 30.0,  # Every 30 seconds
                'options': {'queue': 'outbox', 'priority': 5}
            },
            
            'health-check': {
                'task': 'app.workers.tasks.outbox.health_check_task',
                'schedule': 60.0,  # Every minute
                'options': {'queue': 'outbox', 'priority': 3}
            }
        },
        
        # Monitoring
        worker_send_task_events=True,
        task_send_sent_event=True,
        
        # Security
        worker_hijack_root_logger=False,
        worker_log_color=False
    )
    
    return celery_app


# Create global Celery instance
celery = make_celery()


@after_setup_logger.connect
def setup_celery_logger(sender=None, logger=None, loglevel=None, logfile=None, format=None, colorize=None, **kwargs):
    """Setup structured logging for Celery."""
    pass  # We use our own logging setup


@after_setup_task_logger.connect 
def setup_task_logger(sender=None, logger=None, loglevel=None, logfile=None, format=None, colorize=None, **kwargs):
    """Setup structured logging for Celery tasks."""
    pass  # We use our own logging setup


@task_prerun.connect
def task_prerun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, **kwds):
    """Handle task start event."""
    with with_logging_context(task_id=task_id):
        task_logger = get_logger(f"celery.task.{sender.name}")
        
        task_logger.info(
            "Task started",
            task_name=sender.name,
            task_id=task_id,
            args=args,
            kwargs=kwargs
        )
        
        # Track metrics
        queue = getattr(sender, 'queue', 'default')
        metrics.update_active_celery_tasks(queue, 1)


@task_postrun.connect
def task_postrun_handler(sender=None, task_id=None, task=None, args=None, kwargs=None, 
                        retval=None, state=None, **kwds):
    """Handle task completion event."""
    import time
    
    with with_logging_context(task_id=task_id):
        task_logger = get_logger(f"celery.task.{sender.name}")
        
        # Calculate execution time
        start_time = getattr(task, '_start_time', time.time())
        execution_time = time.time() - start_time
        
        task_logger.info(
            "Task completed",
            task_name=sender.name,
            task_id=task_id,
            state=state,
            execution_time=execution_time
        )
        
        # Track metrics
        queue = getattr(sender, 'queue', 'default')
        success = state == 'SUCCESS'
        
        metrics.track_celery_task(sender.name, queue, state.lower(), execution_time)
        metrics.update_active_celery_tasks(queue, -1)


@task_success.connect
def task_success_handler(sender=None, result=None, **kwargs):
    """Handle successful task completion."""
    task_logger = get_logger(f"celery.task.{sender.name}")
    
    task_logger.info(
        "Task succeeded",
        task_name=sender.name,
        result_type=type(result).__name__ if result else None
    )


@task_failure.connect
def task_failure_handler(sender=None, task_id=None, exception=None, traceback=None, einfo=None, **kwargs):
    """Handle task failure."""
    with with_logging_context(task_id=task_id):
        task_logger = get_logger(f"celery.task.{sender.name}")
        
        task_logger.error(
            "Task failed", 
            task_name=sender.name,
            task_id=task_id,
            error=str(exception),
            traceback=traceback,
            exc_info=einfo
        )
        
        # Track failure metrics
        queue = getattr(sender, 'queue', 'default')
        metrics.track_celery_task(sender.name, queue, 'failure', 0)


# Task base class with common functionality
class BaseTask(celery.Task):
    """Base task class with common functionality."""
    
    def on_retry(self, exc, task_id, args, kwargs, einfo):
        """Handle task retry."""
        with with_logging_context(task_id=task_id):
            task_logger = get_logger(f"celery.task.{self.name}")
            
            task_logger.warning(
                "Task retry",
                task_name=self.name,
                task_id=task_id,
                retry_count=self.request.retries,
                max_retries=self.max_retries,
                error=str(exc)
            )
    
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Handle task failure."""
        with with_logging_context(task_id=task_id):
            task_logger = get_logger(f"celery.task.{self.name}")
            
            task_logger.error(
                "Task failed permanently",
                task_name=self.name,
                task_id=task_id,
                retry_count=self.request.retries,
                error=str(exc),
                exc_info=einfo
            )


# Set base task class
celery.Task = BaseTask


# Health check function
def ping():
    """Simple health check for Celery."""
    try:
        # Check broker connection
        with celery.connection_or_acquire() as conn:
            conn.default_channel.queue_declare(
                queue="health_check", 
                passive=True
            )
        
        logger.info("Celery health check passed")
        return True
        
    except Exception as e:
        logger.error("Celery health check failed", error=str(e))
        return False


if __name__ == "__main__":
    """Start Celery worker for development."""
    celery.start(argv=[
        'worker',
        '--loglevel=info',
        '--concurrency=4',
        '--queues=ingest,enrich,captionize,transcode,preflight,publish,finalize,outbox'
    ])