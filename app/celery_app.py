from celery import Celery
from app.config import get_settings

settings = get_settings()
celery_app = Celery("everpay", broker=settings.redis_url, include=["app.tasks.health"])
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    enable_utc=True,
    timezone="UTC",
    task_ignore_result=True,
    task_store_errors_even_if_ignored=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 3600},
    task_soft_time_limit=50,
    task_time_limit=60,
    task_default_queue="default",
    task_routes={"app.tasks.health.worker_health": {"queue": "health"}},
)
