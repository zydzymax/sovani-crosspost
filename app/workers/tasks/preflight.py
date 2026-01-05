"""Preflight stage tasks for SalesWhisper Crosspost."""

import time
from typing import Any

from ...core.logging import get_logger, with_logging_context
from ...observability.metrics import metrics
from ...services.preflight_rules import (
    MediaMetadata,
    PostContent,
    get_optimal_posting_times,
    get_platform_performance_insights,
    validate_aspect_ratio_compliance,
    validate_business_compliance,
    validate_content_quality,
    validate_post_content,
)
from ..celery_app import celery

logger = get_logger("tasks.preflight")

@celery.task(bind=True, name="app.workers.tasks.preflight.run_preflight_checks")
def run_preflight_checks(self, stage_data: dict[str, Any]) -> dict[str, Any]:
    """Run comprehensive preflight validation checks before publishing."""
    task_start_time = time.time()
    post_id = stage_data["post_id"]

    with with_logging_context(task_id=self.request.id, post_id=post_id):
        logger.info("Starting preflight validation checks", post_id=post_id)

        try:
            # Extract platform-specific posts from stage data
            platform_posts = stage_data.get("platform_posts", {})
            validation_results = {}
            all_validations_passed = True
            blocking_violations_summary = []

            # Validate each platform post
            for platform, post_data in platform_posts.items():
                logger.info(f"Validating content for {platform}", post_id=post_id, platform=platform)

                try:
                    # Extract content for validation
                    caption = post_data.get("caption", "")
                    hashtags = post_data.get("hashtags", [])
                    mentions = post_data.get("mentions", [])
                    links = post_data.get("links", [])

                    # Convert media metadata
                    media_list = []
                    media_data = post_data.get("media", [])
                    if isinstance(media_data, list):
                        for media_item in media_data:
                            if isinstance(media_item, dict):
                                media_list.append(MediaMetadata(
                                    file_path=media_item.get("file_path"),
                                    file_size=media_item.get("file_size"),
                                    width=media_item.get("width"),
                                    height=media_item.get("height"),
                                    duration=media_item.get("duration"),
                                    format=media_item.get("format"),
                                    mime_type=media_item.get("mime_type"),
                                    aspect_ratio=media_item.get("aspect_ratio")
                                ))

                    # Create post content for validation
                    content = PostContent(
                        caption=caption,
                        hashtags=hashtags,
                        mentions=mentions,
                        links=links,
                        media=media_list,
                        platform=platform
                    )

                    # Run comprehensive validation
                    validation_result = validate_post_content(
                        caption=caption,
                        platform=platform,
                        hashtags=hashtags,
                        mentions=mentions,
                        links=links,
                        media_metadata=[media.to_dict() if hasattr(media, 'to_dict') else media.__dict__ for media in media_list]
                    )

                    # Enhanced validation checks
                    additional_violations = []

                    # Check aspect ratio compliance for each media item
                    for media_item in media_list:
                        aspect_violations = validate_aspect_ratio_compliance(media_item, platform)
                        additional_violations.extend(aspect_violations)

                    # Check business compliance
                    business_violations = validate_business_compliance(content)
                    additional_violations.extend(business_violations)

                    # Add additional violations to the main result
                    if additional_violations:
                        validation_result.violations.extend(additional_violations)
                        validation_result.is_valid = len(validation_result.get_blocking_violations()) == 0

                    # Get content quality insights
                    quality_insights = validate_content_quality(content)

                    # Get optimal posting times
                    posting_insights = get_optimal_posting_times(platform)

                    # Get performance insights
                    performance_insights = get_platform_performance_insights(platform)

                    # Add insights to validation result
                    validation_result.metadata = {
                        "quality_score": quality_insights.get("overall_score", 0),
                        "optimal_posting_times": posting_insights,
                        "performance_insights": performance_insights,
                        "content_analysis": quality_insights
                    }

                    validation_results[platform] = validation_result.to_dict()

                    # Check for blocking violations
                    blocking_violations = validation_result.get_blocking_violations()
                    if blocking_violations:
                        all_validations_passed = False

                        # Log detailed violations
                        for violation in blocking_violations:
                            logger.error(
                                "Preflight validation violation",
                                post_id=post_id,
                                platform=platform,
                                violation_type=violation.type.value,
                                violation_message=violation.message,
                                field=violation.field,
                                current_value=violation.current_value,
                                limit_value=violation.limit_value,
                                suggestion=violation.suggestion
                            )

                            blocking_violations_summary.append({
                                "platform": platform,
                                "type": violation.type.value,
                                "message": violation.message,
                                "field": violation.field,
                                "suggestion": violation.suggestion
                            })

                    # Log warnings
                    warnings = validation_result.get_warnings()
                    for warning in warnings:
                        logger.warning(
                            "Preflight validation warning",
                            post_id=post_id,
                            platform=platform,
                            warning_type=warning.type.value,
                            warning_message=warning.message,
                            field=warning.field,
                            suggestion=warning.suggestion
                        )

                    logger.info(
                        "Platform validation completed",
                        post_id=post_id,
                        platform=platform,
                        is_valid=validation_result.is_valid,
                        violations_count=len(validation_result.violations),
                        blocking_violations_count=len(blocking_violations),
                        warnings_count=len(warnings),
                        quality_score=quality_insights.get("overall_score", 0),
                        is_optimal_time=posting_insights.get("is_optimal_time", False),
                        expected_engagement=performance_insights.get("expected_engagement", "unknown")
                    )

                except Exception as e:
                    logger.error(
                        "Failed to validate platform content",
                        post_id=post_id,
                        platform=platform,
                        error=str(e),
                        exc_info=True
                    )
                    all_validations_passed = False
                    validation_results[platform] = {
                        "is_valid": False,
                        "error": str(e),
                        "platform": platform
                    }
                    blocking_violations_summary.append({
                        "platform": platform,
                        "type": "validation_error",
                        "message": f"Validation failed: {str(e)}",
                        "field": "system",
                        "suggestion": "Check system logs and retry"
                    })

            # Aggregate results
            checks_result = {
                "content_approved": all_validations_passed,
                "media_validated": all_validations_passed,
                "platform_compliance": all_validations_passed,
                "brand_guidelines": all_validations_passed,
                "all_checks_passed": all_validations_passed,
                "validation_results": validation_results,
                "blocking_violations": blocking_violations_summary,
                "platforms_validated": len(platform_posts),
                "platforms_passed": len([r for r in validation_results.values() if r.get("is_valid", False)])
            }

            processing_time = time.time() - task_start_time

            # Calculate quality metrics
            avg_quality_score = 0
            optimal_time_platforms = 0
            if validation_results:
                quality_scores = [r.get("metadata", {}).get("quality_score", 0) for r in validation_results.values() if isinstance(r, dict)]
                avg_quality_score = sum(quality_scores) / len(quality_scores) if quality_scores else 0

                optimal_times = [r.get("metadata", {}).get("optimal_posting_times", {}).get("is_optimal_time", False) for r in validation_results.values() if isinstance(r, dict)]
                optimal_time_platforms = sum(1 for is_optimal in optimal_times if is_optimal)

            # Track enhanced metrics
            metrics.track_preflight_stage(
                post_id=post_id,
                platforms_count=len(platform_posts),
                all_passed=all_validations_passed,
                violations_count=len(blocking_violations_summary),
                processing_time=processing_time,
                avg_quality_score=avg_quality_score,
                optimal_time_platforms=optimal_time_platforms
            )

            if all_validations_passed:
                # Trigger next stage if all validations passed
                from .publish import publish_to_platforms
                next_task = publish_to_platforms.delay({**stage_data, "preflight_results": checks_result})

                logger.info(
                    "Preflight checks completed successfully - proceeding to publish",
                    post_id=post_id,
                    processing_time=processing_time,
                    platforms_validated=len(platform_posts),
                    next_task_id=next_task.id
                )

                return {
                    "success": True,
                    "post_id": post_id,
                    "processing_time": processing_time,
                    "checks_passed": True,
                    "validation_results": validation_results,
                    "next_stage": "publish",
                    "next_task_id": next_task.id,
                    "platforms_validated": len(platform_posts)
                }
            else:
                # Log failure and stop pipeline
                logger.error(
                    "Preflight checks failed - blocking publication",
                    post_id=post_id,
                    processing_time=processing_time,
                    platforms_validated=len(platform_posts),
                    platforms_passed=checks_result["platforms_passed"],
                    total_violations=len(blocking_violations_summary),
                    violations_summary=blocking_violations_summary
                )

                # Don't trigger next stage - stop pipeline
                return {
                    "success": False,
                    "post_id": post_id,
                    "processing_time": processing_time,
                    "checks_passed": False,
                    "validation_results": validation_results,
                    "blocking_violations": blocking_violations_summary,
                    "next_stage": None,
                    "platforms_validated": len(platform_posts),
                    "platforms_passed": checks_result["platforms_passed"],
                    "failure_reason": f"Preflight validation failed for {len(platform_posts) - checks_result['platforms_passed']} platforms"
                }

        except Exception as e:
            processing_time = time.time() - task_start_time

            logger.error(
                "Preflight checks task failed",
                post_id=post_id,
                error=str(e),
                processing_time=processing_time,
                exc_info=True
            )

            # Track failure metrics
            metrics.track_preflight_stage(
                post_id=post_id,
                platforms_count=0,
                all_passed=False,
                violations_count=0,
                processing_time=processing_time,
                error=str(e)
            )

            if self.request.retries < self.max_retries:
                logger.info(
                    "Retrying preflight checks",
                    post_id=post_id,
                    retry_count=self.request.retries + 1,
                    max_retries=self.max_retries
                )
                raise self.retry(countdown=60 * (self.request.retries + 1))

            raise


def to_dict(obj):
    """Convert MediaMetadata to dict if it has to_dict method."""
    if hasattr(obj, 'to_dict'):
        return obj.to_dict()
    elif hasattr(obj, '__dict__'):
        return obj.__dict__
    else:
        return obj
