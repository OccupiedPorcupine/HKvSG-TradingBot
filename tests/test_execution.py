"""Unit tests for execution engine (Layer 7).

Tests position tracking, decision logging, priority queue, and order
manager logic without requiring API credentials or network access.
"""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from src.execution.position_tracker import Position, PositionTracker
from src.execution.decision_logger import DecisionLogger, DecisionEntry
from src.execution.priority_queue import (
    OrderPriorityQueue,
    PendingOrder,
    OrderPriority,
)
from src.execution.roostoo_client import PairInfo, OrderResult
from src.execution.order_manager import OrderManager, OrderManagerConfig


# =========================================================================
# Position Tests
# =========================================================================

class TestPosition:
    """Tests for the Position dataclass."""

    def test_initial_state(self) -> None:
        pos = Position(
            asset="BTC",
            quantity=1.0,
            cost_basis=80000.0,
            current_price=80000.0,
            peak_price_since_entry=80000.0,
        )
        assert pos.market_value == 80000.0
        assert pos.cost_value == 80000.0
        assert pos.unrealized_pnl == 0.0
        assert pos.unrealized_pnl_pct == 0.0

    def test_price_update(self) -> None:
        pos = Position(
            asset="BTC",
            quantity=1.0,
            cost_basis=80000.0,
            current_price=80000.0,
            peak_price_since_entry=80000.0,
        )
        pos.update_price(85000.0)
        assert pos.current_price == 85000.0
        assert pos.peak_price_since_entry == 85000.0
        assert pos.unrealized_pnl == 5000.0
        assert pos.unrealized_pnl_pct == pytest.approx(0.0625, rel=1e-4)

    def test_peak_price_only_increases(self) -> None:
        pos = Position(
            asset="ETH",
            quantity=10.0,
            cost_basis=3000.0,
            current_price=3500.0,
            peak_price_since_entry=3500.0,
        )
        pos.update_price(3200.0)
        assert pos.peak_price_since_entry == 3500.0
        assert pos.current_price == 3200.0

    def test_add_fill_updates_cost_basis(self) -> None:
        pos = Position(
            asset="ETH",
            quantity=10.0,
            cost_basis=3000.0,
            current_price=3000.0,
            peak_price_since_entry=3000.0,
        )
        # Buy 10 more at 3200
        pos.add_fill(10.0, 3200.0)
        assert pos.quantity == 20.0
        assert pos.cost_basis == pytest.approx(3100.0, rel=1e-4)

    def test_reduce_fill_returns_realized_pnl(self) -> None:
        pos = Position(
            asset="BTC",
            quantity=1.0,
            cost_basis=80000.0,
            current_price=85000.0,
            peak_price_since_entry=85000.0,
        )
        realized = pos.reduce_fill(0.5, 85000.0)
        assert realized == pytest.approx(2500.0, rel=1e-4)
        assert pos.quantity == pytest.approx(0.5, rel=1e-4)

    def test_distance_to_stop(self) -> None:
        pos = Position(
            asset="BTC",
            quantity=1.0,
            cost_basis=80000.0,
            current_price=84000.0,
            peak_price_since_entry=90000.0,
        )
        # Stop at 6% below peak = 90000 * 0.94 = 84600
        distance = pos.distance_to_stop(0.06)
        # current (84000) vs stop (84600): should be negative (below stop)
        assert distance < 0

    def test_to_dict(self) -> None:
        pos = Position(
            asset="BTC",
            quantity=1.0,
            cost_basis=80000.0,
            current_price=85000.0,
            peak_price_since_entry=85000.0,
        )
        pos._recalc_pnl()
        d = pos.to_dict()
        assert d["asset"] == "BTC"
        assert d["quantity"] == 1.0
        assert d["market_value"] == 85000.0


# =========================================================================
# Position Tracker Tests
# =========================================================================

class TestPositionTracker:
    """Tests for the PositionTracker."""

    def test_initial_state(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        assert tracker.nav == 1_000_000.0
        assert tracker.cash_balance == 1_000_000.0
        assert tracker.crypto_exposure == 0.0
        assert len(tracker.positions) == 0

    def test_buy_fill_creates_position(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0, commission_pct=0.0005)

        assert "BTC" in tracker.positions
        pos = tracker.positions["BTC"]
        assert pos.quantity == 1.0
        assert pos.cost_basis == 80000.0

        # Cash should be reduced by cost + commission
        expected_cash = 1_000_000.0 - (1.0 * 80000.0 * 1.0005)
        assert tracker.cash_balance == pytest.approx(expected_cash, rel=1e-6)

    def test_sell_fill_reduces_position(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("ETH", 10.0, 3000.0)
        tracker.on_sell_fill("ETH", 5.0, 3200.0)

        pos = tracker.positions["ETH"]
        assert pos.quantity == 5.0
        assert tracker.realized_pnl == pytest.approx(1000.0, rel=1e-4)

    def test_sell_fill_removes_position_at_zero(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)
        tracker.on_sell_fill("BTC", 1.0, 85000.0)

        assert "BTC" not in tracker.positions

    def test_nav_includes_positions(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)

        # NAV should be cash + position value
        assert tracker.nav == pytest.approx(1_000_000.0, rel=1e-4)

    def test_crypto_exposure(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)

        exposure = tracker.crypto_exposure
        assert 0.0 < exposure < 1.0

    def test_update_prices(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)
        tracker.update_prices({"BTC": 85000.0})

        pos = tracker.positions["BTC"]
        assert pos.current_price == 85000.0
        assert pos.peak_price_since_entry == 85000.0

    def test_get_weight(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)

        weight = tracker.get_weight("BTC")
        assert 0.0 < weight < 1.0

        assert tracker.get_weight("ETH") == 0.0

    def test_snapshot(self) -> None:
        tracker = PositionTracker(starting_capital=1_000_000.0)
        tracker.on_buy_fill("BTC", 1.0, 80000.0)

        snap = tracker.snapshot()
        assert "nav" in snap
        assert "positions" in snap
        assert "BTC" in snap["positions"]


# =========================================================================
# Decision Logger Tests
# =========================================================================

class TestDecisionLogger:
    """Tests for the DecisionLogger."""

    def test_log_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "trades.jsonl"
            dl = DecisionLogger(log_path=str(log_path), flush_threshold=1)

            dl.log_order(
                asset="BTC",
                action="NEW_ENTRY",
                trigger="MOMENTUM_SIGNAL",
                submitted_price=80000.0,
            )

            # Should have flushed (threshold=1)
            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 1

            entry = json.loads(lines[0])
            assert entry["asset"] == "BTC"
            assert entry["action"] == "NEW_ENTRY"
            assert entry["trigger"] == "MOMENTUM_SIGNAL"
            assert entry["order_type"] == "LIMIT"

    def test_buffered_flush(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "trades.jsonl"
            dl = DecisionLogger(
                log_path=str(log_path),
                flush_threshold=100,
                flush_interval_sec=3600,
            )

            # Log 5 entries — should stay buffered
            for i in range(5):
                dl.log_order(
                    asset=f"ASSET_{i}",
                    action="NEW_ENTRY",
                    trigger="TEST",
                )

            assert len(dl.buffer) == 5
            assert not log_path.exists()

            # Explicit flush
            dl._sync_flush()
            assert len(dl.buffer) == 0
            assert log_path.exists()
            lines = log_path.read_text().strip().split("\n")
            assert len(lines) == 5

    def test_close_flushes(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "trades.jsonl"
            dl = DecisionLogger(log_path=str(log_path), flush_threshold=100)

            dl.log_order(asset="BTC", action="EXIT", trigger="TRAILING_STOP")
            dl.close()

            assert log_path.exists()
            assert dl.total_logged == 1

    def test_suppressed_entry(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "trades.jsonl"
            dl = DecisionLogger(log_path=str(log_path), flush_threshold=1)

            dl.log_order(
                asset="DOGE",
                action="SUPPRESS",
                trigger="MOMENTUM_SIGNAL",
                suppressed=True,
                suppression_reason="BELOW_THRESHOLD",
            )

            lines = log_path.read_text().strip().split("\n")
            entry = json.loads(lines[0])
            assert entry["suppressed"] is True
            assert entry["suppression_reason"] == "BELOW_THRESHOLD"


# =========================================================================
# Priority Queue Tests
# =========================================================================

class TestOrderPriorityQueue:
    """Tests for the OrderPriorityQueue."""

    def _make_order(
        self,
        priority: OrderPriority,
        asset: str = "BTC",
    ) -> PendingOrder:
        return PendingOrder(
            priority=priority,
            asset=asset,
            pair=f"{asset}/USD",
            side="SELL" if priority == OrderPriority.CRITICAL_EXIT else "BUY",
            quantity_usd=10000.0,
            trigger="TEST",
        )

    def test_priority_ordering(self) -> None:
        queue = OrderPriorityQueue(max_orders=10)

        queue.add(self._make_order(OrderPriority.SIZE_ADJUSTMENT, "ADA"))
        queue.add(self._make_order(OrderPriority.CRITICAL_EXIT, "BTC"))
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ETH"))
        queue.add(self._make_order(OrderPriority.POSITION_REDUCTION, "SOL"))

        # Should come out in priority order
        first = queue.pop()
        assert first is not None
        assert first.priority == OrderPriority.CRITICAL_EXIT

        second = queue.pop()
        assert second is not None
        assert second.priority == OrderPriority.POSITION_REDUCTION

    def test_critical_exits_bypass_queue_limit(self) -> None:
        queue = OrderPriorityQueue(max_orders=2)

        # Fill the queue
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ETH"))
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "BNB"))

        # Non-critical should be rejected
        accepted = queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ADA"))
        assert not accepted

        # CRITICAL should always be accepted
        accepted = queue.add(
            self._make_order(OrderPriority.CRITICAL_EXIT, "BTC")
        )
        assert accepted
        assert queue.size == 3

    def test_has_critical(self) -> None:
        queue = OrderPriorityQueue()
        assert not queue.has_critical()

        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ETH"))
        assert not queue.has_critical()

        queue.add(self._make_order(OrderPriority.CRITICAL_EXIT, "BTC"))
        assert queue.has_critical()

    def test_remove_for_asset(self) -> None:
        queue = OrderPriorityQueue()
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ETH"))
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "BTC"))
        queue.add(self._make_order(OrderPriority.SIZE_ADJUSTMENT, "ETH"))

        removed = queue.remove_for_asset("ETH")
        assert removed == 2
        assert queue.size == 1

    def test_pop_batch(self) -> None:
        queue = OrderPriorityQueue()
        for asset in ["BTC", "ETH", "BNB", "ADA", "SOL"]:
            queue.add(self._make_order(OrderPriority.NEW_ENTRY, asset))

        batch = queue.pop_batch(3)
        assert len(batch) == 3
        assert queue.size == 2

    def test_clear(self) -> None:
        queue = OrderPriorityQueue()
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "BTC"))
        queue.add(self._make_order(OrderPriority.NEW_ENTRY, "ETH"))

        cleared = queue.clear()
        assert cleared == 2
        assert queue.is_empty


# =========================================================================
# PairInfo Tests
# =========================================================================

class TestPairInfo:
    """Tests for the PairInfo dataclass."""

    def test_pair_info_fields(self) -> None:
        info = PairInfo(
            pair="BTC/USD",
            coin="BTC",
            price_precision=2,
            amount_precision=5,
            min_order=1.0,
            can_trade=True,
        )
        assert info.pair == "BTC/USD"
        assert info.price_precision == 2
        assert info.amount_precision == 5


# =========================================================================
# OrderResult Tests
# =========================================================================

class TestOrderResult:
    """Tests for the OrderResult dataclass."""

    def test_filled_order(self) -> None:
        result = OrderResult(
            order_id=12345,
            status="FILLED",
            pair="BTC/USD",
            side="BUY",
            requested_price=80000.0,
            filled_price=80000.0,
            requested_quantity=0.1,
            filled_quantity=0.1,
            commission_pct=0.0005,
            role="MAKER",
            is_filled=True,
            raw={},
        )
        assert result.is_filled
        assert result.commission_pct == 0.0005

    def test_pending_order(self) -> None:
        result = OrderResult(
            order_id=12346,
            status="PENDING",
            pair="ETH/USD",
            side="BUY",
            requested_price=3000.0,
            filled_price=0.0,
            requested_quantity=1.0,
            filled_quantity=0.0,
            commission_pct=0.0,
            role="",
            is_filled=False,
            raw={},
        )
        assert not result.is_filled


# =========================================================================
# Order Manager Tests
# =========================================================================

class MockExecClient:
    """Mock ExecutionClient for OrderManager tests."""

    def __init__(self) -> None:
        self.placed_orders = []
        self.cancelled_orders = []
        self.queries = {}
        self.next_order_id = 1000

    async def place_limit_buy(self, pair, quantity, price) -> OrderResult:
        order_id = self.next_order_id
        self.next_order_id += 1
        result = OrderResult(
            order_id=order_id,
            status="PENDING",
            pair=pair,
            side="BUY",
            requested_price=price,
            filled_price=0.0,
            requested_quantity=quantity,
            filled_quantity=0.0,
            commission_pct=0.0,
            role="",
            is_filled=False,
            raw={},
        )
        self.placed_orders.append(result)
        return result

    async def place_limit_sell(self, pair, quantity, price) -> OrderResult:
        order_id = self.next_order_id
        self.next_order_id += 1
        result = OrderResult(
            order_id=order_id,
            status="PENDING",
            pair=pair,
            side="SELL",
            requested_price=price,
            filled_price=0.0,
            requested_quantity=quantity,
            filled_quantity=0.0,
            commission_pct=0.0,
            role="",
            is_filled=False,
            raw={},
        )
        self.placed_orders.append(result)
        return result

    async def place_market_sell(self, pair, quantity) -> OrderResult:
        order_id = self.next_order_id
        self.next_order_id += 1
        result = OrderResult(
            order_id=order_id,
            status="FILLED",
            pair=pair,
            side="SELL",
            requested_price=0.0,
            filled_price=50000.0,  # Mock price
            requested_quantity=quantity,
            filled_quantity=quantity,
            commission_pct=0.001,  # Market order commission
            role="TAKER",
            is_filled=True,
            raw={},
        )
        self.placed_orders.append(result)
        return result

    async def query_order(self, order_id) -> Optional[OrderResult]:
        return self.queries.get(order_id)

    async def cancel_order(self, order_id) -> bool:
        self.cancelled_orders.append(order_id)
        return True


class TestOrderManager:
    """Tests for the OrderManager."""

    @pytest.fixture
    def anyio_backend(self):
        return "asyncio"

    @pytest.fixture
    def setup_om(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            exec_client = MockExecClient()
            tracker = PositionTracker(1_000_000.0)
            logger = DecisionLogger(log_path=str(Path(tmpdir) / "trades.jsonl"))
            queue = OrderPriorityQueue()
            om = OrderManager(exec_client, tracker, logger, queue)
            # Disable delays for testing
            om.config.fill_check_delay_sec = 0
            om.config.order_timeout_sec = 60
            om.update_prices({"BTC/USD": 80000.0, "ETH/USD": 3000.0})
            yield om, exec_client, tracker, logger, queue

    @pytest.mark.anyio
    async def test_submit_order_from_queue(self, setup_om) -> None:
        om, exec_client, tracker, logger, queue = setup_om

        queue.add(PendingOrder(
            priority=OrderPriority.NEW_ENTRY,
            asset="BTC",
            pair="BTC/USD",
            side="BUY",
            quantity_usd=80000.0,
            trigger="TEST",
        ))

        results = await om.process_queue()
        assert len(exec_client.placed_orders) == 1
        assert len(om.active_orders) == 1

    @pytest.mark.anyio
    async def test_fill_processing(self, setup_om) -> None:
        om, exec_client, tracker, logger, queue = setup_om

        # Queue and submit
        queue.add(PendingOrder(
            priority=OrderPriority.NEW_ENTRY,
            asset="BTC",
            pair="BTC/USD",
            side="BUY",
            quantity_usd=80000.0,
            trigger="TEST",
        ))
        await om.process_queue()
        order_id = list(om.active_orders.keys())[0]

        # Mock a fill
        exec_client.queries[order_id] = OrderResult(
            order_id=order_id,
            status="FILLED",
            pair="BTC/USD",
            side="BUY",
            requested_price=80000.0,
            filled_price=80000.0,
            requested_quantity=1.0,
            filled_quantity=1.0,
            commission_pct=0.0005,
            role="MAKER",
            is_filled=True,
            raw={},
        )

        # Check active orders
        filled = await om.check_active_orders()
        assert len(filled) == 1
        assert len(om.active_orders) == 0
        assert "BTC" in tracker.positions
        assert tracker.positions["BTC"].quantity == 1.0

    @pytest.mark.anyio
    async def test_timeout_and_resubmission(self, setup_om) -> None:
        om, exec_client, tracker, logger, queue = setup_om
        om.config.order_timeout_sec = 0  # Force immediate timeout

        queue.add(PendingOrder(
            priority=OrderPriority.NEW_ENTRY,
            asset="ETH",
            pair="ETH/USD",
            side="BUY",
            quantity_usd=3000.0,
            trigger="TEST",
        ))
        await om.process_queue()
        order_id = list(om.active_orders.keys())[0]

        # No fill mock
        exec_client.queries[order_id] = OrderResult(
            order_id=order_id, status="PENDING", pair="ETH/USD", side="BUY",
            requested_price=3000.0, filled_price=0.0,
            requested_quantity=1.0, filled_quantity=0.0,
            commission_pct=0.0, role="", is_filled=False, raw={},
        )

        # Should cancel and resubmit to queue
        await om.check_active_orders()
        assert order_id in exec_client.cancelled_orders
        assert len(om.active_orders) == 0
        assert queue.size == 1
        assert queue.peek().resubmission_count == 1

    @pytest.mark.anyio
    async def test_emergency_market_escalation(self, setup_om) -> None:
        om, exec_client, tracker, logger, queue = setup_om
        om.config.order_timeout_sec = 0
        om.config.max_resubmissions = 1

        # CRITICAL risk exit
        pending = PendingOrder(
            priority=OrderPriority.CRITICAL_EXIT,
            asset="BTC",
            pair="BTC/USD",
            side="SELL",
            quantity_usd=80000.0,
            trigger="TRAILING_STOP",
            risk_event_severity="CRITICAL",
        )
        # Manually set resubmission count to trigger escalation on next timeout
        pending.resubmission_count = 1

        queue.add(pending)
        await om.process_queue()
        order_id = list(om.active_orders.keys())[0]

        exec_client.queries[order_id] = OrderResult(
            order_id=order_id, status="PENDING", pair="BTC/USD", side="SELL",
            requested_price=80000.0, filled_price=0.0,
            requested_quantity=1.0, filled_quantity=0.0,
            commission_pct=0.0, role="", is_filled=False, raw={},
        )

        # Trigger check_active_orders — should escalate to market
        filled = await om.check_active_orders()
        assert len(filled) == 1
        assert filled[0].status == "FILLED"
        assert any(o.side == "SELL" and o.requested_price == 0.0 for o in exec_client.placed_orders)

    @pytest.mark.anyio
    async def test_min_trade_threshold(self, setup_om) -> None:
        om, exec_client, tracker, logger, queue = setup_om
        om.config.min_trade_threshold_pct = 0.01  # 1% of $1M = $10,000

        # Small order should be suppressed
        queue.add(PendingOrder(
            priority=OrderPriority.NEW_ENTRY,
            asset="ETH",
            pair="ETH/USD",
            side="BUY",
            quantity_usd=5000.0,
            trigger="MOMENTUM",
        ))

        results = await om.process_queue()
        assert len(results) == 0
        assert len(exec_client.placed_orders) == 0
        assert len(om.active_orders) == 0

        # CRITICAL order should NOT be suppressed even if small
        queue.add(PendingOrder(
            priority=OrderPriority.CRITICAL_EXIT,
            asset="ETH",
            pair="ETH/USD",
            side="SELL",
            quantity_usd=5000.0,
            trigger="TRAILING_STOP",
        ))

        await om.process_queue()
        assert len(exec_client.placed_orders) == 1
