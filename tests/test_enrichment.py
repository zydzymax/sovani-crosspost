"""
Unit tests for product enrichment service.

Tests:
- Product attribute retrieval from different sources
- Product not found scenarios
- LLM context generation
- Error handling and caching
"""

import pytest
import asyncio
from unittest.mock import patch, MagicMock

from app.services.enrichment import (
    ProductEnrichmentService,
    ProductAttributes,
    ProductSourceError,
    ProductNotFoundError,
    get_product_attrs,
    get_llm_context
)


class TestProductEnrichmentService:
    """Test product enrichment service functionality."""
    
    @pytest.fixture
    def enrichment_service(self):
        """Create enrichment service for testing."""
        return ProductEnrichmentService()
    
    @pytest.mark.asyncio
    async def test_get_existing_product_local_source(self, enrichment_service):
        """Test retrieving existing product from local source."""
        # Test getting existing product
        product = await enrichment_service.get_product_attrs("local", "dress_001")
        
        assert product is not None
        assert product.external_id == "dress_001"
        assert product.source == "local"
        assert product.title == "Элегантное платье SalesWhisper Classic"
        assert product.brand == "SalesWhisper"
        assert product.price == 5990.0
        assert product.original_price == 7990.0
        assert product.currency == "RUB"
        assert "Черный" in product.colors
        assert "S" in product.sizes
        assert product.in_stock is True
        assert product.confidence_score == 1.0
    
    @pytest.mark.asyncio 
    async def test_get_nonexistent_product_raises_error(self, enrichment_service):
        """Test that non-existent product raises ProductNotFoundError."""
        with pytest.raises(ProductNotFoundError) as exc_info:
            await enrichment_service.get_product_attrs("local", "nonexistent_product")
        
        assert "not found" in str(exc_info.value).lower()
        assert "nonexistent_product" in str(exc_info.value)
        assert "local" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_get_product_invalid_source(self, enrichment_service):
        """Test that invalid source raises ProductSourceError."""
        with pytest.raises(ProductSourceError) as exc_info:
            await enrichment_service.get_product_attrs("invalid_source", "any_id")
        
        assert "unknown source" in str(exc_info.value).lower()
        assert "invalid_source" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_get_product_wildberries_source(self, enrichment_service):
        """Test retrieving product from Wildberries source."""
        # Test existing product in Wildberries
        product = await enrichment_service.get_product_attrs("wildberries", "wb_12345")
        
        assert product is not None
        assert product.external_id == "wb_12345"
        assert product.source == "wildberries"
        assert product.title == "Товар с Wildberries"
        assert product.brand == "Test Brand"
        assert product.price == 1500.0
        assert product.confidence_score == 0.8
        
        # Test non-existent product in Wildberries
        with pytest.raises(ProductNotFoundError):
            await enrichment_service.get_product_attrs("wildberries", "nonexistent_wb")
    
    @pytest.mark.asyncio
    async def test_llm_context_generation(self, enrichment_service):
        """Test LLM context generation from product data."""
        # Test existing product
        context = await enrichment_service.get_enriched_context_for_llm("local", "dress_001")
        
        assert "Товар: Элегантное платье SalesWhisper Classic" in context
        assert "Бренд: SalesWhisper" in context
        assert "Категория: Платья" in context
        assert "Цена: 5990.0 RUB (было 7990.0 RUB)" in context
        assert "Цвета: Черный, Синий, Бордовый" in context
        assert "Размеры: XS, S, M, L, XL" in context
        
        # Test non-existent product
        context = await enrichment_service.get_enriched_context_for_llm("local", "nonexistent")
        assert "не найден" in context.lower()
    
    def test_product_attributes_dataclass(self):
        """Test ProductAttributes dataclass functionality."""
        # Test basic creation
        product = ProductAttributes(
            external_id="test_001",
            source="test",
            title="Test Product",
            price=1000.0
        )
        
        assert product.external_id == "test_001"
        assert product.source == "test"
        assert product.title == "Test Product"
        assert product.price == 1000.0
        assert product.currency == "RUB"  # Default value
        assert product.confidence_score == 1.0  # Default value
        assert isinstance(product.colors, list)  # Auto-initialized in __post_init__
        assert isinstance(product.sizes, list)
        assert isinstance(product.tags, list)
    
    def test_product_to_dict_conversion(self):
        """Test ProductAttributes to dictionary conversion."""
        product = ProductAttributes(
            external_id="test_001",
            source="test",
            title="Test Product",
            colors=["Red", "Blue"],
            sizes=["M", "L"]
        )
        
        product_dict = product.to_dict()
        
        assert isinstance(product_dict, dict)
        assert product_dict["external_id"] == "test_001"
        assert product_dict["title"] == "Test Product"
        assert product_dict["colors"] == ["Red", "Blue"]
        assert product_dict["sizes"] == ["M", "L"]
    
    def test_product_llm_context_formatting(self):
        """Test ProductAttributes LLM context formatting."""
        product = ProductAttributes(
            external_id="test_001",
            source="test",
            title="Тестовое платье",
            description="Красивое платье для особых случаев",
            category="Платья",
            brand="TestBrand",
            price=2500.0,
            original_price=3500.0,
            colors=["Красный", "Синий"],
            sizes=["S", "M", "L"]
        )
        
        context = product.to_llm_context()
        lines = context.split('\n')
        
        assert "Товар: Тестовое платье" in lines
        assert "Бренд: TestBrand" in lines  
        assert "Категория: Платья" in lines
        assert "Описание: Красивое платье для особых случаев" in lines
        assert "Цена: 2500.0 RUB (было 3500.0 RUB)" in lines
        assert "Цвета: Красный, Синий" in lines
        assert "Размеры: S, M, L" in lines
    
    def test_product_freshness_check(self):
        """Test product data freshness validation."""
        from datetime import datetime, timedelta
        
        # Fresh product (just created)
        product = ProductAttributes(
            external_id="test_001",
            source="test", 
            title="Fresh Product"
        )
        assert product.is_fresh(max_age_hours=24) is True
        
        # Old product (simulate old timestamp)
        old_timestamp = (datetime.utcnow() - timedelta(hours=25)).isoformat()
        product.updated_at = old_timestamp
        assert product.is_fresh(max_age_hours=24) is False
        
        # Product without timestamp
        product.updated_at = None
        assert product.is_fresh(max_age_hours=24) is False
    
    @pytest.mark.asyncio
    async def test_convenience_functions(self):
        """Test module-level convenience functions."""
        # Test get_product_attrs function
        product = await get_product_attrs("local", "dress_001")
        assert product is not None
        assert product.title == "Элегантное платье SalesWhisper Classic"
        
        # Test get_llm_context function  
        context = await get_llm_context("local", "dress_001")
        assert "Товар: Элегантное платье SalesWhisper Classic" in context
        
        # Test with non-existent product
        context = await get_llm_context("local", "nonexistent")
        assert "не найден" in context.lower()
    
    def test_available_sources(self, enrichment_service):
        """Test getting available sources."""
        sources = enrichment_service.get_available_sources()
        
        assert isinstance(sources, list)
        assert "local" in sources
        assert "wildberries" in sources
        assert len(sources) >= 2
    
    @pytest.mark.asyncio
    async def test_source_error_handling(self, enrichment_service):
        """Test error handling in product sources."""
        
        # Mock a source that raises an exception
        with patch.object(enrichment_service.sources["local"], "get_product_data") as mock_get:
            mock_get.side_effect = Exception("Database connection error")
            
            with pytest.raises(ProductSourceError) as exc_info:
                await enrichment_service.get_product_attrs("local", "any_id")
            
            assert "error fetching product from local" in str(exc_info.value).lower()
            assert "database connection error" in str(exc_info.value)
    
    @pytest.mark.asyncio
    async def test_metrics_tracking(self, enrichment_service):
        """Test that metrics are tracked during product retrieval."""
        
        with patch("app.services.enrichment.metrics") as mock_metrics:
            # Test successful retrieval
            product = await enrichment_service.get_product_attrs("local", "dress_001")
            
            # Verify metrics were called
            mock_metrics.track_external_api_call.assert_called_once()
            call_args = mock_metrics.track_external_api_call.call_args
            
            assert call_args[1]["service"] == "product_local"
            assert call_args[1]["endpoint"] == "get_product"
            assert call_args[1]["status_code"] == 200
            assert call_args[1]["duration"] > 0
    
    @pytest.mark.asyncio
    async def test_error_metrics_tracking(self, enrichment_service):
        """Test that error metrics are tracked when retrieval fails."""
        
        with patch("app.services.enrichment.metrics") as mock_metrics:
            # Test error case
            with pytest.raises(ProductNotFoundError):
                await enrichment_service.get_product_attrs("local", "nonexistent")
            
            # Verify error metrics were tracked
            mock_metrics.track_external_api_call.assert_called_once()
            call_args = mock_metrics.track_external_api_call.call_args
            
            assert call_args[1]["service"] == "product_local"
            assert call_args[1]["endpoint"] == "get_product"
            assert call_args[1]["status_code"] == 500


class TestProductAttributesEdgeCases:
    """Test edge cases for ProductAttributes."""
    
    def test_minimal_product_creation(self):
        """Test creating product with minimal required fields."""
        product = ProductAttributes(
            external_id="min_001",
            source="test",
            title="Minimal Product"
        )
        
        # Check all defaults are set correctly
        assert product.external_id == "min_001"
        assert product.source == "test"
        assert product.title == "Minimal Product"
        assert product.description is None
        assert product.price is None
        assert product.currency == "RUB"
        assert product.in_stock is True
        assert product.confidence_score == 1.0
        assert product.colors == []
        assert product.sizes == []
        assert product.materials == []
        assert product.tags == []
        assert product.keywords == []
        assert product.created_at is not None
        assert product.updated_at is not None
    
    def test_product_with_empty_collections(self):
        """Test product with explicitly empty collections."""
        product = ProductAttributes(
            external_id="empty_001",
            source="test", 
            title="Empty Collections",
            colors=[],
            sizes=[],
            tags=[]
        )
        
        context = product.to_llm_context()
        
        # Should not include empty collections in context
        assert "Цвета:" not in context
        assert "Размеры:" not in context
        assert context.count('\n') <= 3  # Only basic info
    
    def test_product_context_with_no_price(self):
        """Test LLM context when product has no price."""
        product = ProductAttributes(
            external_id="no_price_001",
            source="test",
            title="No Price Product",
            price=None
        )
        
        context = product.to_llm_context()
        assert "Цена:" not in context
        assert "Товар: No Price Product" in context


@pytest.mark.asyncio
async def test_integration_full_workflow():
    """Integration test for full enrichment workflow."""
    
    # Test complete workflow: get product -> generate LLM context
    service = ProductEnrichmentService()
    
    # Step 1: Get product attributes
    product = await service.get_product_attrs("local", "dress_001")
    assert product is not None
    
    # Step 2: Generate LLM context
    context = await service.get_enriched_context_for_llm("local", "dress_001")
    assert context is not None
    assert len(context) > 0
    
    # Step 3: Verify context contains key information
    assert product.title in context
    assert product.brand in context
    assert str(product.price) in context
    
    # Step 4: Test error case
    context_error = await service.get_enriched_context_for_llm("local", "nonexistent")
    assert "не найден" in context_error.lower()


if __name__ == "__main__":
    # Run specific tests
    pytest.main([__file__, "-v"])