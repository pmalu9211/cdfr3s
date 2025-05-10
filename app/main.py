from fastapi import FastAPI, Depends, HTTPException, status, Request
from sqlalchemy.orm import Session
from typing import List
import uuid
from . import crud, models, schemas, tasks
from .database import SessionLocal, engine, get_db
from .cache import get_subscription_from_cache, set_subscription_in_cache, invalidate_subscription_cache
import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# Create database tables on startup - useful for development
# In production, migrations are preferred. init.sql handles this via docker-entrypoint.
# def create_db_tables():
#     models.Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Webhook Delivery Service",
    description="A reliable webhook delivery system with retries and logging.",
    version="1.0.0",
)

# Dependency to get cache client
def get_cache_client():
    # Cache client is global, no need for complex dependency injection here
    # just import it where needed or pass it explicitly
    pass # Placeholder, cache functions use the global client


@app.on_event("startup")
async def startup_event():
    # Optional: create tables if using this approach instead of init.sql
    # create_db_tables()
    logger.info("Application startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
    # Close any resources if necessary
    logger.info("Application shutting down.")


# --- Subscription Endpoints ---
@app.post("/subscriptions/", response_model=schemas.SubscriptionRead, status_code=status.HTTP_201_CREATED)
def create_subscription(subscription: schemas.SubscriptionCreate, db: Session = Depends(get_db)):
    db_subscription = crud.create_subscription(db, subscription)
    # Invalidate cache on creation
    invalidate_subscription_cache(db_subscription.id)
    logger.info(f"Subscription created: {db_subscription.id}")
    return db_subscription

@app.get("/subscriptions/", response_model=List[schemas.SubscriptionRead])
def read_subscriptions(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    subscriptions = crud.get_subscriptions(db, skip=skip, limit=limit)
    return subscriptions

@app.get("/subscriptions/{subscription_id}", response_model=schemas.SubscriptionRead)
def read_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)):
    # Try cache first
    cached_sub = get_subscription_from_cache(subscription_id)
    if cached_sub:
        return cached_sub

    db_subscription = crud.get_subscription(db, subscription_id)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    # Cache the result before returning
    set_subscription_in_cache(schemas.SubscriptionRead.model_validate(db_subscription))

    return db_subscription

@app.put("/subscriptions/{subscription_id}", response_model=schemas.SubscriptionRead)
def update_subscription(subscription_id: uuid.UUID, subscription: schemas.SubscriptionCreate, db: Session = Depends(get_db)):
    db_subscription = crud.update_subscription(db, subscription_id, subscription)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    # Invalidate cache on update
    invalidate_subscription_cache(subscription_id)
    logger.info(f"Subscription updated: {subscription_id}")
    return db_subscription

@app.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)):
    db_subscription = crud.delete_subscription(db, subscription_id)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    # Invalidate cache on delete
    invalidate_subscription_cache(subscription_id)
    logger.info(f"Subscription deleted: {subscription_id}")
    return

# --- Webhook Ingestion ---
@app.post("/ingest/{subscription_id}", status_code=status.HTTP_202_ACCEPTED)
async def ingest_webhook(subscription_id: uuid.UUID, webhook: schemas.WebhookIngest, db: Session = Depends(get_db)):
    # Verify subscription exists (try cache first)
    subscription = get_subscription_from_cache(subscription_id)
    if not subscription:
        db_subscription = crud.get_subscription(db, subscription_id)
        if not db_subscription:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
        subscription = schemas.SubscriptionRead.model_validate(db_subscription)
        set_subscription_in_cache(subscription) # Cache the subscription

    # Optional: Implement signature verification here (Bonus Point)
    # if subscription.secret:
    #     # Get the raw body from the request
    #     body = await Request.body()
    #     signature_header = request.headers.get('X-Hub-Signature-256')
    #     if not signature_header:
    #         raise HTTPException(status_code=401, detail="Signature header missing")
    #     expected_signature = calculate_signature(webhook.payload, subscription.secret) # Needs raw body, not parsed payload
    #     # You would need to calculate signature from the RAW body, not the parsed JSON payload
    #     # This requires reading body() before FastAPI parses JSON
    #     # A dependency or middleware could handle this before json parsing
    #     # Example check (simplified, needs raw body):
    #     # if not hmac.compare_digest(f"sha256={expected_signature}", signature_header):
    #     #      raise HTTPException(status_code=401, detail="Invalid signature")

    # Optional: Event type filtering at ingestion (Bonus Point)
    # event_type = webhook.event_type # Assume event_type is part of the input schema/payload
    # if subscription.event_types and event_type not in subscription.event_types:
    #     logger.info(f"Webhook for subscription {subscription_id} skipped due to event type filter: {event_type}")
    #     # Maybe log a 'skipped' event here? Or just return 202 without queueing.
    #     # For this example, we will create the webhook but the worker will filter (less efficient)
    #     # OR the worker task won't be sent based on this check here.
    #     # Let's assume filtering is worker-side if implemented in tasks.py

    # Save the incoming webhook payload
    db_webhook = crud.create_webhook(db, subscription_id, webhook.payload) # Add event_type here if applicable

    # Enqueue the delivery task
    tasks.process_delivery.send_task(args=[str(db_webhook.id)])
    logger.info(f"Webhook {db_webhook.id} for subscription {subscription_id} ingested and queued.")

    # Return 202 Accepted
    return {"message": "Webhook accepted for processing", "webhook_id": db_webhook.id}


# --- Status and Analytics ---
@app.get("/status/{webhook_id}", response_model=schemas.WebhookStatusRead)
def get_webhook_status(webhook_id: uuid.UUID, db: Session = Depends(get_db)):
    webhook = crud.get_webhook_with_attempts(db, webhook_id) # Eager load attempts
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    latest_attempt = None
    if webhook.attempts:
        # Attempts are ordered by time in the relationship definition
        latest_attempt = webhook.attempts[-1] # Get the last one

    # Manually construct the response model including all attempts
    status_read = schemas.WebhookStatusRead(
        id=webhook.id,
        subscription_id=webhook.subscription_id,
        ingested_at=webhook.ingested_at,
        status=webhook.status,
        latest_attempt=schemas.DeliveryAttemptRead.model_validate(latest_attempt) if latest_attempt else None,
        attempts=[schemas.DeliveryAttemptRead.model_validate(a) for a in webhook.attempts]
    )
    return status_read


@app.get("/subscriptions/{subscription_id}/logs", response_model=List[schemas.DeliveryAttemptRead])
def list_recent_subscription_logs(subscription_id: uuid.UUID, limit: int = 20, db: Session = Depends(get_db)):
    # Verify subscription exists first
    subscription = crud.get_subscription(db, subscription_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    attempts = crud.list_recent_delivery_attempts_for_subscription(db, subscription_id, limit=limit)

    # Convert SQLAlchemy models to Pydantic schemas
    return [schemas.DeliveryAttemptRead.model_validate(a) for a in attempts]

# Optional: Implement signature calculation helper (needs raw body, complex with FastAPI's default JSON parsing)
# See notes in ingest_webhook for complexity