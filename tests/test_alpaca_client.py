"""Tests for AlpacaBroker — credential resolution and __init__ refactor.

Since alpaca-py may not be installed in the test environment, we mock the
SDK classes so we can verify the credential/paper resolution logic without
making real API calls.
"""

import sys
import types
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Mock the alpaca SDK before importing our module
# ---------------------------------------------------------------------------

def _build_alpaca_mocks():
    """Create a minimal mock tree for the alpaca SDK."""
    alpaca = types.ModuleType("alpaca")
    trading = types.ModuleType("alpaca.trading")
    trading_client = types.ModuleType("alpaca.trading.client")
    trading_requests = types.ModuleType("alpaca.trading.requests")
    trading_enums = types.ModuleType("alpaca.trading.enums")
    data = types.ModuleType("alpaca.data")
    data_historical = types.ModuleType("alpaca.data.historical")
    data_requests = types.ModuleType("alpaca.data.requests")
    data_timeframe = types.ModuleType("alpaca.data.timeframe")
    data_enums = types.ModuleType("alpaca.data.enums")

    trading_client.TradingClient = MagicMock(name="TradingClient")
    trading_requests.GetOrdersRequest = MagicMock()
    trading_requests.GetPortfolioHistoryRequest = MagicMock()
    trading_requests.LimitOrderRequest = MagicMock()
    trading_requests.MarketOrderRequest = MagicMock()
    trading_enums.OrderSide = MagicMock()
    trading_enums.TimeInForce = MagicMock()
    data_historical.StockHistoricalDataClient = MagicMock(name="StockHistoricalDataClient")
    data_historical_option = types.ModuleType("alpaca.data.historical.option")
    data_historical_option.OptionHistoricalDataClient = MagicMock(
        name="OptionHistoricalDataClient"
    )
    data_requests.StockBarsRequest = MagicMock()
    data_requests.StockLatestQuoteRequest = MagicMock()
    data_requests.OptionChainRequest = MagicMock()
    data_timeframe.TimeFrame = MagicMock()
    data_enums.DataFeed = MagicMock()
    data_enums.DataFeed.IEX = "IEX"
    data_enums.OptionsFeed = MagicMock()
    data_enums.OptionsFeed.INDICATIVE = "INDICATIVE"

    modules = {
        "alpaca": alpaca,
        "alpaca.trading": trading,
        "alpaca.trading.client": trading_client,
        "alpaca.trading.requests": trading_requests,
        "alpaca.trading.enums": trading_enums,
        "alpaca.data": data,
        "alpaca.data.historical": data_historical,
        "alpaca.data.historical.option": data_historical_option,
        "alpaca.data.requests": data_requests,
        "alpaca.data.timeframe": data_timeframe,
        "alpaca.data.enums": data_enums,
    }
    return (
        modules,
        trading_client.TradingClient,
        data_historical.StockHistoricalDataClient,
        data_historical_option.OptionHistoricalDataClient,
    )


_mocks, MockTradingClient, MockDataClient, MockOptionHistoricalClient = _build_alpaca_mocks()


@pytest.fixture(autouse=True)
def _patch_alpaca_sdk():
    """Inject mock alpaca SDK modules for the duration of every test."""
    saved = {}
    for name, mod in _mocks.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # Force re-import so the module picks up our mocks
    for mod_name in list(sys.modules):
        if mod_name.startswith("src.brokers"):
            del sys.modules[mod_name]

    yield

    # Restore original modules
    for name, orig in saved.items():
        if orig is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = orig
    for mod_name in list(sys.modules):
        if mod_name.startswith("src.brokers"):
            del sys.modules[mod_name]


def _import_broker():
    """Import AlpacaBroker after mocks are in place."""
    from src.brokers.alpaca_client import AlpacaBroker
    return AlpacaBroker


# ---------------------------------------------------------------------------
# Tests: explicit credentials (multi-user mode)
# ---------------------------------------------------------------------------

class TestExplicitCredentials:

    def test_explicit_key_secret_paper(self):
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker(api_key="my_key", secret="my_secret", paper=True)
        assert broker.paper is True
        MockTradingClient.assert_called_with("my_key", "my_secret", paper=True)

    def test_explicit_key_secret_live(self):
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker(api_key="live_key", secret="live_secret", paper=False)
        assert broker.paper is False
        MockTradingClient.assert_called_with("live_key", "live_secret", paper=False)

    def test_explicit_credentials_ignore_env(self, monkeypatch):
        monkeypatch.setenv("APCA_API_KEY_ID", "env_key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "env_secret")
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker(api_key="explicit_key", secret="explicit_secret", paper=True)
        MockTradingClient.assert_called_with("explicit_key", "explicit_secret", paper=True)

    def test_explicit_paper_overrides_config(self):
        AlpacaBroker = _import_broker()
        config = {"broker": {"paper": True}}
        broker = AlpacaBroker(config=config, api_key="k", secret="s", paper=False)
        assert broker.paper is False

    def test_explicit_with_config_overrides(self):
        AlpacaBroker = _import_broker()
        config = {"broker": {"data_feed": "sip", "api_retry_times": 5}}
        broker = AlpacaBroker(config=config, api_key="k", secret="s", paper=True)
        assert broker._retry_times == 5


# ---------------------------------------------------------------------------
# Tests: legacy env var resolution (backward compat)
# ---------------------------------------------------------------------------

class TestLegacyEnvCredentials:

    def test_paper_from_env(self, monkeypatch):
        monkeypatch.setenv("APCA_API_KEY_ID", "paper_key")
        monkeypatch.setenv("APCA_API_SECRET_KEY", "paper_secret")
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker()
        assert broker.paper is True
        MockTradingClient.assert_called_with("paper_key", "paper_secret", paper=True)

    def test_missing_credentials_raises(self, monkeypatch):
        monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
        monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_API_KEY_ID", raising=False)
        monkeypatch.delenv("ALPACA_LIVE_API_SECRET_KEY", raising=False)
        AlpacaBroker = _import_broker()
        with pytest.raises(ValueError, match="Alpaca credentials required"):
            AlpacaBroker()

    def test_paper_env_override(self, monkeypatch):
        monkeypatch.setenv("APCA_PAPER", "false")
        monkeypatch.setenv("ALPACA_LIVE_API_KEY_ID", "live_k")
        monkeypatch.setenv("ALPACA_LIVE_API_SECRET_KEY", "live_s")
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker()
        assert broker.paper is False

    def test_config_paper_default(self, monkeypatch):
        monkeypatch.delenv("APCA_PAPER", raising=False)
        monkeypatch.delenv("ALPACA_LIVE", raising=False)
        monkeypatch.setenv("ALPACA_LIVE_API_KEY_ID", "lk")
        monkeypatch.setenv("ALPACA_LIVE_API_SECRET_KEY", "ls")
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker(config={"broker": {"paper": False}})
        assert broker.paper is False

    def test_live_env_resolved_creds_passed_to_option_client(self, monkeypatch):
        """OptionHistoricalDataClient must use resolved_key/secret, not ctor args (often None)."""
        monkeypatch.delenv("APCA_PAPER", raising=False)
        monkeypatch.delenv("ALPACA_LIVE", raising=False)
        monkeypatch.setenv("ALPACA_LIVE_API_KEY_ID", "live_k")
        monkeypatch.setenv("ALPACA_LIVE_API_SECRET_KEY", "live_s")
        MockOptionHistoricalClient.reset_mock()
        AlpacaBroker = _import_broker()
        AlpacaBroker(config={"broker": {"paper": False}})
        MockOptionHistoricalClient.assert_called_once_with("live_k", "live_s")


# ---------------------------------------------------------------------------
# Tests: mixed — explicit partial args should still fail
# ---------------------------------------------------------------------------

class TestEdgeCases:

    def test_explicit_key_without_secret_falls_back_to_env(self, monkeypatch):
        """api_key alone is not enough — both must be provided for explicit mode."""
        monkeypatch.delenv("APCA_API_KEY_ID", raising=False)
        monkeypatch.delenv("APCA_API_SECRET_KEY", raising=False)
        AlpacaBroker = _import_broker()
        # api_key given but secret is None → falls to env path → env not set → raises
        with pytest.raises(ValueError, match="Alpaca credentials required"):
            AlpacaBroker(api_key="only_key")

    def test_no_config_defaults(self):
        AlpacaBroker = _import_broker()
        broker = AlpacaBroker(api_key="k", secret="s", paper=True)
        assert broker.config == {}
        assert broker._retry_times == 3
        assert broker._retry_delay_sec == 3.0
