from ._deps import get_colony, get_ready_colony, get_updater
from .chat import router as chat_router
from .console import router as console_router
from .providers import router as providers_router
from .testlab import _parse_generate_response, router as testlab_router
from .updates import router as updates_router

__all__ = [
    "chat_router",
    "console_router",
    "get_colony",
    "get_ready_colony",
    "get_updater",
    "providers_router",
    "updates_router",
    "testlab_router",
    "_parse_generate_response",
]
