from celery import Celery
from celery.schedules import timedelta
from .config import settings

# Use a single Celery app instance
celery_app = Celery(
    "webhook_delivery_service",
    broker=settings.redis_url,
    backend=settings.redis_url # Use Redis as backend for result storage (needed for retries)
)

# Optional: Configure timezone if needed, otherwise UTC is default
# celery_app.conf.enable_utc = True
# celery_app.conf.timezone = 'UTC'

# Configure tasks - point to where tasks are defined
celery_app.autodiscover_tasks(['app.tasks'])

# Configure periodic tasks for Celery Beat
celery_app.conf.beat_schedule = {
    'cleanup-old-logs-daily': {
        'task': 'app.tasks.cleanup_old_logs',
        'schedule': timedelta(hours=24), # Run daily
        # Optional arguments for the task
        # 'args': (settings.log_retention_hours,)
    },
}
celery_app.conf.timezone = 'UTC' # Ensure beat schedule is interpreted in UTC