from __future__ import annotations

from pathlib import Path

import pytest

from promo_bot.providers.base import ProviderError
from promo_bot.providers.mercadolivre import (
    BrowserProfileInUse,
    BrowserProfileLock,
    GeneratorUiContract,
    MercadoLivreProductReference,
    PlaywrightLinkGeneratorAdapter,
    ensure_profile_outside_workspace,
    validate_affiliate_link,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "mercadolivre" / "link_generator.html"


class FakeLocator:
    def __init__(self, page: FakePage, selector: str) -> None:
        self.page = page
        self.selector = selector

    async def fill(self, value: str) -> None:
        self.page.values[self.selector] = value

    async def click(self) -> None:
        self.page.clicked.append(self.selector)

    async def input_value(self) -> str:
        return self.page.values[self.selector]

    async def select_option(self, *, label: str) -> object:
        self.page.selected[self.selector] = label
        return object()


class FakePage:
    def __init__(self, result: str = "https://links.example.test/fixture") -> None:
        self.visited: list[str] = []
        self.clicked: list[str] = []
        self.selected: dict[str, str] = {}
        self.values = {"#generated-link": result}

    async def goto(self, url: str, *, wait_until: str, timeout: float) -> object:
        assert FIXTURE.read_text(encoding="utf-8").startswith("<!doctype html>")
        assert wait_until == "domcontentloaded"
        assert timeout == 90_000
        self.visited.append(url)
        return object()

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)


def local_contract(*, label: bool = False) -> GeneratorUiContract:
    return GeneratorUiContract(
        version="local-fixture-v1",
        page_url=FIXTURE.as_uri(),
        product_input_selector="#product-url",
        generate_button_selector="#generate",
        result_selector="#generated-link",
        label_selector="#registered-label" if label else None,
    )


def reference() -> MercadoLivreProductReference:
    return MercadoLivreProductReference(
        external_product_id="MLB123456789",
        canonical_url="https://produto.mercadolivre.com.br/MLB-123456789",
    )


@pytest.mark.asyncio
async def test_playwright_compatible_adapter_uses_only_local_fake_page() -> None:
    page = FakePage()
    generated = await PlaywrightLinkGeneratorAdapter(local_contract()).generate(
        page,
        reference(),
        allowed_hosts=frozenset({"links.example.test"}),
    )

    assert page.visited == [FIXTURE.as_uri()]
    assert page.values["#product-url"] == reference().canonical_url
    assert page.clicked == ["#generate"]
    assert generated.short_link == "https://links.example.test/fixture"
    assert generated.source == "official_link_generator_ui"
    assert not generated.label_used


@pytest.mark.asyncio
async def test_official_contract_gate_stops_before_navigation() -> None:
    page = FakePage()
    contract = GeneratorUiContract(
        version="unconfirmed",
        page_url="https://affiliate.example.invalid/generator",
        product_input_selector="#product",
        generate_button_selector="#generate",
        result_selector="#result",
        environment="official",
        official_automation_authorized=False,
    )

    with pytest.raises(ProviderError, match="MERCADO_LIVRE_LIVE_BROWSER_GATE_CLOSED"):
        await PlaywrightLinkGeneratorAdapter(contract).generate(
            page,
            reference(),
            allowed_hosts=frozenset({"links.example.test"}),
        )

    assert page.visited == []


@pytest.mark.asyncio
async def test_optional_label_must_be_pre_registered() -> None:
    page = FakePage()
    adapter = PlaywrightLinkGeneratorAdapter(local_contract(label=True))

    with pytest.raises(ValueError, match="registered labels"):
        await adapter.generate(
            page,
            reference(),
            allowed_hosts=frozenset({"links.example.test"}),
            label="inventada",
        )

    assert page.visited == []

    generated = await adapter.generate(
        page,
        reference(),
        allowed_hosts=frozenset({"links.example.test"}),
        label="canal-cadastrado",
        registered_labels=frozenset({"canal-cadastrado"}),
    )
    assert generated.label_used
    assert page.selected == {"#registered-label": "canal-cadastrado"}


@pytest.mark.parametrize(
    "value,hosts",
    [
        ("http://links.example.test/fixture", frozenset({"links.example.test"})),
        ("https://unexpected.example/fixture", frozenset({"links.example.test"})),
        ("https://user@links.example.test/fixture", frozenset({"links.example.test"})),
        ("https://links.example.test:444/fixture", frozenset({"links.example.test"})),
        ("https://links.example.test/fixture", frozenset()),
    ],
)
def test_affiliate_link_validation_is_fail_closed(value: str, hosts: frozenset[str]) -> None:
    with pytest.raises(ValueError):
        validate_affiliate_link(value, allowed_hosts=hosts)


def test_profile_must_be_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    with pytest.raises(ValueError, match="outside"):
        ensure_profile_outside_workspace(workspace / "profile", workspace)

    external = ensure_profile_outside_workspace(tmp_path / "runtime" / "profile", workspace)
    assert external == (tmp_path / "runtime" / "profile").resolve()


def test_local_fixture_contract_rejects_non_loopback_http() -> None:
    with pytest.raises(ValueError, match="loopback"):
        GeneratorUiContract(
            version="unsafe-fixture",
            page_url="http://example.com/fake",
            product_input_selector="#product",
            generate_button_selector="#generate",
            result_selector="#result",
        )


def test_profile_lock_prevents_two_process_handles(tmp_path: Path) -> None:
    first = BrowserProfileLock(tmp_path / "profile")
    second = BrowserProfileLock(tmp_path / "profile")
    first.acquire()
    try:
        with pytest.raises(BrowserProfileInUse):
            second.acquire()
    finally:
        first.release()

    second.acquire()
    second.release()
