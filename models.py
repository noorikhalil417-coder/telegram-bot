from dataclasses import dataclass, field
from typing import Any

@dataclass
class ProviderResult:
    rates: dict[str, float] = field(default_factory=dict)
    gold: dict[str, Any] = field(default_factory=dict)
    available: bool = False
    message: str | None = None
