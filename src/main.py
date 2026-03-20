"""APEX — Autonomous Portfolio Execution Agent.

Main entry point. Handles startup, orchestration of all layers,
and the main event loop.
"""

import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

from apex.core.config import Config
from src.data.api_client import RoostooClient
from src.data.binance_client import BinancePriceClient
from src.data.ingestion import DataIngestionManager
from src.data.features import FeatureEngine
from src.regime.detector import RegimeDetector
from src.regime.regime_state import RegimeState
from src.regime.contagion import ContagionResult
from src.signals.momentum import MomentumSignal
from src.portfolio.factory import create_portfolio_constructor, get_current_weights, build_orders_from_weights
from src.risk.factory import create_risk_manager
from src.execution.roostoo_client import ExecutionClient
from src.execution.position_tracker import PositionTracker
from src.execution.decision_logger import DecisionLogger
from src.execution.priority_queue import OrderPriorityQueue, OrderPriority, PendingOrder
from src.execution.order_manager import OrderManager, OrderManagerConfig

from src.orchestration.startup import run_preflight_checks, PreFlightCheckError
from src.orchestration.scheduler import Scheduler
from src.orchestration.safe_state import SystemState

from src.adaptation.signal_health import SignalHealthMonitor, RebalanceOutcome
from src.adaptation.performance_log import PerformanceLogger
from src.portfolio.beta_monitor import BetaMonitor
from src.regime.contagion import ContagionProbe
from apex.monitoring.status_writer import write_status

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
def setup_logging(config: Config):
    log_cfg = config.get("logging", {})
    log_dir = Path(log_cfg.get("directory", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)

    log_file = log_dir / "apex.log"
    fmt = log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    level = getattr(logging, log_cfg.get("level", "INFO"))

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(logging.FileHandler(log_file))
    root.addHandler(logging.StreamHandler(sys.stdout))
    for handler in root.handlers:
        handler.setFormatter(logging.Formatter(fmt))

    return logging.getLogger("apex")

logger = logging.getLogger("apex")


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
async def main():
    # 1. Load basic config and setup logging
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config.yaml"
    
    try:
        # 2. Pre-flight checks (gated startup)
        raw_config_dict = await run_preflight_checks(config_path)
        config = Config(config_path)
        setup_logging(config)
        logger.info("Pre-flight checks passed. Initializing layers...")
        
    except PreFlightCheckError as e:
        logger.critical("STARTUP_FAILED: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.exception("UNEXPECTED_STARTUP_ERROR: %s", e)
        sys.exit(1)

    # 3. Initialize Shared State & Infrastructure
    system_state = SystemState(
        max_consecutive_failures=config.get("orchestration.max_consecutive_failures", 3)
    )
    
    # API Clients
    base_client = RoostooClient(
        base_url=config.base_url,
        api_key_env=config.get("api.key_env_var", "ROOSTOO_API_KEY"),
        api_secret_env=config.get("api.secret_env_var", "ROOSTOO_API_SECRET")
    )
    exec_client = ExecutionClient(base_client)

    # Load exchange info into exec_client (required before placing any orders).
    # SAFETY: API failure here crashes startup before recovery logic runs (E-03).
    for _attempt in range(3):
        try:
            await exec_client.load_exchange_info()
            break
        except Exception as _exc:
            if _attempt == 2:
                logger.critical("load_exchange_info() failed after 3 attempts: %s", _exc)
                sys.exit(1)
            _backoff = 2 ** _attempt
            logger.warning("load_exchange_info() attempt %d failed: %s — retrying in %ds", _attempt + 1, _exc, _backoff)
            await asyncio.sleep(_backoff)

    # Decision Logger
    decision_logger = DecisionLogger(
        log_path=config.get("logging.trade_log", "logs/trades.jsonl")
    )

    # Initialize API Clients for Data
    binance_client = BinancePriceClient()
    ingestion = DataIngestionManager(config.raw, base_client, binance_client=binance_client)
    await ingestion.initialize()

    # --- FRESH START LOGIC: Sync Balances and Calculate True Baseline NAV ---
    logger.info("Fetching true exchange balances to establish fresh baseline NAV...")
    position_tracker = PositionTracker(starting_capital=0.0)
    
    for _attempt in range(3):
        try:
            startup_balances = await exec_client.get_balance()
            position_tracker.sync_from_exchange(startup_balances)
            break
        except Exception as e:
            if _attempt == 2:
                logger.critical("Failed to fetch startup balances: %s", e)
                sys.exit(1)
            await asyncio.sleep(2 ** _attempt)

    # Force a price fetch to evaluate exact market value of any held crypto
    await ingestion.fetch_prices()
    initial_prices = {
        config.pair_for(a): ingestion.get_latest_price(a)
        for a in ingestion.get_all_assets()
        if ingestion.get_latest_price(a) is not None
    }
    position_tracker.update_prices(initial_prices)

    # Force a price fetch so we can evaluate the exact market value of any held crypto
    await ingestion.fetch_prices()
    initial_prices = {
        config.pair_for(a): ingestion.get_latest_price(a)
        for a in ingestion.get_all_assets()
        if ingestion.get_latest_price(a) is not None
    }
    position_tracker.update_prices(initial_prices)
    
    # Ground cost basis for existing assets
    for asset, pos in position_tracker.positions.items():
        if pos.cost_basis <= 0.0:
            curr_price = ingestion.get_latest_price(asset)
            if curr_price and curr_price > 0:
                pos.cost_basis = curr_price
                pos.peak_price_since_entry = curr_price
    
    # Establish the true baseline NAV for 0% daily profit/loss
    starting_nav = position_tracker.nav
    position_tracker.starting_capital = starting_nav
    logger.info("Bot starting fresh. Initial Baseline NAV strictly set to: $%.2f", starting_nav)
    
    feature_engine = FeatureEngine(config.raw, ingestion)
    
    # Regime Detection
    regime_detector = RegimeDetector(config.raw)
    
    # Risk Management
    risk_manager = create_risk_manager(config, starting_nav=starting_nav, phase=1)
    
    # Signal Generation
    tier_1_3 = set(config.get("universe.tier_1_majors", [])) | \
               set(config.get("universe.tier_2_large_alts", [])) | \
               set(config.get("universe.tier_3_defi", []))
    momentum_signal = MomentumSignal(config.raw, tier_1_3)
    
    # Portfolio Construction
    portfolio_constructor = create_portfolio_constructor(config, phase=1)
    
    # Adaptation Layer (Layer 8) — must be instantiated BEFORE OrderManager
    # which takes signal_health_monitor as a constructor arg (E-01).
    signal_health_monitor = SignalHealthMonitor()
    performance_logger = PerformanceLogger(
        log_path=config.get("logging.snapshots_log", "logs/snapshots.jsonl"),
        daily_returns_path=config.get("logging.daily_returns_log", "logs/daily_returns.jsonl"),
        competition_start_nav=starting_nav,
    )
    beta_monitor = BetaMonitor(
        target_bull_min=config.get("portfolio.beta_targeting.target_trend_bull_min", 0.40),
        target_bull_max=config.get("portfolio.beta_targeting.target_trend_bull_max", 0.60),
        target_other_max=config.get("portfolio.beta_targeting.target_other_max", 0.30),
        enabled=config.get("portfolio.beta_targeting.enabled", False),
    )

    # Contagion probe — used in regime_update_tick and risk_check_tick (W-01).
    contagion_probe = ContagionProbe(
        return_window=config.get("regime.contagion_return_window_min", 5)
    )

    # Execution Engine
    order_queue = OrderPriorityQueue()
    order_manager = OrderManager(
        exec_client=exec_client,
        position_tracker=position_tracker,
        decision_logger=decision_logger,
        order_queue=order_queue,
        config=OrderManagerConfig(
            order_timeout_sec=config.get("execution.order_timeout_sec", 60),
            max_resubmissions=config.get("execution.max_resubmissions", 3),
            risk_exit_acceleration_threshold=config.get(
                "execution.risk_exit_acceleration_threshold", 0.02
            ),  # C-04: was not passed from config
            max_simultaneous_orders=config.get(
                "execution.max_simultaneous_orders", 15
            ),  # C-05: was not passed from config
            fill_check_delay_sec=config.get(
                "execution.fill_check_delay_sec", 5
            ),  # C-06: was not passed from config
        ),
        signal_health_monitor=signal_health_monitor,
    )

    # 4. Job Definitions

    # Debug tick config
    debug_tick_enabled = config.get("debug_tick.enabled", False)
    debug_watch = config.get("debug_tick.watch_assets", [])

    async def data_ingestion_tick():
        """Every 60s: Fetch prices, update features, run risk checks."""
        # 1. Ingest
        fetch_ok = await ingestion.fetch_prices()

        # 2. Features — skip on failed fetch to avoid pushing a stale bar (E-10)
        if not fetch_ok:
            logger.warning("fetch_prices() failed — skipping on_new_bar to avoid stale bar")
            return
        feature_engine.on_new_bar()

        # 3. Debug heartbeat (disable before competition)
        if debug_tick_enabled and debug_watch:
            parts = []
            for asset in debug_watch:
                price = ingestion.get_latest_price(asset)
                parts.append(f"{asset}={price:.4f}" if price else f"{asset}=N/A")
            logger.info("TICK [%d assets] %s", len(ingestion.get_all_assets()), "  ".join(parts))

        # 4. Portfolio snapshot
        snap = position_tracker.snapshot()
        # E-08: daily_pnl_pct is not in position_tracker.snapshot(); read from risk_manager.
        _daily_pnl_pct = risk_manager.get_daily_pnl_pct(snap["nav"])
        logger.info(
            "PORTFOLIO  NAV=$%.2f  cash=$%.2f  exposure=%.1f%%  positions=%d  daily_pnl=%.2f%%",
            snap["nav"],
            snap["nav"] * (1 - snap["crypto_exposure"]),
            snap["crypto_exposure"] * 100,
            snap["position_count"],
            _daily_pnl_pct * 100,
        )

        # 5. Update Order Manager Prices
        prices = {config.pair_for(a): ingestion.get_latest_price(a)
                  for a in ingestion.get_all_assets()
                  if ingestion.get_latest_price(a) is not None}
        order_manager.update_prices(prices)
        position_tracker.update_prices({a: ingestion.get_latest_price(a) 
                                       for a in ingestion.get_all_assets() 
                                       if ingestion.get_latest_price(a) is not None})

    async def risk_check_tick():
        """Every 60s: Mark-to-market and check trailing stops."""
        current_prices = {a: ingestion.get_latest_price(a)
                          for a in ingestion.get_all_assets()
                          if ingestion.get_latest_price(a) is not None}

        # Compute live contagion ratio from held positions (W-01/E-09/W-05).
        contagion_result = contagion_probe.compute(
            position_tracker.positions,
            feature_engine.get_return,
        )

        # E-08: compute actual daily P&L so the daily-loss circuit breaker fires.
        daily_pnl_pct = risk_manager.get_daily_pnl_pct(position_tracker.nav)

        # Run 1-min risk checks
        risk_events = risk_manager.tick(
            current_prices=current_prices,
            tracker=position_tracker,
            contagion_ratio=contagion_result.contagion_ratio,  # W-01: was hardcoded 0.0
            avg_loss=contagion_result.avg_loss,
            daily_pnl_pct=daily_pnl_pct,  # E-08: was always 0.0
        )
        
        # Convert RiskEvents to Orders

        for event in risk_events:
            asset = event.asset
            pos = position_tracker.get_position(asset)
            if not pos:
                continue
                
            qty_usd = pos.market_value
            order = PendingOrder(
                asset=asset,
                pair=config.pair_for(asset),
                side="SELL",
                quantity_usd=qty_usd,
                # SAFETY: Severity is a str-Enum; compare by value not integer.
                # HIGH_EXIT does not exist in OrderPriority — use POSITION_REDUCTION.
                priority=OrderPriority.CRITICAL_EXIT if event.severity.value == "CRITICAL" else OrderPriority.POSITION_REDUCTION,
                trigger=f"RISK_{event.event_type.name}",  # SAFETY: field is event_type, not type
                target_weight=0.0
            )
            order_queue.add(order)
            
        # End-game sell-all check (runs every 60s to catch T-1h threshold)
        _, _, sell_all_now = portfolio_constructor.get_endgame_cap()
        if sell_all_now:
            for asset, pos in position_tracker.positions.items():
                order = PendingOrder(
                    asset=asset,
                    pair=config.pair_for(asset),
                    side="SELL",
                    quantity_usd=pos.market_value,
                    priority=OrderPriority.CRITICAL_EXIT,
                    trigger="ENDGAME_SELL_ALL",
                    target_weight=0.0,
                )
                order_queue.add(order)
            logger.critical("ENDGAME SELL ALL triggered — queued sell orders for all positions")

        # Process risk exits immediately
        if not order_queue.is_empty:
            await order_manager.process_queue()

        # Also check status of pending orders
        await order_manager.check_active_orders()

    async def regime_update_tick():
        """Every 5m: Update market regime."""
        if feature_engine._regime_inputs is None:
            logger.debug("Regime update skipped — waiting for first price bar.")
            return
        # W-01/W-05: detector now computes contagion internally
        regime_detector.update(
            feature_engine.get_regime_inputs(),
            position_tracker.positions,
            feature_engine.get_return,
        )
        logger.debug("Regime updated: %s (contagion=%.2f)", regime_detector.state.current_regime, regime_detector.state.contagion_proxy)

    async def rebalance_tick():
        """Every 60m: Re-rank signals and rebalance portfolio."""
        if not system_state.can_rebalance:
            logger.warning("Rebalance skipped due to system safe-mode or stale data.")
            return
        if feature_engine._regime_inputs is None:
            # Re-try on_new_bar if regime inputs are missing but buffers are full
            if ingestion.get_bar_count("BTC") > 0:
                logger.info("Regime inputs missing but data present; forcing feature recomputation.")
                feature_engine.on_new_bar()
            
            if feature_engine._regime_inputs is None:
                logger.info("Rebalance skipped — waiting for first price bar.")
                return

        # 1. Signals
        scores = feature_engine.get_momentum_scores()
        selections = momentum_signal.generate(
            momentum_scores=scores,
            regime=regime_detector.state,
            get_ema_fn=feature_engine.get_ema_values,
            get_vol_fn=feature_engine.get_asset_volatility,
            sentiment_scores=feature_engine.get_sentiment_scores(),
        )

        # Update order manager context so every order this cycle logs the correct
        # regime and momentum score (audit Section 5 Screen 1 compliance).
        order_manager.update_context(
            regime_state=regime_detector.state.current_regime.value,
            momentum_scores=scores,
        )
        
        # 2. Portfolio Construction
        regime_inputs = feature_engine.get_regime_inputs()
        current_weights = get_current_weights(position_tracker)
        target_weights, construct_events = portfolio_constructor.compute(
            regime=regime_detector.state.current_regime.value,
            btc_vol_percentile=regime_inputs.btc_vol_percentile or 50.0,
            selected_assets=list(selections.keys()),
            current_weights=current_weights,
            current_nav=position_tracker.nav,
        )

        # 3. Pre-trade Risk Checks
        max_deployment = portfolio_constructor.get_deployment_target(
            regime_detector.state.current_regime.value,
            regime_inputs.btc_vol_percentile or 50.0,
        )
        final_weights, risk_events = risk_manager.pre_trade_check(
            target_weights, max_deployment
        )
        
        ## 4. Queue Orders
        # (Convert weight diffs to USD orders via factory bridge)
        pending_orders = build_orders_from_weights(
            target_weights=final_weights,
            current_weights=current_weights,
            nav=position_tracker.nav,
            pair_suffix=config.get("universe.pair_suffix", "/USD")
        )
        
        for order in pending_orders:
            # Re-apply global min_trade_threshold just in case
            if order.quantity_usd >= (position_tracker.nav * config.get("portfolio.min_trade_threshold_pct_nav", 0.002)):
                order_queue.add(order)
            
        # 5. Execute
        await order_manager.process_queue()

        # 6. Record rebalance outcomes in signal health monitor (audit Section 3-2 wiring).
        from datetime import datetime, timezone as _tz
        _now = datetime.now(_tz.utc)
        for asset, tgt_w in final_weights.items():
            entry_price = ingestion.get_latest_price(asset)
            if entry_price and entry_price > 0:
                signal_health_monitor.record_outcome(
                    RebalanceOutcome(
                        asset=asset,
                        entry_time=_now,
                        entry_price=entry_price,
                        exit_price=entry_price,  # updated at next rebalance; stub for now
                        return_pct=0.0,           # Phase 2: track actual exit price
                    )
                )

    async def monitoring_tick():
        """Every 1h: Performance snapshots and Parquet backup."""
        # Snapshot
        snap = position_tracker.snapshot()
        logger.info("Hourly Performance: NAV=$%.2f, Exposure=%.2f, Positions=%d",
                     snap["nav"], snap["crypto_exposure"], snap["position_count"])

        # Sync balance from exchange to catch any discrepancies
        try:
            balances = await exec_client.get_balance()
            position_tracker.sync_from_exchange(balances)
        except Exception as e:
            logger.warning("Hourly balance sync failed: %s", e)

        # Parquet Backup
        ingestion.save_parquet_backup()

        # W-02/E-12: await check() to evaluate signal health and raise alerts.
        # snapshot() only reads state; check() runs the threshold evaluation.
        await signal_health_monitor.check()

        # Performance snapshot to logs/snapshots.jsonl (audit Section 3-3 wiring).
        sh_snap = signal_health_monitor.snapshot()
        positions_fmt = [
            {
                "symbol": a,
                "weight_pct": round(p.current_weight * 100, 4),
                "unrealised_pnl_pct": round(p.unrealized_pnl_pct * 100, 4),
                "stop_distance_pct": 0.0,  # Phase 2: wire trailing stop distance
            }
            for a, p in position_tracker.positions.items()
        ]

        # BTC beta — observability only; must never crash monitoring job.
        btc_beta_value = None
        try:
            current_weights = {a: p.current_weight for a, p in position_tracker.positions.items()}
            btc_beta_value = beta_monitor.estimate_portfolio_beta(weights=current_weights)
        except Exception as e:
            logger.warning("beta_monitor failed (non-fatal): %s", e)

        await performance_logger.snapshot(
            nav=position_tracker.nav,
            positions=positions_fmt,
            regime=regime_detector.state.current_regime.value,
            signal_health=sh_snap,
            btc_beta=btc_beta_value,
        )

        # W-03: write atomic status.json for external monitoring (was never called).
        try:
            _nav = position_tracker.nav
            _start_nav_safe = starting_nav if starting_nav > 0 else 1.0
            write_status(
                status_path=config.get("logging.status_file", "logs/status.json"),
                regime=regime_detector.state.current_regime.value,
                nav=_nav,
                pnl_daily_pct=risk_manager.get_daily_pnl_pct(_nav) * 100,
                pnl_total_pct=(_nav - starting_nav) / _start_nav_safe * 100,
                drawdown_pct=performance_logger.nav_peak - _nav if performance_logger.nav_peak > _nav else 0.0,
                crypto_exposure_pct=position_tracker.crypto_exposure * 100,
                cash_pct=(1 - position_tracker.crypto_exposure) * 100,
                num_positions=len(position_tracker.positions),
                open_orders=len(order_manager.active_orders),
                positions=positions_fmt,
                recent_trades=[],
                signal_health=sh_snap,
                risk_flags=[],
                next_rebalance_utc="",
                endgame_hours_remaining=0.0,
                loop_duration_ms=0.0,
                api_calls_remaining=0,
                errors_last_hour=0,
            )
        except Exception as _e:
            logger.warning("write_status failed (non-fatal): %s", _e)

        # Flush decision log
        await decision_logger.flush()

    # 5. Start Scheduler
    scheduler = Scheduler(
        system_state=system_state,
        heartbeat_path=Path(config.get("logging.heartbeat_file", "logs/heartbeat"))
    )
    
    # Check for Zero-Hour Readiness: If buffers are warm, rebalance immediately
    # before starting the periodic scheduler.
    if ingestion.get_bar_count("BTC") >= config.get("data_ingestion.ring_buffer_maxlen", 1440):
        logger.info("Buffers are warm — triggering initial rebalance for Zero-Hour Readiness")
        try:
            # Update Order Manager and Position Tracker prices from buffers
            prices = {config.pair_for(a): ingestion.get_latest_price(a)
                      for a in ingestion.get_all_assets()
                      if ingestion.get_latest_price(a) is not None}
            order_manager.update_prices(prices)
            position_tracker.update_prices({a: ingestion.get_latest_price(a) 
                                           for a in ingestion.get_all_assets() 
                                           if ingestion.get_latest_price(a) is not None})

            # We need to ensure features are computed for the initial state
            feature_engine.warm_start() 
            
            # Update regime immediately so first rebalance uses current market state
            regime_detector.update(
                feature_engine.get_regime_inputs(),
                position_tracker.positions,
                feature_engine.get_return,
            )

            await rebalance_tick()
        except Exception as e:
            logger.error("Initial rebalance failed: %s", e)
    else:
        # If buffers NOT warm, at least perform one data ingestion to populate
        # current prices and allow a rebalance on the very first scheduled tick.
        logger.info("Buffers not warm — performing initial data fetch...")
        await data_ingestion_tick()
        # Trigger an immediate rebalance if we now have at least one bar
        if feature_engine._regime_inputs is not None:
             logger.info("Initial data fetch successful — triggering immediate rebalance")
             # Update regime before rebalancing
             regime_detector.update(
                 feature_engine.get_regime_inputs(),
                 position_tracker.positions,
                 feature_engine.get_return,
             )
             await rebalance_tick()

    # Schedule all ticks
    # data_ingestion and risk_check should run immediately to keep system fresh
    await scheduler.schedule_job("data_ingestion", 60, data_ingestion_tick, immediate=True)
    await scheduler.schedule_job("risk_check", 60, risk_check_tick, immediate=True)
    # regime and rebalance should wait for their first interval since we already triggered them above
    await scheduler.schedule_job("regime_update", 300, regime_update_tick, immediate=False)
    await scheduler.schedule_job("rebalance", config.get("portfolio.rebalance_cadence_sec", 3600), rebalance_tick, immediate=False)
    await scheduler.schedule_job("monitoring", 3600, monitoring_tick, immediate=False)

    # 6. Graceful Shutdown
    loop = asyncio.get_running_loop()
    shutdown_event = asyncio.Event()

    def shutdown_handler():
        logger.info("Shutdown signal received...")
        shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    logger.info("APEX main loop started. Press Ctrl+C to stop.")

    # Block until shutdown signal
    await shutdown_event.wait()

    # Clean up
    await scheduler.stop_all()
    await decision_logger.flush()
    decision_logger.close()
    await binance_client.close()
    await base_client.close()
    logger.info("APEX shutdown complete.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception("Fatal system error: %s", e)
        sys.exit(1)
