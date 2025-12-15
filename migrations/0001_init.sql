-- Migration: 0001_init.sql
-- Description: Base tables for SoVAni Crosspost MVP
-- Created: 2024-01-01

BEGIN;

-- Enable UUID extension
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Enable JSONB operators
CREATE EXTENSION IF NOT EXISTS btree_gin;

-- Products table (source content from Telegram)
CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source_type VARCHAR(50) NOT NULL DEFAULT 'telegram',
    source_id VARCHAR(255) NOT NULL,
    source_data JSONB NOT NULL DEFAULT '{}',
    
    title TEXT,
    description TEXT,
    tags TEXT[] DEFAULT '{}',
    category VARCHAR(100),
    
    -- Metadata
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status VARCHAR(50) DEFAULT 'draft',
    
    -- Constraints
    UNIQUE(source_type, source_id)
);

-- Media assets table (original files)
CREATE TABLE media_assets (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    
    -- File information
    filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    
    -- Storage information
    storage_provider VARCHAR(50) DEFAULT 's3',
    storage_path TEXT NOT NULL,
    storage_bucket VARCHAR(100),
    
    -- Media metadata
    media_type VARCHAR(20) CHECK (media_type IN ('image', 'video', 'audio', 'document')),
    duration_seconds INTEGER, -- for video/audio
    width_pixels INTEGER,     -- for images/video
    height_pixels INTEGER,    -- for images/video
    fps DECIMAL(5,2),        -- for video
    
    -- Content analysis
    metadata JSONB DEFAULT '{}',
    extracted_text TEXT,
    content_hash VARCHAR(64), -- SHA-256 for deduplication
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Renditions table (processed versions for different platforms)
CREATE TABLE renditions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    media_asset_id UUID REFERENCES media_assets(id) ON DELETE CASCADE,
    
    -- Platform targeting
    platform VARCHAR(50) NOT NULL,
    format_profile VARCHAR(100) NOT NULL, -- 'instagram_post', 'tiktok_video', etc.
    
    -- File information
    filename VARCHAR(255) NOT NULL,
    mime_type VARCHAR(100) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    
    -- Storage information  
    storage_path TEXT NOT NULL,
    storage_bucket VARCHAR(100),
    
    -- Rendition specs
    width_pixels INTEGER,
    height_pixels INTEGER,
    duration_seconds INTEGER,
    bitrate_kbps INTEGER,
    fps DECIMAL(5,2),
    
    -- Processing info
    processing_params JSONB DEFAULT '{}',
    processing_status VARCHAR(50) DEFAULT 'pending',
    processing_error TEXT,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    completed_at TIMESTAMP WITH TIME ZONE
);

-- Posts table (publication attempts)
CREATE TABLE posts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    
    -- Platform information
    platform VARCHAR(50) NOT NULL,
    platform_account_id VARCHAR(255),
    
    -- Content
    title TEXT,
    caption TEXT,
    hashtags TEXT[] DEFAULT '{}',
    
    -- Media attachments
    media_asset_ids UUID[] DEFAULT '{}',
    rendition_ids UUID[] DEFAULT '{}',
    
    -- Publication status
    status VARCHAR(50) DEFAULT 'draft', -- draft, scheduled, published, failed, cancelled
    scheduled_at TIMESTAMP WITH TIME ZONE,
    published_at TIMESTAMP WITH TIME ZONE,
    
    -- Platform response
    platform_post_id VARCHAR(255),
    platform_url TEXT,
    platform_response JSONB DEFAULT '{}',
    
    -- Error tracking
    error_message TEXT,
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Accounts table (social media accounts)
CREATE TABLE accounts (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Platform information
    platform VARCHAR(50) NOT NULL,
    platform_user_id VARCHAR(255) NOT NULL,
    username VARCHAR(255),
    display_name VARCHAR(255),
    
    -- Authentication
    access_token_encrypted TEXT,
    refresh_token_encrypted TEXT,
    token_expires_at TIMESTAMP WITH TIME ZONE,
    
    -- Account metadata
    account_data JSONB DEFAULT '{}',
    permissions TEXT[] DEFAULT '{}',
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    last_used_at TIMESTAMP WITH TIME ZONE,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    
    -- Constraints
    UNIQUE(platform, platform_user_id)
);

-- Tasks table (Celery task tracking)
CREATE TABLE tasks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Task identification
    celery_task_id VARCHAR(255) UNIQUE NOT NULL,
    task_name VARCHAR(255) NOT NULL,
    queue_name VARCHAR(100) NOT NULL,
    
    -- Related entities
    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    post_id UUID REFERENCES posts(id) ON DELETE SET NULL,
    
    -- Task data
    task_args JSONB DEFAULT '{}',
    task_kwargs JSONB DEFAULT '{}',
    
    -- Execution tracking
    status VARCHAR(50) DEFAULT 'pending', -- pending, running, success, failure, retry
    result JSONB DEFAULT '{}',
    error_message TEXT,
    traceback TEXT,
    
    -- Timing
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE,
    runtime_seconds DECIMAL(10,3),
    
    -- Retry logic
    retry_count INTEGER DEFAULT 0,
    max_retries INTEGER DEFAULT 3,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Logs table (application and audit logs)
CREATE TABLE logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    
    -- Log metadata
    level VARCHAR(20) NOT NULL, -- DEBUG, INFO, WARN, ERROR, CRITICAL
    logger VARCHAR(255) NOT NULL,
    message TEXT NOT NULL,
    
    -- Context
    user_id UUID,
    session_id VARCHAR(255),
    request_id VARCHAR(255),
    
    -- Related entities
    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    post_id UUID REFERENCES posts(id) ON DELETE SET NULL,
    task_id UUID REFERENCES tasks(id) ON DELETE SET NULL,
    
    -- Additional data
    extra_data JSONB DEFAULT '{}',
    
    -- Source information
    module VARCHAR(255),
    function VARCHAR(255),
    line_number INTEGER,
    
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Create indexes for performance
CREATE INDEX idx_products_source ON products(source_type, source_id);
CREATE INDEX idx_products_status ON products(status);
CREATE INDEX idx_products_created_at ON products(created_at);

CREATE INDEX idx_media_assets_product_id ON media_assets(product_id);
CREATE INDEX idx_media_assets_content_hash ON media_assets(content_hash);
CREATE INDEX idx_media_assets_media_type ON media_assets(media_type);

CREATE INDEX idx_renditions_media_asset_id ON renditions(media_asset_id);
CREATE INDEX idx_renditions_platform ON renditions(platform, format_profile);
CREATE INDEX idx_renditions_status ON renditions(processing_status);

CREATE INDEX idx_posts_product_id ON posts(product_id);
CREATE INDEX idx_posts_platform ON posts(platform);
CREATE INDEX idx_posts_status ON posts(status);
CREATE INDEX idx_posts_scheduled_at ON posts(scheduled_at) WHERE scheduled_at IS NOT NULL;
CREATE INDEX idx_posts_published_at ON posts(published_at) WHERE published_at IS NOT NULL;

CREATE INDEX idx_accounts_platform ON accounts(platform, platform_user_id);
CREATE INDEX idx_accounts_active ON accounts(is_active) WHERE is_active = TRUE;

CREATE INDEX idx_tasks_celery_task_id ON tasks(celery_task_id);
CREATE INDEX idx_tasks_status ON tasks(status);
CREATE INDEX idx_tasks_queue_name ON tasks(queue_name);
CREATE INDEX idx_tasks_product_id ON tasks(product_id) WHERE product_id IS NOT NULL;
CREATE INDEX idx_tasks_created_at ON tasks(created_at);

CREATE INDEX idx_logs_level ON logs(level);
CREATE INDEX idx_logs_logger ON logs(logger);
CREATE INDEX idx_logs_created_at ON logs(created_at);
CREATE INDEX idx_logs_request_id ON logs(request_id) WHERE request_id IS NOT NULL;

-- GIN indexes for JSONB fields
CREATE INDEX idx_products_source_data ON products USING GIN(source_data);
CREATE INDEX idx_media_assets_metadata ON media_assets USING GIN(metadata);
CREATE INDEX idx_posts_platform_response ON posts USING GIN(platform_response);
CREATE INDEX idx_accounts_account_data ON accounts USING GIN(account_data);
CREATE INDEX idx_tasks_result ON tasks USING GIN(result);
CREATE INDEX idx_logs_extra_data ON logs USING GIN(extra_data);

-- Triggers for updated_at timestamps
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_media_assets_updated_at BEFORE UPDATE ON media_assets
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_posts_updated_at BEFORE UPDATE ON posts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_accounts_updated_at BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_tasks_updated_at BEFORE UPDATE ON tasks
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

COMMIT;