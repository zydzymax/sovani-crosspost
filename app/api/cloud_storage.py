"""
Cloud Storage API routes for SalesWhisper Crosspost.

Provides endpoints for:
- Connecting Google Drive / Yandex Disk accounts
- OAuth flow handling
- Managing cloud folder connections
- Manual sync triggers
- Listing synced media files
"""

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import settings
from ..core.logging import get_logger
from ..models.entities import (
    CloudConnectionStatus,
    CloudProvider,
    CloudStorageConnection,
    CloudSyncedFile,
    MediaType,
    User,
)
from ..services.cloud_storage import cloud_storage_service
from .deps import get_current_user, get_db_session

logger = get_logger("api.cloud_storage")

router = APIRouter(prefix="/cloud-storage", tags=["Cloud Storage"])


# ==================== SCHEMAS ====================

class ConnectCloudRequest(BaseModel):
    """Request to connect cloud storage."""
    provider: str = Field(..., description="Provider: google_drive or yandex_disk")
    folder_url: str = Field(..., description="Sharing URL or folder path")
    is_public: bool = Field(default=False, description="Is public folder (no OAuth needed)")


class ConnectCloudResponse(BaseModel):
    """Response for cloud connection."""
    success: bool
    connection_id: str | None = None
    oauth_url: str | None = None
    message: str
    folder_info: dict | None = None


class OAuthCallbackRequest(BaseModel):
    """OAuth callback request."""
    code: str = Field(..., description="OAuth authorization code")
    state: str = Field(..., description="State parameter with connection ID")


class CloudConnectionResponse(BaseModel):
    """Cloud connection details."""
    id: str
    provider: str
    folder_name: str | None
    folder_url: str | None
    status: str
    is_public: bool
    sync_enabled: bool
    last_sync_at: datetime | None
    files_synced_total: int
    error_message: str | None
    created_at: datetime


class CloudConnectionsListResponse(BaseModel):
    """List of cloud connections."""
    connections: list[CloudConnectionResponse]
    total: int


class SyncTriggerResponse(BaseModel):
    """Sync trigger response."""
    success: bool
    message: str
    task_id: str | None = None


class CloudFileResponse(BaseModel):
    """Synced file info."""
    id: str
    cloud_file_name: str
    cloud_file_path: str | None
    media_type: str
    cloud_file_size: int | None
    is_synced: bool
    last_synced_at: datetime | None


class CloudFilesListResponse(BaseModel):
    """List of synced files."""
    files: list[CloudFileResponse]
    total: int
    page: int
    page_size: int


class FolderStructureResponse(BaseModel):
    """Expected folder structure guide."""
    description: str
    structure: dict
    supported_video_formats: list[str]
    supported_image_formats: list[str]
    tips: list[str]


class UpdateConnectionRequest(BaseModel):
    """Update connection settings."""
    sync_enabled: bool | None = None
    sync_videos: bool | None = None
    sync_photos: bool | None = None
    sync_interval_minutes: int | None = None


# ==================== ROUTES ====================

@router.get("/folder-structure", response_model=FolderStructureResponse)
async def get_folder_structure():
    """
    Get expected folder structure for cloud storage.
    Returns guide for users on how to organize their media files.
    """
    structure = cloud_storage_service.get_expected_folder_structure()
    return FolderStructureResponse(**structure)


@router.post("/connect", response_model=ConnectCloudResponse)
async def connect_cloud_storage(
    request: ConnectCloudRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """
    Connect a cloud storage folder.

    For public folders (Yandex Disk public links), no OAuth is needed.
    For private folders, returns OAuth URL for user to authorize.
    """
    # Validate provider
    try:
        provider = CloudProvider(request.provider)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid provider. Supported: google_drive, yandex_disk"
        )

    # Extract folder ID from URL
    folder_id = cloud_storage_service.extract_folder_id(provider, request.folder_url)
    if not folder_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Could not extract folder ID from URL"
        )

    # Check for existing connection to same folder
    existing = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.user_id == current_user.id,
            CloudStorageConnection.provider == provider,
            CloudStorageConnection.folder_id == folder_id
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This folder is already connected"
        )

    # Create connection record
    connection = CloudStorageConnection(
        user_id=current_user.id,
        provider=provider,
        folder_id=folder_id,
        folder_url=request.folder_url,
        is_public=request.is_public,
        status=CloudConnectionStatus.PENDING if not request.is_public else CloudConnectionStatus.ACTIVE,
        public_url=request.folder_url if request.is_public else None,
    )

    db.add(connection)
    await db.commit()
    await db.refresh(connection)

    logger.info(
        "Cloud connection created",
        user_id=str(current_user.id),
        provider=provider.value,
        connection_id=str(connection.id),
        is_public=request.is_public
    )

    # For public folders, validate and return immediately
    if request.is_public:
        # Validate connection
        is_valid, error = await cloud_storage_service.validate_connection(
            provider=provider,
            folder_id=folder_id,
            public_url=request.folder_url
        )

        if not is_valid:
            connection.status = CloudConnectionStatus.ERROR
            connection.error_message = error
            await db.commit()

            return ConnectCloudResponse(
                success=False,
                connection_id=str(connection.id),
                message=f"Failed to access folder: {error}"
            )

        # Get folder info
        folder_info = await cloud_storage_service.get_folder_info(
            provider=provider,
            folder_id=folder_id,
            public_url=request.folder_url
        )

        if folder_info:
            connection.folder_name = folder_info.folder_name
            await db.commit()

        return ConnectCloudResponse(
            success=True,
            connection_id=str(connection.id),
            message="Public folder connected successfully",
            folder_info=folder_info.__dict__ if folder_info else None
        )

    # For private folders, generate OAuth URL
    redirect_uri = f"{settings.API_BASE_URL}/api/v1/cloud-storage/oauth/callback"
    state = str(connection.id)  # Pass connection ID in state

    oauth_url = cloud_storage_service.get_oauth_url(
        provider=provider,
        redirect_uri=redirect_uri,
        state=state
    )

    return ConnectCloudResponse(
        success=True,
        connection_id=str(connection.id),
        oauth_url=oauth_url,
        message="Please authorize access via the OAuth URL"
    )


@router.get("/oauth/callback")
async def oauth_callback(
    code: str = Query(..., description="OAuth authorization code"),
    state: str = Query(..., description="Connection ID"),
    db: AsyncSession = Depends(get_db_session)
):
    """
    OAuth callback handler.
    Exchanges authorization code for tokens and activates connection.
    """
    # Find connection by state (connection ID)
    try:
        connection_id = UUID(state)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid state parameter"
        )

    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    # Exchange code for tokens
    redirect_uri = f"{settings.API_BASE_URL}/api/v1/cloud-storage/oauth/callback"

    tokens = await cloud_storage_service.exchange_oauth_code(
        provider=connection.provider,
        code=code,
        redirect_uri=redirect_uri
    )

    if not tokens:
        connection.status = CloudConnectionStatus.ERROR
        connection.error_message = "Failed to exchange OAuth code"
        await db.commit()

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Failed to exchange OAuth code for tokens"
        )

    # Store tokens and activate connection
    connection.access_token = tokens.get('access_token')
    connection.refresh_token = tokens.get('refresh_token')
    if tokens.get('expires_in'):
        from datetime import timedelta
        connection.token_expires_at = datetime.utcnow() + timedelta(seconds=tokens['expires_in'])

    connection.status = CloudConnectionStatus.ACTIVE
    connection.error_message = None

    # Validate connection and get folder info
    credentials = {
        'access_token': connection.access_token,
        'refresh_token': connection.refresh_token
    }

    folder_info = await cloud_storage_service.get_folder_info(
        provider=connection.provider,
        folder_id=connection.folder_id,
        credentials=credentials
    )

    if folder_info:
        connection.folder_name = folder_info.folder_name

    await db.commit()

    logger.info(
        "OAuth callback successful",
        connection_id=str(connection.id),
        provider=connection.provider.value
    )

    # Redirect to frontend with success
    frontend_url = f"{settings.FRONTEND_URL}/dashboard/cloud-storage?connected={connection.id}"
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url=frontend_url)


@router.get("/connections", response_model=CloudConnectionsListResponse)
async def list_connections(
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """List all cloud storage connections for current user."""
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.user_id == current_user.id
        ).order_by(CloudStorageConnection.created_at.desc())
    )
    connections = result.scalars().all()

    return CloudConnectionsListResponse(
        connections=[
            CloudConnectionResponse(
                id=str(c.id),
                provider=c.provider.value,
                folder_name=c.folder_name,
                folder_url=c.folder_url,
                status=c.status.value,
                is_public=c.is_public,
                sync_enabled=c.sync_enabled,
                last_sync_at=c.last_sync_at,
                files_synced_total=c.files_synced_total,
                error_message=c.error_message,
                created_at=c.created_at
            )
            for c in connections
        ],
        total=len(connections)
    )


@router.get("/connections/{connection_id}", response_model=CloudConnectionResponse)
async def get_connection(
    connection_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Get details of a specific cloud connection."""
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id,
            CloudStorageConnection.user_id == current_user.id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    return CloudConnectionResponse(
        id=str(connection.id),
        provider=connection.provider.value,
        folder_name=connection.folder_name,
        folder_url=connection.folder_url,
        status=connection.status.value,
        is_public=connection.is_public,
        sync_enabled=connection.sync_enabled,
        last_sync_at=connection.last_sync_at,
        files_synced_total=connection.files_synced_total,
        error_message=connection.error_message,
        created_at=connection.created_at
    )


@router.patch("/connections/{connection_id}", response_model=CloudConnectionResponse)
async def update_connection(
    connection_id: UUID,
    request: UpdateConnectionRequest,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Update connection settings."""
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id,
            CloudStorageConnection.user_id == current_user.id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    # Update fields
    if request.sync_enabled is not None:
        connection.sync_enabled = request.sync_enabled
    if request.sync_videos is not None:
        connection.sync_videos = request.sync_videos
    if request.sync_photos is not None:
        connection.sync_photos = request.sync_photos
    if request.sync_interval_minutes is not None:
        connection.sync_interval_minutes = max(15, min(1440, request.sync_interval_minutes))

    connection.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(connection)

    return CloudConnectionResponse(
        id=str(connection.id),
        provider=connection.provider.value,
        folder_name=connection.folder_name,
        folder_url=connection.folder_url,
        status=connection.status.value,
        is_public=connection.is_public,
        sync_enabled=connection.sync_enabled,
        last_sync_at=connection.last_sync_at,
        files_synced_total=connection.files_synced_total,
        error_message=connection.error_message,
        created_at=connection.created_at
    )


@router.delete("/connections/{connection_id}")
async def delete_connection(
    connection_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Delete a cloud connection."""
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id,
            CloudStorageConnection.user_id == current_user.id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    await db.delete(connection)
    await db.commit()

    logger.info(
        "Cloud connection deleted",
        connection_id=str(connection_id),
        user_id=str(current_user.id)
    )

    return {"success": True, "message": "Connection deleted"}


@router.post("/connections/{connection_id}/sync", response_model=SyncTriggerResponse)
async def trigger_sync(
    connection_id: UUID,
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """Manually trigger sync for a cloud connection."""
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id,
            CloudStorageConnection.user_id == current_user.id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    if connection.status != CloudConnectionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Connection is not active: {connection.status.value}"
        )

    # Queue sync task
    try:
        from ..workers.tasks.cloud_sync import sync_cloud_connection
        task = sync_cloud_connection.delay(str(connection_id))

        logger.info(
            "Sync task queued",
            connection_id=str(connection_id),
            task_id=task.id
        )

        return SyncTriggerResponse(
            success=True,
            message="Sync task queued",
            task_id=task.id
        )
    except Exception as e:
        logger.error(f"Failed to queue sync task: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to queue sync task"
        )


@router.get("/connections/{connection_id}/files", response_model=CloudFilesListResponse)
async def list_synced_files(
    connection_id: UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    media_type: str | None = Query(None, description="Filter by media type: video or image"),
    db: AsyncSession = Depends(get_db_session),
    current_user: User = Depends(get_current_user)
):
    """List files synced from a cloud connection."""
    # Verify connection belongs to user
    result = await db.execute(
        select(CloudStorageConnection).where(
            CloudStorageConnection.id == connection_id,
            CloudStorageConnection.user_id == current_user.id
        )
    )
    connection = result.scalar_one_or_none()

    if not connection:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Connection not found"
        )

    # Build query
    query = select(CloudSyncedFile).where(
        CloudSyncedFile.connection_id == connection_id
    )

    if media_type:
        try:
            mt = MediaType(media_type)
            query = query.where(CloudSyncedFile.media_type == mt)
        except ValueError:
            pass

    # Count total
    from sqlalchemy import func
    count_result = await db.execute(
        select(func.count()).select_from(query.subquery())
    )
    total = count_result.scalar()

    # Get page
    query = query.order_by(CloudSyncedFile.created_at.desc())
    query = query.offset((page - 1) * page_size).limit(page_size)

    result = await db.execute(query)
    files = result.scalars().all()

    return CloudFilesListResponse(
        files=[
            CloudFileResponse(
                id=str(f.id),
                cloud_file_name=f.cloud_file_name,
                cloud_file_path=f.cloud_file_path,
                media_type=f.media_type.value,
                cloud_file_size=f.cloud_file_size,
                is_synced=f.is_synced,
                last_synced_at=f.last_synced_at
            )
            for f in files
        ],
        total=total,
        page=page,
        page_size=page_size
    )


@router.get("/providers")
async def list_providers():
    """List supported cloud storage providers."""
    return {
        "providers": [
            {
                "id": "google_drive",
                "name": "Google Drive",
                "icon": "google-drive",
                "supports_public": False,
                "requires_oauth": True,
                "description": "Connect your Google Drive folder"
            },
            {
                "id": "yandex_disk",
                "name": "Яндекс.Диск",
                "icon": "yandex-disk",
                "supports_public": True,
                "requires_oauth": True,
                "description": "Connect your Yandex Disk folder (supports public links)"
            }
        ]
    }
