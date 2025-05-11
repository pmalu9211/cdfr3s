-- Create uuid-ossp extension if not already present
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Subscriptions table
CREATE TABLE subscriptions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    target_url VARCHAR(255) NOT NULL,
    secret VARCHAR(255), -- Optional secret for signature verification
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    -- Add event_types column for filtering
    event_types TEXT[] -- Array of text strings for event types
);

-- Webhooks table (represents an incoming ingested webhook)
CREATE TABLE webhooks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subscription_id UUID NOT NULL REFERENCES subscriptions(id) ON DELETE CASCADE,
    payload JSONB NOT NULL,
    -- Add event_type column to store the incoming event type
    event_type VARCHAR(100),
    ingested_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    status VARCHAR(50) NOT NULL DEFAULT 'queued' -- e.g., queued, processing, succeeded, failed
);
CREATE INDEX idx_webhooks_subscription_id ON webhooks (subscription_id);
CREATE INDEX idx_webhooks_status ON webhooks (status);
-- Optional: Index event_type if you plan to query/filter by it frequently
-- CREATE INDEX idx_webhooks_event_type ON webhooks (event_type);


-- Delivery Attempts table (logs each attempt for a webhook)
CREATE TABLE delivery_attempts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    webhook_id UUID NOT NULL REFERENCES webhooks(id) ON DELETE CASCADE,
    attempt_number INTEGER NOT NULL,
    attempted_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    outcome VARCHAR(50) NOT NULL, -- e.g., attempted, succeeded, failed_attempt, permanently_failed
    http_status_code INTEGER, -- NULL if network error
    error_details TEXT,
    next_attempt_at TIMESTAMP WITH TIME ZONE -- For scheduled retries
);
CREATE INDEX idx_delivery_attempts_webhook_id ON delivery_attempts (webhook_id);
CREATE INDEX idx_delivery_attempts_attempted_at ON delivery_attempts (attempted_at);
CREATE INDEX idx_delivery_attempts_outcome ON delivery_attempts (outcome);
-- Optional index for efficient retry fetching if needed (though Celery handles scheduling)
-- CREATE INDEX idx_delivery_attempts_next_attempt_at ON delivery_attempts (next_attempt_at) WHERE next_attempt_at IS NOT NULL;

-- Trigger to update updated_at on subscriptions
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = CURRENT_TIMESTAMP;
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_subscription_updated_at
BEFORE UPDATE ON subscriptions
FOR EACH ROW
EXECUTE FUNCTION update_updated_at_column();
