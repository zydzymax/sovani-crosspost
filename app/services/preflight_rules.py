"""
Preflight Rules Service for SalesWhisper Crosspost.

This module loads and validates publishing rules for different platforms,
ensuring content meets platform requirements before publishing.
"""

import yaml
import os
import re
from pathlib import Path
from typing import Dict, Any, List, Optional, Union
from dataclasses import dataclass, asdict
from enum import Enum
import time
from datetime import datetime, timezone

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics


logger = get_logger("services.preflight_rules")


class ViolationType(Enum):
    """Types of preflight rule violations."""
    CAPTION_TOO_LONG = "caption_too_long"
    CAPTION_EMPTY = "caption_empty"
    HASHTAGS_TOO_MANY = "hashtags_too_many"
    HASHTAGS_TOO_LONG = "hashtags_too_long"
    MEDIA_MISSING = "media_missing"
    MEDIA_TOO_LARGE = "media_too_large"
    MEDIA_WRONG_FORMAT = "media_wrong_format"
    MEDIA_WRONG_DIMENSIONS = "media_wrong_dimensions"
    MEDIA_TOO_LONG = "media_too_long"
    FORBIDDEN_WORDS = "forbidden_words"
    LINKS_NOT_ALLOWED = "links_not_allowed"
    MENTIONS_TOO_MANY = "mentions_too_many"
    PLATFORM_NOT_SUPPORTED = "platform_not_supported"


class ViolationSeverity(Enum):
    """Severity levels for violations."""
    ERROR = "error"      # Blocks publishing
    WARNING = "warning"  # Allows publishing but logs warning
    INFO = "info"       # Informational only


@dataclass
class RuleViolation:
    """Represents a preflight rule violation."""
    type: ViolationType
    severity: ViolationSeverity
    message: str
    platform: str
    field: Optional[str] = None
    current_value: Optional[Union[str, int, float]] = None
    limit_value: Optional[Union[str, int, float]] = None
    suggestion: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert violation to dictionary."""
        return {
            "type": self.type.value,
            "severity": self.severity.value,
            "message": self.message,
            "platform": self.platform,
            "field": self.field,
            "current_value": self.current_value,
            "limit_value": self.limit_value,
            "suggestion": self.suggestion,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }


@dataclass
class MediaMetadata:
    """Media metadata for validation."""
    file_path: Optional[str] = None
    file_size: Optional[int] = None  # bytes
    width: Optional[int] = None
    height: Optional[int] = None
    duration: Optional[float] = None  # seconds
    format: Optional[str] = None
    mime_type: Optional[str] = None
    aspect_ratio: Optional[str] = None


@dataclass
class PostContent:
    """Post content for validation."""
    caption: str
    hashtags: List[str]
    mentions: List[str]
    links: List[str]
    media: List[MediaMetadata]
    platform: str
    
    def __post_init__(self):
        """Initialize lists if None."""
        if self.hashtags is None:
            self.hashtags = []
        if self.mentions is None:
            self.mentions = []
        if self.links is None:
            self.links = []
        if self.media is None:
            self.media = []


@dataclass
class ValidationResult:
    """Result of content validation."""
    is_valid: bool
    violations: List[RuleViolation]
    platform: str
    validation_time: float
    metadata: Dict[str, Any]
    
    def get_blocking_violations(self) -> List[RuleViolation]:
        """Get violations that block publishing."""
        return [v for v in self.violations if v.severity == ViolationSeverity.ERROR]
    
    def get_warnings(self) -> List[RuleViolation]:
        """Get warning violations."""
        return [v for v in self.violations if v.severity == ViolationSeverity.WARNING]
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert result to dictionary."""
        return {
            "is_valid": self.is_valid,
            "violations": [v.to_dict() for v in self.violations],
            "platform": self.platform,
            "validation_time": self.validation_time,
            "metadata": self.metadata,
            "blocking_violations_count": len(self.get_blocking_violations()),
            "warnings_count": len(self.get_warnings())
        }


class PreflightRulesService:
    """Service for loading and validating preflight rules."""
    
    def __init__(self):
        """Initialize preflight rules service."""
        self.rules_cache = {}
        self.rules_loaded_at = None
        self.rules_file_path = self._get_rules_file_path()
        self.cache_ttl = 300  # 5 minutes
        
        # Load rules on initialization
        self._load_rules()
        
        logger.info("PreflightRulesService initialized", rules_file=self.rules_file_path)
    
    def _get_rules_file_path(self) -> str:
        """Get path to rules YAML file."""
        # Try to find rules file relative to project root
        current_dir = Path(__file__).parent
        project_root = current_dir.parent.parent
        rules_path = project_root / "config" / "publishing_rules.yml"
        
        if rules_path.exists():
            return str(rules_path)
        
        # Fallback to configured path if available
        if hasattr(settings, 'publishing_rules_path'):
            return settings.publishing_rules_path
        
        # Create default rules if file doesn't exist
        return self._create_default_rules_file(str(rules_path))
    
    def _create_default_rules_file(self, file_path: str) -> str:
        """Create default rules file if it doesn't exist."""
        default_rules = {
            "version": "1.0",
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "platforms": {
                "instagram": {
                    "caption": {
                        "max_length": 2200,
                        "min_length": 1,
                        "required": True
                    },
                    "hashtags": {
                        "max_count": 30,
                        "max_length_each": 100,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 20,
                        "required": False
                    },
                    "links": {
                        "allowed": True,
                        "max_count": 1
                    },
                    "media": {
                        "required": True,
                        "max_count": 10,
                        "max_file_size": 104857600,  # 100MB
                        "supported_formats": ["jpg", "jpeg", "png", "mp4", "mov"],
                        "video": {
                            "max_duration": 60,
                            "min_duration": 3,
                            "max_width": 1920,
                            "max_height": 1920
                        },
                        "image": {
                            "max_width": 1080,
                            "max_height": 1080,
                            "min_width": 320,
                            "min_height": 320
                        }
                    },
                    "content": {
                        "forbidden_words": ["spam", "fake", "scam"],
                        "forbidden_patterns": ["\\b\\d{4}-\\d{4}-\\d{4}-\\d{4}\\b"]  # Credit card pattern
                    }
                },
                "vk": {
                    "caption": {
                        "max_length": 15000,
                        "min_length": 1,
                        "required": True
                    },
                    "hashtags": {
                        "max_count": 10,
                        "max_length_each": 50,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 10,
                        "required": False
                    },
                    "links": {
                        "allowed": True,
                        "max_count": 5
                    },
                    "media": {
                        "required": False,
                        "max_count": 10,
                        "max_file_size": 209715200,  # 200MB
                        "supported_formats": ["jpg", "jpeg", "png", "gif", "mp4", "avi"],
                        "video": {
                            "max_duration": 300,
                            "min_duration": 1
                        }
                    },
                    "content": {
                        "forbidden_words": ["M:AB@5<87<", "=0@:>B8:8"],
                        "forbidden_patterns": []
                    }
                },
                "tiktok": {
                    "caption": {
                        "max_length": 150,
                        "min_length": 1,
                        "required": True
                    },
                    "hashtags": {
                        "max_count": 5,
                        "max_length_each": 25,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 5,
                        "required": False
                    },
                    "links": {
                        "allowed": False,
                        "max_count": 0
                    },
                    "media": {
                        "required": True,
                        "max_count": 1,
                        "max_file_size": 287309824,  # 274MB
                        "supported_formats": ["mp4", "mov"],
                        "video": {
                            "max_duration": 180,
                            "min_duration": 1,
                            "aspect_ratios": ["9:16", "1:1"]
                        }
                    },
                    "content": {
                        "forbidden_words": ["hate", "violence"],
                        "forbidden_patterns": []
                    }
                },
                "youtube": {
                    "caption": {
                        "max_length": 5000,
                        "min_length": 1,
                        "required": True
                    },
                    "hashtags": {
                        "max_count": 15,
                        "max_length_each": 50,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 10,
                        "required": False
                    },
                    "links": {
                        "allowed": True,
                        "max_count": 10
                    },
                    "media": {
                        "required": True,
                        "max_count": 1,
                        "max_file_size": 137438953472,  # 128GB
                        "supported_formats": ["mp4", "mov", "avi", "wmv"],
                        "video": {
                            "max_duration": 43200,  # 12 hours
                            "min_duration": 1
                        }
                    },
                    "content": {
                        "forbidden_words": ["copyright infringement"],
                        "forbidden_patterns": []
                    }
                },
                "telegram": {
                    "caption": {
                        "max_length": 4096,
                        "min_length": 0,
                        "required": False
                    },
                    "hashtags": {
                        "max_count": 10,
                        "max_length_each": 50,
                        "required": False
                    },
                    "mentions": {
                        "max_count": 20,
                        "required": False
                    },
                    "links": {
                        "allowed": True,
                        "max_count": 10
                    },
                    "media": {
                        "required": False,
                        "max_count": 10,
                        "max_file_size": 2097152000,  # 2GB
                        "supported_formats": ["jpg", "jpeg", "png", "gif", "mp4", "mov", "avi", "pdf", "doc"],
                        "video": {
                            "max_duration": 3600,  # 1 hour
                            "min_duration": 1
                        }
                    },
                    "content": {
                        "forbidden_words": [],
                        "forbidden_patterns": []
                    }
                }
            }
        }
        
        # Create directory if it doesn't exist
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        # Write default rules
        with open(file_path, 'w', encoding='utf-8') as f:
            yaml.dump(default_rules, f, default_flow_style=False, allow_unicode=True)
        
        logger.info("Created default publishing rules file", file_path=file_path)
        return file_path
    
    def _load_rules(self):
        """Load rules from YAML file."""
        try:
            with open(self.rules_file_path, 'r', encoding='utf-8') as f:
                self.rules_cache = yaml.safe_load(f)
            
            self.rules_loaded_at = time.time()
            
            logger.info(
                "Publishing rules loaded successfully",
                file_path=self.rules_file_path,
                platforms=list(self.rules_cache.get("platforms", {}).keys()),
                version=self.rules_cache.get("version", "unknown")
            )
            
        except Exception as e:
            logger.error(f"Failed to load publishing rules: {e}", file_path=self.rules_file_path)
            # Use minimal fallback rules
            self.rules_cache = {
                "version": "fallback",
                "platforms": {
                    "instagram": {"caption": {"max_length": 2200, "required": True}},
                    "vk": {"caption": {"max_length": 15000, "required": True}},
                    "tiktok": {"caption": {"max_length": 150, "required": True}},
                    "youtube": {"caption": {"max_length": 5000, "required": True}},
                    "telegram": {"caption": {"max_length": 4096, "required": False}}
                }
            }
            self.rules_loaded_at = time.time()
    
    def _maybe_reload_rules(self):
        """Reload rules if cache is expired."""
        if (not self.rules_loaded_at or 
            time.time() - self.rules_loaded_at > self.cache_ttl):
            logger.info("Reloading publishing rules due to cache expiration")
            self._load_rules()
    
    def get_platform_rules(self, platform: str) -> Optional[Dict[str, Any]]:
        """Get rules for specific platform."""
        self._maybe_reload_rules()
        
        platforms = self.rules_cache.get("platforms", {})
        return platforms.get(platform)
    
    def validate_post(self, content: PostContent) -> ValidationResult:
        """
        Validate post content against platform rules.
        
        Args:
            content: Post content to validate
            
        Returns:
            Validation result with violations
        """
        start_time = time.time()
        
        with with_logging_context(platform=content.platform):
            logger.info(
                "Starting preflight validation",
                platform=content.platform,
                caption_length=len(content.caption),
                hashtags_count=len(content.hashtags),
                media_count=len(content.media)
            )
            
            violations = []
            
            # Get platform rules
            platform_rules = self.get_platform_rules(content.platform)
            if not platform_rules:
                violations.append(RuleViolation(
                    type=ViolationType.PLATFORM_NOT_SUPPORTED,
                    severity=ViolationSeverity.ERROR,
                    message=f"Platform '{content.platform}' is not supported",
                    platform=content.platform,
                    suggestion="Use supported platforms: instagram, vk, tiktok, youtube, telegram"
                ))
                
                return ValidationResult(
                    is_valid=False,
                    violations=violations,
                    platform=content.platform,
                    validation_time=time.time() - start_time,
                    metadata={"rules_version": self.rules_cache.get("version", "unknown")}
                )
            
            # Validate caption
            violations.extend(self._validate_caption(content, platform_rules))
            
            # Validate hashtags
            violations.extend(self._validate_hashtags(content, platform_rules))
            
            # Validate mentions
            violations.extend(self._validate_mentions(content, platform_rules))
            
            # Validate links
            violations.extend(self._validate_links(content, platform_rules))
            
            # Validate media
            violations.extend(self._validate_media(content, platform_rules))
            
            # Validate content restrictions
            violations.extend(self._validate_content_restrictions(content, platform_rules))
            
            # Determine if valid (no blocking violations)
            blocking_violations = [v for v in violations if v.severity == ViolationSeverity.ERROR]
            is_valid = len(blocking_violations) == 0
            
            validation_time = time.time() - start_time
            
            # Track metrics
            metrics.track_preflight_validation(
                platform=content.platform,
                is_valid=is_valid,
                violations_count=len(violations),
                blocking_violations_count=len(blocking_violations),
                validation_time=validation_time
            )
            
            logger.info(
                "Preflight validation completed",
                platform=content.platform,
                is_valid=is_valid,
                total_violations=len(violations),
                blocking_violations=len(blocking_violations),
                validation_time=validation_time
            )
            
            return ValidationResult(
                is_valid=is_valid,
                violations=violations,
                platform=content.platform,
                validation_time=validation_time,
                metadata={
                    "rules_version": self.rules_cache.get("version", "unknown"),
                    "rules_loaded_at": self.rules_loaded_at,
                    "total_checks": 6  # caption, hashtags, mentions, links, media, content
                }
            )
    
    def _validate_caption(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate caption against rules."""
        violations = []
        caption_rules = rules.get("caption", {})
        
        caption_length = len(content.caption)
        
        # Check if caption is required
        if caption_rules.get("required", True) and not content.caption.strip():
            violations.append(RuleViolation(
                type=ViolationType.CAPTION_EMPTY,
                severity=ViolationSeverity.ERROR,
                message="Caption is required but empty",
                platform=content.platform,
                field="caption",
                suggestion="Add a caption to your post"
            ))
        
        # Check minimum length
        min_length = caption_rules.get("min_length", 0)
        if caption_length < min_length and content.caption.strip():
            violations.append(RuleViolation(
                type=ViolationType.CAPTION_TOO_LONG,
                severity=ViolationSeverity.ERROR,
                message=f"Caption is too short ({caption_length} chars, minimum {min_length})",
                platform=content.platform,
                field="caption",
                current_value=caption_length,
                limit_value=min_length,
                suggestion=f"Caption must be at least {min_length} characters long"
            ))
        
        # Check maximum length
        max_length = caption_rules.get("max_length", 10000)
        if caption_length > max_length:
            violations.append(RuleViolation(
                type=ViolationType.CAPTION_TOO_LONG,
                severity=ViolationSeverity.ERROR,
                message=f"Caption is too long ({caption_length} chars, maximum {max_length})",
                platform=content.platform,
                field="caption",
                current_value=caption_length,
                limit_value=max_length,
                suggestion=f"Shorten caption to {max_length} characters or less"
            ))
        
        return violations
    
    def _validate_hashtags(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate hashtags against rules."""
        violations = []
        hashtag_rules = rules.get("hashtags", {})
        
        hashtag_count = len(content.hashtags)
        
        # Check maximum count
        max_count = hashtag_rules.get("max_count", 30)
        if hashtag_count > max_count:
            violations.append(RuleViolation(
                type=ViolationType.HASHTAGS_TOO_MANY,
                severity=ViolationSeverity.ERROR,
                message=f"Too many hashtags ({hashtag_count}, maximum {max_count})",
                platform=content.platform,
                field="hashtags",
                current_value=hashtag_count,
                limit_value=max_count,
                suggestion=f"Reduce hashtags to {max_count} or fewer"
            ))
        
        # Check individual hashtag length
        max_length_each = hashtag_rules.get("max_length_each", 100)
        for i, hashtag in enumerate(content.hashtags):
            if len(hashtag) > max_length_each:
                violations.append(RuleViolation(
                    type=ViolationType.HASHTAGS_TOO_LONG,
                    severity=ViolationSeverity.ERROR,
                    message=f"Hashtag #{i+1} is too long ({len(hashtag)} chars, maximum {max_length_each})",
                    platform=content.platform,
                    field=f"hashtags[{i}]",
                    current_value=len(hashtag),
                    limit_value=max_length_each,
                    suggestion=f"Shorten hashtag to {max_length_each} characters or less"
                ))
        
        return violations
    
    def _validate_mentions(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate mentions against rules."""
        violations = []
        mention_rules = rules.get("mentions", {})
        
        mention_count = len(content.mentions)
        
        # Check maximum count
        max_count = mention_rules.get("max_count", 20)
        if mention_count > max_count:
            violations.append(RuleViolation(
                type=ViolationType.MENTIONS_TOO_MANY,
                severity=ViolationSeverity.ERROR,
                message=f"Too many mentions ({mention_count}, maximum {max_count})",
                platform=content.platform,
                field="mentions",
                current_value=mention_count,
                limit_value=max_count,
                suggestion=f"Reduce mentions to {max_count} or fewer"
            ))
        
        return violations
    
    def _validate_links(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate links against rules."""
        violations = []
        link_rules = rules.get("links", {})
        
        link_count = len(content.links)
        
        # Check if links are allowed
        if not link_rules.get("allowed", True) and link_count > 0:
            violations.append(RuleViolation(
                type=ViolationType.LINKS_NOT_ALLOWED,
                severity=ViolationSeverity.ERROR,
                message=f"Links are not allowed on {content.platform}",
                platform=content.platform,
                field="links",
                current_value=link_count,
                limit_value=0,
                suggestion="Remove all links from the post"
            ))
        
        # Check maximum count
        max_count = link_rules.get("max_count", 10)
        if link_count > max_count:
            violations.append(RuleViolation(
                type=ViolationType.LINKS_NOT_ALLOWED,
                severity=ViolationSeverity.ERROR,
                message=f"Too many links ({link_count}, maximum {max_count})",
                platform=content.platform,
                field="links",
                current_value=link_count,
                limit_value=max_count,
                suggestion=f"Reduce links to {max_count} or fewer"
            ))
        
        return violations
    
    def _validate_media(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate media against rules."""
        violations = []
        media_rules = rules.get("media", {})
        
        media_count = len(content.media)
        
        # Check if media is required
        if media_rules.get("required", False) and media_count == 0:
            violations.append(RuleViolation(
                type=ViolationType.MEDIA_MISSING,
                severity=ViolationSeverity.ERROR,
                message=f"Media is required for {content.platform} posts",
                platform=content.platform,
                field="media",
                current_value=0,
                limit_value=1,
                suggestion="Add at least one image or video to your post"
            ))
        
        # Check maximum count
        max_count = media_rules.get("max_count", 10)
        if media_count > max_count:
            violations.append(RuleViolation(
                type=ViolationType.MEDIA_TOO_LARGE,
                severity=ViolationSeverity.ERROR,
                message=f"Too many media files ({media_count}, maximum {max_count})",
                platform=content.platform,
                field="media",
                current_value=media_count,
                limit_value=max_count,
                suggestion=f"Reduce media files to {max_count} or fewer"
            ))
        
        # Validate individual media files
        max_file_size = media_rules.get("max_file_size", 100 * 1024 * 1024)  # 100MB default
        supported_formats = media_rules.get("supported_formats", [])
        
        for i, media in enumerate(content.media):
            # Check file size
            if media.file_size and media.file_size > max_file_size:
                violations.append(RuleViolation(
                    type=ViolationType.MEDIA_TOO_LARGE,
                    severity=ViolationSeverity.ERROR,
                    message=f"Media file #{i+1} is too large ({media.file_size} bytes, maximum {max_file_size})",
                    platform=content.platform,
                    field=f"media[{i}].file_size",
                    current_value=media.file_size,
                    limit_value=max_file_size,
                    suggestion=f"Compress file to under {max_file_size // (1024*1024)}MB"
                ))
            
            # Check format
            if media.format and supported_formats and media.format.lower() not in [f.lower() for f in supported_formats]:
                violations.append(RuleViolation(
                    type=ViolationType.MEDIA_WRONG_FORMAT,
                    severity=ViolationSeverity.ERROR,
                    message=f"Media file #{i+1} format '{media.format}' not supported",
                    platform=content.platform,
                    field=f"media[{i}].format",
                    current_value=media.format,
                    limit_value=", ".join(supported_formats),
                    suggestion=f"Convert to supported format: {', '.join(supported_formats)}"
                ))
            
            # Validate video-specific rules
            if media.format and media.format.lower() in ['mp4', 'mov', 'avi', 'webm']:
                video_rules = media_rules.get("video", {})
                
                # Check duration
                if media.duration:
                    min_duration = video_rules.get("min_duration", 0)
                    max_duration = video_rules.get("max_duration", float('inf'))
                    
                    if media.duration < min_duration:
                        violations.append(RuleViolation(
                            type=ViolationType.MEDIA_TOO_LONG,
                            severity=ViolationSeverity.ERROR,
                            message=f"Video #{i+1} is too short ({media.duration}s, minimum {min_duration}s)",
                            platform=content.platform,
                            field=f"media[{i}].duration",
                            current_value=media.duration,
                            limit_value=min_duration,
                            suggestion=f"Video must be at least {min_duration} seconds long"
                        ))
                    
                    if media.duration > max_duration:
                        violations.append(RuleViolation(
                            type=ViolationType.MEDIA_TOO_LONG,
                            severity=ViolationSeverity.ERROR,
                            message=f"Video #{i+1} is too long ({media.duration}s, maximum {max_duration}s)",
                            platform=content.platform,
                            field=f"media[{i}].duration",
                            current_value=media.duration,
                            limit_value=max_duration,
                            suggestion=f"Trim video to {max_duration} seconds or less"
                        ))
                
                # Check dimensions
                if media.width and media.height:
                    max_width = video_rules.get("max_width")
                    max_height = video_rules.get("max_height")
                    
                    if max_width and media.width > max_width:
                        violations.append(RuleViolation(
                            type=ViolationType.MEDIA_WRONG_DIMENSIONS,
                            severity=ViolationSeverity.ERROR,
                            message=f"Video #{i+1} width too large ({media.width}px, maximum {max_width}px)",
                            platform=content.platform,
                            field=f"media[{i}].width",
                            current_value=media.width,
                            limit_value=max_width,
                            suggestion=f"Resize video width to {max_width}px or less"
                        ))
                    
                    if max_height and media.height > max_height:
                        violations.append(RuleViolation(
                            type=ViolationType.MEDIA_WRONG_DIMENSIONS,
                            severity=ViolationSeverity.ERROR,
                            message=f"Video #{i+1} height too large ({media.height}px, maximum {max_height}px)",
                            platform=content.platform,
                            field=f"media[{i}].height",
                            current_value=media.height,
                            limit_value=max_height,
                            suggestion=f"Resize video height to {max_height}px or less"
                        ))
        
        return violations
    
    def _validate_content_restrictions(self, content: PostContent, rules: Dict[str, Any]) -> List[RuleViolation]:
        """Validate content restrictions (forbidden words, patterns)."""
        violations = []
        content_rules = rules.get("content", {})
        
        # Check forbidden words
        forbidden_words = content_rules.get("forbidden_words", [])
        caption_lower = content.caption.lower()
        
        for word in forbidden_words:
            if word.lower() in caption_lower:
                violations.append(RuleViolation(
                    type=ViolationType.FORBIDDEN_WORDS,
                    severity=ViolationSeverity.ERROR,
                    message=f"Caption contains forbidden word: '{word}'",
                    platform=content.platform,
                    field="caption",
                    current_value=word,
                    suggestion=f"Remove or replace the word '{word}'"
                ))
        
        # Check forbidden patterns (regex)
        forbidden_patterns = content_rules.get("forbidden_patterns", [])
        
        for pattern in forbidden_patterns:
            try:
                if re.search(pattern, content.caption, re.IGNORECASE):
                    violations.append(RuleViolation(
                        type=ViolationType.FORBIDDEN_WORDS,
                        severity=ViolationSeverity.ERROR,
                        message=f"Caption contains forbidden pattern: {pattern}",
                        platform=content.platform,
                        field="caption",
                        current_value=pattern,
                        suggestion="Remove sensitive information from caption"
                    ))
            except re.error as e:
                logger.warning(f"Invalid regex pattern in rules: {pattern}", error=str(e))
        
        return violations
    
    def get_platform_limits(self, platform: str) -> Dict[str, Any]:
        """Get platform limits for display/UI purposes."""
        rules = self.get_platform_rules(platform)
        if not rules:
            return {}
        
        return {
            "caption_max_length": rules.get("caption", {}).get("max_length", 0),
            "hashtags_max_count": rules.get("hashtags", {}).get("max_count", 0),
            "mentions_max_count": rules.get("mentions", {}).get("max_count", 0),
            "links_max_count": rules.get("links", {}).get("max_count", 0),
            "media_max_count": rules.get("media", {}).get("max_count", 0),
            "media_max_file_size": rules.get("media", {}).get("max_file_size", 0),
            "video_max_duration": rules.get("media", {}).get("video", {}).get("max_duration", 0),
            "links_allowed": rules.get("links", {}).get("allowed", True),
            "media_required": rules.get("media", {}).get("required", False)
        }
    
    def get_supported_platforms(self) -> List[str]:
        """Get list of supported platforms."""
        self._maybe_reload_rules()
        return list(self.rules_cache.get("platforms", {}).keys())


# Global service instance
preflight_rules_service = PreflightRulesService()


# Convenience functions
def validate_post_content(caption: str, platform: str, 
                         hashtags: List[str] = None,
                         mentions: List[str] = None,
                         links: List[str] = None,
                         media_metadata: List[Dict[str, Any]] = None) -> ValidationResult:
    """
    Validate post content against platform rules.
    
    Args:
        caption: Post caption text
        platform: Target platform
        hashtags: List of hashtags
        mentions: List of mentions
        links: List of links
        media_metadata: List of media metadata dicts
        
    Returns:
        Validation result
    """
    # Convert media metadata dicts to MediaMetadata objects
    media = []
    if media_metadata:
        for meta in media_metadata:
            media.append(MediaMetadata(**meta))
    
    content = PostContent(
        caption=caption,
        hashtags=hashtags or [],
        mentions=mentions or [],
        links=links or [],
        media=media,
        platform=platform
    )
    
    return preflight_rules_service.validate_post(content)


def get_platform_publishing_limits(platform: str) -> Dict[str, Any]:
    """Get publishing limits for a platform."""
    return preflight_rules_service.get_platform_limits(platform)


def get_all_supported_platforms() -> List[str]:
    """Get list of all supported platforms."""
    return preflight_rules_service.get_supported_platforms()


# Advanced validation functions
def validate_aspect_ratio_compliance(media: MediaMetadata, platform: str) -> List[RuleViolation]:
    """
    Validate media aspect ratio compliance for platform.
    
    Args:
        media: Media metadata to validate
        platform: Target platform
        
    Returns:
        List of aspect ratio violations
    """
    violations = []
    
    if not media.width or not media.height:
        return violations
        
    rules = preflight_rules_service.get_platform_rules(platform)
    if not rules:
        return violations
    
    # Calculate actual aspect ratio
    aspect_ratio = media.width / media.height
    
    # Get supported aspect ratios for the platform
    media_rules = rules.get("media", {})
    if media.format and media.format.lower() in ['mp4', 'mov', 'avi']:
        # Video rules
        video_rules = media_rules.get("video", {})
        supported_ratios = video_rules.get("supported_aspect_ratios", [])
    else:
        # Image rules
        image_rules = media_rules.get("image", {})
        supported_ratios = image_rules.get("supported_aspect_ratios", [])
    
    if supported_ratios:
        # Convert ratio strings to floats and check compliance
        ratio_matches = False
        for ratio_str in supported_ratios:
            if ':' in ratio_str:
                width_ratio, height_ratio = map(float, ratio_str.split(':'))
                expected_ratio = width_ratio / height_ratio
                
                # Allow some tolerance for floating point comparison
                if abs(aspect_ratio - expected_ratio) < 0.01:
                    ratio_matches = True
                    break
        
        if not ratio_matches:
            violations.append(RuleViolation(
                type=ViolationType.MEDIA_WRONG_DIMENSIONS,
                severity=ViolationSeverity.ERROR,
                message=f"Aspect ratio {aspect_ratio:.2f}:1 not supported for {platform}",
                platform=platform,
                field="aspect_ratio",
                current_value=f"{aspect_ratio:.2f}:1",
                limit_value=", ".join(supported_ratios),
                suggestion=f"Resize media to supported aspect ratios: {', '.join(supported_ratios)}"
            ))
    
    return violations


def validate_business_compliance(content: PostContent, custom_rules: Dict[str, Any] = None) -> List[RuleViolation]:
    """
    Validate content against business compliance rules.
    
    Args:
        content: Post content to validate
        custom_rules: Additional business rules to apply
        
    Returns:
        List of business compliance violations
    """
    violations = []
    
    rules = preflight_rules_service.get_platform_rules(content.platform)
    if not rules:
        return violations
    
    content_rules = rules.get("content", {})
    
    # Check for required words (brand mentions, etc.)
    required_words = content_rules.get("required_words", [])
    if custom_rules:
        required_words.extend(custom_rules.get("required_words", []))
    
    caption_lower = content.caption.lower()
    
    for required_word in required_words:
        if required_word.lower() not in caption_lower:
            violations.append(RuleViolation(
                type=ViolationType.FORBIDDEN_WORDS,  # Reusing enum, could add REQUIRED_WORDS
                severity=ViolationSeverity.ERROR,
                message=f"Required word '{required_word}' missing from caption",
                platform=content.platform,
                field="caption",
                current_value="missing",
                limit_value=required_word,
                suggestion=f"Include '{required_word}' in the caption"
            ))
    
    # Advanced pattern matching for business rules
    business_patterns = content_rules.get("business_patterns", [])
    if custom_rules:
        business_patterns.extend(custom_rules.get("business_patterns", []))
    
    for pattern_config in business_patterns:
        if isinstance(pattern_config, dict):
            pattern = pattern_config.get("pattern", "")
            severity = pattern_config.get("severity", "error")
            message = pattern_config.get("message", f"Content violates business pattern: {pattern}")
            
            try:
                if re.search(pattern, content.caption, re.IGNORECASE):
                    violations.append(RuleViolation(
                        type=ViolationType.FORBIDDEN_WORDS,
                        severity=ViolationSeverity.ERROR if severity == "error" else ViolationSeverity.WARNING,
                        message=message,
                        platform=content.platform,
                        field="caption",
                        current_value=pattern,
                        suggestion=pattern_config.get("suggestion", "Modify content to comply with business rules")
                    ))
            except re.error as e:
                logger.warning(f"Invalid business pattern regex: {pattern}", error=str(e))
    
    return violations


def get_optimal_posting_times(platform: str) -> Dict[str, Any]:
    """
    Get optimal posting times for a platform.
    
    Args:
        platform: Target platform
        
    Returns:
        Dictionary with optimal posting times and recommendations
    """
    service = preflight_rules_service
    service._maybe_reload_rules()
    
    business_rules = service.rules_cache.get("business", {})
    posting_windows = business_rules.get("posting_windows", {})
    
    platform_windows = posting_windows.get(platform, {})
    
    return {
        "platform": platform,
        "optimal_hours": platform_windows.get("optimal_hours", []),
        "avoid_hours": platform_windows.get("avoid_hours", []),
        "peak_days": platform_windows.get("peak_days", []),
        "timezone": business_rules.get("default_timezone", "UTC"),
        "max_posts_per_hour": business_rules.get("content_strategy", {}).get("max_per_hour", 1),
        "max_posts_per_day": business_rules.get("content_strategy", {}).get("max_per_day", 10)
    }


def validate_content_quality(content: PostContent) -> Dict[str, Any]:
    """
    Validate content quality using advanced checks.
    
    Args:
        content: Post content to validate
        
    Returns:
        Dictionary with quality metrics and recommendations
    """
    service = preflight_rules_service
    service._maybe_reload_rules()
    
    quality_rules = service.rules_cache.get("quality_checks", {})
    
    quality_result = {
        "overall_score": 0.0,
        "checks": {},
        "recommendations": []
    }
    
    # Text analysis
    text_analysis = quality_rules.get("text_analysis", {})
    if text_analysis.get("enabled", False):
        text_checks = text_analysis.get("checks", [])
        
        caption_text = content.caption.strip()
        
        # Basic readability check
        if "readability_score" in text_checks:
            # Simple readability metric based on sentence length and word complexity
            sentences = caption_text.split('.')
            words = caption_text.split()
            avg_sentence_length = len(words) / max(len(sentences), 1)
            
            readability_score = min(100, max(0, 100 - (avg_sentence_length - 10) * 2))
            quality_result["checks"]["readability"] = {
                "score": readability_score,
                "status": "good" if readability_score >= 70 else "needs_improvement",
                "details": f"Average sentence length: {avg_sentence_length:.1f} words"
            }
            
            if readability_score < 70:
                quality_result["recommendations"].append(
                    "Consider shorter sentences for better readability"
                )
        
        # Grammar and spelling basic check
        if "grammar_check" in text_checks or "spelling_check" in text_checks:
            # Basic checks - in production this would integrate with services like Grammarly API
            has_typos = any(word in caption_text.lower() for word in ["teh", "adn", "youre", "its" "recieve"])
            
            quality_result["checks"]["grammar"] = {
                "status": "needs_review" if has_typos else "good",
                "details": "Basic grammar check completed"
            }
            
            if has_typos:
                quality_result["recommendations"].append(
                    "Review text for potential spelling errors"
                )
    
    # Calculate overall score
    scores = [check.get("score", 80) for check in quality_result["checks"].values() 
             if "score" in check]
    quality_result["overall_score"] = sum(scores) / len(scores) if scores else 75.0
    
    return quality_result


def get_platform_performance_insights(platform: str) -> Dict[str, Any]:
    """
    Get performance insights and recommendations for a platform.
    
    Args:
        platform: Target platform
        
    Returns:
        Dictionary with performance insights
    """
    service = preflight_rules_service
    service._maybe_reload_rules()
    
    business_rules = service.rules_cache.get("business", {})
    
    insights = {
        "platform": platform,
        "content_strategy": {},
        "posting_recommendations": {},
        "performance_factors": []
    }
    
    # Content strategy insights
    content_strategy = business_rules.get("content_strategy", {})
    if content_strategy:
        hashtag_mixing = content_strategy.get("hashtag_mixing", {})
        
        insights["content_strategy"] = {
            "hashtag_strategy": {
                "popular_ratio": hashtag_mixing.get("popular_ratio", 0.3),
                "niche_ratio": hashtag_mixing.get("niche_ratio", 0.4),
                "branded_ratio": hashtag_mixing.get("branded_ratio", 0.3),
                "recommendation": "Mix popular, niche, and branded hashtags for optimal reach"
            },
            "posting_frequency": {
                "max_per_hour": content_strategy.get("max_per_hour", 1),
                "max_per_day": content_strategy.get("max_per_day", 10),
                "recommendation": "Maintain consistent posting schedule without oversaturation"
            }
        }
    
    # Platform-specific performance factors
    platform_rules = service.get_platform_rules(platform)
    if platform_rules:
        algorithm_rules = platform_rules.get("algorithm", {})
        
        if platform == "tiktok" and algorithm_rules:
            insights["performance_factors"].extend([
                "Use trending sounds for better algorithm visibility",
                "Keep text overlay under 30% of screen space",
                "Vertical (9:16) format performs best",
                "15-30 second videos have highest engagement"
            ])
        
        elif platform == "instagram":
            insights["performance_factors"].extend([
                "Post during optimal hours (9-12, 17-20 UTC)",
                "Use mix of hashtags (#popular #niche #branded)",
                "Square (1:1) or portrait (4:5) images perform well",
                "Stories and Reels boost overall account reach"
            ])
        
        elif platform == "youtube":
            insights["performance_factors"].extend([
                "Custom thumbnails increase click-through rate",
                "First 15 seconds are crucial for retention",
                "Consistent posting schedule builds audience",
                "End screens and cards improve watch time"
            ])
    
    return insights


# Export enhanced service instance
enhanced_preflight_service = preflight_rules_service