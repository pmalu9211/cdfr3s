import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime, timezone

def utcnow():
    return datetime.now(timezone.utc)

# Define the Subscription model, mapping to the 'subscriptions' table
class Subscription(Base):
    # Specify the table name in the database
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_url = Column(String, nullable=False)
    secret = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # Define relationship to Webhook model
    # 'webhooks' attribute will be a list of Webhook objects related to this subscription
    # back_populates creates a bidirectional relationship
    webhooks = relationship("Webhook", back_populates="subscription")

# Define the Webhook model, mapping to the 'webhooks' table
# This represents a single incoming webhook payload
class Webhook(Base):
    __tablename__ = "webhooks"

    # id: UUID primary key, automatically generated
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # ondelete="CASCADE" ensures that deleting a subscription deletes its related webhooks
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    payload = Column(JSONB, nullable=False)
    event_type = Column(String, nullable=True) # For bonus
    ingested_at = Column(DateTime(timezone=True), default=utcnow)
    status = Column(String, nullable=False, default="queued") # e.g., queued, processing, succeeded, failed

    # Define relationship back to Subscription model
    # 'subscription' attribute will be the single Subscription object this webhook belongs to
    subscription = relationship("Subscription", back_populates="webhooks")
    # Define relationship to DeliveryAttempt model
    # 'attempts' attribute will be a list of DeliveryAttempt objects for this webhook
    # order_by ensures attempts are retrieved in chronological order
    attempts = relationship("DeliveryAttempt", back_populates="webhook", order_by="DeliveryAttempt.attempted_at")


# Define the DeliveryAttempt model, mapping to the 'delivery_attempts' table
# This logs each individual attempt to deliver a specific webhook
class DeliveryAttempt(Base):
    # Specify the table name
    __tablename__ = "delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # ondelete="CASCADE" ensures that deleting a webhook deletes its related attempts
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    attempted_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    outcome = Column(String, nullable=False)
    http_status_code = Column(Integer, nullable=True)
    error_details = Column(String, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    # Define relationship back to Webhook model
    # 'webhook' attribute will be the single Webhook object this attempt belongs to
    webhook = relationship("Webhook", back_populates="attempts")