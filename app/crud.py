import uuid
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import desc, func

from . import models, schemas

def utcnow():
    return datetime.now(timezone.utc)

# --- Subscription CRUD ---
def get_subscription(db: Session, subscription_id: uuid.UUID):
    return db.query(models.Subscription).filter(models.Subscription.id == subscription_id).first()

def get_subscriptions(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.Subscription).offset(skip).limit(limit).all()

def create_subscription(db: Session, subscription: schemas.SubscriptionCreate):
    db_subscription = models.Subscription(
        target_url=str(subscription.target_url),
        secret=subscription.secret,
        event_types=subscription.event_types # Use event_types from schema
    )
    db.add(db_subscription)
    db.commit()
    db.refresh(db_subscription)
    return db_subscription

def update_subscription(db: Session, subscription_id: uuid.UUID, subscription: schemas.SubscriptionCreate):
    db_subscription = db.query(models.Subscription).filter(models.Subscription.id == subscription_id).first()
    if db_subscription:
        db_subscription.target_url = str(subscription.target_url)
        db_subscription.secret = subscription.secret
        db_subscription.event_types = subscription.event_types # Use event_types from schema
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
        status="queued"
    )
    db.add(db_webhook)
    db.commit()
    db.refresh(db_webhook)
    return db_webhook

def get_webhook_with_attempts(db: Session, webhook_id: uuid.UUID):
    return db.query(models.Webhook)\
             .options(joinedload(models.Webhook.attempts).joinedload(models.DeliveryAttempt.webhook).joinedload(models.Webhook.subscription))\
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
    outcome: str,
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
    return db.query(
                models.DeliveryAttempt.id,
                models.DeliveryAttempt.webhook_id,
                models.Webhook.subscription_id,
                models.Subscription.target_url,
                models.DeliveryAttempt.attempt_number,
                models.DeliveryAttempt.attempted_at,
                models.DeliveryAttempt.outcome,
                models.DeliveryAttempt.http_status_code,
                models.DeliveryAttempt.error_details,
                models.DeliveryAttempt.next_attempt_at
            )\
             .join(models.Webhook, models.DeliveryAttempt.webhook_id == models.Webhook.id)\
             .join(models.Subscription, models.Webhook.subscription_id == models.Subscription.id)\
             .filter(models.Webhook.subscription_id == subscription_id)\
             .order_by(models.DeliveryAttempt.attempted_at.desc())\
             .limit(limit)\
             .all()

def list_all_delivery_attempts(db: Session, skip: int = 0, limit: int = 100):
    return db.query(
                models.DeliveryAttempt.id,
                models.DeliveryAttempt.webhook_id,
                models.Webhook.subscription_id,
                models.Subscription.target_url,
                models.DeliveryAttempt.attempt_number,
                models.DeliveryAttempt.attempted_at,
                models.DeliveryAttempt.outcome,
                models.DeliveryAttempt.http_status_code,
                models.DeliveryAttempt.error_details,
                models.DeliveryAttempt.next_attempt_at
            )\
             .join(models.Webhook, models.DeliveryAttempt.webhook_id == models.Webhook.id)\
             .join(models.Subscription, models.Webhook.subscription_id == models.Subscription.id)\
             .order_by(models.DeliveryAttempt.attempted_at.desc())\
             .offset(skip)\
             .limit(limit)\
             .all()


def cleanup_old_logs(db: Session, retention_hours: int):
    time_threshold = utcnow() - timedelta(hours=retention_hours)

    deleted_attempts_count = db.query(models.DeliveryAttempt)\
                               .filter(models.DeliveryAttempt.attempted_at < time_threshold)\
                               .delete(synchronize_session=False)

    deleted_webhooks_count = db.query(models.Webhook)\
                               .filter(models.Webhook.ingested_at < time_threshold)\
                               .filter(models.Webhook.status.in_(['succeeded', 'failed']))\
                               .delete(synchronize_session=False)

    db.commit()
    return deleted_attempts_count, deleted_webhooks_count
