# -*- coding: utf-8 -*-
"""
Spriglingのステータス算出・検証環境（verify_lab.py / lab_stats.py）の回帰テスト。
  $ PYTHONHASHSEED=0 python3 -m pytest -q test_sprigling_lab.py   （または python3 test_sprigling_lab.py）
"""

import random

import lab_stats
import sprigling as S
from config import CONFIG, reset_config
from game import Game, role_of, type_multiplier


def test_reference_builds_are_middle_class_and_slightly_stronger():
    reset_config()
    for name, g in S.REFERENCE_BUILDS.items():
        st = S.derive_stats(g, 0)
        assert st["sprigling"]["weight_class"] == "middle", name
        power = st["hp"] * st["atk"]
        assert 7200 * 0.9 <= power <= 7200 * 1.4, (name, power)


def test_derive_stats_is_deterministic_and_restores_global_random():
    random.seed(123)
    expected_next = random.random()
    random.seed(123)
    a = S.derive_stats(S.REFERENCE_BUILDS["W"], seed=7)
    assert random.random() == expected_next  # グローバル乱数を汚さない
    b = S.derive_stats(S.REFERENCE_BUILDS["W"], seed=7)
    assert a == b


def test_power_scales_with_mobilization_cost():
    reset_config()
    rng = random.Random(0)
    light = [S.derive_stats(S.random_genome(rng, "light"), 0) for _ in range(40)]
    heavy = [S.derive_stats(S.random_genome(rng, "heavy"), 0) for _ in range(40)]
    assert max(s["hp"] * s["atk"] for s in light) < 7200  # 軽量級は既存駒（7200）未満
    assert min(s["hp"] * s["atk"] for s in heavy) > 7200
    for s in light + heavy:
        assert s["produce_cost"] <= CONFIG["sprigling_cost"]["cost_at_heavy_limit"]


def test_validate_genome_rejects_illegal_builds():
    g = S._genome(tail=S.part(count=1))
    assert S.validate_genome(g) == []
    assert S.validate_genome(S._genome())  # 非vital部位が1つも無い
    bad = S._genome(tail=S.part(size=3, count=1))
    assert S.validate_genome(bad)
    bad = S._genome(tail=S.part(count=1), head=S.part(count=2))
    assert S.validate_genome(bad)


def test_registered_sprigling_plays_with_attribute_and_role():
    reset_config()
    g = S.REFERENCE_BUILDS["R"]
    kind = S.register_sprigling("test", g, 0)
    assert kind in CONFIG["pieces"]
    assert role_of(kind) in ("歩兵", "騎兵", "重装兵")
    assert type_multiplier(kind, "騎兵") == 2.0  # R は bind → pierce(棘走) に有利
    reset_config()
    assert kind not in CONFIG["pieces"]


def test_roster_limits_production():
    reset_config()
    kind = S.register_sprigling("only0", S.REFERENCE_BUILDS["W"], 0)
    from heuristic_bot import HeuristicBot
    g = Game(roster={0: None, 1: ["歩兵", "騎兵", "重装兵", "弓兵", "工兵"]})
    g.run({0: HeuristicBot(rng=random.Random(1)), 1: HeuristicBot(rng=random.Random(2))})
    produced1 = [rec["produced"].get(1) for rec in g.kifu]
    assert kind not in produced1
    reset_config()


def test_sprt_and_ci():
    wins = [1.0] * 30 + [0.5] * 10
    assert lab_stats.sprt_decision(wins, 0.5, 0.6)[0] == "H1"
    even = [0.5, 0.25, 0.75, 0.5] * 60
    assert lab_stats.sprt_decision(even, 0.5, 0.6)[0] == "H0"
    m, lo, hi = lab_stats.mean_ci([0.0, 1.0] * 50)
    assert lo < 0.5 < hi


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
