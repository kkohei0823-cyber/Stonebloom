# -*- coding: utf-8 -*-
"""
属性（三すくみ）・VP税1/100・重量→RP換算の回帰テスト。
  $ python3 -m pytest -q test_attribute_economy.py   （pytestが無ければ python3 test_attribute_economy.py）
"""

from config import CONFIG, reset_config, apply_config_overrides, weight_to_rp_cost
from game import Board, Economy, make_piece, type_multiplier, attribute_of


def test_existing_pieces_have_attributes():
    assert attribute_of("歩兵") == "bind"     # 苔兵
    assert attribute_of("騎兵") == "pierce"   # 棘走
    assert attribute_of("重装兵") == "crush"  # 岩守
    for kind in ("弓兵", "工兵", "本拠"):
        assert attribute_of(kind) is None


def test_attribute_triangle_doubles_damage():
    kinds = ["歩兵", "騎兵", "重装兵", "弓兵", "工兵", "本拠"]
    advantaged = {("歩兵", "騎兵"), ("騎兵", "重装兵"), ("重装兵", "歩兵")}
    for a in kinds:
        for d in kinds:
            expected = 2.0 if (a, d) in advantaged else 1.0
            assert type_multiplier(a, d) == expected, (a, d)


def _midgame_income(rp_before, owner_kind="歩兵"):
    """隅スポット1つを保有した状態でcompute_incomeを1回回し、(RP増分, VP増分)を返す。"""
    reset_config()
    ms = CONFIG["midgame_spots"]
    board = Board(CONFIG["board_size"])
    board.place(ms["points"][0], make_piece(owner_kind, 0))
    econ = Economy()
    econ.rp = [rp_before, 0]
    econ.spot_owner[ms["points"][0]] = 0
    econ.spot_owned_since[ms["points"][0]] = 0
    vp0 = econ.cumulative_vp[0]
    econ.compute_income(board, ms["start_round"], set())
    return econ, econ.cumulative_vp[0] - vp0


def test_midgame_vp_tax_is_one_per_hundred_rp():
    # base_income(100)は非課税、隅スポット200RPに対してVP-2
    econ, dvp = _midgame_income(0)
    assert econ.rp[0] == 300
    assert dvp == -2
    assert econ.vp_tax_carry[0] == 0


def test_midgame_vp_tax_carries_fraction_when_capped():
    cap = CONFIG["economy"]["rp_cap"]
    # 残り枠250: base100 → 隅スポットで150だけ実際に得る → VP-1、端数50を繰り越し
    econ, dvp = _midgame_income(cap - 250)
    assert econ.rp[0] == cap
    assert dvp == -1
    assert econ.vp_tax_carry[0] == 50
    assert isinstance(econ.cumulative_vp[0], int)


def test_weight_to_rp_cost():
    reset_config()
    assert weight_to_rp_cost(600) == 1000  # 重量級上限 = rp_cap
    assert weight_to_rp_cost(310) == 517   # 中量級上限
    assert weight_to_rp_cost(140) == 233   # 軽量級上限
    assert weight_to_rp_cost(0) == 0       # 最低料金なし（純粋な比例式）
    for bad in (-1, 600.5):
        try:
            weight_to_rp_cost(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(bad)
    # base_feeを入れると最低料金つきの式になる
    apply_config_overrides({"sprigling_cost": {"base_fee": 100}})
    assert weight_to_rp_cost(600) == 1000
    assert weight_to_rp_cost(0) == 100
    reset_config()


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
