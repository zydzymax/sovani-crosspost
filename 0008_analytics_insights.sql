-- Migration: Content Analytics and AI Insights System
-- Date: 2024-12-31

-- 1. Post Metrics
CREATE TABLE IF NOT EXISTS post_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    publish_result_id UUID NOT NULL REFERENCES publish_results(id) ON DELETE CASCADE,
    post_id UUID NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    shares INTEGER DEFAULT 0,
    saves INTEGER DEFAULT 0,
    
    engagement_rate DECIMAL(5,4) DEFAULT 0,
    click_through_rate DECIMAL(5,4) DEFAULT 0,
    
    platform_metrics JSONB DEFAULT '{}',
    audience_data JSONB DEFAULT '{}',
    
    followers_before INTEGER,
    followers_after INTEGER,
    followers_gained INTEGER DEFAULT 0,
    
    hours_since_publish INTEGER DEFAULT 0,
    measured_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT unique_metric_snapshot UNIQUE (publish_result_id, hours_since_publish)
);

CREATE INDEX IF NOT EXISTS idx_metrics_post_id ON post_metrics(post_id);
CREATE INDEX IF NOT EXISTS idx_metrics_platform ON post_metrics(platform);
CREATE INDEX IF NOT EXISTS idx_metrics_engagement ON post_metrics(engagement_rate DESC);

-- 2. Insight types
DO $$ BEGIN
    CREATE TYPE insight_type AS ENUM (
        'performance_analysis',
        'content_recommendation',
        'timing_suggestion',
        'audience_insight',
        'trend_alert',
        'optimization_action'
    );
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE insight_priority AS ENUM ('low', 'medium', 'high', 'critical');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

DO $$ BEGIN
    CREATE TYPE insight_status AS ENUM ('pending', 'shown', 'applied', 'dismissed', 'auto_applied');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 3. Content Insights
CREATE TABLE IF NOT EXISTS content_insights (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    
    post_id UUID REFERENCES posts(id) ON DELETE CASCADE,
    platform VARCHAR(50),
    
    insight_type insight_type NOT NULL,
    priority insight_priority DEFAULT 'medium',
    status insight_status DEFAULT 'pending',
    
    title VARCHAR(255) NOT NULL,
    summary TEXT NOT NULL,
    detailed_analysis TEXT,
    
    recommendations JSONB DEFAULT '[]',
    confidence_score DECIMAL(3,2) DEFAULT 0.8,
    ai_reasoning TEXT,
    supporting_data JSONB DEFAULT '{}',
    
    auto_action_type VARCHAR(50),
    auto_action_payload JSONB,
    auto_action_executed BOOLEAN DEFAULT FALSE,
    auto_action_result JSONB,
    
    user_feedback VARCHAR(20),
    user_notes TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    shown_at TIMESTAMP,
    applied_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_insights_user ON content_insights(user_id);
CREATE INDEX IF NOT EXISTS idx_insights_status ON content_insights(status);
CREATE INDEX IF NOT EXISTS idx_insights_priority ON content_insights(priority DESC);

-- 4. Optimization mode type
DO $$ BEGIN
    CREATE TYPE optimization_mode AS ENUM ('disabled', 'hints_only', 'confirm', 'auto');
EXCEPTION WHEN duplicate_object THEN null;
END $$;

-- 5. Analytics Settings
CREATE TABLE IF NOT EXISTS analytics_settings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE UNIQUE,
    
    collect_metrics BOOLEAN DEFAULT TRUE,
    metrics_frequency_hours INTEGER DEFAULT 24,
    
    optimization_mode optimization_mode DEFAULT 'hints_only',
    
    auto_adjust_timing BOOLEAN DEFAULT FALSE,
    auto_optimize_hashtags BOOLEAN DEFAULT FALSE,
    auto_adjust_content_length BOOLEAN DEFAULT FALSE,
    auto_suggest_topics BOOLEAN DEFAULT TRUE,
    
    notify_on_viral BOOLEAN DEFAULT TRUE,
    notify_on_drop BOOLEAN DEFAULT TRUE,
    notify_weekly_report BOOLEAN DEFAULT TRUE,
    
    viral_threshold_multiplier DECIMAL(3,1) DEFAULT 3.0,
    drop_threshold_percent INTEGER DEFAULT 50,
    benchmark_period_days INTEGER DEFAULT 30,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 6. Performance Benchmarks
CREATE TABLE IF NOT EXISTS performance_benchmarks (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    platform VARCHAR(50) NOT NULL,
    
    period_start DATE NOT NULL,
    period_end DATE NOT NULL,
    
    total_posts INTEGER DEFAULT 0,
    avg_views DECIMAL(12,2) DEFAULT 0,
    avg_likes DECIMAL(12,2) DEFAULT 0,
    avg_comments DECIMAL(12,2) DEFAULT 0,
    avg_shares DECIMAL(12,2) DEFAULT 0,
    avg_engagement_rate DECIMAL(5,4) DEFAULT 0,
    
    best_performing_post_id UUID REFERENCES posts(id),
    worst_performing_post_id UUID REFERENCES posts(id),
    
    best_posting_times JSONB DEFAULT '[]',
    best_days_of_week JSONB DEFAULT '[]',
    top_hashtags JSONB DEFAULT '[]',
    top_content_types JSONB DEFAULT '[]',
    
    followers_start INTEGER,
    followers_end INTEGER,
    followers_growth_rate DECIMAL(5,4),
    
    period_summary TEXT,
    
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    
    CONSTRAINT unique_user_platform_period UNIQUE (user_id, platform, period_start, period_end)
);

CREATE INDEX IF NOT EXISTS idx_benchmarks_user_platform ON performance_benchmarks(user_id, platform);
