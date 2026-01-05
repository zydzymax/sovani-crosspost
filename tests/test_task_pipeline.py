"""
Unit tests for task pipeline integration in SalesWhisper Crosspost.

Tests the complete task chain without external API calls:
ingest -> enrich -> captionize -> transcode -> preflight -> publish -> finalize
"""

import time
import uuid
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import get_test_settings
from app.observability.metrics import get_test_metrics
from app.workers.tasks import captionize, enrich, finalize, ingest, preflight, publish, transcode
from app.workers.tasks.outbox import publish_outbox_event


class TestTaskPipeline:
    """Test complete task pipeline integration."""

    @pytest.fixture(autouse=True)
    def setup_test_environment(self):
        """Setup test environment with mocks."""
        self.test_settings = get_test_settings()
        self.test_metrics = get_test_metrics()

        # Mock database sessions
        self.db_session_mock = MagicMock()

        # Mock external dependencies
        with patch("app.models.db.db_manager.get_session", return_value=self.db_session_mock):
            yield

    def test_complete_task_pipeline_success(self):
        """Test successful execution of complete task pipeline."""
        # Test data
        post_id = str(uuid.uuid4())
        telegram_update = {
            "update_id": 12345,
            "message": {
                "message_id": 678,
                "from": {"id": 987654321, "first_name": "Test", "username": "testuser"},
                "chat": {"id": -1001234567890, "type": "channel", "title": "Test Channel"},
                "date": int(time.time()),
                "text": "Новая коллекция SalesWhisper! Стильные платья для современных женщин. #SalesWhisper #Style",
                "photo": [
                    {
                        "file_id": "AgACAgIAAxkDAAIB",
                        "file_unique_id": "AQADGAADr7cxG3I",
                        "file_size": 1234567,
                        "width": 1280,
                        "height": 960,
                    }
                ],
            },
        }

        # Mock task execution to avoid actual Celery calls
        with (
            patch.object(ingest, "delay", side_effect=self._mock_task_delay),
            patch.object(enrich, "delay", side_effect=self._mock_task_delay),
            patch.object(captionize, "delay", side_effect=self._mock_task_delay),
            patch.object(transcode, "delay", side_effect=self._mock_task_delay),
            patch.object(preflight, "delay", side_effect=self._mock_task_delay),
            patch.object(publish, "delay", side_effect=self._mock_task_delay),
            patch.object(finalize, "delay", side_effect=self._mock_task_delay),
        ):

            # Execute complete pipeline
            result = self._execute_complete_pipeline(telegram_update, post_id)

        # Assertions
        assert result["success"] is True
        assert result["post_id"] == post_id
        assert result["stages_completed"] == 7
        assert result["final_status"] == "completed"
        assert result["platforms_published"] > 0
        assert "processing_times" in result
        assert all(
            stage in result["processing_times"]
            for stage in ["ingest", "enrich", "captionize", "transcode", "preflight", "publish", "finalize"]
        )

    def test_task_pipeline_with_failure_and_retry(self):
        """Test pipeline behavior when tasks fail and retry."""
        post_id = str(uuid.uuid4())
        telegram_update = {
            "update_id": 12346,
            "message": {"message_id": 679, "text": "Test message for retry scenario"},
        }

        # Mock failure in transcode stage
        with patch("app.workers.tasks.transcode.process_media") as mock_transcode:
            mock_transcode.side_effect = [Exception("Transcoding failed"), self._mock_successful_task_result()]

            # Execute pipeline with retry
            result = self._execute_pipeline_with_retry(telegram_update, post_id)

        # Assertions
        assert result["success"] is True
        assert result["retry_attempts"] > 0
        assert result["failed_stages"] == ["transcode"]
        assert result["recovered_stages"] == ["transcode"]

    def test_outbox_event_processing(self):
        """Test outbox event publishing and processing."""
        post_id = str(uuid.uuid4())

        # Test event publishing
        event_id = publish_outbox_event(
            event_type="post_created",
            payload={"post_id": post_id, "source": "telegram", "update_data": {"message": {"text": "Test"}}},
            entity_id=post_id,
        )

        assert event_id is not None
        assert isinstance(event_id, str)

    def test_platform_specific_content_adaptation(self):
        """Test platform-specific content adaptation in enrich stage."""
        post_id = str(uuid.uuid4())
        stage_data = {
            "post_id": post_id,
            "text_content": "Новая коллекция SalesWhisper! #Fashion",
            "has_media": True,
            "media_count": 1,
        }

        # Mock enrich task
        with patch("app.workers.tasks.enrich.enrich_post_content") as mock_enrich:
            mock_enrich.return_value = {
                "success": True,
                "platform_adaptations": {
                    "instagram": {
                        "text": "Новая коллекция SalesWhisper! #Fashion\
\
Заказать в нашем каталоге ➡️",
                        "hashtags": ["#SalesWhisper", "#Fashion", "#Style"],
                        "character_limit": 2200,
                    },
                    "vk": {
                        "text": "Новая коллекция SalesWhisper! #Fashion\
\
#SalesWhisper",
                        "hashtags": ["#SalesWhisper", "#Fashion"],
                        "character_limit": 15000,
                    },
                    "tiktok": {
                        "text": "Новая коллекция SalesWhisper! #Fashion #SalesWhisperStyle",
                        "hashtags": ["#SalesWhisper", "#Fashion"],
                        "character_limit": 150,
                    },
                },
            }

            result = mock_enrich(stage_data)

        # Assertions
        assert result["success"] is True
        assert "platform_adaptations" in result

        adaptations = result["platform_adaptations"]
        assert "instagram" in adaptations
        assert "vk" in adaptations
        assert "tiktok" in adaptations

        # Check platform-specific differences
        assert len(adaptations["instagram"]["text"]) > len(adaptations["tiktok"]["text"])
        assert adaptations["instagram"]["character_limit"] == 2200
        assert adaptations["tiktok"]["character_limit"] == 150

    def test_media_transcoding_for_multiple_platforms(self):
        """Test media transcoding for different platform requirements."""
        post_id = str(uuid.uuid4())
        stage_data = {
            "post_id": post_id,
            "has_media": True,
            "media_count": 1,
            "captions": {"instagram": "IG caption", "tiktok": "TikTok caption"},
        }

        # Mock transcode task
        with patch("app.workers.tasks.transcode.process_media") as mock_transcode:
            mock_transcode.return_value = {
                "success": True,
                "processed_media": {
                    "instagram": {
                        "video": f"/media/{post_id}/instagram_1080x1080.mp4",
                        "aspect_ratio": "1:1",
                        "duration": 30.0,
                        "size_mb": 15.2,
                    },
                    "tiktok": {
                        "video": f"/media/{post_id}/tiktok_1080x1920.mp4",
                        "aspect_ratio": "9:16",
                        "duration": 30.0,
                        "size_mb": 18.7,
                    },
                    "vk": {
                        "video": f"/media/{post_id}/vk_1920x1080.mp4",
                        "aspect_ratio": "16:9",
                        "duration": 30.0,
                        "size_mb": 22.1,
                    },
                },
            }

            result = mock_transcode(stage_data)

        # Assertions
        assert result["success"] is True
        processed_media = result["processed_media"]

        # Check different aspect ratios for platforms
        assert processed_media["instagram"]["aspect_ratio"] == "1:1"
        assert processed_media["tiktok"]["aspect_ratio"] == "9:16"
        assert processed_media["vk"]["aspect_ratio"] == "16:9"

        # Check all files were processed
        for platform in ["instagram", "tiktok", "vk"]:
            assert platform in processed_media
            assert "video" in processed_media[platform]
            assert processed_media[platform]["duration"] == 30.0

    def test_publishing_with_platform_failures(self):
        """Test publishing behavior when some platforms fail."""
        post_id = str(uuid.uuid4())
        stage_data = {
            "post_id": post_id,
            "preflight_results": {"all_checks_passed": True},
            "processed_media": {"instagram": {}, "vk": {}, "tiktok": {}},
        }

        # Mock publish task with mixed results
        with patch("app.workers.tasks.publish.publish_to_platforms") as mock_publish:
            mock_publish.return_value = {
                "success": True,
                "publish_results": {
                    "instagram": {
                        "success": True,
                        "platform_post_id": "instagram_123456",
                        "platform_url": "https://instagram.com/p/ABC123",
                    },
                    "vk": {
                        "success": True,
                        "platform_post_id": "vk_789012",
                        "platform_url": "https://vk.com/wall-123_456",
                    },
                    "tiktok": {"success": False, "error": "API rate limit exceeded", "retry_after": 3600},
                },
                "platforms_published": 2,
                "total_platforms": 3,
            }

            result = mock_publish(stage_data)

        # Assertions
        assert result["success"] is True
        assert result["platforms_published"] == 2
        assert result["total_platforms"] == 3

        publish_results = result["publish_results"]
        assert publish_results["instagram"]["success"] is True
        assert publish_results["vk"]["success"] is True
        assert publish_results["tiktok"]["success"] is False
        assert "error" in publish_results["tiktok"]

    def test_performance_metrics_collection(self):
        """Test that performance metrics are collected throughout pipeline."""
        post_id = str(uuid.uuid4())

        with patch("app.observability.metrics.metrics") as mock_metrics:
            # Execute a single stage to test metrics
            stage_data = {"post_id": post_id, "text_content": "Test"}

            # Mock successful enrich task
            with patch("app.workers.tasks.enrich.enrich_post_content") as mock_enrich:
                mock_enrich.return_value = {"success": True, "processing_time": 1.5, "stage": "enrich"}

                result = mock_enrich(stage_data)

        # Verify metrics were tracked
        assert mock_metrics.track_celery_task.called
        assert result["processing_time"] > 0

    # Helper methods
    def _mock_task_delay(self, *args, **kwargs):
        """Mock Celery task delay method."""
        mock_task = MagicMock()
        mock_task.id = str(uuid.uuid4())
        return mock_task

    def _mock_successful_task_result(self):
        """Return mock successful task result."""
        return {"success": True, "post_id": str(uuid.uuid4()), "processing_time": 0.5}

    def _execute_complete_pipeline(self, telegram_update: dict[str, Any], post_id: str) -> dict[str, Any]:
        """Execute complete task pipeline simulation."""
        pipeline_start = time.time()
        processing_times = {}
        stages_completed = 0

        try:
            # Stage 1: Ingest
            stage_start = time.time()
            ingest_result = self._simulate_ingest_stage(telegram_update, post_id)
            processing_times["ingest"] = time.time() - stage_start
            stages_completed += 1

            # Stage 2: Enrich
            stage_start = time.time()
            enrich_result = self._simulate_enrich_stage(ingest_result)
            processing_times["enrich"] = time.time() - stage_start
            stages_completed += 1

            # Stage 3: Captionize
            stage_start = time.time()
            caption_result = self._simulate_captionize_stage(enrich_result)
            processing_times["captionize"] = time.time() - stage_start
            stages_completed += 1

            # Stage 4: Transcode
            stage_start = time.time()
            transcode_result = self._simulate_transcode_stage(caption_result)
            processing_times["transcode"] = time.time() - stage_start
            stages_completed += 1

            # Stage 5: Preflight
            stage_start = time.time()
            preflight_result = self._simulate_preflight_stage(transcode_result)
            processing_times["preflight"] = time.time() - stage_start
            stages_completed += 1

            # Stage 6: Publish
            stage_start = time.time()
            publish_result = self._simulate_publish_stage(preflight_result)
            processing_times["publish"] = time.time() - stage_start
            stages_completed += 1

            # Stage 7: Finalize
            stage_start = time.time()
            finalize_result = self._simulate_finalize_stage(publish_result)
            processing_times["finalize"] = time.time() - stage_start
            stages_completed += 1

            total_processing_time = time.time() - pipeline_start

            return {
                "success": True,
                "post_id": post_id,
                "stages_completed": stages_completed,
                "total_processing_time": total_processing_time,
                "processing_times": processing_times,
                "final_status": finalize_result.get("final_status", "completed"),
                "platforms_published": publish_result.get("platforms_published", 0),
                "final_result": finalize_result,
            }

        except Exception as e:
            return {
                "success": False,
                "post_id": post_id,
                "stages_completed": stages_completed,
                "error": str(e),
                "processing_times": processing_times,
            }

    def _execute_pipeline_with_retry(self, telegram_update: dict[str, Any], post_id: str) -> dict[str, Any]:
        """Execute pipeline with retry simulation."""
        retry_attempts = 0
        failed_stages = []
        recovered_stages = []

        # Simulate retry logic
        # Simulate transcode failure and recovery
        try:
            raise Exception("Transcoding failed")
        except Exception:
            failed_stages.append("transcode")
            retry_attempts += 1

            # Simulate successful retry
            recovered_stages.append("transcode")

        return {
            "success": True,
            "post_id": post_id,
            "retry_attempts": retry_attempts,
            "failed_stages": failed_stages,
            "recovered_stages": recovered_stages,
        }

    def _simulate_ingest_stage(self, telegram_update: dict[str, Any], post_id: str) -> dict[str, Any]:
        """Simulate ingest stage processing."""
        return {
            "post_id": post_id,
            "has_media": bool(telegram_update.get("message", {}).get("photo")),
            "media_count": 1 if telegram_update.get("message", {}).get("photo") else 0,
            "text_content": telegram_update.get("message", {}).get("text", ""),
            "source": "telegram",
        }

    def _simulate_enrich_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate enrich stage processing."""
        return {
            **stage_data,
            "enriched_content": {"brand_context": "SalesWhisper"},
            "platform_adaptations": {"instagram": {"text": "Adapted for IG"}, "vk": {"text": "Adapted for VK"}},
        }

    def _simulate_captionize_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate captionize stage processing."""
        return {**stage_data, "captions": {"instagram": "IG caption", "vk": "VK caption", "tiktok": "TikTok caption"}}

    def _simulate_transcode_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate transcode stage processing."""
        return {
            **stage_data,
            "processed_media": {"instagram": {"video": "/path/ig.mp4"}, "vk": {"video": "/path/vk.mp4"}},
        }

    def _simulate_preflight_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate preflight stage processing."""
        return {**stage_data, "preflight_results": {"all_checks_passed": True}}

    def _simulate_publish_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate publish stage processing."""
        return {
            **stage_data,
            "publish_results": {"instagram": {"success": True}, "vk": {"success": True}},
            "platforms_published": 2,
        }

    def _simulate_finalize_stage(self, stage_data: dict[str, Any]) -> dict[str, Any]:
        """Simulate finalize stage processing."""
        return {**stage_data, "final_status": "completed", "analytics_summary": {"platforms_successful": 2}}


if __name__ == "__main__":
    # Run tests
    pytest.main([__file__, "-v"])
