import pytest

from bot.services.multigg import multisearch_url, multisearch_urls, opgg_region


@pytest.mark.parametrize(
    ("platform", "region"),
    [
        ("euw1", "euw"), ("EUN1", "eune"), ("na1", "na"), ("kr", "kr"), ("br1", "br"),
        ("jp1", "jp"), ("la1", "lan"), ("la2", "las"), ("oc1", "oce"), ("tr1", "tr"),
        ("ru", "ru"), ("xx9", "euw"), (None, "euw"), ("", "euw"),
    ],
)
def test_opgg_region(platform, region):
    assert opgg_region(platform) == region


def test_multisearch_url_encodes_riot_ids():
    url = multisearch_url(["Faker#KR1", "Le Blanc#EUW"], "euw1")
    assert url == "https://www.op.gg/multisearch/euw?summoners=Faker%23KR1,Le%20Blanc%23EUW"


def test_multisearch_url_unicode_and_dedup():
    url = multisearch_url(["Élo#FR1", "Élo#FR1", " "], "kr")
    assert url == "https://www.op.gg/multisearch/kr?summoners=%C3%89lo%23FR1"


def test_multisearch_url_empty():
    assert multisearch_url([], "euw1") is None


def test_multisearch_urls_chunks():
    ids = [f"P{i}#EUW" for i in range(23)]
    urls = multisearch_urls(ids, "euw1")
    assert len(urls) == 3
    assert urls[0].count(",") == 9
    assert urls[2].count(",") == 2
