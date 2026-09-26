
# -*- coding: utf-8 -*-
"""
config.py
==========================================================
ゲームバランス設定（CONFIG）の一元管理モジュール。他の全モジュールはここから import する。

鉄則: CONFIG = {...} のような再代入は禁止（参照が切れる）。書き換えは必ず
  CONFIG["pieces"]["工兵"]["hp"] = 300 のような直接編集、または
  apply_config_overrides() 経由で行うこと。

AIの評価重み（HEURISTIC_WEIGHTS）は weights.py に分離済み。上書き/リセットは
weights.py の apply_weight_overrides() / resolve_weights() を使うこと。
"""

import copy

# ============================================================
# CONFIG（旧 game.py から移設）
# ============================================================

# ============================================================
# RPスケール（Stonebloom×Whittlewisp統合企画書③タブ「RPスケールの変更」で確定）
# RP関連の数値（生産・配置コスト、RP収入、rp_cap、initial_rp）を全体100倍する。
# 目的はSpriglingのステータス（材質係数込みの重量等）に対応させるための
# 刻み幅の柔軟性確保。比率関係は変えていないため、旧バランス設計はそのまま流用できる。
# VP側（vp_spots等）は今回のスケール変更の対象外（据え置き）。
# ============================================================
RP_SCALE = 100

# ============================================================
# 属性（三すくみ）: Whittlewispの材質（bind/pierce/crush）と共通の語彙
# 既存の駒は 苔兵(歩兵)=bind / 棘走(騎兵)=pierce / 岩守(重装兵)=crush を持つ。
# 相性は bind→pierce→crush→bind の順に有利（有利側から不利側へのダメージが×2.0）。
# これは旧来の 歩兵→騎兵→重装兵→歩兵 と同じ向きなので、既存駒同士の相性は変わらない。
# 弓兵・工兵・本拠は属性なし(None)＝三すくみに関与しない。
# ============================================================
ATTRIBUTES = ("bind", "pierce", "crush")

CONFIG = {
    "board_size": 9,

    "pieces": {
        "歩兵":   {"hp": 200, "atk": 36, "move": 2, "cost": 3 * RP_SCALE, "produce_cost": 4 * RP_SCALE, "upkeep": 0, "ranged": False, "range": 0, "attribute": "bind"},
        "騎兵":   {"hp": 150, "atk": 48, "move": 3, "cost": 3 * RP_SCALE, "produce_cost": 5 * RP_SCALE, "upkeep": 0, "ranged": False, "range": 0, "attribute": "pierce"},
        "重装兵": {"hp": 300, "atk": 24, "move": 1, "cost": 4 * RP_SCALE, "produce_cost": 4 * RP_SCALE, "upkeep": 0, "ranged": False, "range": 0, "attribute": "crush"},
        "弓兵":   {"hp": 150, "atk": 36, "move": 2, "cost": 4 * RP_SCALE, "produce_cost": 5 * RP_SCALE, "upkeep": 0, "ranged": True,  "range": 1, "attribute": None},
        "工兵":   {"hp": 100, "atk": 12, "move": 1, "cost": 2 * RP_SCALE, "produce_cost": 3 * RP_SCALE, "upkeep": 0, "ranged": False, "range": 0, "attribute": None},
        "本拠":   {"hp": 600, "atk": 0, "move": 0, "cost": 0, "produce_cost": 0, "upkeep": 0, "ranged": False, "range": 0, "attribute": None},
    },

    "economy": {
        "base_income": 1 * RP_SCALE,
        "engineer_bonus": 1 * RP_SCALE,
        "rp_cap": 10 * RP_SCALE,       # 旧上限10→新上限1000
        "move_action_cost": 1,          # 移動アクション自体のコスト（RPではなく手数の消費）のため据え置き
        "initial_rp": 5 * RP_SCALE,
        "initial_vp": 1000,  # 表示・内部計算上のバッファ。両者同額なので勝敗判定には無関係（VPは今回のスケール対象外）
    },

    # RPスポット（4-4点）: RPのみ産出、VPには直結しない
    # （旧称「資源スポット」「経済スポット」。「星」は誤記だったため削除――
    # 「星」は下記vp_spotsの3-3点(三々)を指す語であり、ここでは無関係）
    "rp_spots": {
        "points": [(3, 3), (3, 5), (5, 3), (5, 5)],
        "income": 1 * RP_SCALE,
        "capture_income_delay": 1,  # 占有してからRP収入が発生するまでのラウンド数
    },

    # 中盤解禁スポット（隅）: start_round以降のみ産出。得たRPに比例したVPを同時に徴収する
    # ことで「序盤=RPスポット→中盤=ここの争奪→終盤=VP化」を狙う。
    # RP100倍化の副作用（旧「RP1につきVP1」のままだとVP負担まで100倍になる）への対応として、
    # 換算比率を「実際に得たRP rp_per_vp_upkeep(=100)につきVP1」に変更した
    # （例: 200RP獲得→VP-2。旧スケールの「RP2→VP-2」と同じ負担）。
    # RP上限に当たって100未満の端数しか得られなかった分は、プレイヤーごとに繰り越して
    # 累計100に達した時点でVP1を徴収する（VPは常に整数、長期的な負担は厳密に1/100）。
    "midgame_spots": {
        "points": [(1, 1), (1, 7), (7, 1), (7, 7)],
        "start_round": 10,
        "income": 2 * RP_SCALE,
        "capture_income_delay": 1,
        "rp_per_vp_upkeep": 1 * RP_SCALE,
    },

    # VP（勝利条件）: 3-3点(三々)と天元。RPは生まない
    "vp_spots": {
        "stars": [(2, 2), (2, 6), (6, 2), (6, 6)],
        "tengen": [(4, 4)],
        "star_vp": 3,
        "tengen_vp": 5,
        "count_start_round": 4,  # 4ラウンド目からVP加算開始
        "kill_bonus_vp": 30,  # VP地点駐留中の駒を撃破されると相手にこのVPが入る
    },

    # 属性相性（三すくみ）: 駒の"attribute"で判定する。bind→pierce→crush→bindの順に有利（×2.0）。
    # 既存駒では 苔兵(歩兵)→棘走(騎兵)→岩守(重装兵)→苔兵。属性なし(None)の駒は関与しない。
    "type_advantage": {
        "pairs": {"bind": "pierce", "pierce": "crush", "crush": "bind"},
        "multiplier": 2.0,
    },

    # 完全ダメージ無効化: 工兵は弓兵タイプの攻撃を距離を問わず常に無効化する
    # （呼吸点方式の分割対象からも除外。三すくみのtype_advantageとは別枠の判定）
    "damage_immunity": {
        "pairs": {"弓兵": "工兵"},
    },

    # 素早さ（game.resolve_combat参照）。既存5種は全てbase_speedなので、どちらのフラグを
    # 有効にしても既存駒どうしの戦闘は変わらない（Spriglingとの戦闘にだけ効く）。
    "combat": {
        "base_speed": 100,
        "initiative": False,       # 速い駒から先に攻撃し、撃破された駒は反撃できない
        "multi_attack": False,     # 素早さの比が閾値以上なら攻撃回数が増える
        "multi_attack_thresholds": [2.0, 3.0, 4.0],   # 2倍→2回, 3倍→3回, 4倍→4回
    },

    "starting_reserve": ["歩兵", "騎兵", "重装兵", "弓兵", "工兵"],
    "base_positions": {0: (4, 8), 1: (4, 0)},  # 2026-09-03変更: 先手(player0)=下段中央 / 後手(player1)=上段中央

    "turn_limit_per_player": 30,
    "pie_rule": False,  # 凍結中（cut-and-choose構造の不公平さのため）
    "pie_rule_swap_turn_order": True,  # Falseにすると所有権のみスワップ（手番順は維持）
    "alternate_initiative_each_round": True,  # 毎ラウンド先後を交互に入れ替える
    "recapture_ban_enabled": True,  # 駒を失った地点への即時再配置を次ラウンドのみ禁止
    "production_enabled": True,  # RPで新規駒を手持ちに追加できる（ターン消費なし）
    "max_rounds_safety": 200,  # 無限ループ防止の安全装置
    "max_owned_pieces": 9,  # reserve+盤上駒の合計上限（本拠は除く）。企画書2.2節

    # Whittlewisp重量 → 動員コスト(RP)の換算（weight_to_rp_cost()参照）
    # 「動員コスト」はコード上のキー"produce_cost"（旧称: 生産コスト）。駒を一から作るのではなく
    # 呼び寄せる、という表現に合わせて呼び名を変えた（キー名は互換のため据え置き）。
    #   cost = base_fee + (cost_at_heavy_limit - base_fee) * weight / heavy_weight_limit
    # 重量級上限(600)でちょうど1000RP(=rp_cap)。base_feeは「どんなに軽い駒でも最低限かかる
    # 動員費」で、1ラウンド分の基礎収入(100)に揃えてある。base_fee=0にすれば純粋な比例式になる。
    "sprigling_cost": {
        "heavy_weight_limit": 600,          # Whittlewisp config.WEIGHT_CLASS_LIMITS["heavy"]
        "cost_at_heavy_limit": 10 * RP_SCALE,
        "base_fee": 1 * RP_SCALE,
    },

    # Sprigling(Whittlewispの駒)のステータス算出（sprigling.derive_stats()参照）
    #   総合力 HP×攻撃力 = power_per_rp × anchor × (動員コスト/anchor)^power_cost_exponent
    #                      （移動力3なら × move3_power_mult）
    #   既存の苔兵/棘走/岩守はHP×攻撃力=7200、動員コスト400/500/400。power_per_rp=18は
    #   苔兵・岩守と同じ「RPあたりの強さ」、移動力3の割引0.8は棘走(7200/500=14.4)と同じ比率。
    #   → anchor(450RP)で既存駒の1.125倍、R/H/W(約410-510RP)で約0.95-1.4倍、
    #     軽量級上限(310RP)で約0.53倍、重量級上限(1000RP)で約5.6倍。
    #   HPと攻撃力の配分（形）は部位ごとのWhittlewisp式で決める:
    #     shape = (攻撃力の生値 / 耐久の生値) / shape_ref_ratio  を [shape_min, shape_max] にクランプし、
    #     攻撃力/HP = ref_atk_hp_ratio × shape（shape=1で苔兵と同じ比率 36/200）。
    "sprigling_stats": {
        # "cost": 強さの総量を動員コストで決める（下のpower_*）
        # "physical": 部位の式と重量からHP・攻撃力・素早さを直接決める（下のphysical）
        "model": "cost",
        "physical": {
            "weight_ref": 250,        # 重量の基準点（中量級の標準ビルド付近）
            "atk_scale": 2.0,         # R/H/Wの攻撃力が既存駒並み(約40)になる値
            "atk_weight_exp": 1.0,    # 攻撃力 ∝ 技の威力 × (重量/基準)^これ
            "hp_scale": 1.4,          # R/H/WのHPが既存駒並み(約230)になる値
            "hp_weight_exp": 0.5,     # HP ∝ 耐久合計 × (重量/基準)^これ
            "speed_weight_exp": 0.5,  # 素早さ ∝ (基準/重量)^これ
            "leg_speed_coef": 0.1,    # 素早さ × (1 + これ × 脚の平均length)
        },
        "power_per_rp": 18.0,
        # 総合力を動員コストの何乗で伸ばすか（anchor RPでの値は power_per_rp×anchor で固定）。
        #   power = power_per_rp × anchor × (動員コスト/anchor)^power_cost_exponent
        # 1.0なら純粋な比例。1より大きいと安い駒ほど割高になる（数で押す戦術の抑制）。
        # 2.0は verify_lab.py の実対局（階級別に既存5種のみの相手と400局）で決めた暫定値:
        #   1.0だと軽量級込みの側が0.80で圧勝（安い駒の数押し）、2.0で軽量0.48/中量0.56/重量0.55。
        "power_cost_exponent": 2.0,
        "power_anchor_cost": 450,
        "move3_power_mult": 0.8,
        "ref_atk_hp_ratio": 0.18,
        "shape_ref_ratio": 0.115,   # R/H/Wの(攻撃力生値/耐久生値)の平均
        "shape_min": 0.25,
        "shape_max": 4.0,
        "atk_secondary_share": 0.25,
        "move_base": 2,
        "move_long_leg_threshold": 1.0,       # 脚の平均lengthがこれ以上なら移動力+1
        "move_heavy_weight_threshold": 310,   # 重量がこれを超える（重量級）なら移動力-1
        # 配置コスト = 動員コスト × この比率（暫定。既存5種の配置/動員の平均比≈0.76）
        "placement_cost_ratio": 0.75,
    },
}


def weight_to_rp_cost(weight):
    """Whittlewispのビルド重量(Creature.build_weight)を動員コスト(RP, 整数)に換算する。

    base_fee + (cost_at_heavy_limit - base_fee) * weight / heavy_weight_limit を
    四捨五入して整数RPにする。既定値では
      light上限140 → 310RP / middle上限310 → 565RP / heavy上限600 → 1000RP。
    重量級上限を超えるビルドは動員できないためValueErrorにする。"""
    sc = CONFIG["sprigling_cost"]
    limit = sc["heavy_weight_limit"]
    if weight < 0 or weight > limit + 1e-9:
        raise ValueError(f"weight {weight} is outside 0..{limit} (heavy class limit)")
    base = sc["base_fee"]
    raw = base + (sc["cost_at_heavy_limit"] - base) * weight / limit
    return int(raw + 0.5 + 1e-9)


# ============================================================
# 上書き・リセット用のヘルパー（全チューニング/検証スクリプトはこれ経由で統一する）
# 重み(HEURISTIC_WEIGHTS)側の同種ヘルパーはweights.pyにある。混在させないこと。
# ============================================================
_BASELINE_CONFIG = copy.deepcopy(CONFIG)

# 2026-09-07追加（AI思考速度の高速化対応。検証ハンドブック3.18節参照）:
# CONFIGはreset_config()/apply_config_overrides()経由でのみ書き換えられる
# （鉄則。ファイル冒頭のコメント参照）ため、この2関数が呼ばれた回数を数えるだけで
# 「CONFIGの中身が変わった可能性がある」ことを検出できる。search_bot_skeleton.py側で
# CONFIG["vp_spots"]/["rp_spots"]/["midgame_spots"]から毎回setを作り直す代わりに、
# このバージョン番号をキーにした結果をキャッシュし、Optunaのtrialが切り替わって
# apply_config_overrides()が呼ばれた時だけ再計算するようにする
# （spotsの座標自体はどのtrialでもWEIGHT_SEARCH_SPACE/pieces調整の対象になっておらず
# 実質不変だが、将来対象になった場合でも安全なようにバージョン方式にしてある）。
_CONFIG_VERSION = 0


def config_version():
    """現在のCONFIGの「世代」を返す。reset_config()/apply_config_overrides()が
    呼ばれるたびに1つ進む。呼び出し側はこの値をキャッシュキーとして使うことで、
    CONFIGの該当部分が実際に変わった時だけ再計算すればよくなる。"""
    return _CONFIG_VERSION


def reset_config():
    """CONFIGをこのモジュール読み込み時点の値に戻す（オブジェクトはその場書き換え）。"""
    global _CONFIG_VERSION
    CONFIG.clear()
    CONFIG.update(copy.deepcopy(_BASELINE_CONFIG))
    _CONFIG_VERSION += 1


def _deep_update(base, updates):
    for k, v in updates.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_update(base[k], v)
        else:
            base[k] = v


def apply_config_overrides(overrides):
    """CONFIGへの上書きをベースライン(初期値)から再適用する。

    overrides は以下のいずれかの形式:
      - [(piece_kind, stat, value), ...]  例: [("工兵", "hp", 300)]
      - {"pieces": {"工兵": {"hp": 300}}, "economy": {"rp_cap": 8}} のようなネスト辞書

    毎回ベースラインから再適用するのは、Optunaの各trialが独立した設定の
    上で評価されるようにするため（前のtrialの上書きが残らないようにする）。
    """
    reset_config()
    if overrides is None:
        return
    if isinstance(overrides, dict):
        _deep_update(CONFIG, overrides)
    else:
        for kind, stat, value in overrides:
            CONFIG["pieces"][kind][stat] = value





