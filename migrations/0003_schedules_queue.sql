-- Migration: 0003_schedules_queue.sql
-- Description: Add scheduling and content queue tables for SoVAni Crosspost
-- Created: 2024-12-13

BEGIN;

-- Schedules table (publishing schedule configurations)
CREATE TABLE IF NOT EXISTS schedules (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- Schedule identification
    name VARCHAR(255) NOT NULL,
    description TEXT,

    -- Target platforms
    platforms TEXT[] NOT NULL DEFAULT '{}',

    -- Scheduling configuration
    cron_expression VARCHAR(100),
    publish_times TEXT[], -- Array of HH:MM times
    days_of_week INTEGER[], -- 0=Monday, 6=Sunday

    -- Timezone
    timezone VARCHAR(50) DEFAULT 'Europe/Moscow',

    -- Limits
    max_posts_per_day INTEGER DEFAULT 10,
    min_interval_minutes INTEGER DEFAULT 60,

    -- Content filtering
    content_filter JSONB DEFAULT '{}',

    -- Status
    is_active BOOLEAN DEFAULT TRUE,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    last_run_at TIMESTAMP WITH TIME ZONE,
    next_run_at TIMESTAMP WITH TIME ZONE
);

-- Content queue table (scheduled content items)
CREATE TABLE IF NOT EXISTS content_queue (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- References
    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    schedule_id UUID REFERENCES schedules(id) ON DELETE SET NULL,

    -- Target platform
    platform VARCHAR(50) NOT NULL,

    -- Scheduling
    scheduled_for TIMESTAMP WITH TIME ZONE NOT NULL,
    priority INTEGER DEFAULT 0,

    -- Status
    status VARCHAR(50) DEFAULT 'pending', -- pending, processing, published, failed, cancelled

    -- Processing
    attempts INTEGER DEFAULT 0,
    last_attempt_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT,

    -- Timestamps
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    published_at TIMESTAMP WITH TIME ZONE
);

-- Add missing columns to posts table if they don't exist
DO $$
BEGIN
    -- Add source columns if they don't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'source_platform') THEN
        ALTER TABLE posts ADD COLUMN source_platform VARCHAR(50);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'source_message_id') THEN
        ALTER TABLE posts ADD COLUMN source_message_id VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'source_chat_id') THEN
        ALTER TABLE posts ADD COLUMN source_chat_id VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'source_user_id') THEN
        ALTER TABLE posts ADD COLUMN source_user_id VARCHAR(255);
    END IF;

    -- Add original_text column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'original_text') THEN
        ALTER TABLE posts ADD COLUMN original_text TEXT;
    END IF;

    -- Add generated_caption column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'generated_caption') THEN
        ALTER TABLE posts ADD COLUMN generated_caption TEXT;
    END IF;

    -- Add platform_captions column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'platform_captions') THEN
        ALTER TABLE posts ADD COLUMN platform_captions JSONB DEFAULT '{}';
    END IF;

    -- Add current_stage column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'current_stage') THEN
        ALTER TABLE posts ADD COLUMN current_stage VARCHAR(50);
    END IF;

    -- Add enrichment_data column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'enrichment_data') THEN
        ALTER TABLE posts ADD COLUMN enrichment_data JSONB DEFAULT '{}';
    END IF;

    -- Add source_data column if doesn't exist
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'source_data') THEN
        ALTER TABLE posts ADD COLUMN source_data JSONB;
    END IF;

    -- Add is_scheduled column
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'posts' AND column_name = 'is_scheduled') THEN
        ALTER TABLE posts ADD COLUMN is_scheduled BOOLEAN DEFAULT FALSE;
    END IF;
END $$;

-- Add missing columns to accounts table
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'accounts' AND column_name = 'publish_enabled') THEN
        ALTER TABLE accounts ADD COLUMN publish_enabled BOOLEAN DEFAULT TRUE;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'accounts' AND column_name = 'publish_priority') THEN
        ALTER TABLE accounts ADD COLUMN publish_priority INTEGER DEFAULT 0;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'accounts' AND column_name = 'extra_credentials') THEN
        ALTER TABLE accounts ADD COLUMN extra_credentials JSONB DEFAULT '{}';
    END IF;
END $$;

-- Add transcode_status to media_assets
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'media_assets' AND column_name = 'transcode_status') THEN
        ALTER TABLE media_assets ADD COLUMN transcode_status JSONB DEFAULT '{}';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'media_assets' AND column_name = 'transcoded_paths') THEN
        ALTER TABLE media_assets ADD COLUMN transcoded_paths JSONB DEFAULT '{}';
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'media_assets' AND column_name = 'thumbnail_path') THEN
        ALTER TABLE media_assets ADD COLUMN thumbnail_path TEXT;
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'media_assets' AND column_name = 'original_file_id') THEN
        ALTER TABLE media_assets ADD COLUMN original_file_id VARCHAR(255);
    END IF;

    IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                   WHERE table_name = 'media_assets' AND column_name = 'duration') THEN
        ALTER TABLE media_assets ADD COLUMN duration DECIMAL(10, 3);
    END IF;
END $$;

-- Publish results table (results of publishing attempts per platform)
CREATE TABLE IF NOT EXISTS publish_results (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),

    -- References
    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    account_id UUID REFERENCES accounts(id) ON DELETE SET NULL,

    -- Platform info
    platform VARCHAR(50) NOT NULL,

    -- Result
    success BOOLEAN DEFAULT FALSE,
    platform_post_id VARCHAR(255),
    platform_post_url TEXT,

    -- Error handling
    error_code VARCHAR(50),
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,

    -- Platform response
    platform_response JSONB,

    -- Timestamps
    published_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    -- Unique constraint: one result per post per platform
    UNIQUE(post_id, platform)
);

-- Create indexes
CREATE INDEX IF NOT EXISTS idx_schedules_active ON schedules(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_schedules_next_run ON schedules(next_run_at);

CREATE INDEX IF NOT EXISTS idx_content_queue_scheduled ON content_queue(scheduled_for, status);
CREATE INDEX IF NOT EXISTS idx_content_queue_platform ON content_queue(platform, status);
CREATE INDEX IF NOT EXISTS idx_content_queue_post ON content_queue(post_id);

CREATE INDEX IF NOT EXISTS idx_publish_results_post ON publish_results(post_id);
CREATE INDEX IF NOT EXISTS idx_publish_results_platform ON publish_results(platform, success);

CREATE INDEX IF NOT EXISTS idx_posts_source ON posts(source_platform, source_chat_id);
CREATE INDEX IF NOT EXISTS idx_posts_is_scheduled ON posts(is_scheduled) WHERE is_scheduled = TRUE;

-- Update trigger for schedules
CREATE TRIGGER update_schedules_updated_at BEFORE UPDATE ON schedules
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- Update trigger for publish_results
CREATE TRIGGER update_publish_results_updated_at BEFORE UPDATE ON publish_results
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;
