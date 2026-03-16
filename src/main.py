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
from src.data.ingestion import DataIngestionManager
from src.data.features import FeatureEngine
from src.regime.detector import RegimeDetector
from src.regime.regime_state import RegimeState
from src.signals.momentum import MomentumSignal
from src.portfolio.factory import create_portfolio_constructor, get_current_weights
from src.risk.factory import create_risk_manager
from src.execution.roostoo_client import ExecutionClient
from src.execution.position_tracker import PositionTracker
from src.execution.decision_logger import DecisionLogger
from src.execution.priority_queue import OrderPriorityQueue
from src.execution.order_manager import OrderManager, OrderManagerConfig

from src.orchestration.startup import run_preflight_checks, PreFlightCheckError
from src.orchestration.scheduler import Scheduler
from src.orchestration.safe_state import SystemState
from src.orchestration.recovery import reconstruct_positions

# ---------------------------------------------------------------------------
# Logging Setup
# ---------------------------------------------------------------------------
def setup_logging(config: Config):
    log_cfg = config.get("logging", {})
    log_dir = Path(log_cfg.get("directory", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    
    log_file = log_dir / "apex.log"
    
    logging.basicConfig(
        level=getattr(logging, log_cfg.get("level", "INFO")),
        format=log_cfg.get("format", "%(asctime)s [%(levelname)s] %(name)s: %(message)s"),
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(sys.stdout)
        ]
    )
    return logging.getLogger("apex")

logger = logging.getLogger("apex")


# ---------------------------------------------------------------------------
# Main Orchestrator
# ---------------------------------------------------------------------------
async def main():
    # 1. Load basic config and setup logging
    project_root = Path(__file__).resolve().parent.parent
    config_path = project_root / "config.yaml"
    
    # We use a temp logger before full setup
    logging.basicConfig(level=logging.INFO)
    
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
    
    # Decision Logger
    decision_logger = DecisionLogger(
        log_path=config.get("logging.trade_log", "logs/trades.jsonl")
    )
    
    # Position Tracker (with recovery)
    starting_capital = config.get("competition.starting_capital_usd", 1_000_000)
    position_tracker = PositionTracker(starting_capital=starting_capital)
    
    # Crash Recovery: Positions
    if raw_config_dict.get("recovery_trade_log_exists"):
        reconstruct_positions(position_tracker, Path(decision_logger.log_path))

    # Data Ingestion & Features
    ingestion = DataIngestionManager(config.raw, base_client)
    await ingestion.initialize() # Discovers universe, reloads Parquet
    
    feature_engine = FeatureEngine(config.raw, ingestion)
    
    # Regime Detection
    regime_detector = RegimeDetector(config.raw)
    
    # Risk Management
    risk_manager = create_risk_manager(config, starting_nav=position_tracker.nav, phase=1)
    
    # Signal Generation
    tier_1_3 = set(config.get("universe.tier_1_majors", [])) | \
               set(config.get("universe.tier_2_large_alts", [])) | \
               set(config.get("universe.tier_3_defi", []))
    momentum_signal = MomentumSignal(config.raw, tier_1_3)
    
    # Portfolio Construction
    portfolio_constructor = create_portfolio_constructor(config, phase=1)
    
    # Execution Engine
    order_queue = OrderPriorityQueue()
    order_manager = OrderManager(
        exec_client=exec_client,
        position_tracker=position_tracker,
        decision_logger=decision_logger,
        order_queue=order_queue,
        config=OrderManagerConfig(
            order_timeout_sec=config.get("execution.order_timeout_sec", 60),
            max_resubmissions=config.get("execution.max_resubmissions", 3)
        )
    )

    # 4. Job Definitions
    
    async def data_ingestion_tick():
        """Every 60s: Fetch prices, update features, run risk checks."""
        # 1. Ingest
        await ingestion.fetch_prices()
        
        # 2. Features
        feature_engine.on_new_bar()
        
        # 3. Update Order Manager Prices
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
        
        # Run 1-min risk checks
        risk_events = risk_manager.tick(
            current_prices=current_prices,
            tracker=position_tracker,
            daily_pnl_pct=risk_manager.breakers.get_daily_pnl_pct(position_tracker.nav)
        )
        
        # Convert RiskEvents to Orders
        from src.execution.priority_queue import PendingOrder, OrderPriority
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
                priority=OrderPriority.CRITICAL_EXIT if event.severity.value >= 3 else OrderPriority.HIGH_EXIT,
                trigger=f"RISK_{event.type.name}",
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
        # Phase 1: Minimal vol guard or hardcoded BULL
        # (detector.update uses inputs from feature_engine)
        regime_detector.update(
            feature_engine.get_regime_inputs(),
            None # Contagion result handled internally in Phase 2
        )
        logger.debug("Regime updated: %s", regime_detector.state.current_regime)

    async def rebalance_tick():
        """Every 60m: Re-rank signals and rebalance portfolio."""
        if not system_state.can_rebalance:
            logger.warning("Rebalance skipped due to system safe-mode or stale data.")
            return

        # 1. Signals
        scores = feature_engine.get_momentum_scores()
        selections = momentum_signal.generate(
            momentum_scores=scores,
            regime=regime_detector.state
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
        
        # 4. Queue Orders
        # (Convert weight diffs to USD orders)
        
        for asset, target_w in final_weights.items():
            current_w = current_weights.get(asset, 0.0)
            diff_w = target_w - current_w
            
            # Threshold check
            if abs(diff_w) < config.get("portfolio.min_trade_threshold_pct_nav", 0.002):
                continue
                
            qty_usd = diff_w * position_tracker.nav
            from src.execution.priority_queue import PendingOrder, OrderPriority
            
            order = PendingOrder(
                asset=asset,
                pair=config.pair_for(asset),
                side="BUY" if qty_usd > 0 else "SELL",
                quantity_usd=abs(qty_usd),
                priority=OrderPriority.NORMAL_REBALANCE,
                trigger="STRATEGY_REBALANCE",
                target_weight=target_w
            )
            order_queue.add(order)
            
        # 5. Execute
        await order_manager.process_queue()

    async def monitoring_tick():
        """Every 1h: Performance snapshots and Parquet backup."""
        # Snapshot
        snap = position_tracker.snapshot()
        logger.info("Hourly Performance: NAV=$%.2f, Exposure=%.2f, Positions=%d", 
                     snap["nav"], snap["crypto_exposure"], snap["position_count"])
        
        # Parquet Backup
        ingestion.save_parquet_backup()
        
        # Flush decision log
        await decision_logger.flush()

    # 5. Start Scheduler
    scheduler = Scheduler(
        system_state=system_state,
        heartbeat_path=Path(config.get("logging.heartbeat_file", "logs/heartbeat"))
    )
    
    # Schedule all ticks
    await scheduler.schedule_job("data_ingestion", 60, data_ingestion_tick)
    await scheduler.schedule_job("risk_check", 60, risk_check_tick)
    await scheduler.schedule_job("regime_update", 300, regime_update_tick)
    await scheduler.schedule_job("rebalance", config.get("portfolio.rebalance_cadence_sec", 3600), rebalance_tick)
    await scheduler.schedule_job("monitoring", 3600, monitoring_tick)

    # 6. Graceful Shutdown
    loop = asyncio.get_running_loop()
    
    def shutdown_handler():
        logger.info("Shutdown signal received...")
        asyncio.create_task(scheduler.stop_all())
        decision_logger.close()
        # In a real system we might want to cancel all orders here
        
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_handler)

    logger.info("APEX main loop started. Press Ctrl+C to stop.")
    
    # Keep the main coroutine alive
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        logger.info("Main loop cancelled.")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
    except Exception as e:
        logger.exception("Fatal system error: %s", e)
        sys.exit(1)
