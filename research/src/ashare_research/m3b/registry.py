from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class FactorSpec:
    factor_id: str
    kind: str
    sessions: int
    prior_sessions: int
    units: str = "decimal"
    definition_version: str = "1.0"
    direction: str = "ascending_raw_value"
    ddof: int = 1

    def manifest(self):
        return asdict(self)


REGISTRY = {s.factor_id: s for s in [
    FactorSpec("momentum_20", "momentum", 20, 20),
    FactorSpec("momentum_60", "momentum", 60, 60),
    FactorSpec("momentum_120", "momentum", 120, 120),
    FactorSpec("ma60_distance", "ma_distance", 60, 59),
    FactorSpec("volatility_20", "volatility", 20, 20),
    FactorSpec("liquidity_20", "liquidity", 20, 19, "CNY_per_session"),
]}
