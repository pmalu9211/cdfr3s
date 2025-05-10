import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from .database import Base
from datetime import datetime, timezone

# Helper to get timezone-aware current time
def utcnow():
    return datetime.now(timezone.utc)

class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_url = Column(String, nullable=False)
    secret = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # event_types = Column(ARRAY(String), nullable=True) # For bonus

    webhooks = relationship("Webhook", back_populates="subscription")

class Webhook(Base):
    __tablename__ = "webhooks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    payload = Column(JSONB, nullable=False)
    event_type = Column(String, nullable=True) # For bonus
    ingested_at = Column(DateTime(timezone=True), default=utcnow)
    status = Column(String, nullable=False, default="queued") # queued, processing, succeeded, failed

    subscription = relationship("Subscription", back_populates="webhooks")
    attempts = relationship("DeliveryAttempt", back_populates="webhook", order_by="DeliveryAttempt.attempted_at")


class DeliveryAttempt(Base):
    __tablename__ = "delivery_attempts"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    attempt_number = Column(Integer, nullable=False)
    attempted_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    outcome = Column(String, nullable=False) # attempted, succeeded, failed_attempt, permanently_failed
    http_status_code = Column(Integer, nullable=True)
    error_details = Column(String, nullable=True)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True) # For scheduled retries

    webhook = relationship("Webhook", back_populates="attempts")