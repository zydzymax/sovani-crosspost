"""Product enrichment service for SalesWhisper Crosspost."""

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Any

from ..core.logging import get_logger, with_logging_context
from ..observability.metrics import metrics

logger = get_logger("services.enrichment")


@dataclass
class ProductAttributes:
    """Normalized product attributes structure for LLM/Publishers."""

    external_id: str
    source: str
    title: str
    description: str | None = None
    category: str | None = None
    brand: str | None = None
    price: float | None = None
    original_price: float | None = None
    currency: str = "RUB"
    colors: list[str] = None
    sizes: list[str] = None
    materials: list[str] = None
    image_urls: list[str] = None
    in_stock: bool = True
    tags: list[str] = None
    keywords: list[str] = None
    collection: str | None = None
    sku: str | None = None
    product_url: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_llm_context(self) -> str:
        """Convert to LLM-friendly text context."""
        context_parts = [
            f"Товар: {self.title}",
            f"Бренд: {self.brand or 'SalesWhisper'}",
            f"Категория: {self.category or 'Одежда'}",
        ]

        if self.description:
            context_parts.append(f"Описание: {self.description}")

        if self.price:
            price_text = f"Цена: {self.price} {self.currency}"
            if self.original_price and self.original_price > self.price:
                price_text += f" (было {self.original_price} {self.currency})"
            context_parts.append(price_text)

        if self.colors:
            context_parts.append(f"Цвета: {', '.join(self.colors)}")

        if self.sizes:
            context_parts.append(f"Размеры: {', '.join(self.sizes)}")

        return "\n".join(context_parts)

    def is_fresh(self, max_age_hours: int = 24) -> bool:
        if not self.updated_at:
            return False

        try:
            updated_time = datetime.fromisoformat(self.updated_at.replace("Z", "+00:00"))
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
    async def get_product_data(self, external_id: str) -> ProductAttributes | None:
        pass

    @abstractmethod
    def get_source_name(self) -> str:
        pass


class LocalProductSource(ProductSource):
    def __init__(self):
        self.source_name = "local"

    async def get_product_data(self, external_id: str) -> ProductAttributes | None:
        try:
            mock_products = self._get_mock_local_products()
            product_data = mock_products.get(external_id)

            if product_data:
                return ProductAttributes(**product_data)
            return None
        except Exception as e:
            logger.exception("Error getting local product data")
            raise ProductSourceError(f"Local source error: {e}")

    def get_source_name(self) -> str:
        return self.source_name

    def _get_mock_local_products(self) -> dict[str, dict[str, Any]]:
        return {
            "dress_001": {
                "external_id": "dress_001",
                "source": "local",
                "title": "Элегантное платье SalesWhisper Classic",
                "description": "Стильное платье из качественного трикотажа",
                "category": "Платья",
                "brand": "SalesWhisper",
                "price": 5990.0,
                "original_price": 7990.0,
                "currency": "RUB",
                "colors": ["Черный", "Синий", "Бордовый"],
                "sizes": ["XS", "S", "M", "L", "XL"],
                "materials": ["Трикотаж", "Эластан"],
                "image_urls": ["https://saleswhisper.ru/images/dress_001_1.jpg"],
                "in_stock": True,
                "tags": ["офис", "элегантность"],
                "keywords": ["платье", "трикотаж", "SalesWhisper"],
                "collection": "Классика 2024",
                "sku": "SOV-DR-001",
                "product_url": "https://saleswhisper.ru/products/dress_001",
                "confidence_score": 1.0,
            }
        }


class WildberriesSource(ProductSource):
    def __init__(self):
        self.source_name = "wildberries"

    async def get_product_data(self, external_id: str) -> ProductAttributes | None:
        # Stub implementation
        await asyncio.sleep(0.1)

        if external_id == "wb_12345":
            return ProductAttributes(
                external_id=external_id,
                source=self.source_name,
                title="Товар с Wildberries",
                description="Описание товара из маркетплейса",
                category="Одежда",
                brand="Test Brand",
                price=1500.0,
                currency="RUB",
                colors=["Белый"],
                sizes=["M", "L"],
                confidence_score=0.8,
            )
        return None

    def get_source_name(self) -> str:
        return self.source_name


class ProductEnrichmentService:
    def __init__(self):
        self.sources: dict[str, ProductSource] = {"local": LocalProductSource(), "wildberries": WildberriesSource()}
        self.cache_ttl_hours = 24

    @staticmethod
    def _processing_time(start_time: float) -> float:
        return time.time() - start_time

    async def get_product_attrs(self, source: str, external_id: str) -> ProductAttributes | None:
        start_time = time.time()

        with with_logging_context(source=source, external_id=external_id):
            logger.info("Getting product attributes", source=source, external_id=external_id)

            try:
                if source not in self.sources:
                    raise ProductSourceError(f"Unknown source '{source}'")

                source_instance = self.sources[source]
                product = await source_instance.get_product_data(external_id)

                if product:
                    processing_time = self._processing_time(start_time)

                    logger.info(
                        "Product attributes retrieved successfully",
                        source=source,
                        external_id=external_id,
                        processing_time=processing_time,
                    )

                    metrics.track_external_api_call(
                        service=f"product_{source}", endpoint="get_product", status_code=200, duration=processing_time
                    )

                    return product
                else:
                    logger.info("Product not found", source=source, external_id=external_id)
                    raise ProductNotFoundError(f"Product {external_id} not found in {source}")

            except ProductNotFoundError:
                processing_time = self._processing_time(start_time)
                metrics.track_external_api_call(
                    service=f"product_{source}", endpoint="get_product", status_code=500, duration=processing_time
                )
                raise
            except ProductSourceError:
                raise
            except Exception as e:
                processing_time = self._processing_time(start_time)

                logger.exception(
                    "Error getting product attributes",
                    source=source,
                    external_id=external_id,
                    error=str(e),
                    processing_time=processing_time,
                )

                metrics.track_external_api_call(
                    service=f"product_{source}", endpoint="get_product", status_code=500, duration=processing_time
                )

                raise ProductSourceError(f"Error fetching product from {source}: {str(e).lower()}")

    async def get_enriched_context_for_llm(self, source: str, external_id: str) -> str:
        try:
            product = await self.get_product_attrs(source, external_id)
            if product:
                return product.to_llm_context()
            else:
                return f"Товар {external_id} не найден в источнике {source}"
        except ProductNotFoundError:
            return f"Товар {external_id} не найден"
        except Exception as e:
            logger.exception("Error getting LLM context")
            return f"Ошибка получения данных товара: {str(e)}"

    def get_available_sources(self) -> list[str]:
        return list(self.sources.keys())


# Global service instance
enrichment_service = ProductEnrichmentService()


# Convenience functions
async def get_product_attrs(source: str, external_id: str) -> ProductAttributes | None:
    return await enrichment_service.get_product_attrs(source, external_id)


async def get_llm_context(source: str, external_id: str) -> str:
    return await enrichment_service.get_enriched_context_for_llm(source, external_id)
