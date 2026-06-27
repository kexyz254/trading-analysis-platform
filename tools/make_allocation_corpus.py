import struct
from pathlib import Path

ROOT = Path("fuzz/corpus/allocation_plan_fuzzer")
ROOT.mkdir(parents=True, exist_ok=True)

def s8(value: str) -> bytes:
    raw = value.encode("ascii")
    assert 1 <= len(raw) <= 255
    return bytes([len(raw)]) + raw

def rule(opcode, threshold, limit, window, flags):
    return struct.pack("<BiiHH", opcode, threshold, limit, window, flags)

def constraint(kind, lower, upper, flags):
    return struct.pack("<BiiI", kind, lower, upper, flags)

def target(symbol, weight, drift, rebalance, bucket, constraints):
    out = bytearray()
    out += s8(symbol)
    out += struct.pack("<d", weight)
    out += struct.pack("<i", drift)
    out += struct.pack("<BHH", 1 if rebalance else 0, bucket, len(constraints))
    for item in constraints:
        out += item
    return bytes(out)

def strategy(kind, name, rules):
    out = bytearray()
    out += struct.pack("<B", kind)
    out += s8(name)
    out += struct.pack("<H", len(rules))
    for item in rules:
        out += item
    return bytes(out)

def account(name, risk_budget_bps, strategies, targets):
    out = bytearray()
    out += s8(name)
    out += struct.pack("<HHI", len(strategies), len(targets), risk_budget_bps)
    for item in strategies:
        out += item
    for item in targets:
        out += item
    return bytes(out)

def document(accounts, version=2, flags=3, plan_id=0x5152535455565758, plan_flags=7):
    out = bytearray()
    out += b"ALOC"
    out += struct.pack("<HHIQI", version, flags, len(accounts), plan_id, plan_flags)
    for item in accounts:
        out += item
    return bytes(out)

risk_rules = [
    rule(1, -100, 250, 60, 0),
    rule(2, 10, 5000, 300, 1),
    rule(5, -2500, 9000, 600, 3),
]

sector_rules = [
    rule(3, 0, 10000, 1200, 0),
    rule(4, -50, 50, 30, 1),
]

constraints_a = [
    constraint(1, -500, 500, 0),
    constraint(2, 0, 10000, 2),
]

constraints_b = [
    constraint(3, -1000, 2500, 1),
]

seed1 = document([
    account(
        "ACCT1",
        9500,
        [strategy(1, "RISK_MAIN", risk_rules)],
        [
            target("AAPL", 0.25, 125, True, 11, constraints_a),
            target("MSFT", 0.25, 100, True, 12, constraints_b),
            target("SPY", 0.20, 50, False, 13, []),
        ],
    )
])

seed2 = document([
    account(
        "CORE1",
        10000,
        [
            strategy(2, "SECTOR_CAP", sector_rules),
            strategy(4, "DRIFT_GATE", risk_rules[:2]),
        ],
        [
            target("NVDA", 0.10, 75, True, 20, constraints_a),
            target("AMD", 0.08, 100, True, 21, constraints_b),
            target("QQQ", 0.35, 40, True, 22, []),
        ],
    ),
    account(
        "HEDGE2",
        6000,
        [strategy(3, "HEDGE_RULE", risk_rules[1:])],
        [
            target("GLD", 0.15, 200, False, 31, constraints_b),
            target("TLT", 0.20, 250, True, 32, constraints_a),
        ],
    ),
])

(ROOT / "binary_plan_one.alloc").write_bytes(seed1)
(ROOT / "binary_plan_two.alloc").write_bytes(seed2)

print("wrote allocation seeds")
for p in sorted(ROOT.iterdir()):
    print(p, p.stat().st_size)
