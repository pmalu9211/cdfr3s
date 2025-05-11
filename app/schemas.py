import uuid
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any
from datetime import datetime

class SubscriptionBase(BaseModel):
    target_url: HttpUrl
    secret: Optional[str] = None
    # event_types: Optional[List[str]] = None # For bonus

class SubscriptionCreate(SubscriptionBase):
    pass

class SubscriptionRead(SubscriptionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True # Allow mapping from SQLAlchemy models


class WebhookIngest(BaseModel):
    # Assume payload is arbitrary JSON
    payload: Dict[str, Any]
    # event_type: Optional[str] = None # For bonus

class DeliveryAttemptRead(BaseModel):
    id: uuid.UUID
    webhook_id: uuid.UUID
    # Ensure these fields are present for log output
    subscription_id: uuid.UUID
    target_url: HttpUrl # Use HttpUrl for validation/typing

    attempt_number: int
    attempted_at: datetime
    outcome: str
    http_status_code: Optional[int] = None
    error_details: Optional[str] = None # This will contain more detail from the worker
    next_attempt_at: Optional[datetime] = None

    class Config:
        from_attributes = True # Allow mapping from SQLAlchemy models


class WebhookStatusRead(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    ingested_at: datetime
    status: str
    latest_attempt: Optional[DeliveryAttemptRead] = None
    attempts: List[DeliveryAttemptRead] # Include all attempts for detail view

    class Config:
        from_attributes = True