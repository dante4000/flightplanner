from award_trip_planner.programs import (
    CURRENCIES,
    REGISTRY,
    fundability,
    funding_options,
    points_needed,
    program_for,
)

NO_BONUS = lambda c, p: 0.0  # noqa: E731


def test_currencies_and_registry_shape():
    assert CURRENCIES == ("amex_mr", "chase_ur", "aeroplan_fixed")
    # every registry entry only references known currencies
    for p in REGISTRY.values():
        assert set(p.transfers) <= set(CURRENCIES)
    # the programs seats.aero actually returns on these routes are all present
    for src in ["aeroplan", "alaska", "american", "united", "delta", "qantas",
                "flyingblue", "singapore", "virginatlantic", "etihad", "emirates",
                "jetblue", "qatar", "smiles", "azul", "velocity", "ethiopian"]:
        assert src in REGISTRY, src


def test_amex_partners_and_non_partners():
    assert "amex_mr" in REGISTRY["aeroplan"].transfers
    assert "amex_mr" in REGISTRY["virginatlantic"].transfers
    assert "amex_mr" in REGISTRY["flyingblue"].transfers
    # Alaska, American and United take no flexible-currency transfers here
    assert REGISTRY["alaska"].transfers == {}
    assert REGISTRY["american"].transfers == {}
    assert REGISTRY["united"].transfers == {"chase_ur": 1.0}
    # the fixed Aeroplan balance reaches Aeroplan only
    assert REGISTRY["aeroplan"].transfers["aeroplan_fixed"] == 1.0
    assert "aeroplan_fixed" not in REGISTRY["virginatlantic"].transfers


def test_stopover_rules_only_where_documented():
    assert REGISTRY["aeroplan"].stopover_extra_miles == 5_000
    for pid, p in REGISTRY.items():
        if pid != "aeroplan":
            assert p.stopover_extra_miles is None, pid


def test_unknown_source_is_non_fundable_not_an_error():
    p = program_for("brandnewprogram")
    assert p.id == "brandnewprogram" and p.transfers == {}
    ok, reason, short = fundability("brandnewprogram", 50_000, {"amex_mr": 999_999}, NO_BONUS)
    assert ok is False and reason == "no transfer partner" and short == 0


def test_points_needed_with_bonus():
    assert points_needed(50_000, 1.0, 0.0) == 50_000
    # a 30% bonus: 100,000 MR yields 130,000 miles, so 130,000 miles costs 100,000 MR
    assert points_needed(130_000, 1.0, 0.30) == 100_000
    # rounds up, never down — you cannot transfer a fraction of a point
    assert points_needed(100_001, 1.0, 0.0) == 100_001
    assert points_needed(10, 3.0, 0.0) == 4       # ceil(10/3)


def test_funding_options_prefers_the_cheapest_currency():
    balances = {"amex_mr": 200_000, "chase_ur": 200_000, "aeroplan_fixed": 100_000}
    bonus = lambda c, p: 0.30 if (c == "amex_mr" and p == "aeroplan") else 0.0  # noqa: E731
    opts = funding_options("aeroplan", 65_000, balances, bonus)
    assert opts[0][0] == "amex_mr"          # bonus makes MR cheapest
    assert opts[0][1] == 50_000             # ceil(65000 / 1.3)
    assert ("aeroplan_fixed", 65_000) in opts
    assert all(c in CURRENCIES for c, _ in opts)


def test_fundability_reports_shortfall():
    ok, reason, short = fundability("aeroplan", 65_000, {"aeroplan_fixed": 60_000}, NO_BONUS)
    assert ok is False and reason == "insufficient balance" and short == 5_000
    ok, reason, short = fundability("aeroplan", 65_000, {"aeroplan_fixed": 65_000}, NO_BONUS)
    assert ok is True and reason == "ok" and short == 0
