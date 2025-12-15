-- Migration: Add User, UserSocialAccount, Topic tables
-- Created: 2024-12-15

-- Enum types
DO $$ BEGIN
    CREATE TYPE subscription_plan AS ENUM ('demo', 'pro', 'business');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE image_gen_provider AS ENUM ('openai', 'stability', 'flux');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

-- Users table
CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    
    -- Telegram auth
    telegram_id BIGINT UNIQUE NOT NULL,
    telegram_username VARCHAR(255),
    telegram_first_name VARCHAR(255),
    telegram_last_name VARCHAR(255),
    telegram_photo_url TEXT,
    
    -- Subscription
    subscription_plan subscription_plan DEFAULT 'demo',
    subscription_expires_at TIMESTAMP,
    demo_started_at TIMESTAMP DEFAULT NOW(),
    
    -- Settings
    image_gen_provider image_gen_provider DEFAULT 'openai',
    
    -- Usage tracking
    posts_count_this_month INTEGER DEFAULT 0,
    images_generated_this_month INTEGER DEFAULT 0,
    usage_reset_at TIMESTAMP DEFAULT NOW(),
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_users_telegram_id ON users(telegram_id);

-- User Social Accounts (link table)
CREATE TABLE IF NOT EXISTS user_social_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    social_account_id UUID NOT NULL REFERENCES social_accounts(id) ON DELETE CASCADE,
    
    -- Permissions
    can_publish BOOLEAN DEFAULT TRUE,
    is_primary BOOLEAN DEFAULT FALSE,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    
    CONSTRAINT uq_user_social_account UNIQUE (user_id, social_account_id)
);

CREATE INDEX IF NOT EXISTS ix_user_social_accounts_user_id ON user_social_accounts(user_id);
CREATE INDEX IF NOT EXISTS ix_user_social_accounts_social_account_id ON user_social_accounts(social_account_id);

-- Topics table
CREATE TABLE IF NOT EXISTS topics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Topic info
    name VARCHAR(255) NOT NULL,
    description TEXT,
    color VARCHAR(7) DEFAULT '#6366f1',
    
    -- AI settings
    tone VARCHAR(100),
    hashtags TEXT[],
    call_to_action TEXT,
    
    -- Status
    is_active BOOLEAN DEFAULT TRUE,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS ix_topics_user_id ON topics(user_id);

-- Add user_id to posts table for ownership
ALTER TABLE posts ADD COLUMN IF NOT EXISTS user_id UUID REFERENCES users(id) ON DELETE SET NULL;
ALTER TABLE posts ADD COLUMN IF NOT EXISTS topic_id UUID REFERENCES topics(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_posts_user_id ON posts(user_id);
CREATE INDEX IF NOT EXISTS ix_posts_topic_id ON posts(topic_id);

COMMENT ON TABLE users IS 'User accounts with Telegram auth and subscription info';
COMMENT ON TABLE user_social_accounts IS 'Link between users and their connected social accounts';
COMMENT ON TABLE topics IS 'Content topics/categories for organizing posts';
