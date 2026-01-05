-- Migration: Unified Auth and Cart System
-- Description: Add unified users, products, subscriptions, cart, orders, payments tables
-- Date: 2025-12-17

-- ============================================
-- ENUM TYPES
-- ============================================

CREATE TYPE subscription_status AS ENUM ('active', 'cancelled', 'expired', 'paused', 'trial');
CREATE TYPE order_status AS ENUM ('pending', 'paid', 'failed', 'refunded', 'cancelled');
CREATE TYPE payment_status AS ENUM ('pending', 'processing', 'completed', 'failed', 'refunded');
CREATE TYPE billing_period AS ENUM ('monthly', 'yearly', 'lifetime');

-- ============================================
-- PRODUCTS AND PLANS
-- ============================================

CREATE TABLE products (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE product_plans (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id UUID REFERENCES products(id) ON DELETE CASCADE,
    code VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    price_rub DECIMAL(10,2) NOT NULL,
    billing_period billing_period DEFAULT 'monthly',
    limits JSONB DEFAULT '{}',
    features JSONB DEFAULT '[]',
    is_active BOOLEAN DEFAULT TRUE,
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(product_id, code)
);

CREATE INDEX idx_product_plans_product ON product_plans(product_id);

-- ============================================
-- UNIFIED USERS (extends existing users)
-- ============================================

-- Add email fields to existing users table
ALTER TABLE users
    ADD COLUMN IF NOT EXISTS email VARCHAR(255) UNIQUE,
    ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS email_verified_at TIMESTAMP,
    ADD COLUMN IF NOT EXISTS company_name VARCHAR(255),
    ADD COLUMN IF NOT EXISTS phone VARCHAR(50),
    ADD COLUMN IF NOT EXISTS last_login_at TIMESTAMP;

-- Make telegram_id nullable (no longer required for email-based auth)
ALTER TABLE users ALTER COLUMN telegram_id DROP NOT NULL;

-- Create index on email
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

-- ============================================
-- USER SUBSCRIPTIONS (replaces subscription_plan field)
-- ============================================

CREATE TABLE user_subscriptions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE,
    product_id UUID REFERENCES products(id) ON DELETE RESTRICT,
    plan_id UUID REFERENCES product_plans(id) ON DELETE RESTRICT,

    status subscription_status DEFAULT 'active',

    started_at TIMESTAMP DEFAULT NOW(),
    expires_at TIMESTAMP,
    cancelled_at TIMESTAMP,

    current_period_start TIMESTAMP DEFAULT NOW(),
    current_period_end TIMESTAMP,

    payment_provider VARCHAR(50),
    external_subscription_id VARCHAR(255),

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),

    UNIQUE(user_id, product_id)
);

CREATE INDEX idx_subscriptions_user ON user_subscriptions(user_id);
CREATE INDEX idx_subscriptions_expires ON user_subscriptions(expires_at);
CREATE INDEX idx_subscriptions_status ON user_subscriptions(status);

-- ============================================
-- SHOPPING CART
-- ============================================

CREATE TABLE carts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE CASCADE UNIQUE,

    items JSONB DEFAULT '[]',

    subtotal_rub DECIMAL(10,2) DEFAULT 0,
    discount_rub DECIMAL(10,2) DEFAULT 0,
    total_rub DECIMAL(10,2) DEFAULT 0,

    promo_code VARCHAR(50),
    promo_discount_percent INTEGER,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_carts_user ON carts(user_id);

-- ============================================
-- ORDERS
-- ============================================

CREATE TABLE orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,

    order_number VARCHAR(50) UNIQUE NOT NULL,
    status order_status DEFAULT 'pending',

    items JSONB NOT NULL,

    subtotal_rub DECIMAL(10,2) NOT NULL,
    discount_rub DECIMAL(10,2) DEFAULT 0,
    total_rub DECIMAL(10,2) NOT NULL,

    promo_code VARCHAR(50),

    customer_email VARCHAR(255) NOT NULL,
    customer_name VARCHAR(255),
    customer_company VARCHAR(255),
    customer_phone VARCHAR(50),

    payment_provider VARCHAR(50),
    payment_method VARCHAR(50),

    notes TEXT,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    paid_at TIMESTAMP,
    expires_at TIMESTAMP
);

CREATE INDEX idx_orders_user ON orders(user_id);
CREATE INDEX idx_orders_status ON orders(status);
CREATE INDEX idx_orders_number ON orders(order_number);
CREATE INDEX idx_orders_created ON orders(created_at);

-- ============================================
-- PAYMENTS
-- ============================================

CREATE TABLE payments (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,

    amount_rub DECIMAL(10,2) NOT NULL,
    currency VARCHAR(3) DEFAULT 'RUB',

    status payment_status DEFAULT 'pending',

    provider VARCHAR(50) NOT NULL,
    provider_payment_id VARCHAR(255),
    provider_response JSONB,

    invoice_number VARCHAR(50),
    invoice_pdf_url TEXT,

    error_message TEXT,

    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP
);

CREATE INDEX idx_payments_order ON payments(order_id);
CREATE INDEX idx_payments_provider ON payments(provider, provider_payment_id);
CREATE INDEX idx_payments_status ON payments(status);

-- ============================================
-- PROMO CODES
-- ============================================

CREATE TABLE promo_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    code VARCHAR(50) UNIQUE NOT NULL,

    discount_percent INTEGER,
    discount_amount_rub DECIMAL(10,2),

    valid_from TIMESTAMP DEFAULT NOW(),
    valid_until TIMESTAMP,

    max_uses INTEGER,
    current_uses INTEGER DEFAULT 0,

    product_id UUID REFERENCES products(id) ON DELETE SET NULL,
    plan_id UUID REFERENCES product_plans(id) ON DELETE SET NULL,

    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_promo_codes_code ON promo_codes(code);

-- ============================================
-- INITIAL DATA: Products and Plans
-- ============================================

-- Crosspost product
INSERT INTO products (id, code, name, description, sort_order) VALUES
    ('11111111-1111-1111-1111-111111111111', 'crosspost', 'Crosspost', 'Кроссплатформенная публикация контента', 1);

-- HeadOfSales product
INSERT INTO products (id, code, name, description, sort_order) VALUES
    ('22222222-2222-2222-2222-222222222222', 'headofsales', 'Head of Sales', 'AI-анализ звонков для руководителей продаж', 2);

-- Crosspost plans
INSERT INTO product_plans (product_id, code, name, price_rub, billing_period, limits, features, sort_order) VALUES
    ('11111111-1111-1111-1111-111111111111', 'demo', 'Demo', 0, 'monthly',
     '{"posts_per_month": 10, "images_per_month": 5, "video_seconds": 0, "platforms_limit": 2}',
     '["10 постов/месяц", "5 изображений", "2 платформы", "7 дней бесплатно"]', 1),

    ('11111111-1111-1111-1111-111111111111', 'starter', 'Starter', 990, 'monthly',
     '{"posts_per_month": 50, "images_per_month": 20, "video_seconds": 30, "platforms_limit": 3}',
     '["50 постов/месяц", "20 изображений", "30 сек видео", "3 платформы"]', 2),

    ('11111111-1111-1111-1111-111111111111', 'pro', 'Pro', 2990, 'monthly',
     '{"posts_per_month": 200, "images_per_month": 100, "video_seconds": 120, "platforms_limit": 5}',
     '["200 постов/месяц", "100 изображений", "2 мин видео", "5 платформ", "Приоритетная поддержка"]', 3),

    ('11111111-1111-1111-1111-111111111111', 'business', 'Business', 9990, 'monthly',
     '{"posts_per_month": -1, "images_per_month": 500, "video_seconds": 600, "platforms_limit": -1}',
     '["Безлимитные посты", "500 изображений", "10 мин видео", "Все платформы", "Персональный менеджер"]', 4);

-- HeadOfSales plans
INSERT INTO product_plans (product_id, code, name, price_rub, billing_period, limits, features, sort_order) VALUES
    ('22222222-2222-2222-2222-222222222222', 'demo', 'Demo', 0, 'monthly',
     '{"calls_per_month": 10, "managers_limit": 2}',
     '["10 звонков/месяц", "2 менеджера", "7 дней бесплатно"]', 1),

    ('22222222-2222-2222-2222-222222222222', 'starter', 'Starter', 1990, 'monthly',
     '{"calls_per_month": 100, "managers_limit": 5}',
     '["100 звонков/месяц", "5 менеджеров", "Базовая аналитика"]', 2),

    ('22222222-2222-2222-2222-222222222222', 'pro', 'Pro', 4990, 'monthly',
     '{"calls_per_month": 500, "managers_limit": 15}',
     '["500 звонков/месяц", "15 менеджеров", "Расширенная аналитика", "API доступ"]', 3),

    ('22222222-2222-2222-2222-222222222222', 'business', 'Business', 14990, 'monthly',
     '{"calls_per_month": -1, "managers_limit": -1}',
     '["Безлимитные звонки", "Без ограничений менеджеров", "Полная аналитика", "Интеграции", "SLA"]', 4);

-- ============================================
-- TRIGGER FOR updated_at
-- ============================================

CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ language 'plpgsql';

CREATE TRIGGER update_products_updated_at BEFORE UPDATE ON products
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_user_subscriptions_updated_at BEFORE UPDATE ON user_subscriptions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_carts_updated_at BEFORE UPDATE ON carts
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_orders_updated_at BEFORE UPDATE ON orders
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

CREATE TRIGGER update_payments_updated_at BEFORE UPDATE ON payments
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
