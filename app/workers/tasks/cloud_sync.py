"""
Cloud Storage Sync tasks for SalesWhisper Crosspost.

This module handles:
- Periodic sync of cloud storage folders
- On-demand sync triggers
- Processing synced files through media adapter
- Managing sync status and error handling
"""

import os
import time
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from ...core.logging import get_logger, with_logging_context
from ...models.db import db_manager
from ...models.entities import CloudConnectionStatus, CloudStorageConnection, CloudSyncedFile
from ...models.entities import MediaType as DBMediaType
from ...services.cloud_storage import cloud_storage_service
from ..celery_app import celery

logger = get_logger("tasks.cloud_sync")


@celery.task(bind=True, name="app.workers.tasks.cloud_sync.sync_cloud_connection")
def sync_cloud_connection(self, connection_id: str) -> dict[str, Any]:
    """
    Sync a single cloud storage connection.

    Downloads new/updated files from cloud storage and stores them locally.

    Args:
        connection_id: Cloud connection ID to sync

    Returns:
        Sync result with statistics
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id, connection_id=connection_id):
        logger.info("Starting cloud sync", connection_id=connection_id)

        import asyncio

        async def _sync():
            async with db_manager.async_session_maker() as session:
                # Get connection
                result = await session.execute(
                    select(CloudStorageConnection).where(
                        CloudStorageConnection.id == UUID(connection_id)
                    )
                )
                connection = result.scalar_one_or_none()

                if not connection:
                    logger.error("Connection not found", connection_id=connection_id)
                    return {"success": False, "error": "Connection not found"}

                if connection.status != CloudConnectionStatus.ACTIVE:
                    logger.warning(
                        "Connection not active",
                        connection_id=connection_id,
                        status=connection.status.value
                    )
                    return {"success": False, "error": f"Connection not active: {connection.status.value}"}

                try:
                    # Check if tokens need refresh
                    if connection.token_expires_at and connection.token_expires_at < datetime.utcnow():
                        if connection.refresh_token:
                            logger.info("Refreshing expired token", connection_id=connection_id)
                            new_tokens = await cloud_storage_service.refresh_token(
                                connection.provider,
                                connection.refresh_token
                            )
                            if new_tokens:
                                connection.access_token = new_tokens.get('access_token')
                                if new_tokens.get('refresh_token'):
                                    connection.refresh_token = new_tokens['refresh_token']
                                if new_tokens.get('expires_in'):
                                    connection.token_expires_at = datetime.utcnow() + timedelta(
                                        seconds=new_tokens['expires_in']
                                    )
                                await session.commit()
                            else:
                                connection.status = CloudConnectionStatus.EXPIRED
                                connection.error_message = "Failed to refresh token"
                                await session.commit()
                                return {"success": False, "error": "Token refresh failed"}

                    # Build media type filter
                    media_types = []
                    if connection.sync_videos:
                        media_types.append('video')
                    if connection.sync_photos:
                        media_types.append('image')

                    # Prepare credentials
                    credentials = None
                    if connection.access_token:
                        credentials = {
                            'access_token': connection.access_token,
                            'refresh_token': connection.refresh_token
                        }

                    # Perform sync
                    sync_result = await cloud_storage_service.sync_folder(
                        provider=connection.provider,
                        folder_id=connection.folder_id,
                        user_id=str(connection.user_id),
                        credentials=credentials,
                        public_url=connection.public_url if connection.is_public else None,
                        media_types=media_types if media_types else None
                    )

                    # Process synced files
                    files_processed = 0
                    for file_path in sync_result.downloaded_files:
                        try:
                            # Record synced file in database
                            file_name = os.path.basename(file_path)

                            # Determine media type
                            ext = os.path.splitext(file_name)[1].lower()
                            if ext in ['.mp4', '.mov', '.avi', '.mkv', '.webm']:
                                media_type = DBMediaType.VIDEO
                            elif ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp']:
                                media_type = DBMediaType.IMAGE
                            else:
                                media_type = DBMediaType.DOCUMENT

                            # Check if file already synced
                            existing = await session.execute(
                                select(CloudSyncedFile).where(
                                    CloudSyncedFile.connection_id == connection.id,
                                    CloudSyncedFile.cloud_file_name == file_name
                                )
                            )
                            synced_file = existing.scalar_one_or_none()

                            if synced_file:
                                # Update existing record
                                synced_file.local_path = file_path
                                synced_file.is_synced = True
                                synced_file.last_synced_at = datetime.utcnow()
                                synced_file.sync_error = None
                            else:
                                # Create new record
                                synced_file = CloudSyncedFile(
                                    connection_id=connection.id,
                                    cloud_file_id=file_name,  # Use filename as ID for now
                                    cloud_file_name=file_name,
                                    cloud_file_path=file_path,
                                    media_type=media_type,
                                    local_path=file_path,
                                    local_file_size=os.path.getsize(file_path) if os.path.exists(file_path) else None,
                                    is_synced=True,
                                    first_synced_at=datetime.utcnow(),
                                    last_synced_at=datetime.utcnow()
                                )
                                session.add(synced_file)

                            files_processed += 1

                        except Exception as e:
                            logger.warning(f"Failed to record synced file: {e}", file_path=file_path)

                    # Update connection status
                    connection.last_sync_at = datetime.utcnow()
                    connection.last_sync_status = "success" if sync_result.success else "partial"
                    connection.files_synced_total += sync_result.files_downloaded
                    connection.last_sync_files_count = sync_result.files_downloaded
                    connection.last_sync_errors = sync_result.errors[:10] if sync_result.errors else []
                    connection.error_message = None

                    await session.commit()

                    processing_time = time.time() - task_start_time

                    logger.info(
                        "Cloud sync completed",
                        connection_id=connection_id,
                        files_found=sync_result.files_found,
                        files_downloaded=sync_result.files_downloaded,
                        files_failed=sync_result.files_failed,
                        processing_time=processing_time
                    )

                    return {
                        "success": sync_result.success,
                        "connection_id": connection_id,
                        "files_found": sync_result.files_found,
                        "files_downloaded": sync_result.files_downloaded,
                        "files_failed": sync_result.files_failed,
                        "files_processed": files_processed,
                        "errors": sync_result.errors,
                        "processing_time": processing_time
                    }

                except Exception as e:
                    logger.error(
                        "Cloud sync failed",
                        connection_id=connection_id,
                        error=str(e),
                        exc_info=True
                    )

                    # Update connection with error
                    connection.last_sync_at = datetime.utcnow()
                    connection.last_sync_status = "failed"
                    connection.error_message = str(e)[:500]
                    await session.commit()

                    # Retry if appropriate
                    if self.request.retries < self.max_retries:
                        raise self.retry(countdown=60 * (self.request.retries + 1), exc=e)

                    return {
                        "success": False,
                        "connection_id": connection_id,
                        "error": str(e)
                    }

        # Run async function
        return asyncio.run(_sync())


@celery.task(bind=True, name="app.workers.tasks.cloud_sync.sync_all_connections")
def sync_all_connections(self) -> dict[str, Any]:
    """
    Sync all active cloud storage connections.

    Triggered by Celery Beat periodically.

    Returns:
        Summary of all sync operations
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id):
        logger.info("Starting sync for all cloud connections")

        import asyncio

        async def _sync_all():
            async with db_manager.async_session_maker() as session:
                # Get all active connections due for sync
                now = datetime.utcnow()

                result = await session.execute(
                    select(CloudStorageConnection).where(
                        CloudStorageConnection.status == CloudConnectionStatus.ACTIVE,
                        CloudStorageConnection.sync_enabled
                    )
                )
                connections = result.scalars().all()

                sync_results = []
                synced_count = 0
                skipped_count = 0
                failed_count = 0

                for conn in connections:
                    try:
                        # Check if sync is due
                        if conn.last_sync_at:
                            next_sync = conn.last_sync_at + timedelta(minutes=conn.sync_interval_minutes)
                            if now < next_sync:
                                skipped_count += 1
                                continue

                        # Queue individual sync task
                        sync_cloud_connection.delay(str(conn.id))
                        synced_count += 1

                        sync_results.append({
                            "connection_id": str(conn.id),
                            "provider": conn.provider.value,
                            "status": "queued"
                        })

                    except Exception as e:
                        logger.error(
                            "Failed to queue sync for connection",
                            connection_id=str(conn.id),
                            error=str(e)
                        )
                        failed_count += 1
                        sync_results.append({
                            "connection_id": str(conn.id),
                            "provider": conn.provider.value,
                            "status": "failed",
                            "error": str(e)
                        })

                processing_time = time.time() - task_start_time

                logger.info(
                    "All connections sync initiated",
                    total_connections=len(connections),
                    synced=synced_count,
                    skipped=skipped_count,
                    failed=failed_count,
                    processing_time=processing_time
                )

                return {
                    "success": True,
                    "total_connections": len(connections),
                    "synced": synced_count,
                    "skipped": skipped_count,
                    "failed": failed_count,
                    "results": sync_results,
                    "processing_time": processing_time
                }

        return asyncio.run(_sync_all())


@celery.task(bind=True, name="app.workers.tasks.cloud_sync.process_synced_file")
def process_synced_file(self, synced_file_id: str) -> dict[str, Any]:
    """
    Process a synced file through the media adapter.

    Applies smart cropping and creates platform-specific versions.

    Args:
        synced_file_id: CloudSyncedFile ID

    Returns:
        Processing result
    """
    task_start_time = time.time()

    with with_logging_context(task_id=self.request.id, synced_file_id=synced_file_id):
        logger.info("Processing synced file", synced_file_id=synced_file_id)

        import asyncio

        async def _process():
            async with db_manager.async_session_maker() as session:
                # Get synced file
                result = await session.execute(
                    select(CloudSyncedFile).where(
                        CloudSyncedFile.id == UUID(synced_file_id)
                    )
                )
                synced_file = result.scalar_one_or_none()

                if not synced_file:
                    return {"success": False, "error": "Synced file not found"}

                if not synced_file.local_path or not os.path.exists(synced_file.local_path):
                    return {"success": False, "error": "Local file not found"}

                try:
                    # Import smart media adapter
                    from ...media.smart_media_adapter import SmartMediaAdapter

                    adapter = SmartMediaAdapter()

                    # Process for different platforms based on media type
                    if synced_file.media_type == DBMediaType.VIDEO:
                        platforms = ["tiktok", "instagram", "youtube"]
                    else:
                        platforms = ["instagram", "vk", "telegram"]

                    processed_versions = {}

                    for platform in platforms:
                        output_path = synced_file.local_path.replace(
                            os.path.splitext(synced_file.local_path)[1],
                            f"_{platform}{os.path.splitext(synced_file.local_path)[1]}"
                        )

                        result = await adapter.adapt_media(
                            input_path=synced_file.local_path,
                            output_path=output_path,
                            platform=platform,
                            format_type="video" if synced_file.media_type == DBMediaType.VIDEO else "feed"
                        )

                        if result.get("success"):
                            processed_versions[platform] = output_path
                            logger.info(
                                f"Processed for {platform}",
                                synced_file_id=synced_file_id,
                                output_path=output_path
                            )

                    processing_time = time.time() - task_start_time

                    logger.info(
                        "Synced file processing completed",
                        synced_file_id=synced_file_id,
                        platforms_processed=list(processed_versions.keys()),
                        processing_time=processing_time
                    )

                    return {
                        "success": True,
                        "synced_file_id": synced_file_id,
                        "processed_versions": processed_versions,
                        "processing_time": processing_time
                    }

                except Exception as e:
                    logger.error(
                        "File processing failed",
                        synced_file_id=synced_file_id,
                        error=str(e),
                        exc_info=True
                    )
                    return {
                        "success": False,
                        "synced_file_id": synced_file_id,
                        "error": str(e)
                    }

        return asyncio.run(_process())


# Celery Beat schedule entry (add to beat_schedule in celery_app.py)
CLOUD_SYNC_BEAT_SCHEDULE = {
    'sync-all-cloud-connections': {
        'task': 'app.workers.tasks.cloud_sync.sync_all_connections',
        'schedule': 3600.0,  # Every hour
        'options': {'queue': 'cloud_sync'}
    },
}
