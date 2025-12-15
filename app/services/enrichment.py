"""Product enrichment service for SoVAni Crosspost."""

import asyncio
import time
from typing import Dict, Any, Optional, List
from datetime import datetime, timedelta
from dataclasses import dataclass, asdict
from abc import ABC, abstractmethod

from ..core.config import settings
from ..core.logging import get_logger, with_logging_context
from ..models.db import db_manager
from ..observability.metrics import metrics

logger = get_logger("services.enrichment")

@dataclass
class ProductAttributes:
    """Normalized product attributes structure for LLM/Publishers."""
    
    external_id: str
    source: str
    title: str
    description: Optional[str] = None
    category: Optional[str] = None
    brand: Optional[str] = None
    price: Optional[float] = None
    original_price: Optional[float] = None
    currency: str = "RUB"
    colors: List[str] = None
    sizes: List[str] = None
    materials: List[str] = None
    image_urls: List[str] = None
    in_stock: bool = True
    tags: List[str] = None
    keywords: List[str] = None
    collection: Optional[str] = None
    sku: Optional[str] = None
    product_url: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    confidence_score: float = 1.0
    
    def __post_init__(self):
        if self.colors is None:
            self.colors = []
        if self.sizes is None:
            self.sizes = []
        if self.materials is None:
            self.materials = []
        if self.image_urls is None:
            self.image_urls = []
        if self.tags is None:
            self.tags = []
        if self.keywords is None:
            self.keywords = []
        if self.created_at is None:
            self.created_at = datetime.utcnow().isoformat()
        if self.updated_at is None:
            self.updated_at = datetime.utcnow().isoformat()
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
    
    def to_llm_context(self) -> str:
        """Convert to LLM-friendly text context."""
        context_parts = [
            f"">20@: {self.title}",
            f"@5=4: {self.brand or 'SoVAni'}",
            f"0B53>@8O: {self.category or '45640'}"
        ]
        
        if self.description:
            context_parts.append(f"?8A0=85: {self.description}")
        
        if self.price:
            price_text = f"&5=0: {self.price} {self.currency}"
            if self.original_price and self.original_price > self.price:
                price_text += f" (1K;> {self.original_price} {self.currency})"
            context_parts.append(price_text)
        
        if self.colors:
            context_parts.append(f"&25B0: {', '.join(self.colors)}")
        
        if self.sizes:
            context_parts.append(f" 07<5@K: {', '.join(self.sizes)}")
        
        return "\n".join(context_parts)
    
    def is_fresh(self, max_age_hours: int = 24) -> bool:
        if not self.updated_at:
            return False
        
        try:
            updated_time = datetime.fromisoformat(self.updated_at.replace('Z', '+00:00'))
            age = datetime.utcnow() - updated_time.replace(tzinfo=None)
            return age < timedelta(hours=max_age_hours)
        except (ValueError, AttributeError):
            return False

class ProductSourceError(Exception):
    pass

class ProductNotFoundError(Exception):
    pass

class ProductSource(ABC):
    @abstractmethod
    async def get_product_data(self, external_id: str) -> Optional[ProductAttributes]:
        pass
    
    @abstractmethod
    def get_source_name(self) -> str:
        pass

class LocalProductSource(ProductSource):
    def __init__(self):
        self.source_name = "local"
    
    async def get_product_data(self, external_id: str) -> Optional[ProductAttributes]:
        try:
            mock_products = self._get_mock_local_products()
            product_data = mock_products.get(external_id)
            
            if product_data:
                return ProductAttributes(**product_data)
            return None
        except Exception as e:
            logger.error(f"Error getting local product data: {e}")
            raise ProductSourceError(f"Local source error: {e}")
    
    def get_source_name(self) -> str:
        return self.source_name
    
    def _get_mock_local_products(self) -> Dict[str, Dict[str, Any]]:
        return {
            "dress_001": {
                "external_id": "dress_001",
                "source": "local",
                "title": "-;530=B=>5 ?;0BL5 SoVAni Classic",
                "description": "!B8;L=>5 ?;0BL5 87 :0G5AB25==>3> B@8:>B060",
                "category": ";0BLO",
                "brand": "SoVAni",
                "price": 5990.0,
                "original_price": 7990.0,
                "currency": "RUB",
                "colors": ["'5@=K9", "!8=89", ">@4>2K9"],
                "sizes": ["XS", "S", "M", "L", "XL"],
                "materials": [""@8:>B06", "-;0AB0="],
                "image_urls": ["https://sovani.ru/images/dress_001_1.jpg"],
                "in_stock": True,
                "tags": [">D8A", "M;530=B=>ABL"],
                "keywords": ["?;0BL5", "B@8:>B06", "SoVAni"],
                "collection": "Classic 2024",
                "sku": "SOV-DR-001",
                "product_url": "https://sovani.ru/products/dress_001",
                "confidence_score": 1.0
            }
        }

class WildberriesSource(ProductSource):
    def __init__(self):
        self.source_name = "wildberries"
    
    async def get_product_data(self, external_id: str) -> Optional[ProductAttributes]:
        # Stub implementation
        await asyncio.sleep(0.1)
        
        if external_id == "wb_12345":
            return ProductAttributes(
                external_id=external_id,
                source=self.source_name,
                title="">20@ A Wildberries",
                description="?8A0=85 B>20@0 A <0@:5B?;59A0",
                category="45640",
                brand="Test Brand",
                price=1500.0,
                currency="RUB",
                colors=["5;K9"],
                sizes=["M", "L"],
                confidence_score=0.8
            )
        return None
    
    def get_source_name(self) -> str:
        return self.source_name

class ProductEnrichmentService:
    def __init__(self):
        self.sources: Dict[str, ProductSource] = {
            "local": LocalProductSource(),
            "wildberries": WildberriesSource()
        }
        self.cache_ttl_hours = 24
    
    async def get_product_attrs(self, source: str, external_id: str) -> Optional[ProductAttributes]:
        start_time = time.time()
        
        with with_logging_context(source=source, external_id=external_id):
            logger.info("Getting product attributes", source=source, external_id=external_id)
            
            try:
                if source not in self.sources:
                    raise ProductSourceError(f"Unknown source '{source}'")
                
                source_instance = self.sources[source]
                product = await source_instance.get_product_data(external_id)
                
                if product:
                    processing_time = time.time() - start_time
                    
                    logger.info(
                        "Product attributes retrieved successfully",
                        source=source,
                        external_id=external_id,
                        processing_time=processing_time
                    )
                    
                    metrics.track_external_api_call(
                        service=f"product_{source}",
                        endpoint="get_product",
                        status_code=200,
                        duration=processing_time
                    )
                    
                    return product
                else:
                    logger.info("Product not found", source=source, external_id=external_id)
                    raise ProductNotFoundError(f"Product {external_id} not found in {source}")
                
            except ProductNotFoundError:
                raise
            except Exception as e:
                processing_time = time.time() - start_time
                
                logger.error(
                    "Error getting product attributes",
                    source=source,
                    external_id=external_id,
                    error=str(e),
                    processing_time=processing_time
                )
                
                metrics.track_external_api_call(
                    service=f"product_{source}",
                    endpoint="get_product",
                    status_code=500,
                    duration=processing_time
                )
                
                raise ProductSourceError(f"Error fetching product from {source}: {str(e)}")
    
    async def get_enriched_context_for_llm(self, source: str, external_id: str) -> str:
        try:
            product = await self.get_product_attrs(source, external_id)
            if product:
                return product.to_llm_context()
            else:
                return f"">20@ {external_id} =5 =0945= 2 8AB>G=8:5 {source}"
        except ProductNotFoundError:
            return f"">20@ {external_id} =5 =0945="
        except Exception as e:
            logger.error(f"Error getting LLM context: {e}")
            return f"H81:0 ?>;CG5=8O 40==KE > B>20@5: {str(e)}"
    
    def get_available_sources(self) -> List[str]:
        return list(self.sources.keys())

# Global service instance
enrichment_service = ProductEnrichmentService()

# Convenience functions
async def get_product_attrs(source: str, external_id: str) -> Optional[ProductAttributes]:
    return await enrichment_service.get_product_attrs(source, external_id)

async def get_llm_context(source: str, external_id: str) -> str:
    return await enrichment_service.get_enriched_context_for_llm(source, external_id)