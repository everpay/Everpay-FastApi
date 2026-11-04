from datetime import datetime, timezone
import os
from app.celery_app import celery_app

@celery_app.task(name="app.tasks.health.worker_health", bind=True, acks_late=True, max_retries=0)
def worker_health(self):
    """Non-financial smoke test proving a Celery worker can execute a task."""
    return {
        "status": "ok",
        "worker": os.getenv("HOSTNAME", "unknown"),
        "task_id": self.request.id,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
