"""Social media publishers."""

from .telegram import TelegramPublisher
from .vk import VKPublisher

__all__ = ["TelegramPublisher", "VKPublisher"]
