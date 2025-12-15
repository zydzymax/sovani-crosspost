-- Migration: 0002_outbox_rules.sql
-- Description: Outbox pattern, deduplication, circuit breakers, publishing rules
-- Created: 2024-01-01

BEGIN;

-- Outbox table for reliable message publishing (Transactional Outbox Pattern)
CREATE TABLE outbox (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Event identification
    aggregate_type VARCHAR(100) NOT NULL, -- 'product', 'post', 'media_asset'
    aggregate_id UUID NOT NULL,
    event_type VARCHAR(100) NOT NULL, -- 'created', 'updated', 'published', 'failed'
    
    -- Idempotency
    idempotency_key VARCHAR(255) UNIQUE NOT NULL,
    
    -- Event payload
    event_data JSONB NOT NULL DEFAULT '{}',
    event_metadata JSONB DEFAULT '{}',
    
    -- Routing information
    destination_queue VARCHAR(100) NOT NULL,
    routing_key VARCHAR(255),
    priority INTEGER DEFAULT 5 CHECK (priority BETWEEN 0 AND 9),
    
    -- Scheduling
    scheduled_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    not_before TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    expires_at TIMESTAMP WITH TIME ZONE,
    
    -- Processing status
    status VARCHAR(50) DEFAULT 'pending', -- pending, processing, completed, failed, expired
    attempts INTEGER DEFAULT 0,
    max_attempts INTEGER DEFAULT 3,
    
    -- Error tracking
    last_error TEXT,
    error_count INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    processed_at TIMESTAMP WITH TIME ZONE,
    
    -- Constraints
    CHECK (scheduled_at <= expires_at OR expires_at IS NULL),
    CHECK (not_before <= scheduled_at)
);

-- Deduplication table for preventing duplicate processing
CREATE TABLE dedupe (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Deduplication key (content hash, external ID, etc.)
    dedupe_key VARCHAR(255) UNIQUE NOT NULL,
    dedupe_type VARCHAR(100) NOT NULL, -- 'content_hash', 'telegram_message', 'post_id'
    
    -- Reference to original entity
    entity_type VARCHAR(100) NOT NULL,
    entity_id UUID NOT NULL,
    
    -- Metadata
    metadata JSONB DEFAULT '{}',
    
    -- Expiration for cleanup
    expires_at TIMESTAMP WITH TIME ZONE DEFAULT NOW() + INTERVAL '30 days',
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Circuit breakers for external service reliability
CREATE TABLE circuit_breakers (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Service identification
    service_name VARCHAR(100) UNIQUE NOT NULL, -- 'instagram_api', 'vk_api', 'tiktok_api'
    endpoint VARCHAR(255),
    
    -- Circuit breaker state
    state VARCHAR(20) DEFAULT 'closed', -- closed, open, half_open
    failure_count INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    
    -- Thresholds
    failure_threshold INTEGER DEFAULT 5,
    recovery_timeout_seconds INTEGER DEFAULT 60,
    success_threshold INTEGER DEFAULT 2, -- for half_open -> closed
    
    -- Timing
    last_failure_at TIMESTAMP WITH TIME ZONE,
    last_success_at TIMESTAMP WITH TIME ZONE,
    state_changed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Statistics (rolling window)
    total_requests INTEGER DEFAULT 0,
    total_failures INTEGER DEFAULT 0,
    avg_response_time_ms DECIMAL(10,2) DEFAULT 0,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Publishing rules for platform-specific logic
CREATE TABLE publishing_rules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Rule identification
    rule_name VARCHAR(100) UNIQUE NOT NULL,
    platform VARCHAR(50) NOT NULL,
    rule_type VARCHAR(50) NOT NULL, -- 'content_filter', 'format_requirement', 'schedule_constraint'
    
    -- Rule definition
    conditions JSONB NOT NULL DEFAULT '{}', -- JSON schema for matching conditions
    actions JSONB NOT NULL DEFAULT '{}',    -- Actions to take when rule matches
    
    -- Rule metadata
    priority INTEGER DEFAULT 100,
    is_active BOOLEAN DEFAULT TRUE,
    description TEXT,
    
    -- Usage statistics
    match_count INTEGER DEFAULT 0,
    last_matched_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Add idempotency fields to existing tables
ALTER TABLE posts ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) UNIQUE;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP WITH TIME ZONE;

ALTER TABLE media_assets ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) UNIQUE;
ALTER TABLE renditions ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) UNIQUE;

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) UNIQUE;

-- Add scheduled_at to products for delayed processing
ALTER TABLE products ADD COLUMN IF NOT EXISTS scheduled_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE products ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR(255) UNIQUE;

-- Performance indexes for outbox
CREATE INDEX idx_outbox_status ON outbox(status);
CREATE INDEX idx_outbox_scheduled_at ON outbox(scheduled_at) WHERE status IN ('pending', 'processing');
CREATE INDEX idx_outbox_aggregate ON outbox(aggregate_type, aggregate_id);
CREATE INDEX idx_outbox_destination_queue ON outbox(destination_queue);
CREATE INDEX idx_outbox_created_at ON outbox(created_at);
CREATE INDEX idx_outbox_priority ON outbox(priority DESC);

-- Partial indexes for active outbox entries
CREATE INDEX idx_outbox_pending ON outbox(scheduled_at, priority DESC)
    WHERE status = 'pending';

CREATE INDEX idx_outbox_retry ON outbox(scheduled_at)
    WHERE status = 'failed';

-- Deduplication indexes
CREATE INDEX idx_dedupe_type_key ON dedupe(dedupe_type, dedupe_key);
CREATE INDEX idx_dedupe_entity ON dedupe(entity_type, entity_id);
CREATE INDEX idx_dedupe_expires_at ON dedupe(expires_at);

-- Circuit breaker indexes
CREATE INDEX idx_circuit_breakers_service ON circuit_breakers(service_name);
CREATE INDEX idx_circuit_breakers_state ON circuit_breakers(state);

-- Publishing rules indexes
CREATE INDEX idx_publishing_rules_platform ON publishing_rules(platform, is_active) WHERE is_active = TRUE;
CREATE INDEX idx_publishing_rules_type ON publishing_rules(rule_type);
CREATE INDEX idx_publishing_rules_priority ON publishing_rules(priority) WHERE is_active = TRUE;

-- New indexes for idempotency fields
CREATE INDEX idx_posts_idempotency_key ON posts(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_products_idempotency_key ON products(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_media_assets_idempotency_key ON media_assets(idempotency_key) WHERE idempotency_key IS NOT NULL;
CREATE INDEX idx_tasks_idempotency_key ON tasks(idempotency_key) WHERE idempotency_key IS NOT NULL;

-- Enhanced scheduling indexes
CREATE INDEX IF NOT EXISTS idx_posts_scheduled_status ON posts(scheduled_at, status) WHERE scheduled_at IS NOT NULL AND status IN ('draft', 'scheduled');
CREATE INDEX IF NOT EXISTS idx_products_scheduled_at ON products(scheduled_at) WHERE scheduled_at IS NOT NULL;

-- Composite indexes for common queries
CREATE INDEX idx_posts_platform_status_scheduled ON posts(platform, status, scheduled_at) 
    WHERE status IN ('draft', 'scheduled');

CREATE INDEX idx_tasks_queue_status_created ON tasks(queue_name, status, created_at) 
    WHERE status IN ('pending', 'running');

-- GIN indexes for new JSONB fields
CREATE INDEX idx_outbox_event_data ON outbox USING GIN(event_data);
CREATE INDEX idx_outbox_event_metadata ON outbox USING GIN(event_metadata);
CREATE INDEX idx_dedupe_metadata ON dedupe USING GIN(metadata);
CREATE INDEX idx_publishing_rules_conditions ON publishing_rules USING GIN(conditions);
CREATE INDEX idx_publishing_rules_actions ON publishing_rules USING GIN(actions);

-- Add triggers for new tables
CREATE TRIGGER update_circuit_breakers_updated_at BEFORE UPDATE ON circuit_breakers
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_publishing_rules_updated_at BEFORE UPDATE ON publishing_rules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Cleanup function for expired entries
CREATE OR REPLACE FUNCTION cleanup_expired_entries()
RETURNS INTEGER AS $$
DECLARE
    deleted_count INTEGER := 0;
    temp_count INTEGER;
BEGIN
    -- Clean up expired outbox entries
    DELETE FROM outbox 
    WHERE status = 'completed' 
    AND processed_at < NOW() - INTERVAL '7 days';
    
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    -- Clean up expired dedupe entries
    DELETE FROM dedupe 
    WHERE expires_at < NOW();
    
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    -- Clean up old logs (keep only last 30 days)
    DELETE FROM logs 
    WHERE created_at < NOW() - INTERVAL '30 days'
    AND level NOT IN ('ERROR', 'CRITICAL');
    
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    -- Clean up old completed tasks
    DELETE FROM tasks 
    WHERE status IN ('success', 'failure')
    AND completed_at < NOW() - INTERVAL '14 days';
    
    GET DIAGNOSTICS temp_count = ROW_COUNT;
    deleted_count := deleted_count + temp_count;
    
    RETURN deleted_count;
END;
$$ LANGUAGE plpgsql;

-- Insert default circuit breakers
INSERT INTO circuit_breakers (service_name, failure_threshold, recovery_timeout_seconds) VALUES
    ('instagram_api', 5, 300),
    ('vk_api', 3, 180),
    ('tiktok_api', 5, 600),
    ('youtube_api', 3, 300),
    ('telegram_api', 3, 120),
    ('s3_storage', 5, 60);

-- Insert default publishing rules
INSERT INTO publishing_rules (rule_name, platform, rule_type, conditions, actions, description) VALUES
    ('instagram_video_duration', 'instagram', 'content_filter', 
     '{"media_type": "video", "max_duration_seconds": 60}', 
     '{"action": "reject", "message": "Instagram videos must be under 60 seconds"}',
     'Instagram video duration limit'),
    
    ('tiktok_video_format', 'tiktok', 'format_requirement',
     '{"media_type": "video", "aspect_ratio": "9:16"}',
     '{"action": "transcode", "target_aspect_ratio": "9:16"}',
     'TikTok requires vertical video format'),
    
    ('vk_posting_hours', 'vk', 'schedule_constraint',
     '{"time_range": {"start": "09:00", "end": "23:00"}, "timezone": "Europe/Moscow"}',
     '{"action": "reschedule", "next_available": true}',
     'VK posting allowed only during business hours'),
    
    ('youtube_title_length', 'youtube', 'content_filter',
     '{"field": "title", "max_length": 100}',
     '{"action": "truncate", "add_ellipsis": true}',
     'YouTube title length limit');

COMMIT;