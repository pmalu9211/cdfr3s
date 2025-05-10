import uuid
from datetime import datetime, timezone, timedelta # <-- Ensure timedelta is imported here
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func

from . import models, schemas # Make sure these imports are correct

# Helper to get timezone-aware current time
def utcnow():
    return datetime.now(timezone.utc)

# --- Subscription CRUD ---
def get_subscription(db: Session, subscription_id: uuid.UUID):
    return db.query(models.Subscription).filter(models.Subscription.id == subscription_id).first()

def get_subscriptions(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Subscription).offset(skip).limit(limit).all()

def create_subscription(db: Session, subscription: schemas.SubscriptionCreate):
    db_subscription = models.Subscription(
        # FIX: Convert the Pydantic HttpUrl object to a string before saving to DB
        target_url=str(subscription.target_url),
        secret=subscription.secret,
        # event_types=subscription.event_types # For bonus
    )
    db.add(db_subscription)
    db.commit()
    db.refresh(db_subscription)
    return db_subscription

def update_subscription(db: Session, subscription_id: uuid.UUID, subscription: schemas.SubscriptionCreate):
    db_subscription = db.query(models.Subscription).filter(models.Subscription.id == subscription_id).first()
    if db_subscription:
        # FIX: Convert the Pydantic HttpUrl object to a string before updating in DB
        db_subscription.target_url = str(subscription.target_url)
        db_subscription.secret = subscription.secret
        # db_subscription.event_types = subscription.event_types # For bonus
        # updated_at is set by the trigger (or onupdate in model)
        db.commit()
        db.refresh(db_subscription)
    return db_subscription

def delete_subscription(db: Session, subscription_id: uuid.UUID):
    db_subscription = db.query(models.Subscription).filter(models.Subscription.id == subscription_id).first()
    if db_subscription:
        db.delete(db_subscription)
        db.commit()
    return db_subscription

# --- Webhook and Delivery Operations ---
def create_webhook(db: Session, subscription_id: uuid.UUID, payload: dict, event_type: Optional[str] = None):
    db_webhook = models.Webhook(
        subscription_id=subscription_id,
        payload=payload,
        event_type=event_type,
        status="queued" # Initial status
    )
    db.add(db_webhook)
    db.commit()
    db.refresh(db_webhook)
    return db_webhook

def get_webhook_with_attempts(db: Session, webhook_id: uuid.UUID):
     # Eager load attempts for the status endpoint
    return db.query(models.Webhook)\
             .options(joinedload(models.Webhook.attempts))\
             .filter(models.Webhook.id == webhook_id).first()

def get_webhook(db: Session, webhook_id: uuid.UUID):
    return db.query(models.Webhook).filter(models.Webhook.id == webhook_id).first()

def update_webhook_status(db: Session, webhook_id: uuid.UUID, status: str):
    db_webhook = get_webhook(db, webhook_id)
    if db_webhook:
        db_webhook.status = status
        db.commit()
        db.refresh(db_webhook)
    return db_webhook

def create_delivery_attempt(
    db: Session,
    webhook_id: uuid.UUID,
    attempt_number: int,
    outcome: str, # attempted, succeeded, failed_attempt, permanently_failed
    http_status_code: Optional[int] = None,
    error_details: Optional[str] = None,
    next_attempt_at: Optional[datetime] = None
):
    db_attempt = models.DeliveryAttempt(
        webhook_id=webhook_id,
        attempt_number=attempt_number,
        outcome=outcome,
        http_status_code=http_status_code,
        error_details=error_details,
        next_attempt_at=next_attempt_at
    )
    db.add(db_attempt)
    db.commit()
    db.refresh(db_attempt)
    return db_attempt

def get_delivery_attempts_for_webhook(db: Session, webhook_id: uuid.UUID):
    return db.query(models.DeliveryAttempt)\
             .filter(models.DeliveryAttempt.webhook_id == webhook_id)\
             .order_by(models.DeliveryAttempt.attempted_at)\
             .all()

def get_latest_attempt_for_webhook(db: Session, webhook_id: uuid.UUID):
     return db.query(models.DeliveryAttempt)\
              .filter(models.DeliveryAttempt.webhook_id == webhook_id)\
              .order_by(models.DeliveryAttempt.attempted_at.desc())\
              .first()

def list_recent_delivery_attempts_for_subscription(db: Session, subscription_id: uuid.UUID, limit: int = 20):
    # This query is a bit complex: get attempts, join webhooks, filter by sub_id, order by attempt time.
    # Could optimize with materialized views or dedicated logging sink for high volume.
    return db.query(models.DeliveryAttempt)\
             .join(models.Webhook)\
             .filter(models.Webhook.subscription_id == subscription_id)\
             .order_by(models.DeliveryAttempt.attempted_at.desc())\
             .limit(limit)\
             .all()

def cleanup_old_logs(db: Session, retention_hours: int):
    time_threshold = utcnow() - timedelta(hours=retention_hours)

    # Delete attempts first due to foreign key constraint
    deleted_attempts_count = db.query(models.DeliveryAttempt)\
                               .filter(models.DeliveryAttempt.attempted_at < time_threshold)\
                               .delete(synchronize_session=False)

    # Delete webhooks that have no remaining attempts and are older than the threshold
    # (This is a safer approach to avoid deleting webhooks that still have recent retry attempts)
    # A simpler approach for this assignment is to delete webhooks if ALL attempts are older,
    # or just delete webhooks older than the threshold IF they are not 'queued' or 'processing'.
    # Let's go with a simple delete of webhooks whose *ingestion time* is older than the threshold
    # and whose status is final ('succeeded' or 'failed'). This prevents deleting webhooks
    # that might still have pending retries even if ingested long ago.
    deleted_webhooks_count = db.query(models.Webhook)\
                               .filter(models.Webhook.ingested_at < time_threshold)\
                               .filter(models.Webhook.status.in_(['succeeded', 'failed']))\
                               .delete(synchronize_session=False)

    db.commit()
    return deleted_attempts_count, deleted_webhooks_count
