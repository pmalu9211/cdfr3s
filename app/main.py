from fastapi import FastAPI, Depends, HTTPException, status, Request, Query, Header, Body
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session, joinedload
from typing import List, Optional, Dict, Any
import uuid
import json
import hmac
import hashlib
import secrets

from . import crud, models, schemas
from .database import SessionLocal, engine, get_db
from .cache import get_subscription_from_cache, set_subscription_in_cache, invalidate_subscription_cache
from .config import settings

from . import tasks
from .celery_app import celery_app

import logging

# Configure basic logging - ensure level is INFO or DEBUG to see these logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Define the expected signature header name
SIGNATURE_HEADER_NAME = "X-Hub-Signature-256"
SIGNATURE_PREFIX = "sha256="

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
    invalidate_subscription_cache(db_subscription.id)
    logger.info(f"Subscription created: {db_subscription.id}")
    return db_subscription

@app.get("/subscriptions/", response_model=List[schemas.SubscriptionRead])
def read_subscriptions(skip: int = 0, limit: int = 100, db: Session = Depends(get_db)):
    subscriptions = crud.get_subscriptions(db, skip=skip, limit=limit)
    return subscriptions

@app.get("/subscriptions/{subscription_id}", response_model=schemas.SubscriptionRead)
def read_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)):
    cached_sub = get_subscription_from_cache(subscription_id)
    if cached_sub:
        return cached_sub

    db_subscription = crud.get_subscription(db, subscription_id)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    subscription_read_schema = schemas.SubscriptionRead.model_validate(db_subscription)
    set_subscription_in_cache(subscription_read_schema) # Cache the result

    return subscription_read_schema

@app.put("/subscriptions/{subscription_id}", response_model=schemas.SubscriptionRead)
def update_subscription(subscription_id: uuid.UUID, subscription: schemas.SubscriptionCreate, db: Session = Depends(get_db)):
    db_subscription = crud.update_subscription(db, subscription_id, subscription)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    invalidate_subscription_cache(subscription_id)
    logger.info(f"Subscription updated: {subscription_id}")
    return db_subscription

@app.delete("/subscriptions/{subscription_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_subscription(subscription_id: uuid.UUID, db: Session = Depends(get_db)):
    db_subscription = crud.delete_subscription(db, subscription_id)
    if db_subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
    invalidate_subscription_cache(subscription_id)
    logger.info(f"Subscription deleted: {subscription_id}")
    return

# Helper function to calculate the HMAC-SHA256 signature
def calculate_signature(secret: str, payload_bytes: bytes) -> str:
    """Calculates HMAC-SHA256 signature for a payload."""
    # logger.debug(f"Calculating signature with secret: '{secret}' and payload bytes: {payload_bytes}") # Keep this for debugging if needed
    secret_bytes = secret.encode('utf-8')
    signature = hmac.new(
        secret_bytes,
        payload_bytes,
        hashlib.sha256
    ).hexdigest()
    # logger.debug(f"Calculated hex signature: {signature}") # Keep this for debugging if needed
    return signature

# --- Webhook Ingestion ---
@app.post("/ingest/{subscription_id}", status_code=status.HTTP_202_ACCEPTED)
async def ingest_webhook(
    subscription_id: uuid.UUID,
    request: Request, # Keep Request to access raw body
    # Use Body for Swagger documentation and validation
    webhook_data: schemas.WebhookIngest = Body(..., description="The webhook payload and event type."),
    # Use Header for Swagger documentation and access to the header value
    x_hub_signature_256: Optional[str] = Header(None, alias=SIGNATURE_HEADER_NAME, description=f"HMAC-SHA256 signature, prefixed with '{SIGNATURE_PREFIX}'."),
    db: Session = Depends(get_db)
):
    # Verify subscription exists (try cache first)
    subscription = get_subscription_from_cache(subscription_id)
    if not subscription:
        db_subscription = crud.get_subscription(db, subscription_id)
        if not db_subscription:
            logger.warning(f"Ingest failed: Subscription {subscription_id} not found.")
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")
        subscription = schemas.SubscriptionRead.model_validate(db_subscription)
        set_subscription_in_cache(subscription) # Cache the subscription

    # --- Signature Verification (Bonus Point 1) ---
    raw_body = await request.body() # Read the raw request body

    # Add debug logging for the raw body received by the server
    logger.info(f"Ingest request for subscription {subscription_id}. Raw body received: {raw_body}")

    # Standardize the payload for signature calculation
    try:
        # Parse the incoming raw JSON body
        payload_dict = json.loads(raw_body)
        # Re-serialize into a standardized, compact JSON string
        standardized_payload_bytes = json.dumps(
            payload_dict,
            separators=(',', ':'), # Use compact separators
            sort_keys=True         # Sort keys for consistent order
        ).encode('utf-8')          # Encode to bytes
        logger.info(f"Standardized payload bytes for signature: {standardized_payload_bytes}")
    except json.JSONDecodeError:
        logger.warning(f"Ingest failed for subscription {subscription_id}: Invalid JSON payload.")
        # Reject if payload is not valid JSON
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid JSON payload."}
        )
    except Exception as e:
        logger.error(f"Error standardizing payload for signature: {e}", exc_info=True)
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error during payload standardization."}
        )


    # Check if the subscription has a secret configured
    if subscription.secret:
        received_signature_header = x_hub_signature_256

        if not received_signature_header:
            logger.warning(f"Ingest failed for subscription {subscription_id}: Missing {SIGNATURE_HEADER_NAME} header.")
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Missing {SIGNATURE_HEADER_NAME} header."}
            )

        if not received_signature_header.startswith(SIGNATURE_PREFIX):
             logger.warning(f"Ingest failed for subscription {subscription_id}: Invalid signature header format.")
             return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": f"Invalid {SIGNATURE_HEADER_NAME} format. Expected '{SIGNATURE_PREFIX}...'."}
            )

        received_signature = received_signature_header[len(SIGNATURE_PREFIX):]

        # Calculate the expected signature using the STANDARDIZED payload bytes
        # logger.info(f"Using secret for signature calculation: '{subscription.secret}'") # Keep for debugging if needed
        expected_signature = calculate_signature(subscription.secret, standardized_payload_bytes)

        logger.info(f"Calculated expected signature (standardized): {expected_signature}")
        logger.info(f"Received signature: {received_signature}")


        # Securely compare the received and expected signatures
        if not secrets.compare_digest(expected_signature, received_signature):
            # logger.warning(f"Mismatch: Expected '{expected_signature}', Received '{received_signature}'") # Keep for debugging if needed
            logger.warning(f"Ingest failed for subscription {subscription_id}: Invalid signature.")
            return JSONResponse(
                status_code=status.HTTP_403_FORBIDDEN,
                content={"detail": "Invalid signature."}
            )
        logger.info(f"Signature verified successfully for subscription {subscription_id}.")

    # --- End Signature Verification ---

    # --- Event Type Filtering (Bonus Point 2) ---
    # Use the payload_dict obtained from json.loads(raw_body) for filtering and saving
    payload_data = payload_dict # Use the dictionary form for accessing fields
    incoming_event_type = payload_data.get("event_type")

    # Check if the subscription has event type filters configured
    if subscription.event_types: # If event_types list is not empty or None
        if incoming_event_type is None:
             logger.warning(f"Ingest failed for subscription {subscription_id}: Event type filter configured, but 'event_type' missing in payload.")
             return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST,
                content={"detail": "Event type filter configured for subscription, but 'event_type' field is missing in the payload."}
            )

        # Check if the incoming event type is in the subscription's allowed list
        if incoming_event_type not in subscription.event_types:
            logger.info(f"Ingest skipped for subscription {subscription_id}: Event type '{incoming_event_type}' does not match filter.")
            # If event type doesn't match, accept but don't queue for *this* subscription.
            # We return 202 Accepted because the request itself was valid, just filtered.
            # No webhook record or task is created for this filtered event.
            return JSONResponse(
                status_code=status.HTTP_202_ACCEPTED,
                content={"message": f"Webhook accepted but filtered by event type: '{incoming_event_type}'."}
            )
        logger.info(f"Event type '{incoming_event_type}' matched subscription filter for {subscription_id}.")

    # --- End Event Type Filtering ---

    # If we reached here, the request is valid, verified (if needed), and not filtered.

    # Save the incoming webhook payload and its event type
    # Use the original payload_dict (or payload_data) for saving to the database
    db_webhook = crud.create_webhook(db, subscription_id, payload_data, event_type=incoming_event_type)

    # Enqueue the delivery task
    celery_app.send_task(
        'app.tasks.process_delivery',
        args=[str(db_webhook.id)],
    )
    logger.info(f"Webhook {db_webhook.id} for subscription {subscription_id} ingested and queued.")

    return {"message": "Webhook accepted for processing", "webhook_id": db_webhook.id}


# --- Status and Analytics ---
@app.get("/status/{webhook_id}", response_model=schemas.WebhookStatusRead)
def get_webhook_status(webhook_id: uuid.UUID, db: Session = Depends(get_db)):
    webhook = crud.get_webhook_with_attempts(db, webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    latest_attempt = None
    if webhook.attempts:
        latest_attempt = webhook.attempts[-1]

    status_read = schemas.WebhookStatusRead(
        id=webhook.id,
        subscription_id=webhook.subscription_id,
        ingested_at=webhook.ingested_at,
        status=webhook.status,
        latest_attempt=schemas.DeliveryAttemptRead(
            id=latest_attempt.id,
            webhook_id=latest_attempt.webhook_id,
            subscription_id=webhook.subscription.id,
            target_url=webhook.subscription.target_url,
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
                subscription_id=webhook.subscription.id,
                target_url=webhook.subscription.target_url,
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
    subscription = crud.get_subscription(db, subscription_id)
    if not subscription:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subscription not found")

    attempts_data = crud.list_recent_delivery_attempts_for_subscription(db, subscription_id, limit=limit)

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

@app.get("/logs/", response_model=List[schemas.DeliveryAttemptRead])
def list_all_logs(skip: int = Query(0, ge=0), limit: int = Query(100, ge=1, le=100), db: Session = Depends(get_db)):
    attempts_data = crud.list_all_delivery_attempts(db, skip=skip, limit=limit)

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
