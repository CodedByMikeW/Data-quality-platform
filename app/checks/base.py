from dataclasses import dataclass, field
from typing import Any, Optional

import pandas as pd


@dataclass
class CheckResult:
    """
    The common shape every check returns. This is what makes the scoring
    formula and the API generic across check types: add a new check class,
    and scoring/persistence/API endpoints don't need to change.
    """

    check_name: str
    passed: bool
    total_rows: int
    affected_rows: int
    affected_row_keys: list[Any] = field(default_factory=list)
    details: dict = field(default_factory=dict)

    @property
    def pass_rate(self) -> float:
        if self.total_rows == 0:
            return 1.0
        return 1 - (self.affected_rows / self.total_rows)


class BaseCheck:
    name: str = "base_check"

    def run(self, df: pd.DataFrame, config: Optional[dict] = None) -> CheckResult:
        raise NotImplementedError
