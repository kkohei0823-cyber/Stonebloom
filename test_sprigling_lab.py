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

# 旧cost式（最低料金100RP時代の係数）の検証用
COST_MODEL = {"sprigling_stats": {"model": "cost"}, "combat": {"multi_attack": False},
              "sprigling_cost": {"base_fee": 100}}


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
    apply_config_overrides({"sprigling_stats": {"model": "physical"}, "combat": {"multi_attack": True}})
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


def test_default_model_meets_1v1_rules():
    """既定（mass式）: 重量級は軽量級・中量級に、苔兵・棘走・岩守・毒舞は軽量級に1対1で負けない。
    動員コストが根張以上のSpriglingは根張（非戦闘職）に負けない。"""
    import verify_lab as V
    reset_config()
    rep = V.duel_report(80, "test", attribute=False)
    assert rep["class"]["heavy_vs_light"]["loss"] == 0.0
    assert rep["class"]["heavy_vs_middle"]["loss"] == 0.0
    assert rep["class"]["pricier_vs_root"]["loss"] == 0.0
    assert all(r["loss"] == 0.0 for r in rep["core_vs_light"].values())


def test_swap_roster_mode_swaps_rosters_in_second_game():
    import verify_lab as V
    sc = V.ai_eval_scenario("t", "heuristic", "heuristic", None, size=2, stratified=True, mode="swap")
    swapped = V._swap_sides_of_rosters(sc)
    assert swapped["rosters"]["A"] == sc["rosters"]["B"]
    assert swapped["start_reserve"]["B"] == sc["start_reserve"]["A"]
    assert sc["rosters"]["A"] != sc["rosters"]["B"]


def test_sprigling_only_game_uses_no_base_pieces():
    """対人戦の形: 既存5種を使わず、各側のSprigling 5体だけで最後まで対局できる。"""
    import verify_lab as V
    sc = V.ai_eval_scenario("only", "heuristic", "heuristic", None, size=5, mode="swap")
    V.apply_scenario(sc)
    kinds = set(sc["rosters"]["A"]) | set(sc["rosters"]["B"])
    for a_seat in (0, 1):
        g = V.play_game(sc, "only", a_seat)
        used = set(g["produced"]["A"]) | set(g["produced"]["B"])
        assert used <= kinds, used - kinds   # 既存5種は1体も動員されない
    game = V.Game(start_reserve={0: sc["start_reserve"]["A"], 1: sc["start_reserve"]["B"]})
    assert game.reserve[0] == sc["start_reserve"]["A"] and len(game.reserve[0]) == 5
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
