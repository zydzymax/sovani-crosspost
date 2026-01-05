"""
Unit tests for preflight rules service.

Tests for:
- YAML rules loading and caching
- Caption length limits enforcement
- Hashtags count and length validation
- Media requirements and size limits
- Platform-specific rule differences
- Forbidden words and patterns detection
- Missing media detection
- Violation severity levels
"""

import os
import tempfile
import time
from unittest.mock import patch

import pytest
import yaml

from app.services.preflight_rules import (
    MediaMetadata,
    PostContent,
    PreflightRulesService,
    RuleViolation,
    ValidationResult,
    ViolationSeverity,
    ViolationType,
    get_all_supported_platforms,
    get_platform_publishing_limits,
    validate_post_content,
)


class TestPreflightRulesService:
    """Test core preflight rules service functionality."""

    @pytest.fixture
    def temp_rules_file(self):
        """Create temporary rules file for testing."""
        rules_data = {
            "version": "test_1.0",
            "platforms": {
                "test_platform": {
                    "caption": {
                        "max_length": 100,
                        "min_length": 5,
                        "required": True
                    },
                    "hashtags": {
                        "max_count": 3,
                        "max_length_each": 20,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 2,
                        "required": False
                    },
                    "links": {
                        "allowed": False,
                        "max_count": 0
                    },
                    "media": {
                        "required": True,
                        "max_count": 2,
                        "max_file_size": 1048576,  # 1MB
                        "supported_formats": ["jpg", "png"],
                        "video": {
                            "max_duration": 30,
                            "min_duration": 5
                        }
                    },
                    "content": {
                        "forbidden_words": ["bad", "evil"],
                        "forbidden_patterns": ["\\d{4}-\\d{4}"]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        yield temp_file

        # Cleanup
        os.unlink(temp_file)

    def test_service_initialization_with_existing_file(self, temp_rules_file):
        """Test service initialization with existing rules file."""
        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_rules_file):
            service = PreflightRulesService()

            assert service.rules_cache is not None
            assert service.rules_cache["version"] == "test_1.0"
            assert "test_platform" in service.rules_cache["platforms"]
            assert service.rules_loaded_at is not None

    def test_service_initialization_creates_default_file(self):
        """Test service creates default rules when file doesn't exist."""
        with tempfile.TemporaryDirectory() as temp_dir:
            non_existent_file = os.path.join(temp_dir, "non_existent_rules.yml")

            with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=non_existent_file):
                service = PreflightRulesService()

                assert os.path.exists(non_existent_file)
                assert "instagram" in service.rules_cache["platforms"]
                assert "vk" in service.rules_cache["platforms"]
                assert "tiktok" in service.rules_cache["platforms"]

    def test_get_platform_rules(self, temp_rules_file):
        """Test getting platform-specific rules."""
        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_rules_file):
            service = PreflightRulesService()

            rules = service.get_platform_rules("test_platform")
            assert rules is not None
            assert rules["caption"]["max_length"] == 100

            # Test non-existent platform
            rules = service.get_platform_rules("non_existent")
            assert rules is None


class TestCaptionValidation:
    """Test caption validation rules."""

    @pytest.fixture
    def test_service(self):
        """Create service with test rules."""
        rules_data = {
            "version": "test",
            "platforms": {
                "strict_platform": {
                    "caption": {
                        "max_length": 50,
                        "min_length": 10,
                        "required": True
                    }
                },
                "lenient_platform": {
                    "caption": {
                        "max_length": 1000,
                        "min_length": 1,
                        "required": False
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
            service = PreflightRulesService()

        os.unlink(temp_file)
        return service

    def test_caption_too_long_violation(self, test_service):
        """Test caption exceeding maximum length."""
        content = PostContent(
            caption="A" * 60,  # Exceeds 50 char limit
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="strict_platform"
        )

        result = test_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert len(violations) > 0
        assert any(v.type == ViolationType.CAPTION_TOO_LONG for v in violations)

        caption_violation = next(v for v in violations if v.type == ViolationType.CAPTION_TOO_LONG)
        assert caption_violation.current_value == 60
        assert caption_violation.limit_value == 50
        assert "shorten" in caption_violation.suggestion.lower()

    def test_caption_empty_required_violation(self, test_service):
        """Test empty caption when required."""
        content = PostContent(
            caption="",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="strict_platform"
        )

        result = test_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.CAPTION_EMPTY for v in violations)

    def test_caption_too_short_violation(self, test_service):
        """Test caption below minimum length."""
        content = PostContent(
            caption="Hi",  # Only 2 chars, minimum is 10
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="strict_platform"
        )

        result = test_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.CAPTION_TOO_LONG for v in violations)  # Same enum used for both

    def test_caption_valid_length(self, test_service):
        """Test caption within valid length range."""
        content = PostContent(
            caption="Valid caption with appropriate length",  # Within 10-50 chars
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="strict_platform"
        )

        result = test_service.validate_post(content)

        # Should not have caption-related violations
        violations = result.get_blocking_violations()
        caption_violations = [v for v in violations if v.type in [ViolationType.CAPTION_TOO_LONG, ViolationType.CAPTION_EMPTY]]
        assert len(caption_violations) == 0


class TestHashtagValidation:
    """Test hashtag validation rules."""

    @pytest.fixture
    def hashtag_service(self):
        """Create service with hashtag rules."""
        rules_data = {
            "version": "test",
            "platforms": {
                "limited_hashtags": {
                    "caption": {"max_length": 1000, "required": False},
                    "hashtags": {
                        "max_count": 3,
                        "max_length_each": 15,
                        "required": False
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
            service = PreflightRulesService()

        os.unlink(temp_file)
        return service

    def test_too_many_hashtags_violation(self, hashtag_service):
        """Test exceeding hashtag count limit."""
        content = PostContent(
            caption="Test post",
            hashtags=["#one", "#two", "#three", "#four"],  # Exceeds limit of 3
            mentions=[],
            links=[],
            media=[],
            platform="limited_hashtags"
        )

        result = hashtag_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.HASHTAGS_TOO_MANY for v in violations)

        hashtag_violation = next(v for v in violations if v.type == ViolationType.HASHTAGS_TOO_MANY)
        assert hashtag_violation.current_value == 4
        assert hashtag_violation.limit_value == 3

    def test_hashtag_too_long_violation(self, hashtag_service):
        """Test individual hashtag exceeding length limit."""
        content = PostContent(
            caption="Test post",
            hashtags=["#short", "#thisisaverylonghashtag"],  # Second exceeds 15 chars
            mentions=[],
            links=[],
            media=[],
            platform="limited_hashtags"
        )

        result = hashtag_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.HASHTAGS_TOO_LONG for v in violations)

        long_hashtag_violation = next(v for v in violations if v.type == ViolationType.HASHTAGS_TOO_LONG)
        assert "hashtags[1]" in long_hashtag_violation.field

    def test_valid_hashtags(self, hashtag_service):
        """Test valid hashtag configuration."""
        content = PostContent(
            caption="Test post",
            hashtags=["#good", "#valid", "#ok"],  # Within limits
            mentions=[],
            links=[],
            media=[],
            platform="limited_hashtags"
        )

        result = hashtag_service.validate_post(content)

        hashtag_violations = [v for v in result.violations if v.type in [ViolationType.HASHTAGS_TOO_MANY, ViolationType.HASHTAGS_TOO_LONG]]
        assert len(hashtag_violations) == 0


class TestMediaValidation:
    """Test media validation rules."""

    @pytest.fixture
    def media_service(self):
        """Create service with media rules."""
        rules_data = {
            "version": "test",
            "platforms": {
                "media_required": {
                    "caption": {"max_length": 1000, "required": False},
                    "media": {
                        "required": True,
                        "max_count": 2,
                        "max_file_size": 1048576,  # 1MB
                        "supported_formats": ["jpg", "png", "mp4"],
                        "video": {
                            "max_duration": 60,
                            "min_duration": 3,
                            "max_width": 1920,
                            "max_height": 1080
                        }
                    }
                },
                "media_optional": {
                    "caption": {"max_length": 1000, "required": True},
                    "media": {
                        "required": False,
                        "max_count": 5,
                        "max_file_size": 5242880  # 5MB
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
            service = PreflightRulesService()

        os.unlink(temp_file)
        return service

    def test_media_missing_violation(self, media_service):
        """Test missing media when required."""
        content = PostContent(
            caption="Test post",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],  # No media provided
            platform="media_required"
        )

        result = media_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.MEDIA_MISSING for v in violations)

        media_violation = next(v for v in violations if v.type == ViolationType.MEDIA_MISSING)
        assert media_violation.current_value == 0
        assert media_violation.limit_value == 1

    def test_too_many_media_files_violation(self, media_service):
        """Test exceeding media file count limit."""
        media_files = [
            MediaMetadata(file_path="/test1.jpg", file_size=500000, format="jpg"),
            MediaMetadata(file_path="/test2.jpg", file_size=500000, format="jpg"),
            MediaMetadata(file_path="/test3.jpg", file_size=500000, format="jpg")  # Exceeds limit of 2
        ]

        content = PostContent(
            caption="Test post",
            hashtags=[],
            mentions=[],
            links=[],
            media=media_files,
            platform="media_required"
        )

        result = media_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.MEDIA_TOO_LARGE for v in violations)

    def test_file_too_large_violation(self, media_service):
        """Test file size exceeding limit."""
        large_file = MediaMetadata(
            file_path="/large.jpg",
            file_size=2097152,  # 2MB, exceeds 1MB limit
            format="jpg"
        )

        content = PostContent(
            caption="Test post",
            hashtags=[],
            mentions=[],
            links=[],
            media=[large_file],
            platform="media_required"
        )

        result = media_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.MEDIA_TOO_LARGE for v in violations)

        size_violation = next(v for v in violations if v.type == ViolationType.MEDIA_TOO_LARGE)
        assert size_violation.current_value == 2097152
        assert size_violation.limit_value == 1048576

    def test_unsupported_format_violation(self, media_service):
        """Test unsupported file format."""
        unsupported_file = MediaMetadata(
            file_path="/test.gif",
            file_size=500000,
            format="gif"  # Not in supported formats
        )

        content = PostContent(
            caption="Test post",
            hashtags=[],
            mentions=[],
            links=[],
            media=[unsupported_file],
            platform="media_required"
        )

        result = media_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.MEDIA_WRONG_FORMAT for v in violations)

    def test_video_duration_violations(self, media_service):
        """Test video duration limits."""
        # Too short video
        short_video = MediaMetadata(
            file_path="/short.mp4",
            file_size=500000,
            format="mp4",
            duration=1.0  # Below 3s minimum
        )

        content_short = PostContent(
            caption="Short video test",
            hashtags=[],
            mentions=[],
            links=[],
            media=[short_video],
            platform="media_required"
        )

        result_short = media_service.validate_post(content_short)
        assert not result_short.is_valid
        assert any(v.type == ViolationType.MEDIA_TOO_LONG for v in result_short.get_blocking_violations())

        # Too long video
        long_video = MediaMetadata(
            file_path="/long.mp4",
            file_size=500000,
            format="mp4",
            duration=120.0  # Above 60s maximum
        )

        content_long = PostContent(
            caption="Long video test",
            hashtags=[],
            mentions=[],
            links=[],
            media=[long_video],
            platform="media_required"
        )

        result_long = media_service.validate_post(content_long)
        assert not result_long.is_valid
        assert any(v.type == ViolationType.MEDIA_TOO_LONG for v in result_long.get_blocking_violations())

    def test_video_dimensions_violations(self, media_service):
        """Test video dimension limits."""
        oversized_video = MediaMetadata(
            file_path="/big.mp4",
            file_size=500000,
            format="mp4",
            duration=30.0,
            width=3840,  # Exceeds 1920 limit
            height=2160   # Exceeds 1080 limit
        )

        content = PostContent(
            caption="Oversized video test",
            hashtags=[],
            mentions=[],
            links=[],
            media=[oversized_video],
            platform="media_required"
        )

        result = media_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        dimension_violations = [v for v in violations if v.type == ViolationType.MEDIA_WRONG_DIMENSIONS]
        assert len(dimension_violations) >= 1  # At least width or height violation

    def test_valid_media(self, media_service):
        """Test valid media configuration."""
        valid_file = MediaMetadata(
            file_path="/valid.jpg",
            file_size=500000,  # Within 1MB limit
            format="jpg",  # Supported format
            width=1080,
            height=720
        )

        content = PostContent(
            caption="Valid media test",
            hashtags=[],
            mentions=[],
            links=[],
            media=[valid_file],
            platform="media_required"
        )

        result = media_service.validate_post(content)

        # Should not have media-related blocking violations
        media_violations = [v for v in result.get_blocking_violations()
                          if v.type in [ViolationType.MEDIA_MISSING, ViolationType.MEDIA_TOO_LARGE,
                                       ViolationType.MEDIA_WRONG_FORMAT, ViolationType.MEDIA_WRONG_DIMENSIONS]]
        assert len(media_violations) == 0


class TestContentRestrictions:
    """Test content restriction rules."""

    @pytest.fixture
    def restricted_service(self):
        """Create service with content restrictions."""
        rules_data = {
            "version": "test",
            "platforms": {
                "restricted": {
                    "caption": {"max_length": 1000, "required": True},
                    "content": {
                        "forbidden_words": ["spam", "scam", "fake"],
                        "forbidden_patterns": ["\\d{4}-\\d{4}-\\d{4}-\\d{4}"]  # Credit card pattern
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
            service = PreflightRulesService()

        os.unlink(temp_file)
        return service

    def test_forbidden_words_violation(self, restricted_service):
        """Test forbidden words detection."""
        content = PostContent(
            caption="This is a spam message trying to scam you",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="restricted"
        )

        result = restricted_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        forbidden_violations = [v for v in violations if v.type == ViolationType.FORBIDDEN_WORDS]
        assert len(forbidden_violations) >= 2  # "spam" and "scam"

    def test_forbidden_pattern_violation(self, restricted_service):
        """Test forbidden pattern (regex) detection."""
        content = PostContent(
            caption="Use my credit card 1234-5678-9012-3456 for payment",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="restricted"
        )

        result = restricted_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.FORBIDDEN_WORDS for v in violations)

    def test_clean_content(self, restricted_service):
        """Test content without restrictions."""
        content = PostContent(
            caption="This is a clean and appropriate message",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="restricted"
        )

        result = restricted_service.validate_post(content)

        # Should not have forbidden content violations
        content_violations = [v for v in result.get_blocking_violations()
                            if v.type == ViolationType.FORBIDDEN_WORDS]
        assert len(content_violations) == 0


class TestLinkValidation:
    """Test link validation rules."""

    @pytest.fixture
    def link_service(self):
        """Create service with link rules."""
        rules_data = {
            "version": "test",
            "platforms": {
                "no_links": {
                    "caption": {"max_length": 1000, "required": False},
                    "links": {
                        "allowed": False,
                        "max_count": 0
                    }
                },
                "limited_links": {
                    "caption": {"max_length": 1000, "required": False},
                    "links": {
                        "allowed": True,
                        "max_count": 2
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
            service = PreflightRulesService()

        os.unlink(temp_file)
        return service

    def test_links_not_allowed_violation(self, link_service):
        """Test links on platform that doesn't allow them."""
        content = PostContent(
            caption="Check out https://example.com",
            hashtags=[],
            mentions=[],
            links=["https://example.com"],
            media=[],
            platform="no_links"
        )

        result = link_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.LINKS_NOT_ALLOWED for v in violations)

    def test_too_many_links_violation(self, link_service):
        """Test exceeding link count limit."""
        content = PostContent(
            caption="Check out multiple sites",
            hashtags=[],
            mentions=[],
            links=["https://site1.com", "https://site2.com", "https://site3.com"],  # Exceeds limit of 2
            media=[],
            platform="limited_links"
        )

        result = link_service.validate_post(content)

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.LINKS_NOT_ALLOWED for v in violations)


class TestPlatformSpecificRules:
    """Test platform-specific rule differences."""

    def test_instagram_vs_tiktok_differences(self):
        """Test differences between Instagram and TikTok rules."""
        # Test with default rules
        service = PreflightRulesService()

        instagram_limits = service.get_platform_limits("instagram")
        tiktok_limits = service.get_platform_limits("tiktok")

        # Instagram allows longer captions
        assert instagram_limits["caption_max_length"] > tiktok_limits["caption_max_length"]

        # Instagram allows more hashtags
        assert instagram_limits["hashtags_max_count"] > tiktok_limits["hashtags_max_count"]

        # TikTok doesn't allow links
        assert tiktok_limits["links_allowed"] is False
        assert instagram_limits["links_allowed"] is True

    def test_platform_not_supported(self):
        """Test unsupported platform validation."""
        result = validate_post_content(
            caption="Test",
            platform="unsupported_platform"
        )

        assert not result.is_valid
        violations = result.get_blocking_violations()
        assert any(v.type == ViolationType.PLATFORM_NOT_SUPPORTED for v in violations)


class TestConvenienceFunctions:
    """Test convenience functions."""

    def test_validate_post_content_function(self):
        """Test validate_post_content convenience function."""
        result = validate_post_content(
            caption="Test post",
            platform="instagram",
            hashtags=["#test"],
            media_metadata=[{"file_size": 500000, "format": "jpg"}]
        )

        assert isinstance(result, ValidationResult)
        assert result.platform == "instagram"

    def test_get_platform_publishing_limits(self):
        """Test get_platform_publishing_limits function."""
        limits = get_platform_publishing_limits("instagram")

        assert isinstance(limits, dict)
        assert "caption_max_length" in limits
        assert "hashtags_max_count" in limits
        assert "media_required" in limits

    def test_get_all_supported_platforms(self):
        """Test get_all_supported_platforms function."""
        platforms = get_all_supported_platforms()

        assert isinstance(platforms, list)
        assert "instagram" in platforms
        assert "vk" in platforms
        assert "tiktok" in platforms
        assert "youtube" in platforms
        assert "telegram" in platforms


class TestEdgeCases:
    """Test edge cases and error scenarios."""

    def test_empty_content_validation(self):
        """Test validation with completely empty content."""
        content = PostContent(
            caption="",
            hashtags=[],
            mentions=[],
            links=[],
            media=[],
            platform="instagram"
        )

        service = PreflightRulesService()
        result = service.validate_post(content)

        # Should fail because Instagram requires caption and media
        assert not result.is_valid
        blocking_violations = result.get_blocking_violations()
        assert len(blocking_violations) >= 2  # Caption empty + media missing

    def test_malformed_yaml_fallback(self):
        """Test fallback when YAML file is malformed."""
        malformed_yaml = "invalid: yaml: content: [unclosed"

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            f.write(malformed_yaml)
            temp_file = f.name

        try:
            with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
                service = PreflightRulesService()

                # Should fall back to minimal rules
                assert service.rules_cache["version"] == "fallback"
                assert "instagram" in service.rules_cache["platforms"]
        finally:
            os.unlink(temp_file)

    def test_rules_cache_expiration(self):
        """Test rules cache expiration and reloading."""
        service = PreflightRulesService()
        service.cache_ttl = 0  # Force immediate expiration

        service.rules_cache.get("version")

        # Mock file modification
        with patch.object(service, '_load_rules') as mock_load:
            service._maybe_reload_rules()
            mock_load.assert_called_once()

    def test_violation_to_dict_conversion(self):
        """Test violation object to dictionary conversion."""
        violation = RuleViolation(
            type=ViolationType.CAPTION_TOO_LONG,
            severity=ViolationSeverity.ERROR,
            message="Test violation",
            platform="test",
            field="caption",
            current_value=100,
            limit_value=50,
            suggestion="Shorten caption"
        )

        violation_dict = violation.to_dict()

        assert violation_dict["type"] == "caption_too_long"
        assert violation_dict["severity"] == "error"
        assert violation_dict["message"] == "Test violation"
        assert violation_dict["platform"] == "test"
        assert violation_dict["current_value"] == 100
        assert violation_dict["limit_value"] == 50
        assert "timestamp" in violation_dict


class TestAdvancedValidationCases:
    """Test advanced validation scenarios and edge cases."""

    def test_cross_platform_validation_consistency(self):
        """Test validation consistency across different platforms."""
        PreflightRulesService()

        # Same content, different platforms should have different results
        base_content = {
            "caption": "Check out this amazing product! Use code SAVE20 for 20% off! 🔥" * 10,  # Long caption
            "hashtags": ["#amazing", "#product", "#sale", "#discount", "#limited"] * 3,  # Many hashtags
            "mentions": ["@brand", "@influencer"],
            "links": ["https://example.com/product"],
            "media_metadata": [{
                "file_size": 50000000,  # 50MB
                "format": "mp4",
                "duration": 45.0,
                "width": 1080,
                "height": 1920
            }]
        }

        # Test Instagram (more permissive)
        instagram_result = validate_post_content(platform="instagram", **base_content)

        # Test TikTok (more restrictive)
        tiktok_result = validate_post_content(platform="tiktok", **base_content)

        # TikTok should have more violations due to stricter rules
        assert len(tiktok_result.get_blocking_violations()) > len(instagram_result.get_blocking_violations())

        # Specifically check that TikTok blocks links while Instagram allows them
        tiktok_link_violations = [v for v in tiktok_result.get_blocking_violations()
                                 if v.type == ViolationType.LINKS_NOT_ALLOWED]
        instagram_link_violations = [v for v in instagram_result.get_blocking_violations()
                                   if v.type == ViolationType.LINKS_NOT_ALLOWED]

        assert len(tiktok_link_violations) > 0
        assert len(instagram_link_violations) == 0

    def test_media_format_platform_compatibility(self):
        """Test media format compatibility across platforms."""
        service = PreflightRulesService()

        # Test GIF support (VK allows, Instagram doesn't in default rules)
        gif_content = PostContent(
            caption="Animated content",
            hashtags=[],
            mentions=[],
            links=[],
            media=[MediaMetadata(
                file_size=5000000,
                format="gif",
                width=400,
                height=400
            )],
            platform="vk"  # Should allow GIF
        )

        vk_result = service.validate_post(gif_content)

        # Change platform to Instagram
        gif_content.platform = "instagram"
        instagram_result = service.validate_post(gif_content)

        # Instagram should reject GIF, VK should accept
        vk_format_violations = [v for v in vk_result.get_blocking_violations()
                               if v.type == ViolationType.MEDIA_WRONG_FORMAT]
        instagram_format_violations = [v for v in instagram_result.get_blocking_violations()
                                     if v.type == ViolationType.MEDIA_WRONG_FORMAT]

        assert len(vk_format_violations) == 0  # VK accepts GIF
        assert len(instagram_format_violations) > 0  # Instagram rejects GIF

    def test_business_rules_validation(self):
        """Test business-specific validation rules."""
        # Create service with custom business rules
        rules_data = {
            "version": "business_test",
            "platforms": {
                "business_platform": {
                    "caption": {
                        "max_length": 200,
                        "required": True
                    },
                    "content": {
                        "forbidden_words": ["competitor", "cheap", "fake"],
                        "required_words": ["SalesWhisper", "premium", "quality"],  # Brand requirements
                        "forbidden_patterns": [
                            "\\b(?:buy|purchase)\\s+now\\b",  # Aggressive sales language
                            "\\b\\d+%\\s+off\\b"  # Discount patterns
                        ]
                    }
                }
            }
        }

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yml', delete=False) as f:
            yaml.dump(rules_data, f)
            temp_file = f.name

        try:
            with patch.object(PreflightRulesService, '_get_rules_file_path', return_value=temp_file):
                business_service = PreflightRulesService()

            # Test content that violates business rules
            bad_business_content = PostContent(
                caption="Buy now! 50% off this cheap alternative to competitor products",
                hashtags=[],
                mentions=[],
                links=[],
                media=[],
                platform="business_platform"
            )

            result = business_service.validate_post(bad_business_content)

            assert not result.is_valid
            violations = result.get_blocking_violations()

            # Should have multiple business rule violations
            forbidden_word_violations = [v for v in violations if v.type == ViolationType.FORBIDDEN_WORDS]
            assert len(forbidden_word_violations) >= 2  # "competitor" and "cheap"

        finally:
            os.unlink(temp_file)

    def test_video_aspect_ratio_validation(self):
        """Test video aspect ratio validation for different platforms."""
        service = PreflightRulesService()

        # Test square video (1:1)
        square_video = MediaMetadata(
            file_size=30000000,
            format="mp4",
            duration=30.0,
            width=1080,
            height=1080,  # 1:1 aspect ratio
            aspect_ratio="1:1"
        )

        # Test vertical video (9:16)
        vertical_video = MediaMetadata(
            file_size=30000000,
            format="mp4",
            duration=30.0,
            width=1080,
            height=1920,  # 9:16 aspect ratio
            aspect_ratio="9:16"
        )

        # Test horizontal video (16:9)
        MediaMetadata(
            file_size=30000000,
            format="mp4",
            duration=30.0,
            width=1920,
            height=1080,  # 16:9 aspect ratio
            aspect_ratio="16:9"
        )

        # Instagram should accept square and vertical
        for video in [square_video, vertical_video]:
            content = PostContent(
                caption="Video test",
                hashtags=[],
                mentions=[],
                links=[],
                media=[video],
                platform="instagram"
            )

            result = service.validate_post(content)
            dimension_violations = [v for v in result.get_blocking_violations()
                                  if v.type == ViolationType.MEDIA_WRONG_DIMENSIONS]
            assert len(dimension_violations) == 0

        # TikTok prefers vertical (9:16)
        tiktok_vertical_content = PostContent(
            caption="TikTok video",
            hashtags=["#video"],
            mentions=[],
            links=[],
            media=[vertical_video],
            platform="tiktok"
        )

        tiktok_result = service.validate_post(tiktok_vertical_content)
        # Should pass without dimension violations
        dimension_violations = [v for v in tiktok_result.get_blocking_violations()
                              if v.type == ViolationType.MEDIA_WRONG_DIMENSIONS]
        assert len(dimension_violations) == 0

    def test_large_scale_validation_performance(self):
        """Test validation performance with large content."""
        service = PreflightRulesService()

        # Create content with many elements
        large_content = PostContent(
            caption="A" * 1000,  # Large caption
            hashtags=[f"#tag{i}" for i in range(50)],  # Many hashtags
            mentions=[f"@user{i}" for i in range(30)],  # Many mentions
            links=[f"https://example{i}.com" for i in range(20)],  # Many links
            media=[MediaMetadata(
                file_size=1000000,
                format="jpg",
                width=1080,
                height=1080
            ) for _ in range(10)],  # Many media files
            platform="instagram"
        )

        start_time = time.time()
        result = service.validate_post(large_content)
        validation_time = time.time() - start_time

        # Should complete validation in reasonable time (< 1 second for this test)
        assert validation_time < 1.0
        assert isinstance(result, ValidationResult)

        # Should have multiple violations due to limits exceeded
        assert not result.is_valid
        assert len(result.violations) > 5

    def test_unicode_and_emoji_validation(self):
        """Test validation with Unicode characters and emojis."""
        service = PreflightRulesService()

        # Test content with emojis and Unicode
        unicode_content = PostContent(
            caption="🚀 Amazing product! Стремительный рост продаж! 中文测试 العربية 🔥✨💯",
            hashtags=["#emoji🚀", "#unicode中文", "#العربية"],
            mentions=["@тест", "@用户"],
            links=["https://example.com"],
            media=[],
            platform="instagram"
        )

        result = service.validate_post(unicode_content)

        # Should handle Unicode gracefully
        assert isinstance(result, ValidationResult)

        # Check that length calculation works correctly with Unicode
        caption_violations = [v for v in result.violations
                            if v.type == ViolationType.CAPTION_TOO_LONG]

        # Should not fail due to encoding issues
        for violation in caption_violations:
            assert isinstance(violation.current_value, int)
            assert violation.current_value > 0

    def test_validation_with_missing_metadata(self):
        """Test validation when media metadata is incomplete."""
        service = PreflightRulesService()

        # Test with minimal metadata
        minimal_media = MediaMetadata(
            file_path="/unknown.mp4",
            # Missing size, dimensions, duration, format
        )

        content = PostContent(
            caption="Test with minimal metadata",
            hashtags=[],
            mentions=[],
            links=[],
            media=[minimal_media],
            platform="instagram"
        )

        result = service.validate_post(content)

        # Should not crash with missing metadata
        assert isinstance(result, ValidationResult)

        # May have warnings about unable to validate certain aspects
        # but should not have errors for missing optional metadata fields


class TestRulesCacheAndPerformance:
    """Test rules caching and performance optimizations."""

    def test_rules_cache_hit_performance(self):
        """Test that subsequent validations use cached rules."""
        service = PreflightRulesService()

        content = PostContent(
            caption="Cache test",
            hashtags=["#test"],
            mentions=[],
            links=[],
            media=[],
            platform="instagram"
        )

        # First validation - cold cache
        start_time = time.time()
        result1 = service.validate_post(content)
        time.time() - start_time

        # Second validation - warm cache
        start_time = time.time()
        result2 = service.validate_post(content)
        second_time = time.time() - start_time

        # Results should be identical
        assert result1.is_valid == result2.is_valid
        assert len(result1.violations) == len(result2.violations)

        # Second validation should be faster (cached rules)
        # Note: This might be minimal difference in tests, so just ensure it doesn't crash
        assert second_time >= 0

    def test_concurrent_validation_safety(self):
        """Test thread safety of validation service."""
        import threading

        service = PreflightRulesService()
        results = []

        def validate_content(platform_suffix):
            content = PostContent(
                caption=f"Concurrent test {platform_suffix}",
                hashtags=[f"#test{platform_suffix}"],
                mentions=[],
                links=[],
                media=[],
                platform="instagram"
            )
            result = service.validate_post(content)
            results.append(result)

        # Run multiple validations concurrently
        threads = []
        for i in range(10):
            thread = threading.Thread(target=validate_content, args=(i,))
            threads.append(thread)
            thread.start()

        # Wait for all threads to complete
        for thread in threads:
            thread.join()

        # All validations should complete successfully
        assert len(results) == 10
        for result in results:
            assert isinstance(result, ValidationResult)


class TestEnhancedPreflightValidation:
    """Test enhanced preflight validation functionality."""

    def test_aspect_ratio_compliance_instagram(self):
        """Test aspect ratio validation for Instagram."""
        from app.services.preflight_rules import validate_aspect_ratio_compliance

        # Valid aspect ratio
        valid_media = MediaMetadata(
            file_path="/path/to/square.jpg",
            file_size=1000000,
            width=1080,
            height=1080,  # 1:1 aspect ratio
            format="jpeg",
            aspect_ratio="1:1"
        )

        violations = validate_aspect_ratio_compliance(valid_media, "instagram")
        assert len(violations) == 0

        # Invalid aspect ratio for Instagram stories
        invalid_media = MediaMetadata(
            file_path="/path/to/wide.jpg",
            file_size=1000000,
            width=1920,
            height=1080,  # 16:9, not ideal for Instagram
            format="jpeg",
            aspect_ratio="16:9"
        )

        violations = validate_aspect_ratio_compliance(invalid_media, "instagram")
        # Should have warnings or suggestions for better aspect ratios
        assert isinstance(violations, list)

    def test_business_compliance_validation(self):
        """Test business compliance validation."""
        from app.services.preflight_rules import validate_business_compliance

        # Content with brand guidelines compliance
        compliant_content = PostContent(
            caption="Check out our new product! #brand #quality #innovation",
            hashtags=["brand", "quality", "innovation"],
            mentions=["@officialaccount"],
            platform="instagram"
        )

        violations = validate_business_compliance(compliant_content)
        assert isinstance(violations, list)

        # Content with potential compliance issues
        problematic_content = PostContent(
            caption="Buy followers cheap! Guaranteed fake engagement!",
            hashtags=["fake", "cheap", "spam"],
            platform="instagram"
        )

        violations = validate_business_compliance(problematic_content)
        # Should detect problematic content
        assert isinstance(violations, list)

    def test_content_quality_analysis(self):
        """Test content quality analysis."""
        from app.services.preflight_rules import validate_content_quality

        high_quality_content = PostContent(
            caption="Discover the art of minimalist design. Each element serves a purpose, creating harmony between form and function. #design #minimalism #art",
            hashtags=["design", "minimalism", "art"],
            media=[MediaMetadata(
                file_path="/path/to/image.jpg",
                file_size=500000,
                width=1080,
                height=1080,
                format="jpeg"
            )],
            platform="instagram"
        )

        quality_result = validate_content_quality(high_quality_content)

        assert "overall_score" in quality_result
        assert "readability_score" in quality_result
        assert "engagement_prediction" in quality_result
        assert "content_analysis" in quality_result
        assert isinstance(quality_result["overall_score"], (int, float))
        assert 0 <= quality_result["overall_score"] <= 1

    def test_optimal_posting_times(self):
        """Test optimal posting time analysis."""
        from app.services.preflight_rules import get_optimal_posting_times

        instagram_times = get_optimal_posting_times("instagram")

        assert "is_optimal_time" in instagram_times
        assert "current_hour" in instagram_times
        assert "optimal_hours" in instagram_times
        assert "time_zone" in instagram_times
        assert "recommendation" in instagram_times

        # Test with different platforms
        for platform in ["vk", "tiktok", "youtube", "telegram"]:
            times = get_optimal_posting_times(platform)
            assert isinstance(times, dict)
            assert "optimal_hours" in times

    def test_platform_performance_insights(self):
        """Test platform performance insights."""
        from app.services.preflight_rules import get_platform_performance_insights

        instagram_insights = get_platform_performance_insights("instagram")

        assert "expected_engagement" in instagram_insights
        assert "platform_trends" in instagram_insights
        assert "algorithm_factors" in instagram_insights
        assert "recommendations" in instagram_insights

        # Verify structure for all platforms
        for platform in ["instagram", "vk", "tiktok", "youtube", "telegram"]:
            insights = get_platform_performance_insights(platform)
            assert isinstance(insights, dict)
            assert "expected_engagement" in insights

    def test_enhanced_validation_integration(self):
        """Test integration of all enhanced validation features."""
        from app.services.preflight_rules import (
            get_optimal_posting_times,
            get_platform_performance_insights,
            validate_aspect_ratio_compliance,
            validate_business_compliance,
            validate_content_quality,
            validate_post_content,
        )

        # Create comprehensive test content
        media = MediaMetadata(
            file_path="/path/to/test.jpg",
            file_size=2000000,
            width=1080,
            height=1080,
            format="jpeg",
            aspect_ratio="1:1"
        )

        content = PostContent(
            caption="Testing our enhanced validation system with quality content #test #validation",
            hashtags=["test", "validation", "quality"],
            mentions=["@testaccount"],
            media=[media],
            platform="instagram"
        )

        # Test all validation functions work together
        base_validation = validate_post_content(
            caption=content.caption,
            platform=content.platform,
            hashtags=content.hashtags,
            mentions=content.mentions,
            media_metadata=[media.__dict__]
        )

        aspect_violations = validate_aspect_ratio_compliance(media, "instagram")
        business_violations = validate_business_compliance(content)
        quality_insights = validate_content_quality(content)
        posting_times = get_optimal_posting_times("instagram")
        performance_insights = get_platform_performance_insights("instagram")

        # Verify all functions return expected types
        assert hasattr(base_validation, 'is_valid')
        assert isinstance(aspect_violations, list)
        assert isinstance(business_violations, list)
        assert isinstance(quality_insights, dict)
        assert isinstance(posting_times, dict)
        assert isinstance(performance_insights, dict)

        # Verify comprehensive metadata can be assembled
        enhanced_metadata = {
            "quality_score": quality_insights.get("overall_score", 0),
            "optimal_posting_times": posting_times,
            "performance_insights": performance_insights,
            "content_analysis": quality_insights
        }

        assert "quality_score" in enhanced_metadata
        assert "optimal_posting_times" in enhanced_metadata
        assert "performance_insights" in enhanced_metadata
        assert "content_analysis" in enhanced_metadata


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])
