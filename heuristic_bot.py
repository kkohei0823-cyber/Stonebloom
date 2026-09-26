"""
ヒューリスティックボット
==========================================================
game.py のゲームエンジンをそのまま使い、1手先だけを評価する
「欲張り法」のボットを実装する。

このファイルは game.py と同じフォルダに置いて実行してください。
  $ python3 heuristic_bot.py

評価する要素（HEURISTIC_WEIGHTS で調整可能）:
  - vp_star / vp_tengen : 星(vp_star)・天元(vp_tengen)に置く・移動することの価値（VP評価）
  - rp_spot    : RPスポットに置く・移動することの価値
  - support         : 自軍の駒が隣接している数（連結による安全性）
  - danger          : 敵の駒が隣接している数（危険度のペナルティ）
  - attack          : その手を打った後、次の戦闘解決で実際に与えられるダメージ量の評価
  - kill_bonus      : その手によって撃破が見込める敵の数に対する追加ボーナス
  - vp_spot_kill_bonus : VPスポット駐留駒の撃破（+100VPを生む）への追加加点
  - advance         : 敵本拠に近づく移動へのボーナス（攻勢の評価）
  - efficiency      : 配置コストに対するHP+ATKの効率（駒選択の評価）
  - rp_cost         : そのアクションが実際に消費するRPに対するペナルティ
                      （「移動は無料ではない」ことを評価に反映させ、無意味な
                      移動でRPを浪費する挙動を抑える）
  - jitter          : 同点候補が並んだ時にランダム性を持たせるための微小ノイズ
                      （これがないとボット同士が毎回全く同じ試合を繰り返してしまう）

このボットは「1手先の盤面変化」のみを見ており、複数手先を読む探索（MCTS等）は
行っていない。あくまでランダムボットより一段階賢い、というレベルの検証用。

2026-08-03 修正: 旧バージョンは「敵に隣接する/射程内に入る」ことを danger で
一方的に減点するだけで、その隣接・射程によって実際に与えられるダメージや
撃破チャンスを一切評価していなかった。このため、駒を撃破できる位置をボットが
自ら手放す・資源より優先すべき攻撃機会を無視する、といった弱い挙動が多数
観測された（棋譜レビューで報告された事例1〜4はいずれもこれが主因）。
attack / kill_bonus 項を追加し、危険（danger）と見返り（attack）を同時に
評価できるようにした。

2026-08-03 その2の修正（対戦検証で新たに判明した弱い挙動への対応）:
  - 「ラウンド1で本拠隣に騎兵を置いて攻撃し続けていたのに、ラウンド2で
    得もしないのに中央へ移動してしまう」という報告があった。原因は、旧実装が
    移動先(dst)のスコアだけを評価し、移動元(src)を離れることで失う価値
    （そこで与え続けていたダメージ、支援、資源/VPスポットの占有など）を
    一切評価していなかったこと。_position_hold_value() を追加し、
    「移動後の価値 - 元の位置に留まる価値」の差分でスコアを計算するように
    修正した（下記 choose() 参照）。これにより、良いポジションを理由なく
    手放す挙動が起きにくくなる。
  - 移動には実際には1RPのコストがかかるが、旧実装のスコアにはRPコストが
    一切反映されておらず、「タダで動かせる」かのように評価していた。
    rp_cost 項を追加し、無意味な移動を選びにくくした。
  - rp_spot の重みを、RPスポットの実収入引き上げ（3→5RP、game.py参照）に
    合わせて 6→10 に引き上げた。
"""

import random
# CONFIGはconfig.py、HEURISTIC_WEIGHTSはweights.pyに実体がある（詳細は各モジュール冒頭参照）
from config import CONFIG, RP_SCALE
from weights import HEURISTIC_WEIGHTS, to_engine_signed, resolve_opponent_weights
from game import (
    Game, Board, Piece, RandomBot, run_batch as random_run_batch, type_multiplier, role_of,
    is_damage_nullified,
)
from collections import Counter

# 低レベル評価関数群はscoring_common.pyに集約（search_bot_skeleton.pyとも共有するため）。
# HeuristicBot側の呼び出しコードを変えずに済むよう再importしてモジュール直下の名前で使う。
from scoring_common import (
    _new_placement_threat, _existing_incoming_damage, _ranged_enemy_pieces,
    _potential_incoming_damage, _own_base_threat, _hypothetical_own_base_threat,
    _potential_attack, _neighbors_count, _position_hold_value,
    _count_adjacent_enemy_kind, _count_matchup_adjacent,
    _distance_to_center, _distance_to_nearest_corner, _nearest_enemy_distance,
    _base_hp_panic_multiplier, _hypothetical_next_round_income_margin,
    _base_pressure_ramp_multiplier,
    _opening_tempo_multiplier, _comeback_desperation_fraction,
    SIGNATURE_AFFINITY_KEY,
)

# 2026-08-25追加: Tier1重み#1-10（駒種別の配置/生産優先度）用のキー対応表。
# HEURISTIC_WEIGHTSのキー名はweights.pyのWEIGHT_TIERSと1対1で一致させている。
KIND_TO_PLACE_PREF = {
    "歩兵": "infantry_place_pref", "騎兵": "cavalry_place_pref",
    "重装兵": "heavy_place_pref", "弓兵": "archer_place_pref",
    "工兵": "engineer_place_pref",
}
KIND_TO_PRODUCE_PREF = {
    "歩兵": "infantry_produce_pref", "騎兵": "cavalry_produce_pref",
    "重装兵": "heavy_produce_pref", "弓兵": "archer_produce_pref",
    "工兵": "engineer_produce_pref",
}


class HeuristicBot:
    def __init__(self, weights=None, epsilon=0.05, rng=None):
        # 符号統一方針（weights.py参照）: 引数weightsはPENALTY_WEIGHT_KEYSも
        # 含め常に0以上の「大きさ」表記で渡ってくる。ここで一度だけ
        # to_engine_signed()を通し、以降の評価式（_score_actions等）は
        # 従来通り `score += x * self.w[key]` の形のまま変更しない。
        # 2026-09-12修正: search_bot_skeleton.SearchBotと同じ理由で、古い世代の
        # 重みスナップショットを渡された場合にKeyErrorにならないよう
        # resolve_opponent_weights()経由にする（詳細はそちらのdocstring参照）。
        self.w = to_engine_signed(
            resolve_opponent_weights(weights) if weights is not None else HEURISTIC_WEIGHTS
        )
        self.epsilon = epsilon
        self.rng = rng or random.Random()   # 専用の乱数生成器を持つ

    def _score_actions(self, game, player, actions):
        """各actionの評価値(jitter込み)を計算して [(score, action), ...] で返す。
        2026-08-09追加: choose()の本体だったループをそのまま抽出しただけで、
        計算式自体は一切変更していない（choose_with_debug()と共有するための
        リファクタリング。search_bot_skeleton.pyの_score_actionsと同種の役割）。"""
        scored = []
        enemy_base = CONFIG["base_positions"][1 - player]
        vp_stars = set(CONFIG["vp_spots"]["stars"])
        vp_tengen = set(CONFIG["vp_spots"]["tengen"])
        vp_spot_positions = vp_stars | vp_tengen
        rp_pts = set(CONFIG["rp_spots"]["points"])
        midgame_pts = set(CONFIG["midgame_spots"]["points"])
        # 2026-09-04追加: 本拠特攻の「序盤はノーリスクで確定加点」を抑えるための
        # base_pressureラウンド立ち上がり倍率。詳細はscoring_common.py
        # _base_pressure_ramp_multiplierのコメント参照。
        bp_mult = _base_pressure_ramp_multiplier(game.round_number, self.w["base_pressure_ramp_rounds"], self.w["rush_opening_pressure"])
        # 2026-09-09追加: advance_ramp_rounds。base_pressure_ramp_roundsと全く同じ
        # 関数を流用する（advanceがrampを持たず常にフル値が乗っていた問題への対応。
        # analyze_tuning_run_handoff.md 3.19節参照）。
        adv_mult = _base_pressure_ramp_multiplier(game.round_number, self.w["advance_ramp_rounds"])
        # 2026-09-09追加: opening_tempo_pref（3.20節#1）。序盤ほど強く、
        # OPENING_TEMPO_ROUNDS以降は0になる倍率。
        tempo_mult = _opening_tempo_multiplier(game.round_number)
        # 2026-09-09追加: comeback_desperation_pref（3.20節#2）。VP劣勢×終盤で[0,1]。
        desperation_frac = _comeback_desperation_fraction(game, player)
        # 2026-09-09追加: first_kill_momentum（3.20節#5）。自分が対局中に1体でも
        # 撃破していればTrue。
        first_kill_active = getattr(game, "total_kills", (0, 0))[player] > 0

        for action in actions:
            score = 0.0

            if action[0] == "place":
                _, kind, pos = action
                cfg = CONFIG["pieces"][kind]
                friendly, enemy = _neighbors_count(game.board, pos, player)
                score += friendly * self.w["support"]
                score += enemy * self.w["danger"]
                # 2026-08-30追加: 配置(place)を移動(move)より原則優先する。
                # 「持ち駒(reserve)」には9体の上限があり、盤上の駒には上限がない
                # （game.Game.owned_piece_count()参照）。配置と移動をほぼ同じ
                # 戦術評価式だけで比較していると、多少良い移動先がある時に
                # 配置を後回しにし続けてしまい、持ち駒が上限まで積み上がって
                # 生産自体ができなくなる。それを避けるため、配置には常に
                # 一定のボーナスを乗せ、「よほど良い移動でない限り配置する」
                # という原則を明示的な数値として表現する。
                score += self.w["placement_priority_bonus"]
                if pos in vp_tengen:
                    score += self.w["vp_tengen"]
                    score += enemy * self.w["vp_spot_guard_bonus"]
                elif pos in vp_stars:
                    score += self.w["vp_star"]
                    score += enemy * self.w["vp_spot_guard_bonus"]
                elif pos in rp_pts:
                    score += self.w["rp_spot"]
                    score += enemy * self.w["rp_spot_guard_bonus"]
                    if kind == "工兵":
                        score += self.w["engineer_econ_bonus"]
                    # 2026-09-09追加: opening_tempo_pref。序盤ほどRPスポット確保を
                    # 強く後押しする（3.20節#1）。
                    score += tempo_mult * self.w["opening_tempo_pref"] * 2.0
                elif pos in midgame_pts:
                    score += self.w["midgame_spot"]
                    score += enemy * self.w["midgame_spot_guard_bonus"]
                    if kind == "工兵":
                        score += self.w["engineer_econ_bonus"]
                elif kind == "工兵":
                    # Tier1 #14: 資源/中盤スポット以外での工兵単独行動（壁役）評価。
                    score += friendly * self.w["engineer_econ_solo_bonus"]
                # RP100倍化: costは新スケール(旧値×RP_SCALE)なので、efficiency側の重みが
                # 旧スケール準拠でチューニングされている前提が崩れないよう、ここでRP_SCALEで
                # 割り戻して旧スケール相当の値に正規化してから使う。
                score += (cfg["hp"] / 100 + cfg["atk"] / 10) / max(cfg["cost"] / RP_SCALE, 1) * self.w["efficiency"]
                dist = game.board.distance(pos, enemy_base)
                # 2026-09-09修正: advance_ramp_rounds（3.19節）。adv_mult=1.0が既定
                # （weight未設定なら従来通り）。
                score += (game.board.size - dist) * (self.w["advance"] * 0.2) * adv_mult

                # ---- Tier1/Tier2 追加重み（2026-08-25追加） ----
                score += self.w[KIND_TO_PLACE_PREF[role_of(kind)]]
                fav, unfav = _count_matchup_adjacent(game.board, pos, player, kind)
                score += fav * self.w["favorable_matchup_bonus"]
                score -= unfav * self.w["unfavorable_matchup_penalty"]
                if kind == "弓兵":
                    nullifiers = _count_adjacent_enemy_kind(game.board, pos, player, "工兵")
                    score -= nullifiers * self.w["archer_immunity_awareness"]
                if kind == "工兵":
                    shield_targets = _count_adjacent_enemy_kind(game.board, pos, player, "弓兵")
                    score += shield_targets * self.w["engineer_shield_bonus"]
                if role_of(kind) == "重装兵" and friendly >= 2:
                    score += self.w["heavy_frontline_bonus"]
                own_base = CONFIG["base_positions"][player]
                base_dist = game.board.distance(pos, own_base)
                if base_dist <= 2:
                    score += (3 - base_dist) * self.w["base_proximity_alert"]
                score += (game.board.size - _distance_to_center(game.board, pos)) \
                    * self.w["center_control"] * 0.1
                corner_dist = _distance_to_nearest_corner(game.board, pos)
                if corner_dist <= 2:
                    score -= (3 - corner_dist) * self.w["corner_edge_avoidance"]

                dmg, kills, vp_spot_kills, rp_spot_kills, base_dmg = _potential_attack(
                    game.board, pos, kind, player,
                    vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
                score += (dmg - base_dmg) * self.w["attack"] + base_dmg * self.w["base_pressure"] * bp_mult
                score += kills * self.w["kill_bonus"]
                score += kills * self.w["finishing_blow_bonus"]
                score += vp_spot_kills * self.w["vp_spot_kill_bonus"]
                score += rp_spot_kills * self.w["rp_spot_kill_bonus"]
                # RP100倍化: 同上の理由でRP_SCALEで正規化してからrp_cost重みを乗じる。
                score += (cfg["cost"] / RP_SCALE) * self.w["rp_cost"]
                # 2026-09-09追加: comeback_desperation_pref（3.20節#2）。VP劣勢×
                # 終盤ほど、危険への接近(enemy)・撃破そのものを積極的に評価する
                # （通常なら忌避したい相打ち上等のリスクを取りに行く）。
                if desperation_frac > 0:
                    boldness = desperation_frac * self.w["comeback_desperation_pref"]
                    score += boldness * enemy
                    score += boldness * kills * 2.0
                # 2026-09-09追加: first_kill_momentum（3.20節#5）。自分が対局中に
                # 既に1体でも撃破していれば、攻勢そのものをさらに後押しする。
                if first_kill_active:
                    score += self.w["first_kill_momentum"] * (dmg - base_dmg)
                    score += self.w["first_kill_momentum"] * kills * 2.0

                # 2026-08-09追加: 被ダメージ側の減点（attackの対）。詳細は
                # _potential_incoming_damage のコメント参照。
                incoming = _potential_incoming_damage(game.board, pos, kind, player)
                score += incoming * self.w["incoming_damage"]

                threat = _new_placement_threat(game, game.board, pos, kind, player)
                score += threat * self.w["exposure"]

                # 2026-08-14追加: この駒を配置した「後」の自陣本拠への被ダメージ見積もり。
                # base_pressure（敵本拠への攻撃評価）と対になる防御側の評価。詳細は
                # _hypothetical_own_base_threat / config.pyのbase_defenseコメント参照。
                base_threat_after = _hypothetical_own_base_threat(game.board, player, new_pos=pos, new_kind=kind)
                # base_hp_panic_threshold（0.86節）。weight=0なら常に1.0で無効化。
                panic = _base_hp_panic_multiplier(game.board, player, self.w["base_hp_panic_threshold"])
                score += base_threat_after * self.w["base_defense"] * panic

                # 2026-09-04追加: rp_income_margin（RPスポット確保を伴わない攻勢の
                # 「息切れ」評価）。この配置を実行した後の(自分の来期RP収入見積もり -
                # 相手の来期RP収入見積もり)を評価に織り込む。詳細はscoring_common.py
                # の_hypothetical_next_round_income_marginのコメント参照。
                income_margin_after = _hypothetical_next_round_income_margin(
                    game, player, new_pos=pos, new_kind=kind)
                score += income_margin_after * self.w["rp_income_margin"]

            elif action[0] == "move":
                _, src, dst = action
                piece = game.board.grid[src]
                friendly, enemy = _neighbors_count(game.board, dst, player, exclude=src)
                score += friendly * self.w["support"]
                score += enemy * self.w["danger"]
                if dst in vp_tengen:
                    score += self.w["vp_tengen"]
                    score += enemy * self.w["vp_spot_guard_bonus"]
                elif dst in vp_stars:
                    score += self.w["vp_star"]
                    score += enemy * self.w["vp_spot_guard_bonus"]
                elif dst in rp_pts:
                    score += self.w["rp_spot"]
                    score += enemy * self.w["rp_spot_guard_bonus"]
                    if piece.kind == "工兵":
                        score += self.w["engineer_econ_bonus"]
                    # 2026-09-09追加: opening_tempo_pref（place側と同じ）。
                    score += tempo_mult * self.w["opening_tempo_pref"] * 2.0
                elif dst in midgame_pts:
                    score += self.w["midgame_spot"]
                    score += enemy * self.w["midgame_spot_guard_bonus"]
                    if piece.kind == "工兵":
                        score += self.w["engineer_econ_bonus"]
                elif piece.kind == "工兵":
                    score += friendly * self.w["engineer_econ_solo_bonus"]
                old_dist = game.board.distance(src, enemy_base)
                new_dist = game.board.distance(dst, enemy_base)
                advanced = new_dist < old_dist
                if advanced:
                    # 2026-09-09修正: advance_ramp_rounds。
                    score += self.w["advance"] * adv_mult
                    if role_of(piece.kind) == "騎兵" and new_dist < game.board.size // 2:
                        score += self.w["cavalry_overextend_tolerance"]

                dmg, kills, vp_spot_kills, rp_spot_kills, base_dmg = _potential_attack(
                    game.board, dst, piece.kind, player,
                    exclude=src, vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
                score += (dmg - base_dmg) * self.w["attack"] + base_dmg * self.w["base_pressure"] * bp_mult
                score += kills * self.w["kill_bonus"]
                score += kills * self.w["finishing_blow_bonus"]
                score += vp_spot_kills * self.w["vp_spot_kill_bonus"]
                score += rp_spot_kills * self.w["rp_spot_kill_bonus"]
                # 2026-09-09追加: comeback_desperation_pref / first_kill_momentum
                # （place側と同じ。3.20節#2・#5）。
                if desperation_frac > 0:
                    boldness = desperation_frac * self.w["comeback_desperation_pref"]
                    score += boldness * enemy
                    score += boldness * kills * 2.0
                if first_kill_active:
                    score += self.w["first_kill_momentum"] * (dmg - base_dmg)
                    score += self.w["first_kill_momentum"] * kills * 2.0
                # 2026-09-09追加: tempo_loss_aversion（3.20節#3）。前進もせず・
                # 攻撃機会も生まず・スポットへも向かわない「意味の薄い移動」への
                # 忌避。dst自体がspot系（vp/rp/midgame）ならそもそも上のelif分岐で
                # 既にスポット価値が加点されているため対象外にする。
                is_spot_dst = dst in vp_tengen or dst in vp_stars or dst in rp_pts or dst in midgame_pts
                if not advanced and dmg <= 0 and not is_spot_dst:
                    score -= self.w["tempo_loss_aversion"]

                # ---- Tier1/Tier2 追加重み（2026-08-25追加） ----
                score += self.w[KIND_TO_PLACE_PREF[role_of(piece.kind)]] * 0.5  # 移動は配置ほど強く出さない
                fav, unfav = _count_matchup_adjacent(game.board, dst, player, piece.kind, exclude=src)
                score += fav * self.w["favorable_matchup_bonus"]
                score -= unfav * self.w["unfavorable_matchup_penalty"]
                if piece.kind == "弓兵":
                    nullifiers = _count_adjacent_enemy_kind(game.board, dst, player, "工兵", exclude=src)
                    score -= nullifiers * self.w["archer_immunity_awareness"]
                    old_nearest = _nearest_enemy_distance(game.board, src, player, exclude=src)
                    new_nearest = _nearest_enemy_distance(game.board, dst, player, exclude=src)
                    if old_nearest is not None and new_nearest is not None \
                            and new_nearest > old_nearest and dmg > 0:
                        score += self.w["archer_kiting_pref"]
                if piece.kind == "工兵":
                    shield_targets = _count_adjacent_enemy_kind(game.board, dst, player, "弓兵", exclude=src)
                    score += shield_targets * self.w["engineer_shield_bonus"]
                if role_of(piece.kind) == "重装兵" and friendly >= 2:
                    score += self.w["heavy_frontline_bonus"]
                own_base = CONFIG["base_positions"][player]
                base_dist = game.board.distance(dst, own_base)
                if base_dist <= 2:
                    score += (3 - base_dist) * self.w["base_proximity_alert"]
                score += (game.board.size - _distance_to_center(game.board, dst)) \
                    * self.w["center_control"] * 0.1
                corner_dist = _distance_to_nearest_corner(game.board, dst)
                if corner_dist <= 2:
                    score -= (3 - corner_dist) * self.w["corner_edge_avoidance"]

                # 2026-08-09追加: 被ダメージ側の減点（attackの対）。移動先(dst)基準で
                # 評価し、移動元(src)はもう自分の駒がいなくなるため exclude で除外する。
                incoming = _potential_incoming_damage(game.board, dst, piece.kind, player, exclude=src)
                score += incoming * self.w["incoming_damage"]

                # 移動元(src)を離れることで失う価値を差し引く（「今いい位置にいるのに
                # 理由なく移動してしまう」問題への対応。ヘッダーコメント参照）。
                hold_value = _position_hold_value(game.board, src, piece.kind, player, self.w,
                                                   vp_stars, vp_tengen, rp_pts, vp_spot_positions,
                                                   midgame_pts=midgame_pts,
                                                   base_pressure_multiplier=bp_mult)
                score -= hold_value

                score += CONFIG["economy"]["move_action_cost"] * self.w["rp_cost"]

                threat = _new_placement_threat(game, game.board, dst, piece.kind, player)
                score += threat * self.w["exposure"]

                # 2026-08-14追加: この移動を実行した「後」の自陣本拠への被ダメージ見積もり。
                # 移動元(src)を離れることでその防衛効果(呼吸点分割による身代わり)を
                # 失う場合はexclude_posで正しく反映される。
                base_threat_after = _hypothetical_own_base_threat(
                    game.board, player, new_pos=dst, new_kind=piece.kind, exclude_pos=src)
                # base_hp_panic_threshold（place側と同じ。0.86節）。
                panic = _base_hp_panic_multiplier(game.board, player, self.w["base_hp_panic_threshold"])
                score += base_threat_after * self.w["base_defense"] * panic

                # 2026-09-04追加: rp_income_margin（place側と同じ。move_action_costで
                # 減点されるRPコストとは別に、「移動元(src)を離れてRPスポットの保有を
                # 手放す/移動先(dst)で新たに確保する」ことによる来期RP収入差への影響を
                # 評価する）。
                income_margin_after = _hypothetical_next_round_income_margin(
                    game, player, new_pos=dst, new_kind=piece.kind, exclude_pos=src)
                score += income_margin_after * self.w["rp_income_margin"]

            else:  # pass
                score -= 1.0
                # 2026-08-14追加: passでも自陣本拠への現状の被ダメージ見積もりは
                # place/moveと同じ形で評価する（本拠防衛の観点で一貫性を保つため）。
                base_threat_after = _own_base_threat(game.board, player)
                # base_hp_panic_threshold（0.86節）。
                panic = _base_hp_panic_multiplier(game.board, player, self.w["base_hp_panic_threshold"])
                score += base_threat_after * self.w["base_defense"] * panic

                # 2026-09-09追加: opening_tempo_pref（3.20節#1）。序盤ほどpassを
                # 強く忌避する。
                score -= tempo_mult * self.w["opening_tempo_pref"] * 3.0
                # 2026-09-09追加: comeback_desperation_pref（3.20節#2）。劣勢終盤の
                # 手をこまねく(pass)選択をさらに減点する。
                if desperation_frac > 0:
                    score -= desperation_frac * self.w["comeback_desperation_pref"] * 3.0

                # 2026-09-04追加: passは盤面を一切変えないため、現状の(自分-相手)来期
                # RP収入差をそのまま評価する（place/moveとの一貫性のため）。
                income_margin_after = _hypothetical_next_round_income_margin(game, player)
                score += income_margin_after * self.w["rp_income_margin"]

            score += self.rng.uniform(-self.w["jitter"], self.w["jitter"])
            scored.append((score, action))

        return scored

    def choose(self, game, player, actions):
        return self.choose_with_debug(game, player, actions)["action"]

    def choose_with_debug(self, game, player, actions, top_k=8):
        """play_vs_ai.html の chooseWithDebug() 相当。棋譜記録・ビューアー表示用に、
        実際の意思決定に使った候補手とその評価値を返す。

        2026-08-09追加: choose()から計算過程を分離しただけで、選ばれる手自体は
        従来のchoose()と完全に同じ（epsilonのランダム判定→乱数消費の順序も
        変えていない）。epsilonによるランダム選択が発動した場合は
        random_pick=Trueを立てて区別する（この場合、実際に選ばれた手が
        candidatesの最上位と一致しないことがある。ビューアー側はこのフラグを
        見て「ランダムに選ばれた手です」等の注記を出すことを想定）。

        2026-08-31修正: 従来はepsilon発動時にactions（合法手全体）から一様
        ランダムに選んでおり、評価が最低クラスの手（候補top_kに一切現れない
        手）が実戦棋譜に出現しうる状態だった（例: 序盤の資源スポット取り合い
        の最中に盤の隅へ工兵を配置する等）。「対局を毎回同じにしない」という
        本来の目的（ヘッダーコメント参照）には、上位候補間でのブレで十分
        なため、ランダム選択の対象をスコア上位top_k件（candidatesと同じ集合）
        に限定した。これにより、ランダム発動時も選ばれる手は必ず
        candidatesの表示範囲内に収まる。"""
        random_pick = self.rng.random() < self.epsilon
        scored = self._score_actions(game, player, actions)
        scored_sorted = sorted(scored, key=lambda t: t[0], reverse=True)
        candidates = [{"action": a, "value": round(s, 2)} for s, a in scored_sorted[:top_k]]

        if random_pick:
            pool_actions = [a for _, a in scored_sorted[:top_k]]
            action = self.rng.choice(pool_actions)
        else:
            best_score = scored_sorted[0][0]
            best_actions = [a for s, a in scored if s == best_score]
            # 2026-09-09追加: flourish_tiebreak_pref（3.20節#7）。スコアが完全に
            # 同点の候補が複数ある場合に限り、演出映えする手（撃破数・スポット
            # キル数が多い手）を優先する。scoreそのものには一切手を加えていない
            # ため、weight=0はもちろんweight>0でもbest_score自体・単独最善手の
            # 選択結果は変化しない（同点が発生した時の「その中でどれを選ぶか」
            # にのみ作用する、バランス影響ゼロ設計）。
            if len(best_actions) > 1 and self.w.get("flourish_tiebreak_pref", 0.0) > 0:
                vp_stars = set(CONFIG["vp_spots"]["stars"])
                vp_tengen = set(CONFIG["vp_spots"]["tengen"])
                vp_spot_positions = vp_stars | vp_tengen
                rp_pts = set(CONFIG["rp_spots"]["points"])

                def _flourish_value(a):
                    if a[0] == "place":
                        _, kind, pos = a
                        _, kills, vps, rps, _ = _potential_attack(
                            game.board, pos, kind, player,
                            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
                    elif a[0] == "move":
                        _, src, dst = a
                        piece = game.board.grid[src]
                        _, kills, vps, rps, _ = _potential_attack(
                            game.board, dst, piece.kind, player, exclude=src,
                            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
                    else:
                        return 0
                    return kills + vps + rps

                top_flourish = max(_flourish_value(a) for a in best_actions)
                if top_flourish > 0:
                    best_actions = [a for a in best_actions if _flourish_value(a) == top_flourish]
            action = self.rng.choice(best_actions)

        return {"action": action, "candidates": candidates, "random_pick": random_pick}

    # 2026-08-30削除: 旧_EARLY_ENGINEER_BOOTSTRAP_ROUND_LIMIT/_MAX_COUNT定数
    # （early_engineer_bootstrap_pref用）は、実測で既定値(1.5)と0.0の差が
    # わずか3%程度、1.5を超えて20.0まで上げても効果が一切変わらないことが
    # 判明し、事実上機能していなかったため撤去した（下記choose_production内
    # のコメント、weights.py BASE_WEIGHTSのコメントも参照）。

    def choose_production(self, game, player, affordable_kinds):
        """
        アップキープ時の生産判断。

        2026-08-25改修: 従来は「afford可能な中で最も効率の良い駒種」を
        毎回必ず1体生産していた。これはRPが入るそばから即座に生産へ
        変換してしまい、(a) 既にreserveに積んである駒を配置するRPが
        常に足りなくなる、(b) RPを温存するという判断が原理的に不可能、
        という2つの実害（本拠特攻への対応検証で判明）につながっていた。

        「そもそも今作るべきか」（=Noneを返して見送るか）と「作るなら何を
        作るか」を、affordable_kinds各々の生産スコアと『何もしない』の
        スコアを同一の物差しで比較するutility方式に変更する（_score_actions
        と同じ「argmaxで選ぶ」設計を踏襲）。分岐④（それ以外は温存）が自然に
        「skipのスコアが最大」というケースとして表現される。

        2026-08-30再改修: 一度は「保有数上限(9体)に絶対到達させない」ための
        専用の安全マージン(piece_cap_safety_margin)を追加したが、これは
        対症療法だった。真因はgame.Game.owned_piece_count()が「持ち駒
        (reserve)」ではなく「reserve＋盤上」を合算してカウントしていた
        実装ミスで、盤面に駒を展開しているだけで上限に達し、生産が止まって
        しまっていた。企画書・検証ハンドブックともに「持ち駒（reserve）」は
        reserveのみを指す用語であり、盤上の駒数に上限はない
        （game.pyのowned_piece_count()参照）。この実装ミスをgame.py側で
        修正したことで、専用の安全マージンは不要になったため削除した。
        production_phase()側のowned_piece_count(=reserve数)>=max_owned_pieces
        というハード制限（絶対ルール）に任せる。
        """
        if self.rng.random() < self.epsilon:
            return self.rng.choice(affordable_kinds + [None])

        def _eff(kind):
            cfg = CONFIG["pieces"][kind]
            # RP100倍化: produce_costは新スケールなので、_effが従来の値域（およそ0.5〜5）を
            # 保つようRP_SCALEで正規化してから使う。
            base = (cfg["hp"] / 100 + cfg["atk"] / 10) / max(cfg["produce_cost"] / RP_SCALE, 1)
            # Tier1 #6-10: 駒種別の生産優先度（2026-08-25追加）。
            # efficiencyと同じレンジに収まるよう単純加算にしている
            # （_effはおよそ0.5〜5程度の値域）。
            return base + self.w[KIND_TO_PRODUCE_PREF[role_of(kind)]]

        rp = game.econ.rp[player]
        reserve = game.reserve[player]
        base_threat = _own_base_threat(game.board, player)

        # ---- 分岐0-b（2026-08-30追加、2026-08-30再改修×2）: 配置を生産より優先する ----
        # 生産コスト(produce_cost)と配置コスト(cost)は同じRPプールから引かれる。
        # reserveに配置待ちの駒が残っているのに構わず追加生産すると、次ラウンドに
        # その駒を配置するRPが足りなくなり、盤面に一切手を出せないラウンドが
        # 発生する。ここが最重要の判断基準：
        # 「今ここで生産しても、配置したい駒の配置コストを維持できるなら生産する」。
        #
        # 2026-08-30再々改修: 一度は「次ラウンドの収入見積もりをどこまであてに
        # するか」(income_confidence_ratio)を導入したが、これはラウンド進行順序
        # を誤認したバグだった。1ラウンドの流れは
        #   [両者の手番(配置/移動)] → [戦闘解決] → [収入加算] → [生産フェイズ]
        # の順であり、生産フェイズの時点でgame.econ.rp[player]には**既に今
        # ラウンドの収入が加算済み**。一方、次ラウンドの配置行動は、次ラウンドの
        # 収入が加算されるより**前**に行われる。つまり「次ラウンドの収入をあて
        # にして今のRPを多く使う」という判断は、その収入が実際に入るより早い
        # タイミングで配置してしまうため成立しない。
        # 今のrpには既に今ラウンド分の収入が反映済みなので、単純に「生産後も
        # 配置コスト分のRPが残るか」だけを見れば、収入が豊富なラウンドほど
        # rp自体が大きくなり自然と生産に回せる余地が増える（終盤ほど積極的に
        # 生産できる、という狙いは"収入を先取りする"のではなく"今のrpが既に
        # 大きい"ことで自然に実現される）。income_confidence_ratioは廃止した。
        #
        # 2026-08-30さらなる修正: 当初は「自陣本拠が脅かされている緊急事態
        # (base_threat>0)なら、この配置優先の判断基準を無視してよい」という
        # 例外を設けていたが、これは矛盾した設計だった。本拠防衛に実際に
        # 効くのは「配置」であって「生産」単体ではない（生産しただけでは
        # reserveに積まれるだけで盤面には何も現れない）。敵の攻撃が連続する
        # 局面でこの例外が毎ラウンド発動し続けると、RPが0になった状態で
        # 生産だけを繰り返し、置けない駒がreserveに積み上がって上限(9体)に
        # 到達する事例が実際に確認された（本拠を守るための生産のはずが、
        # 配置できず何も守れていない）。緊急事態でもこの基準は緩めない。
        if reserve:
            cheapest_placement_cost = min(CONFIG["pieces"][k]["cost"] for k in set(reserve))
            affordable_kinds = [
                k for k in affordable_kinds
                if rp - CONFIG["pieces"][k]["produce_cost"] >= cheapest_placement_cost
            ]
            if not affordable_kinds:
                return None

        # ---- 分岐④のベース: 何もしない(生産見送り)の基礎価値 ----
        # 2026-08-30改修: 旧rp_hoarding_pref（RP残量に比例して加点）は、
        # 「配置に回すRPを確保する」という本来の目的を達成できていなかった
        # ため廃止した。配置優先は上の分岐0-bで直接保証済みなので、
        # ここでの「何もしない」の基礎価値は0（＝生産効率が正なら基本的に
        # 生産する）にする。
        score_skip = 0.0

        # ---- 分岐②の準備: 自陣本拠を今脅かしている敵の駒種を集める ----
        # reach=1+range基準で本拠に届く敵駒を「脅威」とみなす（上で計算済みのbase_threatを流用）
        threatening_kinds = set()
        if base_threat > 0:
            my_base_pos = CONFIG["base_positions"][player]
            for pos, piece in game.board.grid.items():
                if piece.owner == player:
                    continue
                cfg = CONFIG["pieces"][piece.kind]
                reach = 1 + cfg["range"]
                if game.board.distance(pos, my_base_pos) <= reach:
                    threatening_kinds.add(piece.kind)

        def _production_score(kind):
            score = _eff(kind)

            # 分岐②: 本拠脅威に対して有利な相性(type_multiplier>1.0)の駒なら
            # 最優先で生産する。相性のある組み合わせは歩兵→騎兵/騎兵→重装兵/
            # 重装兵→歩兵の3通りのみ（type_multiplier参照）。
            if threatening_kinds:
                is_counter = any(type_multiplier(kind, tk) > 1.0 for tk in threatening_kinds)
                if is_counter:
                    # reserveに既に同じ有利駒があるなら、生産よりも配置の仕事
                    # なのでここでの追加ボーナスは控えめにする（二重生産の抑制）。
                    already_have = kind in reserve
                    urgency = base_threat / 100.0
                    score += self.w["base_threat_counter_priority"] * urgency * (0.3 if already_have else 1.0)

            # 2026-08-30削除: 旧「分岐①」（early_engineer_bootstrap_pref、序盤
            # に工兵を安く生産して経済を立ち上げる志向）は撤去した。実測の
            # 結果、既定値(1.5)と0.0の差はround5時点の平均工兵数で1.855→1.915
            # (+3%程度)とごくわずかで、1.5を超えて20.0まで上げても効果が
            # 一切変わらない（=事実上機能していない）ことが判明したため。
            # 工兵は_eff(kind)の時点で既にHP/ATKに対しproduce_costが低廉な
            # ため自然に選ばれやすく、この専用ボーナスがなくても序盤の工兵
            # 生産は十分に発生する。恒常的な「工兵をどれだけ好むか」という
            # 志向は、KIND_TO_PRODUCE_PREF経由で常時効く`engineer_produce_pref`
            # （Tier1 #10、チャンピオン撃破報酬で解放）が既に担っており、
            # 役割が重複していた。

            # 分岐②': 恒常的な相性評価（production_diversity_pref、2026-08-31新設）。
            # 分岐②のbase_threat_counter_priorityとは異なり、自陣本拠が
            # 脅かされているか否かに関わらず常時発火する。盤上の敵駒構成
            # 全体を見て、kindがどれだけ有利/不利な相性を持つ敵駒と対峙して
            # いるかを[-1, 1]の範囲に正規化し、weightを乗算して加点/減点する。
            # 歩兵一辺倒（_eff(kind)が常に歩兵最大になり、jitterも乗らない
            # ため議論の余地なく歩兵が選ばれ続けていた問題）への対策。
            # 本拠は三すくみに一切関与せず(type_multiplierは常に1.0)、かつ
            # 両陣営に必ず1体存在し続けるため、母数に含めると常に信号が
            # 薄まる（本拠への脅威評価は既存のbase_pressure/base_defenseが
            # 別途担っている）。ここでは戦闘に関与しうる駒のみを対象にする。
            enemy_counts = Counter(
                p.kind for p in game.board.grid.values()
                if p.owner != player and p.kind != "本拠"
            )
            total_enemy = sum(enemy_counts.values())
            if total_enemy > 0:
                favorable = sum(
                    count for enemy_kind, count in enemy_counts.items()
                    if type_multiplier(kind, enemy_kind) > 1.0
                )
                unfavorable = sum(
                    count for enemy_kind, count in enemy_counts.items()
                    if type_multiplier(enemy_kind, kind) > 1.0
                )
                composition_edge = (favorable - unfavorable) / total_enemy
                score += composition_edge * self.w["production_diversity_pref"]

            # 2026-09-09追加: signature_unit_affinity（3.20節#6、5駒種分）。
            # production_diversity_prefとは逆に、特定の駒種そのものへ直接加点する
            # 「〜特化型」演出用のノブ。search_bot_skeleton.py側はevaluate_state
            # （自軍の駒種構成そのものへの状態評価）に配線しており、生産判断だけに
            # 閉じたこちら側とは配線層が異なる点に注意（詳細はscoring_common.py
            # SIGNATURE_AFFINITY_KEYのコメント参照）。
            score += self.w[SIGNATURE_AFFINITY_KEY[role_of(kind)]]

            return score

        best_kind = max(affordable_kinds, key=_production_score)
        best_score = _production_score(best_kind)

        if score_skip >= best_score:
            return None
        return best_kind


# ============================================================
# 対戦バッチ（好きなボットの組み合わせで実行できる）
# ============================================================
def run_matchup(bot0_factory, bot1_factory, n_games=300, label=""):
    results = []
    for _ in range(n_games):
        game = Game()
        bots = {0: bot0_factory(), 1: bot1_factory()}
        res = game.run(bots)
        results.append(res)

    total = len(results)
    win_counts = Counter(r["winner"] for r in results)
    reason_counts = Counter(r["reason"] for r in results)
    avg_rounds = sum(r["rounds"] for r in results) / total

    print("=" * 60)
    print(f"{label}  ({total}戦)")
    print("=" * 60)
    print(f"player0勝率: {win_counts[0] / total * 100:.1f}%  ({win_counts[0]}勝)")
    print(f"player1勝率: {win_counts[1] / total * 100:.1f}%  ({win_counts[1]}勝)")
    print(f"平均決着ラウンド数: {avg_rounds:.1f}")
    print("勝敗理由の内訳:")
    for reason, count in reason_counts.most_common():
        print(f"  {reason}: {count}件 ({count / total * 100:.1f}%)")
    print()
    return results


if __name__ == "__main__":
    print("### 資源(4-4)/VP(星+天元)分離 + 手番順スワップON(従来通り) ###\n")
    CONFIG["pie_rule_swap_turn_order"] = True
    run_matchup(HeuristicBot, HeuristicBot, n_games=300,
                label="ヒューリスティック(先手) vs ヒューリスティック(後手) [手番順スワップ ON]")

    print("### 資源(4-4)/VP(星+天元)分離 + 手番順スワップOFF(所有権のみ交換) ###\n")
    CONFIG["pie_rule_swap_turn_order"] = False
    run_matchup(HeuristicBot, HeuristicBot, n_games=300,
                label="ヒューリスティック(先手) vs ヒューリスティック(後手) [手番順スワップ OFF]")

    # 参考: ランダムとの健全性チェック（スワップOFF設定のまま）
    run_matchup(HeuristicBot, RandomBot, n_games=300,
                label="ヒューリスティック(先手) vs ランダム(後手) [スワップ OFF]")
    run_matchup(RandomBot, HeuristicBot, n_games=300,
                label="ランダム(先手) vs ヒューリスティック(後手) [スワップ OFF]")

    print("### 追加検証: ラウンドごとに先後を交互入れ替え（後出し優位の是正） ###\n")
    CONFIG["pie_rule_swap_turn_order"] = False
    CONFIG["alternate_initiative_each_round"] = True
    run_matchup(HeuristicBot, HeuristicBot, n_games=300,
                label="ヒューリスティック vs ヒューリスティック [交互初期化 ON / スワップ OFF]")

    CONFIG["pie_rule_swap_turn_order"] = True
    run_matchup(HeuristicBot, HeuristicBot, n_games=300,
                label="ヒューリスティック vs ヒューリスティック [交互初期化 ON / スワップ ON]")







