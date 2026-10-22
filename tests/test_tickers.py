"""TradingView -> Yahoo ticker mapping."""

import pytest

from investment_agent.engine.data.tickers import exchange_of, load_mapping, tv_to_yahoo


@pytest.fixture(scope="module")
def mapping():
    return load_mapping()


@pytest.mark.parametrize("tv, market, expected", [
    ("NASDAQ:AUPH", "america", "AUPH"),
    ("TSXV:AFM", "canada", "AFM.V"),
    ("OMXCOP:HLUN_A", "denmark", "HLUN-A.CO"),
    ("NYSE:BRK.B", "america", "BRK-B"),
    ("EURONEXT:NOS", "portugal", "NOS.LS"),
    ("EURONEXT:XXX", "norway", "XXX.OL"),
    ("XETR:KTN", "germany", "KTN.DE"),             # Xetra needs the .DE suffix (KTN alone is another company)
    ("OTC:VXRT", "america", None),
    ("FWB:XYZ", "germany", "XYZ.F"),
    ("ZSE:ABC", "croatia", None),
    ("LSE:BT.A", "uk", "BT-A.L"),
    ("EURONEXT:RYA", "ireland", "RYA.IR"),
    ("EURONEXT:OR", "spain", None),                # Euronext on a market without a known suffix
    ("NGM:PLEJD", "sweden", None),                 # different Yahoo listing
    ("NO_COLON", "america", None),
])
def test_mapping(mapping, tv, market, expected):
    assert tv_to_yahoo(tv, market, mapping) == expected


def test_exception_from_the_yaml(tmp_path):
    path = tmp_path / "mapping.yaml"
    path.write_text('exceptions: {"NYSE:ABC": "ABC-WI"}\nsuffixes: {NYSE: ""}\n', encoding="utf-8")
    mapping = load_mapping(path)
    assert tv_to_yahoo("NYSE:ABC", "america", mapping) == "ABC-WI"
    assert tv_to_yahoo("NYSE:DEF", "america", mapping) == "DEF"


def test_exchange_of():
    assert exchange_of("ZSE:ABC") == "ZSE"
    assert exchange_of("invalid") is None
