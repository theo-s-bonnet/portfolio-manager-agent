"""Exchange rates."""

import pytest

from investment_agent.engine.data.fx import eur_rates, to_eur


def fake_downloader(prices):
    calls = []

    def download(symbols):
        calls.append(list(symbols))
        return {s: prices[s] for s in symbols if s in prices}

    download.calls = calls
    return download


def test_market_cap_in_gbx():
    rates = eur_rates(["GBX"], fake_downloader({"GBPEUR=X": 1.15}))
    assert rates["GBX"] == pytest.approx(0.0115)
    assert to_eur(500e6, "GBX", rates) == pytest.approx(5.75e6)


def test_cad_rate_missing():
    rates = eur_rates(["CAD", "USD", "EUR"], fake_downloader({"USDEUR=X": 0.9}))
    assert "CAD" not in rates
    assert to_eur(1e9, "CAD", rates) is None          # -> R3.2 and R3.3 `missing`
    assert to_eur(1e9, "USD", rates) == pytest.approx(0.9e9)


def test_eur_without_network_call():
    download = fake_downloader({})
    assert eur_rates(["EUR"], download) == {"EUR": 1.0}
    assert download.calls == []


def test_gbp_and_gbx_one_symbol():
    download = fake_downloader({"GBPEUR=X": 1.2})
    rates = eur_rates(["GBP", "GBX", "GBp"], download)
    assert rates == pytest.approx({"GBP": 1.2, "GBX": 0.012, "GBp": 0.012})
    assert download.calls == [["GBPEUR=X"]]


@pytest.mark.parametrize("value", [0, -1.0, float("nan"), None])
def test_invalid_rate_treated_as_missing(value):
    assert "NOK" not in eur_rates(["NOK"], fake_downloader({"NOKEUR=X": value}))


def test_missing_amount():
    assert to_eur(float("nan"), "EUR", {"EUR": 1.0}) is None
    assert to_eur(None, "EUR", {"EUR": 1.0}) is None


@pytest.mark.network
def test_network_yahoo_rates():
    rates = eur_rates(["USD", "GBX"])
    assert 0.5 < rates["USD"] < 1.5
    assert rates["GBX"] == pytest.approx(eur_rates(["GBP"])["GBP"] / 100, rel=0.01)
