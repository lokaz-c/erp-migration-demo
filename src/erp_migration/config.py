"""Paths, defaults and the demo FX rate formula shared by the generator and the ETL."""

from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

DEFAULT_SEED = 42
DEFAULT_START_YEAR = 2015
DEFAULT_END_YEAR = 2024

# Matches compose.yaml. Demo-only credentials for a local container.
DEFAULT_DATABASE_URL = "postgresql://erp:erp@localhost:54329/erp"


@dataclass(frozen=True)
class Paths:
    data_dir: Path

    @property
    def books(self) -> Path:
        return self.data_dir / "books"

    @property
    def truth(self) -> Path:
        return self.data_dir / "truth"


def default_paths() -> Paths:
    return Paths(Path(os.environ.get("ERP_DATA_DIR", "data")))


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


# --- FX -------------------------------------------------------------------
# The demo needs a USD -> RWF rate per month to report everything in one
# currency. Real market rates are deliberately NOT used: this is an invented,
# formula-generated table so nobody mistakes it for central-bank data.
# rate = 1000 RWF per USD in Jan 2015, plus 5 RWF for every month after that.
FX_BASE_YEAR = 2015
FX_BASE_RATE = Decimal(1000)
FX_MONTHLY_STEP = Decimal(5)
FX_SOURCE = "synthetic-demo-formula"


def usd_rwf_rate(year: int, month: int) -> Decimal:
    months = (year - FX_BASE_YEAR) * 12 + (month - 1)
    return FX_BASE_RATE + FX_MONTHLY_STEP * months
