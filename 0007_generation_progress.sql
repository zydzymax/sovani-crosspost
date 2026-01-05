-- Migration: Add post generation progress tracking
-- Date: 2024-12-31

-- Check if type exists before creating
DO $$ BEGIN
    CREATE TYPE generation_step_status AS ENUM ('pending', 'in_progress', 'completed', 'failed', 'skipped');
EXCEPTION
    WHEN duplicate_object THEN null;
END $$;

CREATE TABLE IF NOT EXISTS post_generation_progress (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content_plan_id UUID NOT NULL REFERENCES content_plans(id) ON DELETE CASCADE,
    post_index INTEGER NOT NULL,
    
    -- Post identification
    post_date DATE NOT NULL,
    post_topic VARCHAR(500),
    
    -- Generation steps (JSONB)
    steps JSONB NOT NULL DEFAULT '{}',
    
    -- Overall status
    overall_status generation_step_status DEFAULT 'pending',
    
    -- Progress percentage
    progress_percent INTEGER DEFAULT 0 CHECK (progress_percent >= 0 AND progress_percent <= 100),
    
    -- Errors
    last_error TEXT,
    error_count INTEGER DEFAULT 0,
    
    -- Timestamps
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    
    -- Indexes
    CONSTRAINT unique_plan_post UNIQUE (content_plan_id, post_index)
);

CREATE INDEX IF NOT EXISTS idx_progress_plan_id ON post_generation_progress(content_plan_id);
CREATE INDEX IF NOT EXISTS idx_progress_status ON post_generation_progress(overall_status);
CREATE INDEX IF NOT EXISTS idx_progress_date ON post_generation_progress(post_date);

COMMENT ON TABLE post_generation_progress IS 'Tracks generation progress for each post in a content plan';
