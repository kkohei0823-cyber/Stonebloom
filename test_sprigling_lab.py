# -*- coding: utf-8 -*-
"""
Spriglingのステータス算出・検証環境（verify_lab.py / lab_stats.py）の回帰テスト。
  $ PYTHONHASHSEED=0 python3 -m pytest -q test_sprigling_lab.py   （または python3 test_sprigling_lab.py）
"""

import random

import lab_stats
import sprigling as S
from config import CONFIG, reset_config, apply_config_overrides
from game import Game, role_of, type_multiplier

COST_MODEL = {"sprigling_stats": {"model": "cost"}, "combat": {"multi_attack": False}}


def test_reference_builds_are_middle_class_and_slightly_stronger():
    apply_config_overrides(COST_MODEL)
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
    apply_config_overrides(COST_MODEL)
    rng = random.Random(0)
    light = [S.derive_stats(S.random_genome(rng, "light"), 0) for _ in range(40)]
    heavy = [S.derive_stats(S.random_genome(rng, "heavy"), 0) for _ in range(40)]
    assert max(s["hp"] * s["atk"] for s in light) < 7200  # 軽量級は既存駒（7200）未満
    assert min(s["hp"] * s["atk"] for s in heavy) > 7200
    for s in light + heavy:
        assert s["produce_cost"] <= CONFIG["sprigling_cost"]["cost_at_heavy_limit"]
    reset_config()


def test_physical_model_speed_and_multi_attack():
    reset_config()
    from game import hit_count
    rng = random.Random(0)
    light = [S.derive_stats(S.random_genome(rng, "light"), 0) for _ in range(30)]
    heavy = [S.derive_stats(S.random_genome(rng, "heavy"), 0) for _ in range(30)]
    med = lambda xs: sorted(xs)[len(xs) // 2]
    assert med([s["speed"] for s in light]) > med([s["speed"] for s in heavy])  # 軽いほど速い
    assert med([s["hp"] for s in light]) < med([s["hp"] for s in heavy])        # 重いほど打たれ強い
    CONFIG["pieces"]["S:fast"] = dict(light[0], speed=250.0)
    assert hit_count("S:fast", "歩兵") == 2      # 2.5倍速 → 2回
    CONFIG["pieces"]["S:fast"]["speed"] = 400.0
    assert hit_count("S:fast", "歩兵") == 4      # 4倍速 → 4回
    assert hit_count("歩兵", "騎兵") == 1        # 既存駒どうしは常に1回
    reset_config()


def test_guard_moves_base_damage_to_adjacent_sprigling():
    from game import Board, make_piece, resolve_combat
    apply_config_overrides({"combat": {"guard_ratio": 0.5}})
    CONFIG["pieces"]["S:tank"] = dict(CONFIG["pieces"]["重装兵"], hp=400, atk=0, attribute=None)
    board = Board(CONFIG["board_size"])
    board.place((4, 8), make_piece("本拠", 0, movable=False))
    board.place((3, 8), make_piece("S:tank", 0))
    board.place((4, 7), make_piece("工兵", 1))  # 本拠にだけ隣接（(3, 8)のSpriglingとは隣接しない）
    _, damage = resolve_combat(board)
    base_dmg = damage[(4, 8)]
    assert abs(base_dmg - 12 * 0.5) < 1e-9          # 工兵ATK12の半分だけ本拠へ
    assert abs(damage[(3, 8)] - 12 * 0.5) < 1e-9    # 残り半分は隣のSpriglingが肩代わり
    reset_config()


def test_class_step_keeps_heavier_class_winning_1v1():
    import verify_lab as V
    apply_config_overrides({"sprigling_stats": {"model": "mass", "mass": {"class_step": 0.05}},
                            "combat": {"multi_attack": False}})
    rep = V.duel_report(60, "test", attribute=False)
    assert rep["class"]["heavy_vs_light"]["loss"] == 0.0
    assert all(r["loss"] == 0.0 for r in rep["core_vs_light"].values())
    reset_config()


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
