"""
Unit tests for caption LLM service.

Tests:
- Multi-provider LLM integration (OpenAI, Claude, Mock)
- Platform-specific caption generation and validation
- Text length validation and truncation
- Error handling and fallback mechanisms
- MockProvider functionality
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock, AsyncMock

from app.services.caption_llm import (
    CaptionLLMService,
    PlatformInput,
    CaptionOutput,
    LLMError,
    MockProvider,
    OpenAIProvider,
    generate_all_captions
)


class TestMockProvider:
    """Test MockProvider functionality."""
    
    def test_mock_provider_initialization(self):
        """Test MockProvider creates with correct templates."""
        provider = MockProvider()
        
        assert provider.get_provider_name() == "mock"
        assert isinstance(provider.response_templates, dict)
        assert "instagram" in provider.response_templates
        assert "vk" in provider.response_templates
        assert "tiktok" in provider.response_templates
        assert "youtube" in provider.response_templates
        assert "telegram" in provider.response_templates
    
    @pytest.mark.asyncio
    async def test_mock_provider_instagram_generation(self):
        """Test MockProvider generates Instagram-style captions."""
        provider = MockProvider()
        
        prompt = """Создай привлекательную подпись для INSTAGRAM.
        
        БРЕНД: SoVAni
        ГОЛОС БРЕНДА: Элегантный, стильный
        АУДИТОРИЯ: Женщины 25-45 лет
        
        ИСХОДНЫЙ ТЕКСТ: "Новая коллекция платьев"
        
        ТРЕБОВАНИЯ:
        - Используй эмодзи умеренно
        - Добавь призыв к действию
        - Максимум 2200 символов
        """
        
        result = await provider.generate_text(prompt, max_tokens=500)
        
        assert "Новая коллекция платьев" in result
        assert "#SoVAni" in result
        assert "#Fashion" in result or "#Style" in result
        assert len(result) < 200  # Mock keeps it short
    
    @pytest.mark.asyncio
    async def test_mock_provider_vk_generation(self):
        """Test MockProvider generates VK-style captions."""
        provider = MockProvider()
        
        prompt = """Создай привлекательную подпись для VK.
        
        ИСХОДНЫЙ ТЕКСТ: "Стильные аксессуары"
        """
        
        result = await provider.generate_text(prompt, max_tokens=500)
        
        assert "Стильные аксессуары" in result
        assert "#SoVAni" in result
        assert "#Мода" in result
    
    @pytest.mark.asyncio
    async def test_mock_provider_handles_long_content(self):
        """Test MockProvider truncates long content."""
        provider = MockProvider()
        
        long_content = "А" * 100  # Very long content
        prompt = f'ИСХОДНЫЙ ТЕКСТ: "{long_content}"'
        
        result = await provider.generate_text(prompt, max_tokens=500)
        
        assert "..." in result  # Should be truncated
        assert len(result) < len(long_content) + 50  # Significantly shorter


class TestPlatformInput:
    """Test PlatformInput dataclass."""
    
    def test_platform_input_basic_creation(self):
        """Test basic PlatformInput creation."""
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Новая коллекция"
        )
        
        assert platform_input.platform == "instagram"
        assert platform_input.content_text == "Новая коллекция"
        assert platform_input.hashtags == []  # Default empty list
        assert platform_input.product_context is None
        assert platform_input.media_type is None
        assert platform_input.media_count == 0
        assert platform_input.call_to_action is None
    
    def test_platform_input_with_all_fields(self):
        """Test PlatformInput with all fields."""
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Красивое платье",
            product_context="Платье SoVAni Classic, цена 5990 руб",
            media_type="photo",
            media_count=3,
            hashtags=["#SoVAni", "#Fashion"],
            call_to_action="Купить сейчас"
        )
        
        assert platform_input.platform == "instagram"
        assert platform_input.content_text == "Красивое платье"
        assert platform_input.product_context == "Платье SoVAni Classic, цена 5990 руб"
        assert platform_input.media_type == "photo"
        assert platform_input.media_count == 3
        assert platform_input.hashtags == ["#SoVAni", "#Fashion"]
        assert platform_input.call_to_action == "Купить сейчас"


class TestCaptionOutput:
    """Test CaptionOutput dataclass."""
    
    def test_caption_output_basic_creation(self):
        """Test basic CaptionOutput creation."""
        output = CaptionOutput(
            platform="instagram",
            caption="Красивый текст с хэштегами #SoVAni",
            hashtags=["#SoVAni", "#Fashion"],
            character_count=35
        )
        
        assert output.platform == "instagram"
        assert output.caption == "Красивый текст с хэштегами #SoVAni"
        assert output.hashtags == ["#SoVAni", "#Fashion"]
        assert output.character_count == 35
        assert output.is_truncated is False  # Default
        assert output.confidence_score == 1.0  # Default
        assert output.generation_time == 0.0  # Default
    
    def test_caption_output_to_dict(self):
        """Test CaptionOutput to_dict conversion."""
        output = CaptionOutput(
            platform="vk",
            caption="Тест",
            hashtags=["#test"],
            character_count=10,
            is_truncated=True,
            confidence_score=0.8,
            generation_time=1.5
        )
        
        result_dict = output.to_dict()
        
        assert isinstance(result_dict, dict)
        assert result_dict["platform"] == "vk"
        assert result_dict["caption"] == "Тест"
        assert result_dict["hashtags"] == ["#test"]
        assert result_dict["character_count"] == 10
        assert result_dict["is_truncated"] is True
        assert result_dict["confidence_score"] == 0.8
        assert result_dict["generation_time"] == 1.5


class TestCaptionLLMService:
    """Test CaptionLLMService functionality."""
    
    @pytest.fixture
    def llm_service(self):
        """Create LLM service for testing."""
        return CaptionLLMService()
    
    def test_service_initialization(self, llm_service):
        """Test service initializes with correct configs."""
        assert isinstance(llm_service.provider, MockProvider)  # Should default to mock
        assert isinstance(llm_service.platform_configs, dict)
        assert isinstance(llm_service.brand_guidelines, dict)
        
        # Check platform configs
        assert "instagram" in llm_service.platform_configs
        assert "vk" in llm_service.platform_configs
        assert "tiktok" in llm_service.platform_configs
        assert "youtube" in llm_service.platform_configs
        assert "telegram" in llm_service.platform_configs
        
        # Check config structure
        instagram_config = llm_service.platform_configs["instagram"]
        assert "max_length" in instagram_config
        assert "hashtag_limit" in instagram_config
        assert instagram_config["max_length"] == 2200
        assert instagram_config["hashtag_limit"] == 30
    
    @pytest.mark.asyncio
    async def test_generate_single_caption_instagram(self, llm_service):
        """Test generating single caption for Instagram."""
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Новая коллекция SoVAni",
            hashtags=["#SoVAni", "#Fashion"]
        )
        
        result = await llm_service._generate_single_caption("instagram", platform_input)
        
        assert isinstance(result, CaptionOutput)
        assert result.platform == "instagram"
        assert isinstance(result.caption, str)
        assert len(result.caption) > 0
        assert isinstance(result.hashtags, list)
        assert result.character_count > 0
        assert result.generation_time > 0
        assert result.confidence_score > 0
    
    @pytest.mark.asyncio
    async def test_generate_all_captions_multiple_platforms(self, llm_service):
        """Test generating captions for multiple platforms."""
        platform_inputs = {
            "instagram": PlatformInput(
                platform="instagram",
                content_text="Элегантное платье",
                hashtags=["#SoVAni"]
            ),
            "vk": PlatformInput(
                platform="vk", 
                content_text="Стильная одежда",
                hashtags=["#SoVAni", "#Мода"]
            ),
            "tiktok": PlatformInput(
                platform="tiktok",
                content_text="Модный тренд",
                hashtags=["#sovani"]
            )
        }
        
        results = await llm_service.generate_all(platform_inputs)
        
        assert isinstance(results, dict)
        assert len(results) == 3
        assert "instagram" in results
        assert "vk" in results
        assert "tiktok" in results
        
        # Check each result
        for platform, result in results.items():
            assert isinstance(result, CaptionOutput)
            assert result.platform == platform
            assert len(result.caption) > 0
            assert isinstance(result.hashtags, list)
    
    def test_build_prompt_instagram(self, llm_service):
        """Test prompt building for Instagram."""
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Красивое платье",
            product_context="Платье SoVAni Classic, размеры S-XL",
            hashtags=["#SoVAni", "#Fashion"]
        )
        
        config = llm_service.platform_configs["instagram"]
        prompt = llm_service._build_prompt("instagram", platform_input, config)
        
        assert "INSTAGRAM" in prompt
        assert "SoVAni" in prompt
        assert "Красивое платье" in prompt
        assert "Платье SoVAni Classic" in prompt
        assert "#SoVAni #Fashion" in prompt
        assert "2200 символов" in prompt
    
    def test_validate_caption_length_normal(self, llm_service):
        """Test caption length validation for normal length."""
        caption = "Короткий текст"
        max_length = 100
        
        result_caption, is_truncated = llm_service._validate_caption_length(caption, max_length)
        
        assert result_caption == caption
        assert is_truncated is False
    
    def test_validate_caption_length_truncated(self, llm_service):
        """Test caption length validation with truncation."""
        caption = "Очень длинный текст который нужно обрезать потому что он превышает лимит символов"
        max_length = 30
        
        result_caption, is_truncated = llm_service._validate_caption_length(caption, max_length)
        
        assert len(result_caption) <= max_length
        assert is_truncated is True
        assert "..." in result_caption
    
    def test_parse_llm_response(self, llm_service):
        """Test parsing LLM response for hashtags."""
        generated_text = "Красивое платье от SoVAni! #SoVAni #Fashion #Style"
        existing_hashtags = ["#Brand"]
        
        caption, hashtags = llm_service._parse_llm_response(generated_text, existing_hashtags)
        
        assert caption == generated_text.strip()
        assert "#SoVAni" in hashtags
        assert "#Fashion" in hashtags
        assert "#Style" in hashtags
        assert "#Brand" in hashtags  # Existing hashtag preserved
    
    def test_create_fallback_caption(self, llm_service):
        """Test fallback caption creation."""
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Тестовый контент для фоллбэка"
        )
        
        fallback = llm_service._create_fallback_caption("instagram", platform_input)
        
        assert isinstance(fallback, str)
        assert len(fallback) > 0
        assert "Тестовый контент" in fallback
        assert "#SoVAni" in fallback
    
    @pytest.mark.asyncio
    async def test_error_handling_with_fallback(self, llm_service):
        """Test error handling creates fallback caption."""
        platform_inputs = {
            "instagram": PlatformInput(
                platform="instagram",
                content_text="Тест ошибки"
            )
        }
        
        # Mock provider to raise an error
        with patch.object(llm_service.provider, 'generate_text', side_effect=Exception("Mock error")):
            results = await llm_service.generate_all(platform_inputs)
            
            assert "instagram" in results
            result = results["instagram"]
            assert isinstance(result, CaptionOutput)
            assert result.confidence_score == 0.3  # Low confidence for fallback
            assert len(result.caption) > 0  # Should have fallback content
    
    def test_platform_configs_completeness(self, llm_service):
        """Test that all platform configs are complete."""
        required_platforms = ["instagram", "vk", "tiktok", "youtube", "telegram"]
        
        for platform in required_platforms:
            assert platform in llm_service.platform_configs
            config = llm_service.platform_configs[platform]
            assert "max_length" in config
            assert "hashtag_limit" in config
            assert isinstance(config["max_length"], int)
            assert isinstance(config["hashtag_limit"], int)
            assert config["max_length"] > 0
            assert config["hashtag_limit"] > 0
    
    def test_brand_guidelines_structure(self, llm_service):
        """Test brand guidelines structure."""
        guidelines = llm_service.brand_guidelines
        
        assert "brand_name" in guidelines
        assert "brand_voice" in guidelines
        assert "target_audience" in guidelines
        assert "prohibited_words" in guidelines
        
        assert guidelines["brand_name"] == "SoVAni"
        assert isinstance(guidelines["brand_voice"], str)
        assert isinstance(guidelines["target_audience"], str)
        assert isinstance(guidelines["prohibited_words"], list)


class TestOpenAIProvider:
    """Test OpenAIProvider functionality."""
    
    def test_openai_provider_initialization(self):
        """Test OpenAI provider initialization."""
        provider = OpenAIProvider("test_api_key", "gpt-4")
        
        assert provider.get_provider_name() == "openai"
        assert provider.api_key == "test_api_key"
        assert provider.model == "gpt-4"
        assert provider.api_base == "https://api.openai.com/v1"
    
    @pytest.mark.asyncio
    async def test_openai_provider_successful_generation(self):
        """Test OpenAI provider successful text generation."""
        provider = OpenAIProvider("test_key")
        
        # Mock successful HTTP response
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "Сгенерированный текст для поста"
                    }
                }
            ]
        }
        mock_response.raise_for_status.return_value = None
        
        with patch.object(provider.http_client, 'post', return_value=mock_response) as mock_post:
            result = await provider.generate_text("Тестовый промпт", max_tokens=100)
            
            assert result == "Сгенерированный текст для поста"
            mock_post.assert_called_once()
            
            # Check request parameters
            call_args = mock_post.call_args
            assert "chat/completions" in call_args[0][0]
            assert "json" in call_args[1]
            request_json = call_args[1]["json"]
            assert request_json["max_tokens"] == 100
            assert "Тестовый промпт" in request_json["messages"][1]["content"]
    
    @pytest.mark.asyncio
    async def test_openai_provider_error_handling(self):
        """Test OpenAI provider error handling."""
        provider = OpenAIProvider("test_key")
        
        # Mock HTTP error
        with patch.object(provider.http_client, 'post', side_effect=Exception("Network error")):
            with pytest.raises(LLMError) as exc_info:
                await provider.generate_text("Тест")
            
            assert "OpenAI generation failed" in str(exc_info.value)
            assert "Network error" in str(exc_info.value)


class TestConvenienceFunctions:
    """Test module-level convenience functions."""
    
    @pytest.mark.asyncio
    async def test_generate_all_captions_function(self):
        """Test generate_all_captions convenience function."""
        platform_inputs = {
            "instagram": PlatformInput(
                platform="instagram",
                content_text="Тест функции"
            )
        }
        
        # Mock the global service
        with patch('app.services.caption_llm.caption_service') as mock_service:
            mock_result = {
                "instagram": CaptionOutput(
                    platform="instagram",
                    caption="Мокированный результат",
                    hashtags=["#test"],
                    character_count=20
                )
            }
            mock_service.generate_all.return_value = mock_result
            
            result = await generate_all_captions(platform_inputs)
            
            assert result == mock_result
            mock_service.generate_all.assert_called_once_with(platform_inputs)


class TestEdgeCases:
    """Test edge cases and error scenarios."""
    
    @pytest.mark.asyncio
    async def test_empty_platform_inputs(self):
        """Test handling empty platform inputs."""
        service = CaptionLLMService()
        
        results = await service.generate_all({})
        
        assert isinstance(results, dict)
        assert len(results) == 0
    
    @pytest.mark.asyncio
    async def test_empty_content_text(self):
        """Test handling empty content text."""
        service = CaptionLLMService()
        
        platform_inputs = {
            "instagram": PlatformInput(
                platform="instagram",
                content_text=""
            )
        }
        
        results = await service.generate_all(platform_inputs)
        
        assert "instagram" in results
        result = results["instagram"]
        assert isinstance(result, CaptionOutput)
        assert len(result.caption) > 0  # Should have some content
    
    def test_invalid_platform_fallback(self):
        """Test fallback for invalid/unknown platform."""
        service = CaptionLLMService()
        
        # Test with unknown platform - should fall back to Instagram config
        platform_input = PlatformInput(
            platform="unknown_platform",
            content_text="Тест"
        )
        
        config = service.platform_configs.get("unknown_platform", service.platform_configs["instagram"])
        prompt = service._build_prompt("unknown_platform", platform_input, config)
        
        assert "UNKNOWN_PLATFORM" in prompt
        assert "Тест" in prompt
    
    def test_very_long_hashtag_list(self):
        """Test handling very long hashtag lists."""
        service = CaptionLLMService()
        
        # Create many hashtags (more than limit)
        many_hashtags = [f"#tag{i}" for i in range(50)]
        
        platform_input = PlatformInput(
            platform="instagram",
            content_text="Тест",
            hashtags=many_hashtags
        )
        
        config = service.platform_configs["instagram"]
        prompt = service._build_prompt("instagram", platform_input, config)
        
        # Should include hashtags in prompt
        assert "#tag0" in prompt
        assert "#tag49" in prompt


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])