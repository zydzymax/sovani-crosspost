"""
Database connection and session management for SalesWhisper Crosspost.

This module provides:
- SQLAlchemy database connection setup
- Session management with dependency injection
- Migration utilities
- Database health checks
"""

import logging
import sys
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Base class for all SQLAlchemy models
Base = declarative_base()
metadata = MetaData()


class DatabaseConfig(BaseSettings):
    """Database configuration from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://saleswhisper:saleswhisper_pass@localhost:5432/saleswhisper_crosspost"
    database_url_async: str | None = None

    # Connection pool settings
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 3600

    # Migration settings
    migrations_path: str = "migrations/"

    def model_post_init(self, __context) -> None:
        """Generate async database URL if not provided."""
        if not self.database_url_async:
            object.__setattr__(
                self, "database_url_async", self.database_url.replace("postgresql://", "postgresql+asyncpg://")
            )


# Global database configuration
db_config = DatabaseConfig()


class DatabaseManager:
    """Database connection and session manager."""

    def __init__(self, config: DatabaseConfig = db_config):
        self.config = config
        self._sync_engine = None
        self._async_engine = None
        self._sync_session_factory = None
        self._async_session_factory = None

    @property
    def sync_engine(self):
        """Get or create synchronous database engine."""
        if self._sync_engine is None:
            self._sync_engine = create_engine(
                self.config.database_url,
                pool_size=self.config.pool_size,
                max_overflow=self.config.max_overflow,
                pool_timeout=self.config.pool_timeout,
                pool_recycle=self.config.pool_recycle,
                pool_pre_ping=True,  # Check connection health before use
                echo=False,  # Set to True for SQL debugging
            )
        return self._sync_engine

    @property
    def async_engine(self):
        """Get or create asynchronous database engine."""
        if self._async_engine is None:
            self._async_engine = create_async_engine(
                self.config.database_url_async,
                pool_size=self.config.pool_size,
                max_overflow=self.config.max_overflow,
                pool_timeout=self.config.pool_timeout,
                pool_recycle=self.config.pool_recycle,
                pool_pre_ping=True,  # Check connection health before use
                echo=False,  # Set to True for SQL debugging
            )
        return self._async_engine

    @property
    def sync_session_factory(self):
        """Get or create synchronous session factory."""
        if self._sync_session_factory is None:
            self._sync_session_factory = sessionmaker(
                bind=self.sync_engine, class_=Session, autoflush=True, autocommit=False
            )
        return self._sync_session_factory

    @property
    def async_session_factory(self):
        """Get or create asynchronous session factory."""
        if self._async_session_factory is None:
            self._async_session_factory = async_sessionmaker(
                bind=self.async_engine, class_=AsyncSession, autoflush=True, autocommit=False, expire_on_commit=False
            )
        return self._async_session_factory

    @contextmanager
    def get_sync_session(self) -> Generator[Session, None, None]:
        """Context manager for synchronous database sessions."""
        session = self.sync_session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    async def get_async_session(self) -> AsyncSession:
        """Get asynchronous database session."""
        return self.async_session_factory()

    def health_check(self) -> bool:
        """Check database connection health."""
        try:
            with self.get_sync_session() as session:
                result = session.execute(text("SELECT 1")).scalar()
                return result == 1
        except Exception as e:
            logger.error(f"Database health check failed: {e}")
            return False

    async def async_health_check(self) -> bool:
        """Async database connection health check."""
        try:
            async with self.async_session_factory() as session:
                result = await session.execute(text("SELECT 1"))
                return result.scalar() == 1
        except Exception as e:
            logger.error(f"Async database health check failed: {e}")
            return False

    async def close_async_session(self) -> None:
        """Close async database connections."""
        if self._async_engine is not None:
            await self._async_engine.dispose()
            self._async_engine = None
            logger.info("Async database connections closed")


# Global database manager instance
db_manager = DatabaseManager()


# Dependency injection for FastAPI
def get_db_session() -> Generator[Session, None, None]:
    """FastAPI dependency for synchronous database sessions."""
    with db_manager.get_sync_session() as session:
        yield session


async def get_async_db_session() -> AsyncSession:
    """FastAPI dependency for asynchronous database sessions."""
    return await db_manager.get_async_session()


class MigrationManager:
    """SQL migration management utilities."""

    def __init__(self, config: DatabaseConfig = db_config):
        self.config = config
        self.migrations_path = Path(config.migrations_path)

    def get_migration_files(self) -> list[Path]:
        """Get sorted list of migration files."""
        if not self.migrations_path.exists():
            logger.warning(f"Migration path {self.migrations_path} does not exist")
            return []

        migration_files = list(self.migrations_path.glob("*.sql"))
        return sorted(migration_files)

    def execute_migration(self, migration_file: Path) -> bool:
        """Execute a single migration file."""
        try:
            logger.info(f"Executing migration: {migration_file.name}")

            with migration_file.open("r", encoding="utf-8") as f:
                sql_content = f.read()

            with db_manager.get_sync_session() as session:
                # Execute the migration SQL
                session.execute(text(sql_content))
                session.commit()

            logger.info(f"Migration {migration_file.name} executed successfully")
            return True

        except Exception as e:
            logger.error(f"Migration {migration_file.name} failed: {e}")
            return False

    def run_migrations(self) -> bool:
        """Run all pending migrations."""
        migration_files = self.get_migration_files()

        if not migration_files:
            logger.info("No migration files found")
            return True

        success_count = 0
        for migration_file in migration_files:
            if self.execute_migration(migration_file):
                success_count += 1
            else:
                logger.error(f"Migration failed at {migration_file.name}, stopping")
                break

        logger.info(f"Executed {success_count}/{len(migration_files)} migrations")
        return success_count == len(migration_files)

    def create_database_if_not_exists(self) -> bool:
        """Create database if it doesn't exist."""
        try:
            # Parse database URL to extract database name
            from urllib.parse import urlparse

            parsed_url = urlparse(self.config.database_url)
            db_name = parsed_url.path.lstrip("/")

            # Connect to postgres database to create our database
            postgres_url = self.config.database_url.replace(f"/{db_name}", "/postgres")

            temp_engine = create_engine(postgres_url)
            with temp_engine.connect() as conn:
                # Check if database exists
                result = conn.execute(
                    text("SELECT 1 FROM pg_database WHERE datname = :db_name"), {"db_name": db_name}
                ).fetchone()

                if not result:
                    # Create database
                    conn.execute(text("COMMIT"))  # End any active transaction
                    conn.execute(text(f"CREATE DATABASE {db_name}"))
                    logger.info(f"Created database: {db_name}")
                else:
                    logger.info(f"Database {db_name} already exists")

            temp_engine.dispose()
            return True

        except Exception as e:
            logger.error(f"Failed to create database: {e}")
            return False


# Global migration manager
migration_manager = MigrationManager()


def init_database() -> bool:
    """Initialize database with migrations."""
    logger.info("Initializing database...")

    # Create database if it doesn't exist
    if not migration_manager.create_database_if_not_exists():
        return False

    # Run migrations
    if not migration_manager.run_migrations():
        return False

    # Health check
    if not db_manager.health_check():
        logger.error("Database health check failed after initialization")
        return False

    logger.info("Database initialization completed successfully")
    return True


if __name__ == "__main__":
    """Run migrations when executed directly."""
    import argparse

    parser = argparse.ArgumentParser(description="Database management utility")
    parser.add_argument("command", choices=["init", "migrate", "health"], help="Command to execute")

    args = parser.parse_args()

    if args.command == "init":
        success = init_database()
        sys.exit(0 if success else 1)

    elif args.command == "migrate":
        success = migration_manager.run_migrations()
        sys.exit(0 if success else 1)

    elif args.command == "health":
        healthy = db_manager.health_check()
        print(f"Database health: {'OK' if healthy else 'FAILED'}")
        sys.exit(0 if healthy else 1)
