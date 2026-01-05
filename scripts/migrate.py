#!/usr/bin/env python3
"""
Database migration script for SalesWhisper Crosspost.

Usage:
    python scripts/migrate.py          # Run all migrations
    python scripts/migrate.py --init   # Initialize database + run migrations
    python scripts/migrate.py --check  # Health check only
"""

import sys
from pathlib import Path

# Add the app directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "app"))

import logging

from app.models.db import db_manager, init_database, migration_manager

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    """Main migration script."""
    import argparse

    parser = argparse.ArgumentParser(description="Database migration utility for SalesWhisper Crosspost")
    parser.add_argument("--init", action="store_true", help="Initialize database and run migrations")
    parser.add_argument("--check", action="store_true", help="Check database health")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        if args.check:
            logger.info("Checking database health...")
            healthy = db_manager.health_check()
            if healthy:
                logger.info("✅ Database is healthy")
                return 0
            else:
                logger.error("❌ Database health check failed")
                return 1

        elif args.init:
            logger.info("Initializing database and running migrations...")
            success = init_database()
            if success:
                logger.info("✅ Database initialization completed successfully")
                return 0
            else:
                logger.error("❌ Database initialization failed")
                return 1

        else:
            logger.info("Running database migrations...")
            success = migration_manager.run_migrations()
            if success:
                logger.info("✅ Migrations completed successfully")
                return 0
            else:
                logger.error("❌ Migration failed")
                return 1

    except Exception as e:
        logger.error(f"❌ Unexpected error: {e}")
        if args.verbose:
            import traceback

            traceback.print_exc()
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
