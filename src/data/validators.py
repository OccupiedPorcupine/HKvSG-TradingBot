"""Data quality validation for the ingestion pipeline.

Handles missing bar detection, price anomaly flagging, and asset
staleness tracking. All validation is stateful per-asset.
"""

import logging
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class AssetStatus(Enum):
    """Trading status of an individual asset."""

    ACTIVE = "ACTIVE"
    STALE = "STALE"
    ANOMALY = "ANOMALY"


class DataValidator:
    """Validates incoming price data and tracks per-asset quality state.

    Responsibilities:
    - Track consecutive missing bars per asset (fill forward up to 3,
      then mark STALE).
    - Detect single-bar price anomalies (return > 5 sigma of trailing
      1h realized vol). Flag until next bar confirms.
    - Track global data freshness (stale if no fetch > 5 min).

    Attributes:
        fill_forward_limit: Max consecutive bars to fill forward.
        anomaly_threshold_std: Std dev multiple for anomaly detection.
        stale_max_age_sec: Max age of data before global STALE flag.
    """

    def __init__(
        self,
        fill_forward_limit: int = 3,
        anomaly_threshold_std: float = 5.0,
        stale_max_age_sec: int = 300,
    ) -> None:
        """Initialize the data validator.

        Args:
            fill_forward_limit: Max consecutive missing bars before STALE.
            anomaly_threshold_std: Std dev threshold for anomaly detection.
            stale_max_age_sec: Max data age in seconds before global STALE.
        """
        self.fill_forward_limit = fill_forward_limit
        self.anomaly_threshold_std = anomaly_threshold_std
        self.stale_max_age_sec = stale_max_age_sec

        # Per-asset state
        self._missing_counts: dict[str, int] = {}
        self._statuses: dict[str, AssetStatus] = {}
        self._anomaly_flags: dict[str, bool] = {}

    def register_asset(self, asset: str) -> None:
        """Register a new asset for tracking.

        Args:
            asset: Asset symbol (e.g., "BTC").
        """
        if asset not in self._statuses:
            self._statuses[asset] = AssetStatus.ACTIVE
            self._missing_counts[asset] = 0
            self._anomaly_flags[asset] = False

    def check_missing_bar(self, asset: str, received: bool) -> AssetStatus:
        """Check if a bar was received and update missing bar count.

        If a bar is received, reset the counter and restore ACTIVE status
        (unless ANOMALY-flagged). If missing, increment counter. After
        fill_forward_limit consecutive misses, mark STALE.

        Args:
            asset: Asset symbol.
            received: True if price data was received for this bar.

        Returns:
            Current asset status after the check.
        """
        self.register_asset(asset)

        if received:
            if self._missing_counts[asset] > 0:
                logger.info(
                    "DATA_RESTORED asset=%s after %d missing bars",
                    asset,
                    self._missing_counts[asset],
                )
            self._missing_counts[asset] = 0

            # Restore to ACTIVE if was STALE (anomaly flag handled separately)
            if self._statuses[asset] == AssetStatus.STALE:
                self._statuses[asset] = AssetStatus.ACTIVE
                logger.info("STALE_CLEARED asset=%s now ACTIVE", asset)

            return self._statuses[asset]

        # Missing bar
        self._missing_counts[asset] += 1
        count = self._missing_counts[asset]

        if count <= self.fill_forward_limit:
            logger.debug(
                "FILL_FORWARD asset=%s missing_bar=%d/%d",
                asset,
                count,
                self.fill_forward_limit,
            )
            return self._statuses[asset]

        # Exceeded fill-forward limit
        if self._statuses[asset] != AssetStatus.STALE:
            self._statuses[asset] = AssetStatus.STALE
            logger.warning(
                "ASSET_STALE asset=%s consecutive_missing=%d (limit=%d)",
                asset,
                count,
                self.fill_forward_limit,
            )

        return AssetStatus.STALE

    def check_anomaly(
        self,
        asset: str,
        new_price: float,
        prev_price: float,
        vol_1h: Optional[float],
    ) -> bool:
        """Check if a price bar is anomalous.

        A bar is anomalous if its single-bar return exceeds
        anomaly_threshold_std * trailing 1h realized volatility.

        If previously flagged, the next bar confirms (or denies) the
        move and clears the flag.

        Args:
            asset: Asset symbol.
            new_price: Current bar price.
            prev_price: Previous bar price.
            vol_1h: Trailing 1h realized volatility (std of 1-min returns).
                If None or zero, anomaly check is skipped.

        Returns:
            True if the bar is flagged as anomalous and should not be traded.
        """
        self.register_asset(asset)

        # If previously flagged, this bar confirms the move — clear the flag
        if self._anomaly_flags.get(asset, False):
            self._anomaly_flags[asset] = False
            if self._statuses[asset] == AssetStatus.ANOMALY:
                self._statuses[asset] = AssetStatus.ACTIVE
                logger.info(
                    "ANOMALY_CONFIRMED asset=%s price=%.6f (move confirmed by next bar)",
                    asset,
                    new_price,
                )
            return False

        # Skip anomaly check if no volatility data yet
        if vol_1h is None or vol_1h <= 0 or prev_price <= 0:
            return False

        # Check single-bar return against volatility threshold
        bar_return = abs(new_price - prev_price) / prev_price

        if bar_return > self.anomaly_threshold_std * vol_1h:
            self._anomaly_flags[asset] = True
            self._statuses[asset] = AssetStatus.ANOMALY
            logger.warning(
                "PRICE_ANOMALY asset=%s return=%.4f vol_1h=%.6f "
                "threshold=%.4f price=%.6f prev=%.6f",
                asset,
                bar_return,
                vol_1h,
                self.anomaly_threshold_std * vol_1h,
                new_price,
                prev_price,
            )
            return True

        return False

    def get_status(self, asset: str) -> AssetStatus:
        """Get current status of an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Current AssetStatus.
        """
        return self._statuses.get(asset, AssetStatus.ACTIVE)

    def get_active_assets(self, all_assets: list[str]) -> list[str]:
        """Filter a list of assets to only ACTIVE ones.

        Args:
            all_assets: List of all asset symbols.

        Returns:
            List of assets with ACTIVE status.
        """
        return [
            a for a in all_assets
            if self._statuses.get(a, AssetStatus.ACTIVE) == AssetStatus.ACTIVE
        ]

    def get_non_stale_assets(self, all_assets: list[str]) -> list[str]:
        """Filter a list of assets to non-STALE ones.

        ANOMALY assets are included (they're temporarily flagged but
        not permanently excluded from ranking).

        Args:
            all_assets: List of all asset symbols.

        Returns:
            List of assets that are not STALE.
        """
        return [
            a for a in all_assets
            if self._statuses.get(a, AssetStatus.ACTIVE) != AssetStatus.STALE
        ]

    def get_missing_count(self, asset: str) -> int:
        """Get consecutive missing bar count for an asset.

        Args:
            asset: Asset symbol.

        Returns:
            Number of consecutive missing bars.
        """
        return self._missing_counts.get(asset, 0)

    def is_anomaly_flagged(self, asset: str) -> bool:
        """Check if an asset is currently anomaly-flagged.

        Args:
            asset: Asset symbol.

        Returns:
            True if flagged, waiting for next bar to confirm.
        """
        return self._anomaly_flags.get(asset, False)

    def reset_asset(self, asset: str) -> None:
        """Reset all state for an asset.

        Args:
            asset: Asset symbol.
        """
        self._missing_counts[asset] = 0
        self._statuses[asset] = AssetStatus.ACTIVE
        self._anomaly_flags[asset] = False

    def get_all_statuses(self) -> dict[str, AssetStatus]:
        """Get status of all tracked assets.

        Returns:
            Dict mapping asset symbol to AssetStatus.
        """
        return dict(self._statuses)
