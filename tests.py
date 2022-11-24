import pytest

import secret_santa as santa


@pytest.mark.parametrize(
    "item, expected_class",
    [
        ("amazon/1234567890", santa.ScrapedItem),
        ("https://example.com", santa.LinkedItem),
        ("text", santa.PlainTextItem),
    ],
)
def test_parse_wishlist_items(item, expected_class):
    assert isinstance(santa.parse_wishlist_item(item), expected_class)


@pytest.mark.parametrize(
    "sale_price, list_price, expected",
    [
        (None, None, "PRODUCT"),
        (None, "10", "PRODUCT ($10.00)"),
        ("5", None, "PRODUCT ($5.00)"),
        ("5", "10", "PRODUCT (On sale for $5.00, usually $10.00!)"),
        ("10", "5", "PRODUCT (Be aware, selling for $10.00, usually $5.00.)"),
        ("5", "5", "PRODUCT ($5.00)"),
    ],
)
def test_scraper_details_prices_repr(sale_price, list_price, expected):
    scraper = santa.ScraperDetails("PRODUCT", sale_price, list_price)
    assert repr(scraper) == expected
