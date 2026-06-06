# Code Cleanup Tracker

Updated: 2026-02-17

## Rules

1. Before editing, check if file was already cleaned in this cycle.
2. Re-check cleaned files only if:
   - dependent file changed,
   - tests/lint indicate regression,
   - risk zone (auth, billing, publishing pipeline).
3. Each edit batch must include validation (`ruff` + `pytest` at least targeted).

## Current Cycle (2026-02-17)

| File | Status | Last pass | Notes |
|---|---|---|---|
| `app/api/routes.py` | done (pass 1) | 2026-02-17 | Reduced duplication, moved constants, simplified content extraction, structured logs. |
| `app/adapters/vk.py` | done (pass 1) | 2026-02-17 | Removed duplicated media upload branching, fixed docstring artifact. |
| `app/api/deps.py` | done (pass 1) | 2026-02-17 | Removed redundant try/except, clarified unused dependency params, kept auth behavior intact. |
| `app/adapters/telegram.py` | done (pass 1) | 2026-02-17 | Centralized media method mapping, reduced duplicate URL checks, simplified send strategy branching. |
| `app/api/auth.py` | done (pass 1) | 2026-02-17 | Removed dead code path, improved constant-time hash compare, standardized structured logging and rate-limit constants. |
| `app/services/pricing.py` | done (pass 1) | 2026-02-17 | Added central plan resolver helper to remove repeated fallback logic. |
| `app/api/video_gen.py` | done (pass 1) | 2026-02-17 | Extracted shared validation/result helpers and removed duplicate task response/failure blocks. |
| `app/api/tiktok_oauth.py` | done (pass 1) | 2026-02-17 | Centralized dashboard redirects, hardened fallback display name, standardized structured logging. |
| `app/services/cloud_storage.py` | done (pass 1) | 2026-02-17 | Replaced repeated provider branching in OAuth/token ops with explicit handler dispatch maps. |
| `app/api/cloud_storage.py` | done (pass 1) | 2026-02-17 | Added shared mappers and user-connection loader to remove duplicated DB lookup/response assembly. |
| `app/api/user_routes.py` | done (pass 1) | 2026-02-17 | Added shared response builders, unified VK account lookup, standardized structured logs in account/post flow. |
| `app/services/scheduler.py` | done (pass 1) | 2026-02-17 | Switched to structured logger helper, removed minor IO/style duplication, hardened cron next-run hour handling. |
| `app/api/content_plan.py` | done (pass 1) | 2026-02-17 | Extracted common plan access/response helpers, centralized platform/media validation constants, reduced repeated plan serialization. |
| `app/services/content_planner.py` | done (pass 1) | 2026-02-17 | Replaced fragile f-string logging in error paths with structured logs; kept generation/review flow unchanged. |
| `app/api/checkout.py` | done (pass 1) | 2026-02-17 | Added shared order response builders, reduced duplicated item mapping, standardized structured payment/subscription logs. |
| `app/services/payment_service.py` | done (pass 1) | 2026-02-17 | Added unified error-result helper and cleaned duplicated error return branches; structured exception logging. |
| `app/api/analytics.py` | done (pass 1) | 2026-02-17 | Added reusable mappers for metrics/insight/settings responses to remove repeated serialization blocks. |
| `app/services/content_analytics.py` | done (pass 1) | 2026-02-17 | Unified logger source and centralized simple error-result construction in optimization flow. |
| `app/api/tts.py` | done (pass 1) | 2026-02-17 | Centralized request validation and audio response builder; ensured service client is always closed via `finally`. |
| `app/services/tts_openai.py` | done (pass 1) | 2026-02-17 | Minor file I/O cleanup with `Path.open` and normalized save-error formatting. |
| `app/api/video_gen.py` | done (pass 2) | 2026-02-17 | Added shared task access helper and extracted provider execution paths for text/image generation to remove deep branching duplication. |
| `app/services/video_gen_runway.py` | done (pass 2) | 2026-02-17 | Added common failed-result helper and unified failure returns in generation/polling branches. |
| `app/api/deps.py` | done (pass 2) | 2026-02-17 | Unified Bearer token extraction across JWT dependencies and reduced duplicated auth-header parsing branches. |
| `app/api/auth.py` | done (pass 2) | 2026-02-17 | Added shared email normalization/regex constant and hardened code verification via constant-time compare. |
| `app/services/cloud_storage.py` | done (pass 2) | 2026-02-17 | Added shared access-token/sync-error helpers, removed repeated empty-token branches, and normalized provider folder-id extraction dispatch. |
| `app/api/cloud_storage.py` | done (pass 2) | 2026-02-17 | Added provider parser helper and unified simple success response for connection deletion endpoint. |
| `app/api/user_routes.py` | done (pass 2) | 2026-02-17 | Added shared success-response helper and aligned selected HTTP status constants for consistency/readability. |
| `app/api/checkout.py` | done (pass 2) | 2026-02-17 | Added user-order lookup helper and success-response helper; simplified webhook status branch and simulation response consistency. |
| `app/services/payment_service.py` | done (pass 2) | 2026-02-17 | Added unified payment-status error helper to reduce duplicated error dict construction. |
| `app/services/content_planner.py` | done (pass 2) | 2026-02-17 | Added shared JSON extraction/media-type fallback helpers and removed duplicated model-response parsing blocks. |
| `app/api/analytics.py` | done (pass 2) | 2026-02-17 | Added shared settings mapper and safe enum parsers for query/body values to prevent unhandled ValueError branches. |
| `app/api/routes.py` | done (pass 2) | 2026-02-17 | Replaced queue warning f-string with structured logging and extracted queue overload threshold constant. |
| `app/adapters/vk.py` | done (pass 2) | 2026-02-17 | Added shared async/sync response readers to remove duplicated upload/download response-handling branches. |
| `app/adapters/telegram.py` | done (pass 2) | 2026-02-17 | Centralized media-type detection constants/helpers for convenience send/publish APIs and removed repeated extension branching. |
| `app/services/pricing.py` | done (pass 2) | 2026-02-17 | Added shared provider fallback and overage cost helpers to reduce duplicated credit-cost calculations. |
| `app/api/content_plan.py` | done (pass 2) | 2026-02-17 | Added shared platform/media-type normalization helpers and reused them across generate/manual/CSV upload validation paths. |
| `app/services/content_analytics.py` | done (pass 2) | 2026-02-17 | Applied platform filter in suggestions and fixed benchmark metrics query to include user scope via `Post` join; unified utc-now usage helper. |
| `app/api/tiktok_oauth.py` | done (pass 2) | 2026-02-17 | Added shared utc-now and fallback display-name helpers to remove repeated date/fallback string construction paths. |
| `app/services/scheduler.py` | done (pass 2) | 2026-02-17 | Added safe cron/publish-time parsing helpers and structured schedule error logging to avoid crashes on malformed schedule values. |
| `app/api/tts.py` | done (pass 2) | 2026-02-17 | Extracted shared TTS endpoint execution helper to remove duplicated generate/generate-long validation and response handling flow. |
| `app/services/tts_openai.py` | done (pass 2) | 2026-02-17 | Added shared speed clamp helper and simplified status checks/formatting in generation paths for readability consistency. |
| `app/adapters/tiktok.py` | done (pass 2) | 2026-02-17 | Added shared post-info builder and fixed fragile upload-request handling path to avoid non-awaitable await branch in chunk upload flow. |
| `app/adapters/instagram.py` | done (pass 2) | 2026-02-17 | Replaced unsafe `eval` in rate-limit header parsing with `ast.literal_eval` and centralized extension constants for media-type detection. |
| `app/adapters/facebook.py` | done (pass 2) | 2026-02-17 | Centralized media-type detection for convenience publisher via extension constants/helper to remove repeated branching. |
| `app/adapters/youtube.py` | done (pass 2) | 2026-02-17 | Replaced manual OAuth query string concatenation with `urlencode` to ensure safe/valid auth URL generation. |
| `app/adapters/google_drive.py` | done (pass 2) | 2026-02-17 | Added shared media filter/target-dir helpers and reused them in listing/sync flow to reduce duplicate media-type branching. |
| `app/adapters/yandex_disk.py` | done (pass 2) | 2026-02-17 | Extracted common media filter/target-dir logic and unified duplicated sync loops via shared `_sync_files` helper for private/public folders. |
| `app/adapters/rutube.py` | done (pass 2) | 2026-02-17 | Simplified method/status branching in API request helper and normalized small duplicated hash/status-check logic. |
| `app/adapters/storage_s3.py` | done (pass 2) | 2026-02-17 | Added shared blocking-call runner and S3 key normalizer to remove repeated executor and URL-key normalization logic. |
| `app/adapters/telegram_intake.py` | done (pass 2) | 2026-02-17 | Removed unsafe `eval` in ffmpeg FPS parsing, centralized media/content field constants, and simplified duplicated content-type/media checks. |
| `app/services/email_service.py` | done (pass 2) | 2026-02-17 | Consolidated duplicated SMTP send parameter blocks into a shared helper while preserving TLS mode behavior. |
| `app/services/antifraud.py` | done (pass 2) | 2026-02-17 | Added shared utc-now/risk-level helpers to reduce duplicated threshold logic and normalized hashing input encoding. |
| `app/services/enrichment.py` | done (pass 2) | 2026-02-17 | Added shared processing-time helper and corrected not-found metrics status to 404 for clearer observability semantics. |
| `app/services/caption_llm.py` | done (pass 2) | 2026-02-17 | Centralized mock platform detection and fallback output construction to remove duplicated branching/response assembly. |
| `app/services/image_gen.py` | done (pass 2) | 2026-02-17 | Extracted shared provider init/get-setting helpers to reduce duplicated provider bootstrap logic. |
| `app/services/image_gen_nanobana.py` | done (pass 2) | 2026-02-17 | Added safe enum parsing helpers for model/resolution/aspect-ratio in convenience API to simplify duplicated fallback checks. |
| `app/services/video_gen_minimax.py` | done (pass 2) | 2026-02-17 | Extracted shared request submission helper to remove duplicated API response/error parsing in text/image generation methods. |
| `app/services/image_gen_midjourney.py` | done (pass 2) | 2026-02-17 | Unified upscale/variation action flow via shared action helper with consistent task-id validation and error handling. |
| `app/services/video_gen_kling.py` | done (pass 2) | 2026-02-17 | Added shared generation-submit and task-id extraction helpers to remove duplicated response/error handling across text/image generation endpoints. |
| `app/services/video_gen_runway.py` | done (pass 2) | 2026-02-17 | Consolidated duplicated task-submission flow for text/image generation into shared helper with unified task-id validation/logging. |
| `app/services/preflight_rules.py` | done (pass 2) | 2026-02-17 | Reduced repeated media-format normalization work by precomputing lowercase supported-format set during media validation. |
| `app/services/publishers/telegram.py` | done (pass 2) | 2026-02-17 | Unified Telegram API result parsing and success/error response construction across send methods to remove duplicated platform URL/error handling blocks. |
| `app/services/publishers/vk.py` | done (pass 2) | 2026-02-17 | Added shared VK error parsing/image fetch helpers and reused them across post/group-cover/avatar/edit/get flows to reduce duplicated API error/download branches. |
| `app/api/account.py` | done (pass 2) | 2026-02-17 | Extracted shared profile/subscription builders and loader helper to remove duplicated response assembly and switched account update/link logs to structured fields. |
| `app/api/admin_fraud.py` | done (pass 2) | 2026-02-17 | Added shared enum/masking/success helpers to reduce repeated serialization/log formatting and standardized admin mutate responses. |
| `app/api/cart.py` | done (pass 2) | 2026-02-17 | Added shared cart-item/success/total helpers, reduced repeated cart total recalculation branches, and converted cart logs to structured fields. |
| `app/api/billing.py` | done (pass 2) | 2026-02-17 | Extracted product/plan resolver and order-id helpers to simplify subscription validation flow and remove branching duplication. |
| `app/api/generation_progress.py` | done (pass 2) | 2026-02-17 | Centralized UUID/plan/progress loaders, extracted media-step templates/status constants, and simplified step update/progress recalculation branching. |
| `app/api/pricing.py` | done (pass 2) | 2026-02-17 | Added shared usage/recommendation payload helpers and unified provider-enable checks in quick estimate flow. |
| `app/models/repositories.py` | done (pass 2) | 2026-02-17 | Fixed fragile SQLAlchemy boolean/null filters in failure/token-expiry queries and added shared time/mapping helpers to reduce duplicated update logic. |
| `app/models/db.py` | done (pass 2) | 2026-02-17 | Unified healthcheck SQL/exception logging and replaced repeated f-string log formatting with parameterized logger calls. |
| `app/middleware/antifraud.py` | done (pass 2) | 2026-02-17 | Extracted shared client-IP/masking helpers and standardized blocked-access payload/log formatting to reduce duplicated branching. |
| `app/observability/metrics.py` | done (pass 2) | 2026-02-17 | Added shared request-method extractor for metrics decorators and removed duplicated async/sync tracking blocks. |
| `app/core/config.py` | done (pass 2) | 2026-02-17 | Added shared allowed-value constants and secret-env helper to simplify validation and secret alias construction logic. |
| `app/main.py` | done (pass 2) | 2026-02-17 | Centralized router/CORS/service constants, extracted safe adapter-cleanup helpers, and unified request timing with monotonic clock. |
| `app/workers/tasks/publish.py` | done (pass 2) | 2026-02-17 | Extracted platform/result helpers, moved task duration to monotonic timing, and removed duplicated platform list usage. |
| `app/workers/tasks/ingest.py` | done (pass 2) | 2026-02-17 | Added shared media/time helpers, reduced repeated media-count branching, and standardized monotonic task timing for reliability. |
| `app/workers/tasks/finalize.py` | done (pass 2) | 2026-02-17 | Added shared elapsed-time helper and replaced wall-clock task duration math with monotonic timing. |
| `app/workers/tasks/preflight.py` | done (pass 2) | 2026-02-17 | Extracted media parsing/quality aggregation helpers, reused safe serialization helper, and standardized monotonic duration tracking. |
| `app/core/security.py` | done (pass 2) | 2026-02-17 | Added shared webhook signature computation helper/secret bytes cache to remove duplicated HMAC payload signing logic. |
| `app/core/logging.py` | done (pass 2) | 2026-02-17 | Extracted static sensitive/skip field sets to module constants to reduce repeated allocations and simplify formatter/filter logic. |

## Completed housekeeping

- Removed macOS metadata junk: `._DEPLOY_COMMANDS.md`
