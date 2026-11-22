from app.worker import celery_app

@celery_app.task(name="everpay.health.ping", autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, max_retries=3)
def ping() -> dict[str, str]:
    return {"status": "ok"}
