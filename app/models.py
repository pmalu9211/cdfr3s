import uuid
from sqlalchemy import Column, String, DateTime, ForeignKey, JSON, Integer, text
# Import necessary types for PostgreSQL dialects
from sqlalchemy.dialects.postgresql import UUID, JSONB
# Import relationship for defining model relationships
from sqlalchemy.orm import relationship
# Import Base from your local database setup file
from .database import Base
# Import datetime and timezone for timestamp handling
from datetime import datetime, timezone

# Helper function to get timezone-aware current time
# This is important for consistency when storing timestamps
def utcnow():
    return datetime.now(timezone.utc)

# Define the Subscription model, mapping to the 'subscriptions' table
class Subscription(Base):
    # Specify the table name in the database
    __tablename__ = "subscriptions"

    # Define columns that match the database schema (init.sql)
    # id: UUID primary key, automatically generated
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # target_url: String, cannot be null
    target_url = Column(String, nullable=False)
    # secret: String, can be null (optional for signature verification)
    secret = Column(String, nullable=True)
    # created_at: DateTime with timezone, defaults to current UTC time on creation
    created_at = Column(DateTime(timezone=True), default=utcnow)
    # updated_at: DateTime with timezone, defaults to current UTC time on creation
    # and updates to current UTC time on every model update
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    # event_types: Optional array of strings (commented out, for bonus point)
    # event_types = Column(ARRAY(String), nullable=True) # For bonus

    # Define relationship to Webhook model
    # 'webhooks' attribute will be a list of Webhook objects related to this subscription
    # back_populates creates a bidirectional relationship
    webhooks = relationship("Webhook", back_populates="subscription")

# Define the Webhook model, mapping to the 'webhooks' table
# This represents a single incoming webhook payload
class Webhook(Base):
    # Specify the table name
    __tablename__ = "webhooks"

    # id: UUID primary key, automatically generated
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # subscription_id: Foreign key linking to the subscriptions table
    # ondelete="CASCADE" ensures that deleting a subscription deletes its related webhooks
    subscription_id = Column(UUID(as_uuid=True), ForeignKey("subscriptions.id", ondelete="CASCADE"), nullable=False)
    # payload: JSONB type for storing JSON data, cannot be null
    payload = Column(JSONB, nullable=False)
    # event_type: String, can be null (optional for bonus point)
    event_type = Column(String, nullable=True) # For bonus
    # ingested_at: DateTime with timezone, defaults to current UTC time when webhook is ingested
    ingested_at = Column(DateTime(timezone=True), default=utcnow)
    # status: String, cannot be null, default status is 'queued'
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

    # id: UUID primary key, automatically generated
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # webhook_id: Foreign key linking to the webhooks table
    # ondelete="CASCADE" ensures that deleting a webhook deletes its related attempts
    webhook_id = Column(UUID(as_uuid=True), ForeignKey("webhooks.id", ondelete="CASCADE"), nullable=False)
    # attempt_number: Integer, cannot be null (1 for initial, 2+ for retries)
    attempt_number = Column(Integer, nullable=False)
    # attempted_at: DateTime with timezone, defaults to current UTC time when attempt is logged
    attempted_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    # outcome: String, cannot be null (e.g., attempted, succeeded, failed_attempt, permanently_failed)
    outcome = Column(String, nullable=False)
    # http_status_code: Integer, can be null (if network error before getting status)
    http_status_code = Column(Integer, nullable=True)
    # error_details: String, can be null (details on failure)
    error_details = Column(String, nullable=True)
    # next_attempt_at: DateTime with timezone, can be null (timestamp for next retry)
    next_attempt_at = Column(DateTime(timezone=True), nullable=True)

    # Define relationship back to Webhook model
    # 'webhook' attribute will be the single Webhook object this attempt belongs to
    webhook = relationship("Webhook", back_populates="attempts")

# Note: The trigger for updated_at on the subscriptions table is defined in init.sql,
# not here in the model file. SQLAlchemy's onupdate handles the model side.
