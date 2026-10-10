from models import ProviderResult


class GoldProvider:
    """Automatic gold adapter disabled until the provider documents its unit."""

    def __init__(self, timeout: float = 15.0):
        self.timeout = timeout

    def fetch(self) -> ProviderResult:
        return ProviderResult(
            available=False,
            message=(
                "Gold API واحد قیمت را مستند نکرده است؛ نرخ خودکار طلا نمایش داده نمی‌شود. "
                "مدیر می‌تواند قیمت را همراه واحد، ارز و عیار ثبت کند."
            ),
        )
