from promo_bot.domain.enums import LinkSource
from promo_bot.relay.parser import EntityUrl, extract_links


def test_extracts_multiple_links_from_all_message_surfaces() -> None:
    links = extract_links(
        "Veja https://www.amazon.com.br/dp/B0ABCDEFGH. e o botão",
        entity_urls=[
            EntityUrl(
                "https://produto.mercadolivre.com.br/MLB-123456",
                LinkSource.ENTITY_TEXT_URL,
                60,
            )
        ],
        button_urls=["https://www.kabum.com.br/produto/123"],
    )

    assert [link.source for link in links] == [
        LinkSource.TEXT,
        LinkSource.ENTITY_TEXT_URL,
        LinkSource.BUTTON,
    ]
    assert links[0].url.endswith("B0ABCDEFGH")
    assert [link.ordinal for link in links] == [0, 1, 2]


def test_entity_url_and_visible_url_are_deduplicated() -> None:
    url = "https://www.amazon.com.br/dp/B0ABCDEFGH"
    links = extract_links(
        url,
        entity_urls=[EntityUrl(url, LinkSource.ENTITY_URL, 0)],
    )

    assert len(links) == 1
    assert links[0].url == url


def test_message_without_text_can_use_multiple_buttons() -> None:
    links = extract_links(
        "",
        button_urls=[
            "https://s.shopee.com.br/example",
            "https://a.aliexpress.com/example",
        ],
    )

    assert len(links) == 2
    assert all(link.source is LinkSource.BUTTON for link in links)
