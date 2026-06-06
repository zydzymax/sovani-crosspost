"""
VK publisher for SalesWhisper Crosspost.
Publishes posts to VK groups/pages via VK API.
Includes group customization: cover, avatar, info editing.
"""

from dataclasses import dataclass

import httpx

from ...core.logging import get_logger
from ...core.security import decrypt_data

logger = get_logger("publishers.vk")


@dataclass
class PublishResult:
    success: bool
    platform_post_id: str | None = None
    platform_url: str | None = None
    error: str | None = None


@dataclass
class GroupEditResult:
    success: bool
    error: str | None = None
    data: dict | None = None


class VKPublisher:
    """Publish content to VK groups/pages and manage group settings."""

    API_URL = "https://api.vk.com/method"
    API_VERSION = "5.131"

    def __init__(self, account):
        """
        Initialize with SocialAccount.
        Uses access_token and extra_credentials for group_id/owner_id.
        """
        self.account = account
        extra = account.extra_credentials or {}

        # Decrypt access_token - VK tokens are alphanumeric, encrypted have base64 chars (+/=)
        raw_token = account.access_token or ""
        if raw_token:
            if "/" in raw_token or "+" in raw_token or raw_token.endswith("="):
                try:
                    self.access_token = decrypt_data(raw_token)
                    logger.info("Decrypted VK token successfully")
                except Exception as e:
                    logger.warning("Failed to decrypt VK token, using as-is", error=str(e))
                    self.access_token = raw_token
            else:
                self.access_token = raw_token
        else:
            self.access_token = ""

        self.owner_id = extra.get("owner_id") or extra.get("group_id", "")
        self.group_id = str(self.owner_id).replace("-", "") if self.owner_id else ""

        # VK groups have negative owner_id
        if self.owner_id and not str(self.owner_id).startswith("-"):
            self.owner_id = f"-{self.owner_id}"

        if not self.access_token:
            raise ValueError("VK credentials missing: access_token required")

    @staticmethod
    def _vk_error(data: dict) -> str | None:
        if "error" not in data:
            return None
        return data["error"].get("error_msg", str(data["error"]))

    @staticmethod
    def _group_edit_error(error: str, prefix: str | None = None) -> GroupEditResult:
        return GroupEditResult(success=False, error=f"{prefix}: {error}" if prefix else error)

    async def _fetch_image_bytes(self, client: httpx.AsyncClient, image_url: str) -> bytes | None:
        img_response = await client.get(image_url)
        if img_response.status_code != 200:
            return None
        return img_response.content

    # ==================== POST PUBLISHING ====================

    async def publish(
        self, text: str, image_url: str | None = None, hashtags: list[str] | None = None
    ) -> PublishResult:
        """Publish post to VK."""
        message = self._format_message(text, hashtags)

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                attachments = ""
                if image_url:
                    photo_id = await self._upload_photo(client, image_url)
                    if photo_id:
                        attachments = photo_id

                result = await self._create_post(client, message, attachments)
                return result

            except Exception as e:
                logger.exception("VK publish failed")
                return PublishResult(success=False, error=str(e))

    async def _upload_photo(self, client: httpx.AsyncClient, image_url: str) -> str | None:
        """Upload photo to VK and return attachment string."""
        try:
            params = {
                "access_token": self.access_token,
                "v": self.API_VERSION,
            }
            if self.group_id:
                params["group_id"] = self.group_id

            response = await client.get(f"{self.API_URL}/photos.getWallUploadServer", params=params)
            data = response.json()

            error = self._vk_error(data)
            if error:
                logger.error("VK getWallUploadServer error", error=error)
                return None

            upload_url = data["response"]["upload_url"]

            img_response = await client.get(image_url)
            if img_response.status_code != 200:
                logger.error(f"Failed to download image: {image_url}")
                return None

            files = {"photo": ("image.jpg", img_response.content, "image/jpeg")}
            upload_response = await client.post(upload_url, files=files)
            upload_data = upload_response.json()

            save_params = {
                "access_token": self.access_token,
                "v": self.API_VERSION,
                "server": upload_data["server"],
                "photo": upload_data["photo"],
                "hash": upload_data["hash"],
            }
            if self.group_id:
                save_params["group_id"] = self.group_id

            save_response = await client.get(f"{self.API_URL}/photos.saveWallPhoto", params=save_params)
            save_data = save_response.json()

            error = self._vk_error(save_data)
            if error:
                logger.error("VK saveWallPhoto error", error=error)
                return None

            photo = save_data["response"][0]
            return f"photo{photo['owner_id']}_{photo['id']}"

        except Exception:
            logger.exception("VK photo upload failed")
            return None

    async def _create_post(self, client: httpx.AsyncClient, message: str, attachments: str = "") -> PublishResult:
        """Create wall post."""
        params = {
            "access_token": self.access_token,
            "v": self.API_VERSION,
            "message": message,
            "from_group": 1,
        }

        if self.owner_id:
            params["owner_id"] = self.owner_id

        if attachments:
            params["attachments"] = attachments

        response = await client.get(f"{self.API_URL}/wall.post", params=params)
        data = response.json()

        error = self._vk_error(data)
        if error:
            logger.error("VK wall.post error", error=error)
            return PublishResult(success=False, error=error)

        post_id = data["response"]["post_id"]
        owner = self.owner_id or "0"

        return PublishResult(
            success=True, platform_post_id=str(post_id), platform_url=f"https://vk.com/wall{owner}_{post_id}"
        )

    def _format_message(self, text: str, hashtags: list[str] | None) -> str:
        """Format message with hashtags."""
        message = text or ""
        if hashtags:
            tags = " ".join(f"#{tag.replace('#', '')}" for tag in hashtags)
            message = f"{message}\n\n{tags}"
        return message.strip()

    # ==================== GROUP COVER ====================

    async def set_cover(
        self,
        image_url: str | None = None,
        image_bytes: bytes | None = None,
        crop_x: int = 0,
        crop_y: int = 0,
        crop_x2: int = 1590,
        crop_y2: int = 400,
    ) -> GroupEditResult:
        """
        Set group cover image.
        Recommended size: 1590x400px
        """
        if not self.group_id:
            return GroupEditResult(success=False, error="group_id is required")

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            try:
                # Get upload URL
                params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "group_id": self.group_id,
                    "crop_x": crop_x,
                    "crop_y": crop_y,
                    "crop_x2": crop_x2,
                    "crop_y2": crop_y2,
                }

                response = await client.get(f"{self.API_URL}/photos.getOwnerCoverPhotoUploadServer", params=params)
                data = response.json()

                error = self._vk_error(data)
                if error:
                    return self._group_edit_error(error, "Get upload URL failed")

                upload_url = data["response"]["upload_url"]

                # Get image bytes
                if image_url and not image_bytes:
                    image_bytes = await self._fetch_image_bytes(client, image_url)
                    if image_bytes is None:
                        return GroupEditResult(success=False, error=f"Failed to download image from {image_url}")

                if not image_bytes:
                    return GroupEditResult(success=False, error="No image provided")

                # Upload
                files = {"photo": ("cover.jpg", image_bytes, "image/jpeg")}
                upload_response = await client.post(upload_url, files=files)
                upload_data = upload_response.json()

                if not upload_data.get("photo"):
                    return GroupEditResult(success=False, error="Upload failed: no photo in response")

                # Save cover
                save_params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "photo": upload_data["photo"],
                    "hash": upload_data["hash"],
                }

                save_response = await client.get(f"{self.API_URL}/photos.saveOwnerCoverPhoto", params=save_params)
                save_data = save_response.json()

                error = self._vk_error(save_data)
                if error:
                    return self._group_edit_error(error, "Save cover failed")

                logger.info("VK cover updated", group_id=self.group_id)
                return GroupEditResult(success=True, data=save_data.get("response"))

            except Exception as e:
                logger.exception("VK set cover failed")
                return GroupEditResult(success=False, error=str(e))

    # ==================== GROUP AVATAR ====================

    async def set_avatar(self, image_url: str | None = None, image_bytes: bytes | None = None) -> GroupEditResult:
        """
        Set group avatar/main photo.
        Minimum size: 200x200px, recommended: square image
        """
        if not self.group_id:
            return GroupEditResult(success=False, error="group_id is required")

        async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
            try:
                # Get upload URL
                params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "owner_id": self.owner_id,
                }

                response = await client.get(f"{self.API_URL}/photos.getOwnerPhotoUploadServer", params=params)
                data = response.json()

                error = self._vk_error(data)
                if error:
                    return self._group_edit_error(error, "Get upload URL failed")

                upload_url = data["response"]["upload_url"]

                # Get image bytes
                if image_url and not image_bytes:
                    image_bytes = await self._fetch_image_bytes(client, image_url)
                    if image_bytes is None:
                        return GroupEditResult(success=False, error=f"Failed to download image from {image_url}")

                if not image_bytes:
                    return GroupEditResult(success=False, error="No image provided")

                # Upload
                files = {"photo": ("avatar.jpg", image_bytes, "image/jpeg")}
                upload_response = await client.post(upload_url, files=files)
                upload_data = upload_response.json()

                if not upload_data.get("photo"):
                    return GroupEditResult(success=False, error="Upload failed: no photo in response")

                # Save avatar
                save_params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "server": upload_data["server"],
                    "photo": upload_data["photo"],
                    "hash": upload_data["hash"],
                }

                save_response = await client.get(f"{self.API_URL}/photos.saveOwnerPhoto", params=save_params)
                save_data = save_response.json()

                error = self._vk_error(save_data)
                if error:
                    return self._group_edit_error(error, "Save avatar failed")

                logger.info("VK avatar updated", group_id=self.group_id)
                return GroupEditResult(success=True, data=save_data.get("response"))

            except Exception as e:
                logger.exception("VK set avatar failed")
                return GroupEditResult(success=False, error=str(e))

    # ==================== GROUP INFO ====================

    async def edit_group_info(
        self,
        title: str | None = None,
        description: str | None = None,
        website: str | None = None,
        public_category: int | None = None,
        public_subcategory: int | None = None,
        wall: int | None = None,  # 0-disabled, 1-open, 2-restricted, 3-closed
        topics: int | None = None,  # 0-disabled, 1-open, 2-restricted
        photos: int | None = None,  # 0-disabled, 1-open, 2-restricted
        video: int | None = None,  # 0-disabled, 1-open, 2-restricted
        audio: int | None = None,  # 0-disabled, 1-open, 2-restricted
        docs: int | None = None,  # 0-disabled, 1-open, 2-restricted
        contacts: int | None = None,  # 0-disabled, 1-enabled
        links: int | None = None,  # 0-disabled, 1-enabled
        events: int | None = None,  # 0-disabled, 1-enabled
        addresses: int | None = None,  # 0-disabled, 1-enabled
        age_limits: int | None = None,  # 1-no limit, 2-16+, 3-18+
        obscene_filter: int | None = None,  # 0-disabled, 1-enabled
        obscene_stopwords: int | None = None,  # 0-disabled, 1-enabled
    ) -> GroupEditResult:
        """
        Edit group information and settings.
        """
        if not self.group_id:
            return GroupEditResult(success=False, error="group_id is required")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "group_id": self.group_id,
                }

                # Add only provided parameters
                if title is not None:
                    params["title"] = title
                if description is not None:
                    params["description"] = description
                if website is not None:
                    params["website"] = website
                if public_category is not None:
                    params["public_category"] = public_category
                if public_subcategory is not None:
                    params["public_subcategory"] = public_subcategory
                if wall is not None:
                    params["wall"] = wall
                if topics is not None:
                    params["topics"] = topics
                if photos is not None:
                    params["photos"] = photos
                if video is not None:
                    params["video"] = video
                if audio is not None:
                    params["audio"] = audio
                if docs is not None:
                    params["docs"] = docs
                if contacts is not None:
                    params["contacts"] = contacts
                if links is not None:
                    params["links"] = links
                if events is not None:
                    params["events"] = events
                if addresses is not None:
                    params["addresses"] = addresses
                if age_limits is not None:
                    params["age_limits"] = age_limits
                if obscene_filter is not None:
                    params["obscene_filter"] = obscene_filter
                if obscene_stopwords is not None:
                    params["obscene_stopwords"] = obscene_stopwords

                response = await client.get(f"{self.API_URL}/groups.edit", params=params)
                data = response.json()

                error = self._vk_error(data)
                if error:
                    return self._group_edit_error(error)

                logger.info("VK group info updated", group_id=self.group_id)
                return GroupEditResult(success=True, data={"result": data.get("response")})

            except Exception as e:
                logger.exception("VK edit group info failed")
                return GroupEditResult(success=False, error=str(e))

    # ==================== GET GROUP INFO ====================

    async def get_group_info(self) -> GroupEditResult:
        """Get current group information."""
        if not self.group_id:
            return GroupEditResult(success=False, error="group_id is required")

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                params = {
                    "access_token": self.access_token,
                    "v": self.API_VERSION,
                    "group_id": self.group_id,
                    "fields": "description,site,status,wall,topics,photos,video,audio,docs,contacts,links,events,addresses,age_limits,cover,member_status",
                }

                response = await client.get(f"{self.API_URL}/groups.getById", params=params)
                data = response.json()

                error = self._vk_error(data)
                if error:
                    return self._group_edit_error(error)

                groups = data.get("response", [])
                if groups:
                    return GroupEditResult(success=True, data=groups[0])

                return GroupEditResult(success=False, error="Group not found")

            except Exception as e:
                logger.exception("VK get group info failed")
                return GroupEditResult(success=False, error=str(e))
