-- Migration: Marketplace Credentials
-- Description: Add table for user marketplace API credentials (WB, Ozon, Yandex Market)
-- Date: 2025-12-28

-- ============================================
-- ENUM TYPES
-- ============================================

CREATE TYPE marketplace_platform AS ENUM ('wildberries', 'ozon', 'yandex_market', 'aliexpress');

-- ============================================
-- MARKETPLACE CREDENTIALS TABLE
-- ============================================

CREATE TABLE marketplace_credentials (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform marketplace_platform NOT NULL,

    -- Encrypted credentials
    api_key TEXT,                    -- Main API key (encrypted)
    client_id TEXT,                  -- For Ozon (encrypted)

    -- Non-sensitive identifiers
    seller_id VARCHAR(255),          -- Seller/supplier ID
    campaign_id VARCHAR(255),        -- For Yandex Market campaigns

    -- Status tracking
    is_active BOOLEAN DEFAULT TRUE,
    is_verified BOOLEAN DEFAULT FALSE,
    last_verified_at TIMESTAMP,
    last_error TEXT,

    -- Timestamps
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    -- Each user can have only one credential per platform
    UNIQUE(user_id, platform)
);

-- ============================================
-- INDEXES
-- ============================================

CREATE INDEX idx_marketplace_credentials_user ON marketplace_credentials(user_id);
CREATE INDEX idx_marketplace_credentials_platform ON marketplace_credentials(platform);
CREATE INDEX idx_marketplace_credentials_active ON marketplace_credentials(user_id, is_active) WHERE is_active = TRUE;

-- ============================================
-- COMMENTS
-- ============================================

COMMENT ON TABLE marketplace_credentials IS 'User marketplace API credentials for product data enrichment';
COMMENT ON COLUMN marketplace_credentials.api_key IS 'Encrypted API key for marketplace access';
COMMENT ON COLUMN marketplace_credentials.client_id IS 'Encrypted client ID (used by Ozon)';
COMMENT ON COLUMN marketplace_credentials.seller_id IS 'Marketplace seller/supplier identifier';
COMMENT ON COLUMN marketplace_credentials.campaign_id IS 'Campaign ID for Yandex Market';
COMMENT ON COLUMN marketplace_credentials.is_verified IS 'Whether credentials have been verified to work';
COMMENT ON COLUMN marketplace_credentials.last_error IS 'Last error message if verification failed';
