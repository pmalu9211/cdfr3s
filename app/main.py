from fastapi import FastAPI, Depends, HTTPException, status, Request, Query # Import Query
from sqlalchemy.orm import Session, joinedload
from typing import List
import uuid

from . import crud, models, schemas
from .database import SessionLocal, engine, get_db
from .cache import get_subscription_from_cache, set_subscription_in_cache, invalidate_subscription_cache
from .config import settings

# Explicitly import tasks to ensure Celery app and tasks are registered
from . import tasks

# Import the celery_app instance directly
from .celery_app import celery_app

import logging

# Configure basic logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Webhook Delivery Service",
    description="A reliable webhook delivery system with retries and logging.",
    version="1.0.0",
)

# Dependency to get cache client (placeholder, cache functions use global client)
def get_cache_client():
    pass

@app.on_event("startup")
async def startup_event():
    logger.info("Application startup complete.")

@app.on_event("shutdown")
async def shutdown_event():
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

    # Save the incoming webhook payload
    db_webhook = crud.create_webhook(db, subscription_id, webhook.payload) # Add event_type here if applicable

    # Enqueue the delivery task using the explicit celery_app instance
    celery_app.send_task(
        'app.tasks.process_delivery', # Task name as a string
        args=[str(db_webhook.id)],
        # Optional: countdown=... for delayed start
    )
    logger.info(f"Webhook {db_webhook.id} for subscription {subscription_id} ingested and queued.")

    # Return 202 Accepted
    return {"message": "Webhook accepted for processing", "webhook_id": db_webhook.id}


# --- Status and Analytics ---
@app.get("/status/{webhook_id}", response_model=schemas.WebhookStatusRead)
def get_webhook_status(webhook_id: uuid.UUID, db: Session = Depends(get_db)):
    # Use the updated crud function that eager loads subscription
    webhook = crud.get_webhook_with_attempts(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    latest_attempt = None
    if webhook.attempts:
        latest_attempt = webhook.attempts[-1] # Get the last one

    # Manually construct the response model, mapping data from joined tables
    # The webhook object now has webhook.subscription populated due to the joinedload in crud
    status_read = schemas.WebhookStatusRead(
        id=webhook.id,
        subscription_id=webhook.subscription_id,
        ingested_at=webhook.ingested_at,
        status=webhook.status,
        latest_attempt=schemas.DeliveryAttemptRead(
            id=latest_attempt.id,
            webhook_id=latest_attempt.webhook_id,
            subscription_id=webhook.subscription.id, # Get from webhook -> subscription relationship
            target_url=webhook.subscription.target_url, # Get from webhook -> subscription relationship
            attempt_number=latest_attempt.attempt_number,
            attempted_at=latest_attempt.attempted_at,
            outcome=latest_attempt.outcome,
            http_status_code=latest_attempt.http_status_code,
            error_details=latest_attempt.error_details,
            next_attempt_at=latest_attempt.next_attempt_at
        ) if latest_attempt else None,
        attempts=[
            schemas.DeliveryAttemptRead(
                id=a.id,
                webhook_id=a.webhook_id,
                subscription_id=webhook.subscription.id, # Get from webhook -> subscription relationship
                target_url=webhook.subscription.target_url, # Get from webhook -> subscription relationship
                attempt_number=a.attempt_number,
                attempted_at=a.attempted_at,
                outcome=a.outcome,
                http_status_code=a.http_status_code,
                error_details=a.error_details,
                next_attempt_at=a.next_attempt_at
            ) for a in webhook.attempts
        ]
    )
    return status_read


@app.get("/subscriptions/{subscription_id}/logs", response_model=List[schemas.DeliveryAttemptRead])
def list_recent_subscription_logs(subscription_id: uuid.UUID, limit: int = Query(20, ge=1, le=100), db: Session = Depends(get_db)):
    # Verify subscription exists first
    subscription = crud.get_subscription(db, subscription_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    # Use the updated crud function that joins tables to get the required data
    attempts_data = crud.list_recent_delivery_attempts_for_subscription(db, subscription_id, limit=limit)

    # Manually map the results (which are Row objects from the joined query) to the Pydantic schema
    attempts = [
        schemas.DeliveryAttemptRead(
            id=data.id,
            webhook_id=data.webhook_id,
            subscription_id=data.subscription_id,
            target_url=data.target_url,
            attempt_number=data.attempt_number,
            attempted_at=data.attempted_at,
            outcome=data.outcome,
            http_status_code=data.http_status_code,
            error_details=data.error_details,
            next_attempt_at=data.next_attempt_at
        ) for data in attempts_data
    ]

    return attempts

# New endpoint to get all delivery logs
@app.get("/logs/", response_model=List[schemas.DeliveryAttemptRead])
def list_all_logs(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db)):
    # Use the new crud function to get all logs with subscription details
    attempts_data = crud.list_all_delivery_attempts(db, skip=skip, limit=limit)

    # Manually map the results (Row objects) to the Pydantic schema
    attempts = [
        schemas.DeliveryAttemptRead(
            id=data.id,
            webhook_id=data.webhook_id,
            subscription_id=data.subscription_id,
            target_url=data.target_url,
            attempt_number=data.attempt_number,
            attempted_at=data.attempted_at,
            outcome=data.outcome,
            http_status_code=data.http_status_code,
            error_details=data.error_details,
            next_attempt_at=data.next_attempt_at
        ) for data in attempts_data
    ]

    return attempts
