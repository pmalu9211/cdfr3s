from celery import Celery
from celery.schedules import timedelta
from .config import settings

celery_app = Celery(
    "webhook_delivery_service",
    broker=settings.redis_url,
    backend=settings.redis_url 
)

# Configure tasks - point to where tasks are defined
celery_app.autodiscover_tasks(['app.tasks'])

# Configure periodic tasks for Celery Beat
celery_app.conf.beat_schedule = {
    'cleanup-old-logs-daily': {
        'task': 'app.tasks.cleanup_old_logs',
        'schedule': timedelta(hours=24), # Run daily
    },
}
celery_app.conf.timezone = 'UTC'