"""Tests du client Riot contre un faux serveur HTTP local (aiohttp.web)."""

from __future__ import annotations

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from bot.core.errors import ExternalServiceError, NotFoundError
from bot.services.riot_api import RankDTO, RiotAccountDTO, RiotClient

API_KEY = "RGAPI-test"
PUUID = "puuid-123"


class FakeRiot:
    """Faux serveur Riot configurable test par test."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.headers: list[str | None] = []
        self.account_status = 200
        self.league_entries: list[dict] = [
            {"queueType": "RANKED_FLEX_SR", "tier": "SILVER", "rank": "I", "leaguePoints": 10, "wins": 1, "losses": 1},
            {"queueType": "RANKED_SOLO_5x5", "tier": "GOLD", "rank": "II", "leaguePoints": 54, "wins": 30, "losses": 25},
        ]
        self.league_responses: list[tuple[int, dict[str, str]]] = []  # (status, headers) consommés en premier

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/riot/account/v1/accounts/by-riot-id/{name}/{tag}", self.by_riot_id)
        app.router.add_get("/riot/account/v1/accounts/by-puuid/{puuid}", self.by_puuid)
        app.router.add_get("/lol/league/v4/entries/by-puuid/{puuid}", self.league)
        return app

    def _record(self, request: web.Request) -> None:
        self.calls.append(request.path)
        self.headers.append(request.headers.get("X-Riot-Token"))

    async def by_riot_id(self, request: web.Request) -> web.Response:
        self._record(request)
        if self.account_status != 200:
            return web.json_response({"status": {"status_code": self.account_status}}, status=self.account_status)
        name, tag = request.match_info["name"], request.match_info["tag"]
        if name.lower() == "inconnu":
            return web.json_response({"status": {"status_code": 404}}, status=404)
        # Riot renvoie la casse officielle
        return web.json_response({"puuid": PUUID, "gameName": name.title(), "tagLine": tag.upper()})

    async def by_puuid(self, request: web.Request) -> web.Response:
        self._record(request)
        if request.match_info["puuid"] != PUUID:
            return web.json_response({}, status=404)
        return web.json_response({"puuid": PUUID, "gameName": "Nouveau Nom", "tagLine": "EUW"})

    async def league(self, request: web.Request) -> web.Response:
        self._record(request)
        if self.league_responses:
            status, headers = self.league_responses.pop(0)
            return web.json_response({}, status=status, headers=headers)
        return web.json_response(self.league_entries)


@pytest.fixture
async def fake():
    return FakeRiot()


@pytest.fixture
async def client(fake):
    server = TestServer(fake.app())
    await server.start_server()
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
        yield RiotClient(
            session,
            api_key=API_KEY,
            region="europe",
            platform="euw1",
            base_url=str(server.make_url("")),
            max_retry_wait=2,
        )
    await server.close()


def test_default_urls_use_riot_hosts():
    riot = RiotClient(None, api_key="k", region="europe", platform="euw1")  # type: ignore[arg-type]
    assert riot._url("europe", "/x") == "https://europe.api.riotgames.com/x"
    assert riot._url("euw1", "/y") == "https://euw1.api.riotgames.com/y"


async def test_enabled_depends_on_key():
    async with aiohttp.ClientSession() as session:
        assert RiotClient(session, api_key="k", region="europe", platform="euw1").enabled
        assert not RiotClient(session, api_key=None, region="europe", platform="euw1").enabled
        assert not RiotClient(session, api_key="  ", region="europe", platform="euw1").enabled


async def test_missing_key_raises_explicit_error():
    async with aiohttp.ClientSession() as session:
        riot = RiotClient(session, api_key=None, region="europe", platform="euw1")
        with pytest.raises(ExternalServiceError, match="RIOT_API_KEY"):
            await riot.get_account_by_riot_id("Pseudo", "EUW")


async def test_get_account_by_riot_id(client, fake):
    account = await client.get_account_by_riot_id("faker", "kr1")
    assert account == RiotAccountDTO(puuid=PUUID, game_name="Faker", tag_line="KR1")
    assert fake.headers == [API_KEY]


async def test_riot_id_is_url_encoded(client, fake):
    account = await client.get_account_by_riot_id("le roi", "#euw")
    assert account.game_name == "Le Roi"
    assert fake.calls == ["/riot/account/v1/accounts/by-riot-id/le roi/euw"]


async def test_unknown_account_raises_not_found(client):
    with pytest.raises(NotFoundError, match="Aucun compte Riot"):
        await client.get_account_by_riot_id("inconnu", "EUW")


async def test_get_account_by_puuid(client):
    account = await client.get_account_by_puuid(PUUID)
    assert account.riot_id == "Nouveau Nom#EUW"


@pytest.mark.parametrize("status", [401, 403])
async def test_invalid_key(client, fake, status, caplog):
    fake.account_status = status
    with caplog.at_level("ERROR", logger="bot.services.riot_api"):
        with pytest.raises(ExternalServiceError, match="invalide ou expirée"):
            await client.get_account_by_riot_id("Pseudo", "EUW")
    assert any(r.levelname == "ERROR" for r in caplog.records)


async def test_server_error(client, fake):
    fake.account_status = 503
    with pytest.raises(ExternalServiceError, match="serveurs de Riot"):
        await client.get_account_by_riot_id("Pseudo", "EUW")


async def test_network_error_is_wrapped():
    async with aiohttp.ClientSession() as session:
        riot = RiotClient(session, api_key="k", region="europe", platform="euw1", base_url="http://127.0.0.1:9")
        with pytest.raises(ExternalServiceError, match="Impossible de joindre"):
            await riot.get_account_by_puuid(PUUID)


async def test_solo_rank_keeps_solo_queue_only(client):
    rank = await client.get_solo_rank(PUUID)
    assert rank == RankDTO(tier="GOLD", division="II", league_points=54, wins=30, losses=25)
    assert rank.games == 55


async def test_unranked_returns_none(client, fake):
    fake.league_entries = [{"queueType": "RANKED_FLEX_SR", "tier": "GOLD", "rank": "I", "leaguePoints": 0,
                            "wins": 0, "losses": 0}]
    assert await client.get_solo_rank(PUUID) is None


async def test_master_has_no_division(client, fake):
    fake.league_entries = [{"queueType": "RANKED_SOLO_5x5", "tier": "MASTER", "rank": "I", "leaguePoints": 120,
                            "wins": 100, "losses": 90}]
    rank = await client.get_solo_rank(PUUID)
    assert rank is not None and rank.division is None and rank.league_points == 120


async def test_rank_cache(client, fake):
    await client.get_solo_rank(PUUID)
    await client.get_solo_rank(PUUID)
    assert len(fake.calls) == 1
    await client.get_solo_rank(PUUID, use_cache=False)
    assert len(fake.calls) == 2
    await client.get_solo_rank(PUUID, "na1")  # autre plateforme = autre entrée de cache
    assert len(fake.calls) == 3


async def test_rank_cache_expires(client, fake):
    client.rank_cache_ttl = 0
    await client.get_solo_rank(PUUID)
    await client.get_solo_rank(PUUID)
    assert len(fake.calls) == 2


async def test_rate_limit_retries_once(client, fake):
    fake.league_responses = [(429, {"Retry-After": "0"})]
    rank = await client.get_solo_rank(PUUID)
    assert rank is not None and rank.tier == "GOLD"
    assert len(fake.calls) == 2


async def test_rate_limit_gives_up_after_one_retry(client, fake):
    fake.league_responses = [(429, {"Retry-After": "0"}), (429, {"Retry-After": "0"})]
    with pytest.raises(ExternalServiceError, match="limite de requêtes"):
        await client.get_solo_rank(PUUID)
    assert len(fake.calls) == 2


async def test_rate_limit_with_long_wait_does_not_sleep(client, fake):
    fake.league_responses = [(429, {"Retry-After": "120"})]
    with pytest.raises(ExternalServiceError, match="120 seconde"):
        await client.get_solo_rank(PUUID)
    assert len(fake.calls) == 1
