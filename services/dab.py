from models import ProviderResult


class DABProvider:
    """Placeholder for direct DAB cash/transfer rate retrieval.

    DAB publishes cash and transfer rates on its public page, but the page does
    not document a machine-readable API contract. The documented Frankfurter
    DAB provider is integrated separately for one indicative USD/AFN rate; it
    does not provide these separate buy/sell tables.
    """

    source = "Da Afghanistan Bank"
    url = "https://www.dab.gov.af/exchange-rates"

    def fetch(self) -> ProviderResult:
        return ProviderResult(
            available=False,
            message="منبع رسمی DAB فعلاً در دسترس نیست؛ نرخ ساختگی نمایش داده نمی‌شود.",
        )
