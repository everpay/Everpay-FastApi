from celery import Celery
from app.core.config import settings

broker = settings.redis_url or "redis://localhost:6379/0"
celery_app = Celery("everpay", broker=broker, backend=broker, include=["app.tasks.health"])
celery_app.conf.update(task_serializer="json", accept_content=["json"], result_serializer="json",
    timezone="UTC", enable_utc=True, task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, task_track_started=True, broker_connection_retry_on_startup=True,
    task_always_eager=settings.celery_task_always_eager, beat_schedule={})
