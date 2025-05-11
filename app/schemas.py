import uuid
from pydantic import BaseModel, HttpUrl
from typing import List, Optional, Dict, Any
from datetime import datetime

class SubscriptionBase(BaseModel):
    target_url: HttpUrl
    secret: Optional[str] = None

class SubscriptionCreate(SubscriptionBase):
    pass

class SubscriptionRead(SubscriptionBase):
    id: uuid.UUID
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class WebhookIngest(BaseModel):
    payload: Dict[str, Any]

class DeliveryAttemptRead(BaseModel):
    id: uuid.UUID
    webhook_id: uuid.UUID
    subscription_id: uuid.UUID
    target_url: HttpUrl

    attempt_number: int
    attempted_at: datetime
    outcome: str
    http_status_code: Optional[int] = None
    error_details: Optional[str] = None
    next_attempt_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class WebhookStatusRead(BaseModel):
    id: uuid.UUID
    subscription_id: uuid.UUID
    ingested_at: datetime
    status: str
    latest_attempt: Optional[DeliveryAttemptRead] = None
    attempts: List[DeliveryAttemptRead]

    class Config:
        from_attributes = True