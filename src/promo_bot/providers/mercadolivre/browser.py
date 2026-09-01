"""Playwright-compatible UI adapter with the real-site contract gated off."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol
from urllib.parse import urlsplit

from promo_bot.providers.base import ProviderError
from promo_bot.providers.mercadolivre.models import MercadoLivreProductReference
from promo_bot.providers.mercadolivre.policy import validate_affiliate_link


class LocatorLike(Protocol):
    async def fill(self, value: str) -> None: ...

    async def click(self) -> None: ...

    async def input_value(self) -> str: ...

    async def select_option(self, *, label: str) -> object: ...


class PageLike(Protocol):
    async def goto(self, url: str, *, wait_until: str, timeout: float) -> object: ...

    def locator(self, selector: str) -> LocatorLike: ...


@dataclass(frozen=True, slots=True)
class GeneratorUiContract:
    """Selectors are supplied only by a reviewed local fixture or a confirmed official UI."""

    version: str
    page_url: str
    product_input_selector: str
    generate_button_selector: str
    result_selector: str
    environment: Literal["local_fixture", "official"] = "local_fixture"
    official_automation_authorized: bool = False
    label_selector: str | None = None

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("UI contract version is required")
        selectors = (
            self.product_input_selector,
            self.generate_button_selector,
            self.result_selector,
        )
        if any(not selector.strip() for selector in selectors):
            raise ValueError("UI contract selectors cannot be empty")
        page_parts = urlsplit(self.page_url)
        scheme = page_parts.scheme.casefold()
        if self.environment == "official" and scheme != "https":
            raise ValueError("official UI contract requires HTTPS")
        if self.environment == "local_fixture" and scheme not in {"file", "http"}:
            raise ValueError("local fixture UI contract requires a file or local HTTP URL")
        if (
            self.environment == "local_fixture"
            and scheme == "http"
            and page_parts.hostname not in {"127.0.0.1", "::1", "localhost"}
        ):
            raise ValueError("local HTTP fixture must use a loopback hostname")


@dataclass(frozen=True, slots=True)
class GeneratedAffiliateLink:
    short_link: str
    hostname: str
    source: Literal["official_link_generator_ui"]
    contract_version: str
    label_used: bool


class PlaywrightLinkGeneratorAdapter:
    """Operate a Playwright Page without launching or authenticating a browser itself."""

    def __init__(self, contract: GeneratorUiContract, *, timeout_seconds: int = 90) -> None:
        self.contract = contract
        self.timeout_ms = float(timeout_seconds * 1_000)

    async def generate(
        self,
        page: PageLike,
        reference: MercadoLivreProductReference,
        *,
        allowed_hosts: frozenset[str],
        label: str | None = None,
        registered_labels: frozenset[str] = frozenset(),
    ) -> GeneratedAffiliateLink:
        if (
            self.contract.environment == "official"
            and not self.contract.official_automation_authorized
        ):
            raise ProviderError(
                "MERCADO_LIVRE_LIVE_BROWSER_GATE_CLOSED",
                retryable=False,
                manual_review=True,
            )
        if label is not None:
            if label not in registered_labels:
                raise ValueError("label must be selected from the user's registered labels")
            if self.contract.label_selector is None:
                raise ValueError("the reviewed UI contract does not support labels")

        await page.goto(
            self.contract.page_url,
            wait_until="domcontentloaded",
            timeout=self.timeout_ms,
        )
        await page.locator(self.contract.product_input_selector).fill(reference.canonical_url)
        if label is not None and self.contract.label_selector is not None:
            await page.locator(self.contract.label_selector).select_option(label=label)
        await page.locator(self.contract.generate_button_selector).click()
        raw_link = await page.locator(self.contract.result_selector).input_value()
        short_link, hostname = validate_affiliate_link(raw_link, allowed_hosts=allowed_hosts)
        return GeneratedAffiliateLink(
            short_link=short_link,
            hostname=hostname,
            source="official_link_generator_ui",
            contract_version=self.contract.version,
            label_used=label is not None,
        )
