"""Unit tests for risk_management module."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

import pytest

from polymarket_autopilot.db import Database, PaperTrade
from polymarket_autopilot.risk_management import (
    PositionMonitor,
    RiskManagementConfig,
)


class TestRiskManagementConfig:
    """Test risk management configuration."""
    
    def test_default_thresholds(self) -> None:
        """Test default stop-loss and take-profit thresholds."""
        config = RiskManagementConfig()
        assert config.stop_loss_pct == 0.10
        assert config.take_profit_pct == 0.20
    
    def test_custom_thresholds(self) -> None:
        """Test custom thresholds."""
        config = RiskManagementConfig(stop_loss_pct=0.05, take_profit_pct=0.15)
        assert config.stop_loss_pct == 0.05
        assert config.take_profit_pct == 0.15
    
    def test_calculate_stop_loss_price_yes(self) -> None:
        """Test stop-loss price calculation for YES position."""
        config = RiskManagementConfig(stop_loss_pct=0.10)
        entry_price = 0.50
        stop_loss = config.calculate_stop_loss_price(entry_price, "YES")
        assert stop_loss == 0.45  # 0.50 * (1 - 0.10)
    
    def test_calculate_stop_loss_price_no(self) -> None:
        """Test stop-loss price calculation for NO position."""
        config = RiskManagementConfig(stop_loss_pct=0.10)
        entry_price = 0.30
        stop_loss = config.calculate_stop_loss_price(entry_price, "NO")
        assert stop_loss == 0.33  # 0.30 * (1 + 0.10)
    
    def test_calculate_take_profit_price_yes(self) -> None:
        """Test take-profit price calculation for YES position."""
        config = RiskManagementConfig(take_profit_pct=0.20)
        entry_price = 0.50
        take_profit = config.calculate_take_profit_price(entry_price, "YES")
        assert take_profit == 0.60  # 0.50 * (1 + 0.20)
    
    def test_calculate_take_profit_price_no(self) -> None:
        """Test take-profit price calculation for NO position."""
        config = RiskManagementConfig(take_profit_pct=0.20)
        entry_price = 0.40
        take_profit = config.calculate_take_profit_price(entry_price, "NO")
        assert abs(take_profit - 0.32) < 1e-10  # 0.40 * (1 - 0.20)


class TestPositionMonitor:
    """Test position monitoring and execution."""
    
    @pytest.fixture
    def mock_db(self) -> Mock:
        """Create a mock database."""
        db = Mock(spec=Database)
        return db
    
    @pytest.fixture
    def mock_client(self) -> Mock:
        """Create a mock API client."""
        client = Mock()
        return client
    
    @pytest.fixture
    def sample_trade(self) -> PaperTrade:
        """Create a sample open trade."""
        return PaperTrade(
            id=1,
            condition_id="0x1234",
            question="Will BTC reach $100k?",
            outcome="YES",
            strategy="MOMENTUM",
            shares=100.0,
            entry_price=0.50,
            exit_price=None,
            take_profit=0.60,
            stop_loss=0.45,
            status="open",
            pnl=None,
            opened_at=datetime.now(timezone.utc),
            closed_at=None,
        )
    
    def test_no_open_positions(self, mock_db: Mock, mock_client: Mock) -> None:
        """Test monitoring with no open positions."""
        mock_db.get_open_trades.return_value = []
        
        monitor = PositionMonitor(mock_db, mock_client)
        results = monitor.check_positions()
        
        assert results == {"stop_loss": 0, "take_profit": 0, "unchanged": 0}
    
    def test_stop_loss_triggered_yes_position(
        self, mock_db: Mock, mock_client: Mock, sample_trade: PaperTrade
    ) -> None:
        """Test stop-loss trigger for YES position."""
        mock_db.get_open_trades.return_value = [sample_trade]
        # Current price dropped 15% (0.50 -> 0.425), below -10% threshold
        mock_client.get_market.return_value = Mock(yes_price=0.425, no_price=0.575)
        
        config = RiskManagementConfig(stop_loss_pct=0.10)
        monitor = PositionMonitor(mock_db, mock_client, config)
        results = monitor.check_positions()
        
        assert results["stop_loss"] == 1
        assert results["take_profit"] == 0
        assert results["unchanged"] == 0
        
        # Verify close_trade was called
        mock_db.close_trade.assert_called_once()
        call_args = mock_db.close_trade.call_args
        assert call_args[1]["trade_id"] == 1
        assert call_args[1]["exit_price"] == 0.425
        assert call_args[1]["status"] == "closed_sl"
    
    def test_take_profit_triggered_yes_position(
        self, mock_db: Mock, mock_client: Mock, sample_trade: PaperTrade
    ) -> None:
        """Test take-profit trigger for YES position."""
        mock_db.get_open_trades.return_value = [sample_trade]
        # Current price rose 25% (0.50 -> 0.625), above +20% threshold
        mock_client.get_market.return_value = Mock(yes_price=0.625, no_price=0.375)
        
        config = RiskManagementConfig(take_profit_pct=0.20)
        monitor = PositionMonitor(mock_db, mock_client, config)
        results = monitor.check_positions()
        
        assert results["stop_loss"] == 0
        assert results["take_profit"] == 1
        assert results["unchanged"] == 0
        
        # Verify close_trade was called
        mock_db.close_trade.assert_called_once()
        call_args = mock_db.close_trade.call_args
        assert call_args[1]["trade_id"] == 1
        assert call_args[1]["exit_price"] == 0.625
        assert call_args[1]["status"] == "closed_tp"
    
    def test_position_unchanged(
        self, mock_db: Mock, mock_client: Mock, sample_trade: PaperTrade
    ) -> None:
        """Test position within thresholds remains open."""
        mock_db.get_open_trades.return_value = [sample_trade]
        # Current price moved 5% (0.50 -> 0.525), within thresholds
        mock_client.get_market.return_value = Mock(yes_price=0.525, no_price=0.475)
        
        config = RiskManagementConfig(stop_loss_pct=0.10, take_profit_pct=0.20)
        monitor = PositionMonitor(mock_db, mock_client, config)
        results = monitor.check_positions()
        
        assert results["stop_loss"] == 0
        assert results["take_profit"] == 0
        assert results["unchanged"] == 1
        
        # Verify close_trade was NOT called
        mock_db.close_trade.assert_not_called()
    
    def test_multiple_positions_mixed_results(
        self, mock_db: Mock, mock_client: Mock
    ) -> None:
        """Test monitoring multiple positions with different outcomes."""
        # Trade 1: Stop-loss
        trade1 = PaperTrade(
            id=1,
            condition_id="0x1234",
            question="Market 1",
            outcome="YES",
            strategy="MOMENTUM",
            shares=100.0,
            entry_price=0.50,
            exit_price=None,
            take_profit=0.60,
            stop_loss=0.45,
            status="open",
            pnl=None,
            opened_at=datetime.now(timezone.utc),
            closed_at=None,
        )
        
        # Trade 2: Take-profit
        trade2 = PaperTrade(
            id=2,
            condition_id="0x5678",
            question="Market 2",
            outcome="YES",
            strategy="TAIL",
            shares=50.0,
            entry_price=0.30,
            exit_price=None,
            take_profit=0.36,
            stop_loss=0.27,
            status="open",
            pnl=None,
            opened_at=datetime.now(timezone.utc),
            closed_at=None,
        )
        
        # Trade 3: Unchanged
        trade3 = PaperTrade(
            id=3,
            condition_id="0x9abc",
            question="Market 3",
            outcome="NO",
            strategy="VOLATILITY",
            shares=75.0,
            entry_price=0.40,
            exit_price=None,
            take_profit=0.32,
            stop_loss=0.44,
            status="open",
            pnl=None,
            opened_at=datetime.now(timezone.utc),
            closed_at=None,
        )
        
        mock_db.get_open_trades.return_value = [trade1, trade2, trade3]
        
        def get_market_side_effect(condition_id: str) -> Mock:
            if condition_id == "0x1234":
                return Mock(yes_price=0.425, no_price=0.575)  # -15% loss
            elif condition_id == "0x5678":
                return Mock(yes_price=0.375, no_price=0.625)  # +25% profit
            else:
                return {"yes_price": 0.45, "no_price": 0.41}  # +2.5% (unchanged)
        
        mock_client.get_market.side_effect = get_market_side_effect
        
        config = RiskManagementConfig(stop_loss_pct=0.10, take_profit_pct=0.20)
        monitor = PositionMonitor(mock_db, mock_client, config)
        results = monitor.check_positions()
        
        assert results["stop_loss"] == 1
        assert results["take_profit"] == 1
        assert results["unchanged"] == 1
        
        # Verify close_trade was called twice
        assert mock_db.close_trade.call_count == 2
    
    def test_api_error_handling(
        self, mock_db: Mock, mock_client: Mock, sample_trade: PaperTrade
    ) -> None:
        """Test graceful handling of API errors."""
        mock_db.get_open_trades.return_value = [sample_trade]
        mock_client.get_market.side_effect = Exception("API timeout")
        
        monitor = PositionMonitor(mock_db, mock_client)
        results = monitor.check_positions()
        
        # Position should remain unchanged on error
        assert results["unchanged"] == 1
        mock_db.close_trade.assert_not_called()
