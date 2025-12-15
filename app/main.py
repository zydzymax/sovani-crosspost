"""
FastAPI application entry point for SoVAni Crosspost.

This module provides:
- FastAPI application setup with middleware
- CORS configuration for web clients
- Prometheus metrics endpoint
- Health check and API route integration
- Global exception handling
"""

# Load environment variables BEFORE any other imports
import os
from pathlib import Path
from dotenv import load_dotenv

# Find and load .env file from project root
project_root = Path(__file__).parent.parent
env_file = project_root / ".env"
if env_file.exists():
    load_dotenv(env_file)

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response
from contextlib import asynccontextmanager

from .core.config import settings
from .core.logging import setup_logging, get_logger, with_logging_context
from .observability.metrics import metrics, get_metrics_response
from .models.db import db_manager
from .api.routes import router as api_router
from .api.auth import router as auth_router
from .api.user_routes import router as user_router
from .api.content_plan import router as content_plan_router
from .api.video_gen import router as video_gen_router
from .api.pricing import router as pricing_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager for startup and shutdown tasks."""
    logger = get_logger("app.lifespan")
    
    # Startup
    logger.info("Starting SoVAni Crosspost application")
    
    try:
        # Initialize database connection
        if not db_manager.health_check():
            logger.warning("Database health check failed during startup")
        else:
            logger.info("Database connection verified")
        
        # Track application start
        metrics.app_info.info({
            'version': settings.app.version,
            'environment': settings.app.environment,
            'name': settings.app.app_name
        })
        
        logger.info("Application startup completed successfully")
        
    except Exception as e:
        logger.error("Failed to initialize application", error=str(e))
        raise
    
    yield
    
    # Shutdown
    logger.info("Shutting down SoVAni Crosspost application")
    
    try:
        # Close database connections
        await db_manager.close_async_session()
        logger.info("Database connections closed")
        
        logger.info("Application shutdown completed")
        
    except Exception as e:
        logger.error("Error during application shutdown", error=str(e))


def create_application() -> FastAPI:
    """Create and configure FastAPI application."""
    
    # Initialize logging first
    setup_logging()
    logger = get_logger("app")
    
    # Create FastAPI app
    app = FastAPI(
        title="SoVAni Crosspost API",
        description="Cross-platform content publishing system from Telegram to social media platforms",
        version=settings.app.version,
        debug=settings.app.debug,
        lifespan=lifespan,
        docs_url="/docs" if settings.app.is_development else None,
        redoc_url="/redoc" if settings.app.is_development else None
    )
    
    # Add middleware
    setup_middleware(app)
    
    # Add routes
    app.include_router(api_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(user_router, prefix="/api/v1")
    app.include_router(content_plan_router, prefix="/api/v1")
    app.include_router(video_gen_router, prefix="/api/v1")
    app.include_router(pricing_router, prefix="/api/v1")
    
    # Add metrics endpoint
    @app.get("/metrics", response_class=Response)
    async def metrics_endpoint():
        """Prometheus metrics endpoint."""
        content, headers = get_metrics_response()
        return Response(content=content, headers=headers)
    
    # Global exception handler
    @app.exception_handler(Exception)
    async def global_exception_handler(request: Request, exc: Exception):
        """Handle uncaught exceptions."""
        logger = get_logger("app.error")
        
        with with_logging_context(request_id=getattr(request.state, 'request_id', None)):
            logger.error(
                "Unhandled exception in request",
                path=request.url.path,
                method=request.method,
                error=str(exc),
                exc_info=True
            )
            
        # Track error metric
        metrics.track_request(
            method=request.method,
            endpoint=request.url.path,
            status_code=500,
            duration=0,
        )
        
        from fastapi.responses import JSONResponse
        return JSONResponse(
            status_code=500,
            content={
                "error": "Internal server error",
                "message": "An unexpected error occurred",
                "request_id": getattr(request.state, 'request_id', None)
            }
        )
    
    logger.info(
        "FastAPI application created",
        version=settings.app.version,
        environment=settings.app.environment,
        debug=settings.app.debug
    )
    
    return app


def setup_middleware(app: FastAPI):
    """Configure application middleware."""
    logger = get_logger("app.middleware")
    
    # CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app.is_development else [
            "https://admin.sovani.ru",
            "https://app.sovani.ru"
        ],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=[
            "Accept",
            "Accept-Language",
            "Content-Language",
            "Content-Type",
            "Authorization",
            "X-Request-ID",
            "X-API-Key"
        ]
    )
    
    # Trusted host middleware (security)
    if not settings.app.is_development:
        app.add_middleware(
            TrustedHostMiddleware,
            allowed_hosts=["api.sovani.ru", "*.sovani.ru"]
        )
    
    # Request ID and logging middleware
    @app.middleware("http")
    async def logging_middleware(request: Request, call_next):
        """Add request ID and logging context."""
        import uuid
        import time
        
        # Generate request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        request.state.request_id = request_id
        
        start_time = time.time()
        
        with with_logging_context(request_id=request_id):
            logger = get_logger("app.request")
            
            # Log request start
            logger.info(
                "Request started",
                method=request.method,
                path=request.url.path,
                query=str(request.query_params) if request.query_params else None,
                user_agent=request.headers.get("user-agent"),
                client_ip=request.client.host if request.client else None
            )
            
            try:
                # Process request
                response = await call_next(request)
                
                # Calculate response time
                duration = time.time() - start_time
                
                # Log response
                logger.info(
                    "Request completed",
                    status_code=response.status_code,
                    duration_seconds=round(duration, 3)
                )
                
                # Track metrics
                metrics.track_request(
                    method=request.method,
                    endpoint=request.url.path,
                    status_code=response.status_code,
                    duration=duration
                )
                
                # Add request ID to response headers
                response.headers["X-Request-ID"] = request_id
                
                return response
                
            except Exception as exc:
                duration = time.time() - start_time
                
                logger.error(
                    "Request failed with exception",
                    error=str(exc),
                    duration_seconds=round(duration, 3),
                    exc_info=True
                )
                
                # Track error metrics
                metrics.track_request(
                    method=request.method,
                    endpoint=request.url.path,
                    status_code=500,
                    duration=duration
                )
                
                raise
    
    logger.info("Middleware configuration completed")


# Create application instance
app = create_application()


def main():
    """Run the application with Uvicorn."""
    uvicorn.run(
        "app.main:app",
        host=settings.app.api_host,
        port=settings.app.api_port,
        workers=settings.app.api_workers,
        reload=settings.app.is_development,
        log_level=settings.app.log_level.lower(),
        access_log=True,
        server_header=False,  # Security
        date_header=False,    # Security
    )


if __name__ == "__main__":
    main()