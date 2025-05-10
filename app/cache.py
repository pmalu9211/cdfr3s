import redis
from typing import Optional
from .config import settings
from . import schemas
import uuid

redis_client = redis.StrictRedis.from_url(settings.redis_url, decode_responses=True) # decode_responses=True to get strings

def get_subscription_key(subscription_id: uuid.UUID):
    return f"subscription:{subscription_id}"

def get_subscription_from_cache(subscription_id: uuid.UUID) -> Optional[schemas.SubscriptionRead]:
    key = get_subscription_key(subscription_id)
    cached_data = redis_client.get(key)
    if cached_data:
        try:
            # Deserialize the JSON data back into the Pydantic schema
            return schemas.SubscriptionRead.model_validate_json(cached_data)
        except Exception as e:
            # Log error and invalidate cache if deserialization fails
            print(f"Error deserializing subscription {subscription_id} from cache: {e}")
            redis_client.delete(key)
            return None
    return None

def set_subscription_in_cache(subscription: schemas.SubscriptionRead):
    key = get_subscription_key(subscription.id)
    # Serialize the Pydantic schema to JSON
    redis_client.set(key, subscription.model_dump_json(), ex=settings.cache_ttl_seconds)

def invalidate_subscription_cache(subscription_id: uuid.UUID):
    key = get_subscription_key(subscription_id)
    redis_client.delete(key)