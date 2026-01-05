-- Migration: Create content_plans table
-- Date: 2024-12-31

CREATE TYPE content_plan_status AS ENUM ('draft', 'active', 'completed', 'cancelled');

CREATE TABLE IF NOT EXISTS content_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    -- Plan parameters
    niche VARCHAR(100) NOT NULL,
    duration_days INTEGER NOT NULL,
    posts_per_day INTEGER DEFAULT 1,
    tone VARCHAR(50),
    
    -- Target platforms
    platforms TEXT[] NOT NULL,
    
    -- Generated plan data (JSONB)
    plan_data JSONB NOT NULL DEFAULT '[]',
    
    -- Status
    status content_plan_status DEFAULT 'draft',
    
    -- Statistics
    posts_created INTEGER DEFAULT 0,
    posts_published INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    activated_at TIMESTAMP,
    completed_at TIMESTAMP
);

CREATE INDEX idx_content_plans_user ON content_plans(user_id);
CREATE INDEX idx_content_plans_status ON content_plans(status);

COMMENT ON TABLE content_plans IS 'AI-generated content plans for social media';
