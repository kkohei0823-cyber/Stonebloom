# -*- coding: utf-8 -*-
"""
sprigling.py
==========================================================
Whittlewispのビルド（部位ごとの size/length/angle/本数/材質）から、Stonebloomの駒
（Sprigling）のステータスを算出して CONFIG["pieces"] に登録するブリッジ。

算出の流れ（係数は全て CONFIG["sprigling_stats"] / CONFIG["sprigling_cost"]）:
  1. Whittlewispの Creature を実際に組み立てる（個体差ロールは seed で固定＝再現可能）
  2. 重量   = Creature.build_weight（Whittlewispの重量式そのまま）
  3. 総合力 = HP×攻撃力 = power_per_rp × anchor × (動員コスト/anchor)^power_cost_exponent
             （移動力3なら割引）。強さは動員コスト（＝重量）の2乗で伸びる。比例（1乗）だと
             安い軽量級を数で押す戦術が実対局で圧勝したため（config.pyのコメント参照）。
  4. 配分   = HPと攻撃力の比は、部位ごとのWhittlewisp式から決める:
               耐久の生値   = Σ 全部位インスタンスの耐久(compute_max_dur、材質係数込み)
               攻撃力の生値 = 最強部位の技威力 + atk_secondary_share × 他部位の技威力の合計
                 （技威力は simulator.py の base_damage = (5 + size*3) × 技倍率 を、その部位が
                   獲得している技のうち最大のもので評価。他部位ぶんを少し足すのは、部位疲労で
                   同じ部位を連打できず控えの攻撃部位が継戦能力になるため）
             生値をそのまま使わないのは、顔の器官(重量係数0.5)が重量の割に高い耐久・威力を
             持ち、軽量級でも中量級並みの生値が出てしまうため（Whittlewisp側ではリーチ・高さの
             ルールで釣り合っているが、Stonebloomにはそれが無い）。
  5. 移動力 = 脚の長さと重量から段階的に決める（move_* 係数参照）。
             Whittlewispの raw_speed は現行の重量スケール（200〜300台）だと
             全ビルドで speed_factor=1.0 に張り付いて差が出ないため、ここでは使わない。
  6. 属性   = 重量への寄与が最も大きい材質（bind/pierce/crush）
  7. 動員コスト(produce_cost) = weight_to_rp_cost(重量)
     配置コスト(cost)         = 動員コスト × placement_cost_ratio（四捨五入）
"""

import contextlib
import importlib.util
import io
import math
import os
import random
import sys

import config as sb_config
from config import CONFIG, weight_to_rp_cost

_WW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "whittlewisp")
_ww = {}


def _load_whittlewisp():
    """Whittlewispの config/models を別名で読み込む（Stonebloomのconfigと衝突させない）。"""
    if _ww:
        return _ww["config"], _ww["models"]
    saved = sys.modules.get("config")
    try:
        spec = importlib.util.spec_from_file_location("ww_config", os.path.join(_WW_DIR, "config.py"))
        ww_config = importlib.util.module_from_spec(spec)
        with contextlib.redirect_stdout(io.StringIO()):  # 読み込み時の案内printを抑止
            spec.loader.exec_module(ww_config)
        # 階級超過の警告printを抑止（Stonebloom側では weight_to_rp_cost が上限判定する）
        ww_config.WEIGHT_TEST_CLASS = None
        sys.modules["config"] = ww_config
        spec = importlib.util.spec_from_file_location("ww_models", os.path.join(_WW_DIR, "models.py"))
        ww_models = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(ww_models)
    finally:
        if saved is not None:
            sys.modules["config"] = saved
        else:
            sys.modules.pop("config", None)
    _ww["config"], _ww["models"] = ww_config, ww_models
    return ww_config, ww_models


# ============================================================
# ビルド（genome）の表現
# {part_type: {"size": int, "length": int, "angle": int, "count": int, "material": str}}
# ============================================================
PART_TYPES = ("head", "neck", "torso", "arm", "leg", "tail",
              "mouth", "nose", "eye", "ear", "wing")
VITAL = ("head", "neck", "torso")
PARAM_MIN, PARAM_MAX = -2, 2
ANGLE_TYPES = ("torso", "tail")
# 本数の上限。対の器官は配置パターンが定義されている5まで、単独器官は2まで。
# wingは企画書で凍結中（count=0固定）。
COUNT_MAX = {"arm": 4, "leg": 4, "eye": 4, "ear": 4, "wing": 0,
             "tail": 2, "mouth": 2, "nose": 2}


def part(size=0, length=0, angle=0, count=1, material="bind"):
    return {"size": size, "length": length, "angle": angle, "count": count, "material": material}


def _genome(**parts):
    g = {pt: part(count=(1 if pt in VITAL else 0)) for pt in PART_TYPES}
    g.update(parts)
    return g


# Whittlewisp battle.py の R/H/W（中量級の基準ビルド。材質は全て既定のbind）
REFERENCE_BUILDS = {
    "R": _genome(
        neck=part(0, 2), arm=part(0, 2, count=1), leg=part(-1, 0, count=2), tail=part(0, 0),
        mouth=part(0, 2), nose=part(0, 2), eye=part(0, 0, count=2), ear=part(0, 0, count=2)),
    "H": _genome(
        neck=part(0, 2), torso=part(1, 0, 3), arm=part(0, 0, count=1), leg=part(-1, 1, count=2),
        tail=part(0, 0, 3), mouth=part(0, -2), nose=part(0, -2),
        eye=part(0, 0, count=2), ear=part(0, 0, count=2)),
    "W": _genome(
        arm=part(1, -1, count=2), leg=part(2, -2, count=2), tail=part(0, 0),
        mouth=part(0, -2), nose=part(0, -2), eye=part(0, 0, count=2), ear=part(0, 0, count=2)),
}


def validate_genome(genome):
    """ビルドがルール上作れるものかを検査し、違反内容のリストを返す（空なら合法）。"""
    errors = []
    ww_config, _ = _load_whittlewisp()
    for pt in PART_TYPES:
        p = genome.get(pt)
        if p is None:
            errors.append(f"{pt}: missing")
            continue
        for k in ("size", "length"):
            if not PARAM_MIN <= p[k] <= PARAM_MAX:
                errors.append(f"{pt}.{k}={p[k]} out of {PARAM_MIN}..{PARAM_MAX}")
        if pt in ANGLE_TYPES and not 0 <= p["angle"] <= 3:
            errors.append(f"{pt}.angle={p['angle']} out of 0..3")
        if pt in VITAL:
            if p["count"] != 1:
                errors.append(f"{pt}: vital part must have count=1")
        elif not 0 <= p["count"] <= COUNT_MAX[pt]:
            errors.append(f"{pt}.count={p['count']} out of 0..{COUNT_MAX[pt]}")
        if p["material"] not in sb_config.ATTRIBUTES:
            errors.append(f"{pt}.material={p['material']}")
    if not errors and not any(genome[pt]["count"] > 0 for pt in PART_TYPES if pt not in VITAL):
        # Whittlewispのis_alive()の抜け穴（非vital全0だとcoreHP以外で負けない）を塞ぐ
        errors.append("at least one non-vital part is required")
    return errors


def build_creature(genome, seed=0, name="sprigling"):
    """genomeからWhittlewispのCreatureを組み立てる。個体差ロール・配置パターンの乱数は
    seedで固定し、呼び出し前のグローバル乱数状態は復元する（再現性のため）。"""
    _, ww_models = _load_whittlewisp()
    state = random.getstate()
    random.seed(seed)
    try:
        parts = []
        for pt in PART_TYPES:
            p = genome[pt]
            prof = ww_models.PartProfile(pt, size=p["size"], length=p["length"], angle=p["angle"],
                                         is_vital=pt in VITAL, part_type=pt, material=p["material"])
            prof.build_instances(p["count"])
            parts.append(prof)
        return ww_models.Creature(name, parts)
    finally:
        random.setstate(state)


def _part_attack_power(ww_models, ww_config, prof):
    """その部位が獲得している技のうち最大の base_damage（Whittlewisp simulator.pyと同式）。"""
    if not prof.instances:
        return 0.0
    max_size = max(inst.size for inst in prof.instances)
    max_length = max(inst.length for inst in prof.instances)
    mults = {"thrust": ww_config.ARCH_MULT_THRUST, "low": ww_config.ARCH_MULT_LOW,
             "sweep": ww_config.ARCH_MULT_SWEEP, "heavy": ww_config.ARCH_MULT_HEAVY,
             "strike": ww_config.ARCH_MULT_STRIKE}
    best = 0.0
    for _label, archetype, cond in ww_models.MOVE_ACQUISITION_TABLE:
        if cond(max_size, max_length):
            best = max(best, (5.0 + max_size * 3.0) * mults[archetype])
    return best


def raw_stats(genome, seed=0):
    """係数を掛ける前のWhittlewisp由来の生値を返す。"""
    ww_config, ww_models = _load_whittlewisp()
    cr = build_creature(genome, seed)
    weight = cr.build_weight
    dur = sum(inst.max_surface_dur for p in cr.parts.values() for inst in p.instances)
    powers = sorted((_part_attack_power(ww_models, ww_config, p) for p in cr.parts.values()),
                    reverse=True)
    leg = cr.parts["leg"]
    leg_len = (sum(i.length for i in leg.instances) / len(leg.instances)) if leg.instances else None
    mat_weight = {}
    for p in cr.parts.values():
        for inst in p.instances:
            w = ((inst.length + 3.0) * (inst.size + 3.0) * ww_models.WEIGHT_COEF.get(p.part_type, 1)
                 * ww_models.MATERIAL_WEIGHT_COEF.get(p.material, 1.0))
            mat_weight[p.material] = mat_weight.get(p.material, 0.0) + w
    return {"weight": weight, "durability": dur, "part_powers": powers,
            "leg_len": leg_len, "leg_count": len(leg.instances), "material_weight": mat_weight}


def weight_class_of(weight):
    for name, limit in (("light", 140), ("middle", 310), ("heavy", 600)):
        if weight <= limit:
            return name
    return None


def derive_stats(genome, seed=0):
    """genome(+seed)からStonebloomの駒ステータス（CONFIG["pieces"]の1エントリ形式）を算出する。"""
    errors = validate_genome(genome)
    if errors:
        raise ValueError("invalid genome: " + "; ".join(errors))
    st = CONFIG["sprigling_stats"]
    raw = raw_stats(genome, seed)
    weight = raw["weight"]
    produce_cost = weight_to_rp_cost(weight)  # 重量級上限超過はここでValueError

    if raw["leg_count"] == 0:
        move = 1
    else:
        move = st["move_base"]
        if raw["leg_len"] >= st["move_long_leg_threshold"]:
            move += 1
        if weight > st["move_heavy_weight_threshold"]:
            move -= 1
    move = max(1, min(3, move))

    powers = raw["part_powers"]
    atk_raw = (powers[0] if powers else 0.0) + st["atk_secondary_share"] * sum(powers[1:])
    speed = CONFIG["combat"]["base_speed"]

    if st["model"] == "physical":
        ph = st["physical"]
        wr = weight / ph["weight_ref"]
        # 攻撃力: 技の威力（部位の大きさ×技倍率）に、体重を乗せて殴る分の係数を掛ける
        atk = max(1, int(ph["atk_scale"] * atk_raw * wr ** ph["atk_weight_exp"] + 0.5))
        # HP: 全部位の耐久の合計に、体の重さ（＝体の大きさ）の係数を掛ける
        hp = max(1, int(ph["hp_scale"] * raw["durability"] * wr ** ph["hp_weight_exp"] + 0.5))
        # 素早さ: 軽いほど速く、脚が長いほど速い（Whittlewispのraw_speedと同じ向き）
        leg = raw["leg_len"] if raw["leg_count"] else -2.0
        speed = (CONFIG["combat"]["base_speed"] * wr ** (-ph["speed_weight_exp"])
                 * max(0.25, 1.0 + ph["leg_speed_coef"] * leg))
        speed = round(speed, 1)
        shape = None
        return _finish(genome, seed, raw, weight, produce_cost, hp, atk, move, speed, shape)

    # 総合力（HP×攻撃力）は動員コストで決まり、配分（形）は部位ごとの式で決まる
    anchor = st["power_anchor_cost"]
    power = (st["power_per_rp"] * anchor * (produce_cost / anchor) ** st["power_cost_exponent"]
             * (st["move3_power_mult"] if move == 3 else 1.0))
    shape = (atk_raw / raw["durability"]) / st["shape_ref_ratio"]
    shape = max(st["shape_min"], min(st["shape_max"], shape))
    ratio = st["ref_atk_hp_ratio"] * shape  # 攻撃力/HP
    hp = max(1, int(math.sqrt(power / ratio) + 0.5))
    atk = max(1, int(math.sqrt(power * ratio) + 0.5))
    return _finish(genome, seed, raw, weight, produce_cost, hp, atk, move, speed, round(shape, 3))


def _finish(genome, seed, raw, weight, produce_cost, hp, atk, move, speed, shape):
    st = CONFIG["sprigling_stats"]
    mw = raw["material_weight"]
    attribute = max(sb_config.ATTRIBUTES, key=lambda m: (mw.get(m, 0.0), m == "bind"))

    cost = int(produce_cost * st["placement_cost_ratio"] + 0.5)
    return {
        "hp": hp, "atk": atk, "move": move, "speed": speed,
        "cost": cost, "produce_cost": produce_cost,
        "upkeep": 0, "ranged": False, "range": 0,
        "attribute": attribute,
        "sprigling": {"weight": round(weight, 2), "weight_class": weight_class_of(weight),
                      "seed": seed, "shape": shape},
    }


# ============================================================
# 役割（role）: AIの駒種別の重み（配置/動員の好み、壁役・突撃役ボーナス等）は既存5種の
# 名前で引いている。Spriglingは性能値が最も近い既存の三すくみ駒種の役割を借りる。
# ============================================================
_ROLE_KINDS = ("歩兵", "騎兵", "重装兵")


def nearest_role(stats):
    def dist(kind):
        ref = CONFIG["pieces"][kind]
        return ((stats["hp"] - ref["hp"]) / 300.0) ** 2 + ((stats["atk"] - ref["atk"]) / 48.0) ** 2 \
            + ((stats["move"] - ref["move"]) / 3.0) ** 2
    return min(_ROLE_KINDS, key=dist)


def register_sprigling(name, genome, seed=0):
    """SpriglingをCONFIG["pieces"]に駒種 "S:<name>" として登録し、その駒種名を返す。
    CONFIGはreset_config()/apply_config_overrides()で初期値に戻るため、
    それらの後に呼ぶこと。"""
    stats = derive_stats(genome, seed)
    stats["role"] = nearest_role(stats)
    kind = f"S:{name}"
    CONFIG["pieces"][kind] = stats
    return kind


def is_sprigling(kind):
    return kind.startswith("S:")


# ============================================================
# ランダムビルド生成（検証環境用）
# ============================================================
def random_genome(rng, weight_class=None, material=None, max_tries=2000, seed=0):
    """合法なランダムビルドを返す。weight_classを指定するとその階級の重量帯
    （light: 0-140 / middle: 140-310 / heavy: 310-600）に入るまで引き直す。
    materialを指定しなければ部位ごとにランダム。"""
    for _ in range(max_tries):
        g = {}
        for pt in PART_TYPES:
            if pt in VITAL:
                count = 1
            else:
                count = rng.randint(0, COUNT_MAX[pt])
            g[pt] = part(size=rng.randint(PARAM_MIN, PARAM_MAX),
                         length=rng.randint(PARAM_MIN, PARAM_MAX),
                         angle=rng.randint(0, 3) if pt in ANGLE_TYPES else 0,
                         count=count,
                         material=material or rng.choice(sb_config.ATTRIBUTES))
        if validate_genome(g):
            continue
        w = raw_stats(g, seed)["weight"]
        wc = weight_class_of(w)
        if wc is None or (weight_class is not None and wc != weight_class):
            continue
        return g
    raise RuntimeError(f"could not sample a {weight_class} genome in {max_tries} tries")
