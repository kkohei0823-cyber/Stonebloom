
# -*- coding: utf-8 -*-
"""
search_bot_skeleton.py
==========================================================
候補プルーニング付きαβミニマックス探索ボット（SearchBot）。
HeuristicBot（1手先読みのみ）ではRPの複利効果など数手先の価値を評価できない
問題への対応。反復深化構造。まだ完成品ではなく調整前提。

注意点:
  - choose_production()は候補ごとにclone_gameで実評価するためO(1)ではなく、
    パラメータ次第で1手あたりの計算時間が伸びうる（tune_balance_search.py使用時要注意）
  - 候補手はヒューリスティックスコア上位K件に絞ってから展開する（全探索は非現実的）
"""

import copy
import time
from collections import Counter
from config import CONFIG, RP_SCALE
from weights import HEURISTIC_WEIGHTS, to_engine_signed, resolve_opponent_weights
from game import Game, Board, Piece, Economy, resolve_combat
# （9章-10対応。以前は heuristic_bot.py 経由でimportしていたが、
# HeuristicBot自体には依存しない関数群のため、依存関係をより直接的にした）。
from heuristic_bot import HeuristicBot
from scoring_common import (
    _potential_attack, _neighbors_count, _position_hold_value,
    _new_placement_threat, _potential_incoming_damage, _ranged_enemy_pieces,
    _own_base_threat, _hypothetical_own_base_threat,
    _positions_within_manhattan_radius, _base_hp_panic_multiplier,
    _estimate_next_round_income, _base_pressure_ramp_multiplier,
    _production_diversity_penalty_from_counts, _production_diversity_marginal_delta,
    _add_reserve_kind_counts, _spot_position_sets,
    _opening_tempo_multiplier, _comeback_desperation_fraction,
    SIGNATURE_AFFINITY_KEY,
    # 2026-09-10追加（analyze_tuning_run_handoff.md 3.22.5節・3.23節参照）:
    # favorable_matchup_bonus/unfavorable_matchup_penalty/archer_immunity_awareness/
    # base_proximity_alert/center_control/corner_edge_avoidanceがSearchBotに一切
    # 配線されていなかった事故の修正で新たに必要になったヘルパー。
    # heuristic_bot.py側では2026-08-25から使われていたもので、関数自体は今回新設していない。
    _count_matchup_adjacent, _count_adjacent_enemy_kind,
    _distance_to_center, _distance_to_nearest_corner,
)



# ============================================================
# 1. 軽量クローン（deepcopyより高速。必要フィールドだけ手動コピー）
# ============================================================
def clone_game(game):
    new = Game.__new__(Game)

    new.board = Board(game.board.size)
    new.board.grid = {
        pos: Piece(kind=p.kind, owner=p.owner, hp=p.hp, max_hp=p.max_hp,
                   atk=p.atk, movable=p.movable, uid=p.uid)
        for pos, p in game.board.grid.items()
    }

    new.econ = Economy.__new__(Economy)
    new.econ.rp = list(game.econ.rp)
    new.econ.cumulative_vp = list(game.econ.cumulative_vp)
    new.econ.spot_owner = dict(game.econ.spot_owner)
    new.econ.spot_owned_since = dict(game.econ.spot_owned_since)

    new.reserve = {0: list(game.reserve[0]), 1: list(game.reserve[1])}
    new.engineer_positions = set(game.engineer_positions)
    new.round_number = game.round_number
    new.turn_order = list(game.turn_order)
    new.first_move_done = game.first_move_done
    new.pie_rule_resolved = game.pie_rule_resolved
    new.winner = game.winner
    new.win_reason = game.win_reason
    new.placement_ban = dict(game.placement_ban)
    # 2026-09-09追加: total_kills（first_kill_momentum用）。探索内の仮想対局
    # （simulate_action/resolve_round_end経由）でも正しく引き継がれないと、
    # 深い探索ノードで「まだ誰も撃破していない」という誤った初期状態に
    # 巻き戻ってしまう（game.pyのGame.__init__参照）。
    new.total_kills = list(getattr(game, "total_kills", [0, 0]))

    # 探索中はログを取らない（速度優先。本番プレイのgameでは別途記録される）
    new.tengen_owner_log = []
    new.kifu = []
    return new


def _piece_material_value(kind):
    """駒種ごとの「満タン時」の基準値。reserve（まだ盤面に出ていない駒）の評価用。
    HeuristicBotのefficiency計算と同じ土台。"""
    cfg = CONFIG["pieces"][kind]
    return cfg["hp"] / 100 + cfg["atk"] / 10


def _piece_current_material_value(piece):
    """盤上の駒の「現在の状態」に基づく価値。HPが減っていれば評価も下がる。

    2026-08-06 修正: evaluate_state が旧 _piece_material_value(piece.kind) を
    使っていたため、盤上の駒はHPが1でも満タンでも同じ評価点になっていた
    （死んで盤面から消えるまで一切減点されない）。この結果、探索は「自駒が
    被弾すること」自体にほぼ無関心になり、本拠の被ダメージを肩代わりさせる
    ために弓兵を隣接させて"盾"にする（駒の呼吸点式ダメージ分割ルールにより
    本拠へのダメージが減る）手が、実際にはHPを大きく失っているにもかかわらず
    見かけ上ペナルティなしで高評価になっていた。15章の弓兵配置問題が
    SearchBot(depth=2)で再発していた主因の一つ。"""
    cfg = CONFIG["pieces"][piece.kind]
    return piece.hp / 100 + cfg["atk"] / 10

RESERVE_DISCOUNT = 0.5  # reserveは盤上に置いた場合の価値の半分しかもらえない

# ============================================================
# 2. 状態評価関数（leaf評価。「手」ではなく「盤面状態」を評価する）
# ============================================================
def evaluate_state(game, player, weights=HEURISTIC_WEIGHTS, base_hp_weight=None):
    """
    v2: 独自の係数を新設するのをやめ、HeuristicBotで既に何度も対戦検証済みの
    HEURISTIC_WEIGHTS / _position_hold_value をそのまま流用する版。
    「盤上の全駒について、そこに居続けることの価値」を自分側は+、敵側は-で
    合計するだけ。新しい未調整の係数を極力持ち込まないことを優先している。

    base_hp_weight だけは _position_hold_value でカバーされない項目
    （本拠は「駒」だが support/danger/攻撃機会の評価だけでは本拠特攻の
    致命傷を過小評価しがちなため）として別枠で残してある。
    2026-08-14修正: 従来この引数はデフォルト0.5固定のキーワード引数で、
    HEURISTIC_WEIGHTSの外にあったためOptunaのチューニング対象になっていなかった
    （tune_balance_search.pyがいくら重みを上書きしても常に0.5のまま評価されていた
    不備）。デフォルトをNoneにし、Noneの場合は weights["base_hp_weight"] を使う
    ようにして、他の重みと同様にconfig.py経由でチューニングできるようにした。
    明示的にbase_hp_weightを渡した場合はそちらを優先する（後方互換）。

    2026-08-14追加: 自陣・敵陣それぞれの本拠が次の戦闘解決で受ける被ダメージ見積もり
    （_own_base_threat）を weights["base_defense"] で評価する項を新設。
    従来、盤上の駒ループでは piece.kind == "本拠" の場合に _position_hold_value を
    スキップしていたため（下記参照）、本拠自身に対する support/danger/attack/
    incoming_damage/exposureの評価が一切行われていなかった。唯一「敵駒が本拠へ
    与える見込みダメージ」を拾えていたのは、敵駒側のループでbase_pressureが
    (dmg - base_dmg)*attack + base_dmg*base_pressure という形で加算される経路
    だけだったが、この係数（base_pressure）は「AIが自分から本拠特攻に走りすぎる」
    問題(3.2節)を抑えるために意図的に低く据え置かれてきたため、同じ係数が
    「自陣本拠が実際に脅かされている時の防衛の緊急度」まで一緒に弱めてしまって
    いた。base_defenseを別枠にすることで、本拠特攻の抑制(base_pressure)と実際の
    防衛判断(base_defense)を独立に調整できるようにする。

    2026-08-07 修正（案A）: _position_hold_value を count_vp_spot_bonus=False で
    呼ぶように変更。本関数の直前の行で cumulative_vp の実差分（①）を既に
    score に加算しているが、_position_hold_value のvp_star/vp_tengen占有ボーナス
    （②）はそれとは独立に、盤上の全駒×全探索ノードで無条件に加算され続けていた。
    ①は「実際に確定したVP」、②は「1手ヒューリスティック用に、まだ発生していない
    将来のVP収入を見積もるための簡易プロキシ」であり、本来同じ価値の二重計上に
    なる。しかも②は深い探索の葉ノードすべてで律儀に再加算されるため、キル1回分
    のような単発イベント加点よりも相対的に膨張しやすく、「有利な戦闘機会よりVP
    スポットへの移動を優先する」挙動の一因になっていた（1手ヒューリスティック
    である _score_actions / scoreActions 側は元々「まだ起きていない将来を見積もる」
    目的で使う関数なので、そちらは従来通り count_vp_spot_bonus=True のまま）。
    rp_spot側は実測RP差(②とは独立な項目)との二重計上にならないため、
    深い探索でも維持している。
    """
    if game.winner is not None:
        return 1e6 if game.winner == player else -1e6

    opp = 1 - player
    if base_hp_weight is None:
        base_hp_weight = weights["base_hp_weight"]
    # 2026-09-07最適化（3.18節）: 呼ばれるたびにset(CONFIG[...])を作り直すのをやめ、
    # CONFIGの世代が変わらない限りキャッシュを使い回す（evaluate_stateは探索リーフの
    # 最内周で1局あたり数万回呼ばれるホットパスのため、ここでの4set分の構築コストが
    # 積み上がっていた。挙動は完全に不変、速度のみ改善）。
    vp_stars, vp_tengen, vp_spot_positions, rp_pts, midgame_pts = _spot_position_sets()

    score = 0.0
    score += (game.econ.cumulative_vp[player] - game.econ.cumulative_vp[opp]) * 1.0
    # RP差評価はweights["rp_differential"]（旧ハードコード0.15を切り出したもの）
    score += (game.econ.rp[player] - game.econ.rp[opp]) * weights["rp_differential"]

    # 射程駒リストは1回だけ作って使い回す（毎回フルスキャンするとO(駒数^2)になるため）
    ranged_enemies_by_owner = {
        0: _ranged_enemy_pieces(game.board, 0),
        1: _ranged_enemy_pieces(game.board, 1),
    }

    # 自陣本拠への脅威はbase_defense（負値）で加算、敵本拠への脅威は符号反転して加算
    my_base_threat = _own_base_threat(game.board, player, ranged_enemies=ranged_enemies_by_owner[opp])
    opp_base_threat = _own_base_threat(game.board, opp, ranged_enemies=ranged_enemies_by_owner[player])
    # base_hp_panic_threshold（0.86節。opp側には適用しない＝enemy_base_finish_urgency
    # 未実装枠の役割との切り分け）。weight=0なら常に1.0で無効化。
    panic = _base_hp_panic_multiplier(game.board, player, weights["base_hp_panic_threshold"])
    score += my_base_threat * weights["base_defense"] * panic
    # 2026-09-04追加: 序盤の本拠特攻を抑えるためのbase_pressureラウンド立ち上がり
    # 倍率。詳細はscoring_common.py _base_pressure_ramp_multiplierのコメント参照。
    bp_mult = _base_pressure_ramp_multiplier(game.round_number, weights["base_pressure_ramp_rounds"], weights["rush_opening_pressure"])
    score -= opp_base_threat * weights["base_defense"]

    # 2026-09-07追加: production_diversity_pref用の駒種カウント（player/opp別）。
    # このあとの盤上ループでどのみち全駒を1回ずつ舐めるため、専用に盤面を
    # 再スキャンする_own_army_kind_counts()を呼ばず、このループへインラインで
    # 数え上げを混ぜる（ホットパスであるevaluate_stateで余分なフルスキャンを
    # 増やさないため。詳細はscoring_common.py _production_diversity_penalty_
    # from_countsのコメント参照）。
    own_kind_counts = Counter()
    opp_kind_counts = Counter()
    # 2026-09-09追加: opening_tempo_pref用のRPスポット占有数（player/opp別）。
    # production_diversity_prefと同じ理由でこのループにインラインで数える
    # （専用の再スキャンを避けるホットパス最適化）。
    own_rp_spots_owned = 0
    opp_rp_spots_owned = 0

    for pos, piece in game.board.grid.items():
        sign = 1 if piece.owner == player else -1
        if piece.kind == "本拠":
            score += sign * piece.hp * base_hp_weight
            continue
        if piece.owner == player:
            own_kind_counts[piece.kind] += 1
            if pos in rp_pts:
                own_rp_spots_owned += 1
        else:
            opp_kind_counts[piece.kind] += 1
            if pos in rp_pts:
                opp_rp_spots_owned += 1
        material = _piece_current_material_value(piece)
        hold = _position_hold_value(
            game.board, pos, piece.kind, piece.owner, weights,
            vp_stars, vp_tengen, rp_pts, vp_spot_positions,
            count_vp_spot_bonus=False, midgame_pts=midgame_pts,
            ranged_enemies=ranged_enemies_by_owner[1 - piece.owner],
            ranged_friends=ranged_enemies_by_owner[piece.owner],
            # 2026-09-04追加: base_pressureを「自分の駒が敵本拠を脅かす価値」に限定する
            # （敵駒側の本拠脅威はbase_defense/_own_base_threatが別途正確に評価済み）。
            apply_base_pressure=(piece.owner == player),
            base_pressure_multiplier=bp_mult,
        )
        threat = _new_placement_threat(game, game.board, pos, piece.kind, piece.owner)
        score += sign * (material + hold + threat * weights["exposure"])

    for i, kind in enumerate(game.reserve[player]):
        decay = RESERVE_DISCOUNT * (0.5 ** i)  # 1体目はそのまま、2体目以降は急減
        score += _piece_material_value(kind) * decay
    for i, kind in enumerate(game.reserve[opp]):
        decay = RESERVE_DISCOUNT * (0.5 ** i)
        score -= _piece_material_value(kind) * decay
    _add_reserve_kind_counts(own_kind_counts, game.reserve[player])
    _add_reserve_kind_counts(opp_kind_counts, game.reserve[opp])

    # 2026-09-04追加: rp_income_margin（RPスポット確保を伴わない攻勢の「息切れ」評価）。
    # 背景: 本拠特攻自体は初手の1手だけを見ればdanger=0・incoming=0でリスクが
    # 見えないが、RPスポットを確保せずに攻めた側は数ラウンド後にRP収入で
    # 上回られ、増援・再生産で押し返されて息切れする——という展開は、探索の
    # 地平線(depth2〜3)より先で起きることが多く、単純な浅い探索では直接
    # 読み切れない。ルール変更で特攻そのものを禁止するのではなく、_estimate_
    # next_round_income()（RPスポット/中盤解禁スポットの保有状況から来期RP収入を
    # 名目見積もりする、既存だが従来未使用だった関数）を使い、「現在の盤面が
    # 経済的にどちらに有利か」を毎ノードの静的評価に織り込むことで、経済を
    # 捨てた前のめりな配置がその場で評価を下げるようにする（＝多手先を読まなくても
    # 息切れリスクを"今"の評価値に反映できる）。
    my_income = _estimate_next_round_income(game, player)
    opp_income = _estimate_next_round_income(game, opp)
    score += (my_income - opp_income) * weights["rp_income_margin"]

    # 2026-09-07追加: production_diversity_pref（3.16節対応）。
    # 自軍の駒種構成が偏っているほど減点し、敵側が偏っているほど加点する
    # （my_base_threat/opp_base_threatと同じ、player視点の対称構造）。
    # これがないと、_position_hold_value側の「工兵をRP/中盤スポットに置くと
    # engineer_econ_bonusが無条件・無上限に乗る」という加点だけが存在し、
    # 「工兵ばかりだと損」という対になる項が評価式のどこにも無い状態になる
    # （詳細はscoring_common.py の_production_diversity_penalty_from_counts の
    # コメント、および3.16節を参照）。上のボードループで既に数え上げた
    # own_kind_counts/opp_kind_counts をそのまま渡す（盤面の再スキャンを
    # 避けるためのホットパス最適化。実測で1局あたりの実行時間を約4割削減できた）。
    score -= _production_diversity_penalty_from_counts(own_kind_counts, weights)
    score += _production_diversity_penalty_from_counts(opp_kind_counts, weights)

    # 2026-09-09追加: signature_unit_affinity（3.20節#6、5駒種分）。
    # production_diversity_prefと同じ場所・同じ形（own_kind_counts/opp_kind_counts
    # をそのまま使う）で配線する。production_diversity_prefが「構成の偏り」という
    # 集計値を見るのに対し、こちらは駒種ごとに独立した重みで直接加点/減点する。
    for k, c in own_kind_counts.items():
        score += c * weights[SIGNATURE_AFFINITY_KEY[k]]
    for k, c in opp_kind_counts.items():
        score -= c * weights[SIGNATURE_AFFINITY_KEY[k]]

    # 2026-09-09追加: opening_tempo_pref（3.20節#1）。序盤ほど、RPスポットの
    # 占有数差(own-opp)を強く評価する。「passしても目に見えて損はしない」という
    # round1の縮退（3.19節でtrial278/325のcandidates実測値から直接確認済み）に、
    # 探索の葉評価そのものへ直接効く対策として作用する（_score_actions側の
    # 浅い候補プルーニング用の加点だけでは、深い探索の最終値には反映されないため）。
    tempo_mult = _opening_tempo_multiplier(game.round_number)
    if tempo_mult > 0:
        score += (own_rp_spots_owned - opp_rp_spots_owned) * tempo_mult * weights["opening_tempo_pref"]

    return score


# ============================================================
# 3. ラウンド後処理の切り出し（game.Game.run() の該当部分を抽出）
# ============================================================
def resolve_round_end(game, production_bots=None):
    """両者が1手ずつ打ち終えた後の処理。

    戻り値: (base_destroyed, removed, damage, vp_kill_bonus, rp_wasted)

    2026-09-06変更: 従来はbase_destroyedしか返しておらず、戦闘結果（removed/damage）
    ・VPキルボーナス・RP収入の切り捨て分(rp_wasted)を全て呼び出し側に返さず捨てていた。
    play_vs_ai（web_api.py）はGame.run()を呼ばず、この関数の結果を使って独自に
    棋譜(kifu)を組み立てる必要があるため、game.pyのGame.run()と同じ形式の値を
    ここで返すようにした（web_api.pyのfinalize_round_kifu()参照）。
    既存の呼び出し元simulate_action()はbase_destroyedだけを使うよう変更した。
    """
    vs = CONFIG["vp_spots"]
    vp_spot_positions = set(vs["stars"]) | set(vs["tengen"])

    removed, damage = resolve_combat(game.board)
    vp_kill_bonus = [0, 0]
    for pos, owner, kind in removed:
        game.placement_ban[pos] = (owner, game.round_number + 1)
        # 2026-09-09追加: total_kills（first_kill_momentum用）。game.py
        # Game.run()と同じ更新をこの独立実装側でも行う（clone_gameのコメント参照）。
        if hasattr(game, "total_kills"):
            game.total_kills[1 - owner] += 1
        # game.py Game.run()と同じ防御的修正（このファイルは独立実装なので個別に必要）
        if kind == "工兵":
            game.engineer_positions.discard(pos)
        if pos in vp_spot_positions:
            beneficiary = 1 - owner
            game.econ.cumulative_vp[beneficiary] += vs["kill_bonus_vp"]
            vp_kill_bonus[beneficiary] += vs["kill_bonus_vp"]

    base_destroyed = game.check_base_destroyed()

    _income_total, rp_wasted = game.econ.compute_income(
        game.board, game.round_number, game.engineer_positions)
    game.econ.compute_vp(game.board, game.round_number)
    game.econ.pay_upkeep(game.board)

    if CONFIG["production_enabled"] and production_bots is not None:
        game.production_phase(production_bots)

    return base_destroyed, removed, damage, vp_kill_bonus, rp_wasted


# ============================================================
# 4. 1手適用ラッパー（手番の進行を管理）
# ============================================================
def simulate_action(game, player, action, production_bots=None):
    """
    action を適用する。ラウンドの2人目の手なら resolve_round_end も呼ぶ。
    戻り値: (next_player, round_ended, game_over)
    """
    game.apply_action(player, action)
    if not game.first_move_done:
        game.first_move_done = True
        game.maybe_resolve_pie_rule(action)

    # game.pyのGame.run()と同じ判定式で手番順を決める（alternate_initiative対応）
    if CONFIG["alternate_initiative_each_round"] and game.round_number % 2 == 0:
        order = list(reversed(game.turn_order))
    else:
        order = game.turn_order
    if player == order[0]:
        return order[1], False, False

    base_destroyed, _removed, _damage, _vp_kill_bonus, _rp_wasted = resolve_round_end(game, production_bots)
    game_over = base_destroyed or game.round_number >= CONFIG["turn_limit_per_player"]
    if game_over and not base_destroyed:
        game.score_and_finish()
    game.round_number += 1
    return order[0], True, game_over


# ============================================================
# 5. 候補手の絞り込み（HeuristicBot.choose()のスコア計算を流用し、リストで返す）
# ============================================================
def _score_actions(game, player, actions, weights=HEURISTIC_WEIGHTS):
    enemy_base = CONFIG["base_positions"][1 - player]
    # 2026-09-07最適化（3.18節）: evaluate_stateと同じキャッシュ済みset群を使う
    # （_score_actionsは探索の各ノードで候補手の数だけ呼ばれるため、こちらも高頻度）。
    vp_stars, vp_tengen, vp_spot_positions, rp_pts, midgame_pts = _spot_position_sets()
    bp_mult = _base_pressure_ramp_multiplier(game.round_number, weights["base_pressure_ramp_rounds"], weights["rush_opening_pressure"])
    # 2026-09-09追加: advance_ramp_rounds（3.19節。heuristic_bot.py側と同じ配線）。
    adv_mult = _base_pressure_ramp_multiplier(game.round_number, weights["advance_ramp_rounds"])
    # 2026-09-09追加: opening_tempo_pref / comeback_desperation_pref / first_kill_momentum
    # （3.20節#1・#2・#5。heuristic_bot.py側と同じ配線。opening_tempo_prefは
    # 候補プルーニング用のこの浅い加点に加え、evaluate_state側にも状態評価として
    # 別途配線している＝深い探索の最終値にも反映される。詳細はevaluate_stateの
    # コメント参照）。
    tempo_mult = _opening_tempo_multiplier(game.round_number)
    desperation_frac = _comeback_desperation_fraction(game, player)
    first_kill_active = getattr(game, "total_kills", (0, 0))[player] > 0

    scored = []
    for action in actions:
        score = 0.0
        if action[0] == "place":
            _, kind, pos = action
            cfg = CONFIG["pieces"][kind]
            friendly, enemy = _neighbors_count(game.board, pos, player)
            score += friendly * weights["support"] + enemy * weights["danger"]
            if pos in vp_tengen:
                score += weights["vp_tengen"]
                # 2026-09-10追加（3.22.5節の未配線発見への対応）: vp_spot_guard_bonus。
                score += enemy * weights["vp_spot_guard_bonus"]
            elif pos in vp_stars:
                score += weights["vp_star"]
                score += enemy * weights["vp_spot_guard_bonus"]
            elif pos in rp_pts:
                score += weights["rp_spot"]
                score += enemy * weights["rp_spot_guard_bonus"]
                if kind == "工兵":
                    score += weights["engineer_econ_bonus"]
                # 2026-09-09追加: opening_tempo_pref（place側）。
                score += tempo_mult * weights["opening_tempo_pref"] * 2.0
            elif pos in midgame_pts:
                score += weights["midgame_spot"]
                score += enemy * weights["midgame_spot_guard_bonus"]
                if kind == "工兵":
                    score += weights["engineer_econ_bonus"]
            # RP100倍化: heuristic_bot.pyと同様、costを旧スケール相当にRP_SCALEで
            # 正規化してからefficiency重みを適用する。
            score += (cfg["hp"] / 100 + cfg["atk"] / 10) / max(cfg["cost"] / RP_SCALE, 1) * weights["efficiency"]
            dist = game.board.distance(pos, enemy_base)
            # 2026-09-09修正: advance_ramp_rounds。
            score += (game.board.size - dist) * (weights["advance"] * 0.2) * adv_mult

            # ---- 2026-09-10追加: Tier1/Tier2重み10種の未配線を修正（3.22.5節・
            # 3.16節/production_diversity_prefと同型の事故）。heuristic_bot.pyの
            # _score_actions（2026-08-25追加分）と同じ計算式をそのまま移植した。----
            fav, unfav = _count_matchup_adjacent(game.board, pos, player, kind)
            score += fav * weights["favorable_matchup_bonus"]
            score -= unfav * weights["unfavorable_matchup_penalty"]
            if kind == "弓兵":
                nullifiers = _count_adjacent_enemy_kind(game.board, pos, player, "工兵")
                score -= nullifiers * weights["archer_immunity_awareness"]
            own_base = CONFIG["base_positions"][player]
            base_dist_own = game.board.distance(pos, own_base)
            if base_dist_own <= 2:
                score += (3 - base_dist_own) * weights["base_proximity_alert"]
            score += (game.board.size - _distance_to_center(game.board, pos)) \
                * weights["center_control"] * 0.1
            corner_dist = _distance_to_nearest_corner(game.board, pos)
            if corner_dist <= 2:
                score -= (3 - corner_dist) * weights["corner_edge_avoidance"]

            dmg, kills, vp_kills, rp_kills, base_dmg = _potential_attack(
                game.board, pos, kind, player,
                vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
            score += (dmg - base_dmg) * weights["attack"] + base_dmg * weights["base_pressure"] * bp_mult + kills * weights["kill_bonus"] + vp_kills * weights["vp_spot_kill_bonus"] + rp_kills * weights["rp_spot_kill_bonus"]
            # 2026-09-10追加: finishing_blow_bonus（未配線10種のうちの1つ）。
            score += kills * weights["finishing_blow_bonus"]
            # RP100倍化: 同上の理由でRP_SCALEで正規化してからrp_cost重みを乗じる。
            score += (cfg["cost"] / RP_SCALE) * weights["rp_cost"]
            # 2026-09-09追加: comeback_desperation_pref / first_kill_momentum（place側）。
            if desperation_frac > 0:
                boldness = desperation_frac * weights["comeback_desperation_pref"]
                score += boldness * enemy
                score += boldness * kills * 2.0
            if first_kill_active:
                score += weights["first_kill_momentum"] * (dmg - base_dmg)
                score += weights["first_kill_momentum"] * kills * 2.0

            # 2026-08-09追加: 被ダメージ側の減点（attackの対）。heuristic_bot.py側の
            # 修正と同じ理由・同じ関数で計算する（重複実装なので両方直す必要がある）。
            score += _potential_incoming_damage(game.board, pos, kind, player) * weights["incoming_damage"]

            score += _new_placement_threat(game, game.board, pos, kind, player) * weights["exposure"]

            # base_pressureと対になる防御側の重み（heuristic_bot.pyの重複実装。両方要修正）
            # base_hp_panic_threshold（0.86節）。
            panic = _base_hp_panic_multiplier(game.board, player, weights["base_hp_panic_threshold"])
            score += _hypothetical_own_base_threat(game.board, player, new_pos=pos, new_kind=kind) \
                * weights["base_defense"] * panic

        elif action[0] == "move":
            _, src, dst = action
            piece = game.board.grid[src]
            friendly, enemy = _neighbors_count(game.board, dst, player, exclude=src)
            score += friendly * weights["support"] + enemy * weights["danger"]
            if dst in vp_tengen:
                score += weights["vp_tengen"]
                score += enemy * weights["vp_spot_guard_bonus"]
            elif dst in vp_stars:
                score += weights["vp_star"]
                score += enemy * weights["vp_spot_guard_bonus"]
            elif dst in rp_pts:
                score += weights["rp_spot"]
                score += enemy * weights["rp_spot_guard_bonus"]
                if piece.kind == "工兵":
                    score += weights["engineer_econ_bonus"]
                # 2026-09-09追加: opening_tempo_pref（move側）。
                score += tempo_mult * weights["opening_tempo_pref"] * 2.0
            elif dst in midgame_pts:
                score += weights["midgame_spot"]
                score += enemy * weights["midgame_spot_guard_bonus"]
                if piece.kind == "工兵":
                    score += weights["engineer_econ_bonus"]
            advanced = game.board.distance(dst, enemy_base) < game.board.distance(src, enemy_base)
            if advanced:
                # 2026-09-09修正: advance_ramp_rounds。
                score += weights["advance"] * adv_mult

            # ---- 2026-09-10追加: Tier1/Tier2重み10種の未配線を修正（place側と同じ。
            # 3.22.5節参照）。heuristic_bot.pyのmove側と同じ計算式。----
            fav, unfav = _count_matchup_adjacent(game.board, dst, player, piece.kind, exclude=src)
            score += fav * weights["favorable_matchup_bonus"]
            score -= unfav * weights["unfavorable_matchup_penalty"]
            if piece.kind == "弓兵":
                nullifiers = _count_adjacent_enemy_kind(game.board, dst, player, "工兵", exclude=src)
                score -= nullifiers * weights["archer_immunity_awareness"]
            own_base = CONFIG["base_positions"][player]
            base_dist_own = game.board.distance(dst, own_base)
            if base_dist_own <= 2:
                score += (3 - base_dist_own) * weights["base_proximity_alert"]
            score += (game.board.size - _distance_to_center(game.board, dst)) \
                * weights["center_control"] * 0.1
            corner_dist = _distance_to_nearest_corner(game.board, dst)
            if corner_dist <= 2:
                score -= (3 - corner_dist) * weights["corner_edge_avoidance"]

            dmg, kills, vp_kills, rp_kills, base_dmg = _potential_attack(
                game.board, dst, piece.kind, player,
                exclude=src, vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
            score += (dmg - base_dmg) * weights["attack"] + base_dmg * weights["base_pressure"] * bp_mult + kills * weights["kill_bonus"] + vp_kills * weights["vp_spot_kill_bonus"] + rp_kills * weights["rp_spot_kill_bonus"]
            # 2026-09-10追加: finishing_blow_bonus（未配線10種のうちの1つ）。
            score += kills * weights["finishing_blow_bonus"]
            # 2026-09-09追加: comeback_desperation_pref / first_kill_momentum（move側）。
            if desperation_frac > 0:
                boldness = desperation_frac * weights["comeback_desperation_pref"]
                score += boldness * enemy
                score += boldness * kills * 2.0
            if first_kill_active:
                score += weights["first_kill_momentum"] * (dmg - base_dmg)
                score += weights["first_kill_momentum"] * kills * 2.0

            # 2026-08-09追加: 被ダメージ側の減点（attackの対）。moveなのでexclude=srcで
            # 移動元を除外する（heuristic_bot.py側と同じ理由）。
            score += _potential_incoming_damage(game.board, dst, piece.kind, player, exclude=src) * weights["incoming_damage"]

            hold = _position_hold_value(game.board, src, piece.kind, player, weights,
                                         vp_stars, vp_tengen, rp_pts, vp_spot_positions,
                                         midgame_pts=midgame_pts, base_pressure_multiplier=bp_mult)
            score -= hold
            score += CONFIG["economy"]["move_action_cost"] * weights["rp_cost"]
            # 2026-09-09追加: tempo_loss_aversion（3.20節#3。heuristic_bot.py側と同じ）。
            is_spot_dst = dst in vp_tengen or dst in vp_stars or dst in rp_pts or dst in midgame_pts
            if not advanced and dmg <= 0 and not is_spot_dst:
                score -= weights["tempo_loss_aversion"]

            score += _new_placement_threat(game, game.board, dst, piece.kind, player) * weights["exposure"]

            # 2026-08-14追加: 同上（moveの場合。移動元(src)を離れることの防衛上の
            # 機会費用もexclude_posで反映される）。
            # base_hp_panic_threshold（place側と同じ。0.86節）。
            panic = _base_hp_panic_multiplier(game.board, player, weights["base_hp_panic_threshold"])
            score += _hypothetical_own_base_threat(
                game.board, player, new_pos=dst, new_kind=piece.kind, exclude_pos=src) \
                * weights["base_defense"] * panic
        else:
            score -= 1.0
            # 2026-08-14追加: passでも自陣本拠への現状の被ダメージ見積もりを
            # place/moveと同じ形で評価する（一貫性のため）。
            panic = _base_hp_panic_multiplier(game.board, player, weights["base_hp_panic_threshold"])
            score += _own_base_threat(game.board, player) * weights["base_defense"] * panic
            # 2026-09-09追加: opening_tempo_pref / comeback_desperation_pref
            # （3.20節#1・#2。heuristic_bot.py側と同じ配線）。
            score -= tempo_mult * weights["opening_tempo_pref"] * 3.0
            if desperation_frac > 0:
                score -= desperation_frac * weights["comeback_desperation_pref"] * 3.0
        scored.append((score, action))
    return scored


# 2026-09-09追加: flourish_tiebreak_pref（3.20節#7）用の定数・ヘルパー。
# SearchBot._select_best参照。
_FLOURISH_TIEBREAK_EPS = 1e-6


def _flourish_kill_value(game, player, action):
    """action(place/move)を実行した場合の見込み撃破数＋スポットキル数（多いほど
    「演出映えする」とみなす、flourish_tiebreak_pref専用の値）。passは常に0。"""
    vp_stars, vp_tengen, vp_spot_positions, rp_pts, _midgame_pts = _spot_position_sets()
    if action[0] == "place":
        _, kind, pos = action
        _, kills, vp_kills, rp_kills, _ = _potential_attack(
            game.board, pos, kind, player,
            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
    elif action[0] == "move":
        _, src, dst = action
        piece = game.board.grid[src]
        _, kills, vp_kills, rp_kills, _ = _potential_attack(
            game.board, dst, piece.kind, player, exclude=src,
            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_pts)
    else:
        return 0
    return kills + vp_kills + rp_kills


def _apply_flourish_tiebreak(game, player, rows):
    """rows: [(value, action), ...]（価値降順ソート済み前提）。最良値からEPS以内の
    「実質同点」候補が複数ある場合のみ、その中でflourish値が最大のものだけに
    絞り込む（絞り込んだ結果、元の降順ソート順は維持する）。実質同点が1件以下、
    またはflourish値が全員0（撃破の見込みが無い）の場合はrowsをそのまま返す
    （スコアそのものは一切変更しないため、通常の探索・比較結果には影響しない）。"""
    best_val = rows[0][0]
    tied = [r for r in rows if best_val - r[0] <= _FLOURISH_TIEBREAK_EPS]
    if len(tied) <= 1:
        return rows
    flourish_values = {a: _flourish_kill_value(game, player, a) for _, a in tied}
    top_flourish = max(flourish_values.values())
    if top_flourish <= 0:
        return rows
    dropped = {a for a, v in flourish_values.items() if v < top_flourish}
    if not dropped:
        return rows
    return [r for r in rows if r[1] not in dropped]


def _piece_reach(kind):
    """その駒種が「攻撃可能な距離」。弓兵など遠隔可能な駒は1より大きい。"""
    cfg = CONFIG["pieces"][kind]
    return 1 + cfg["range"] if cfg["ranged"] else 1


def _threat_response_candidates(game, player, actions):
    """自陣本拠に隣接する敵駒がいる場合、それへの迎撃・接近に関わる手を
    ヒューリスティックスコアに関わらず必ず候補へ含める。

    2026-08-06 修正: 距離判定を一律 distance<=1（隣接）固定にしていたため、
    弓兵のような遠隔駒（reach=2）にとっての正しい迎撃位置（隣接せず射程内）が
    force-includeの対象から漏れ、隣接する劣った選択肢だけが必ず候補に残る
    非対称な状態になっていた（15章の弓兵配置問題がSearchBotで再発する主因）。
    駒種ごとの実際の攻撃可能距離（_piece_reach）を基準にすることで、
    「隣接して殴り合う」案と「射程内から安全に攻撃する」案の両方を候補に残し、
    実際にどちらが良いかをminimaxの実探索に委ねるようにした。"""
    my_base = CONFIG["base_positions"][player]
    threats = [p for p in game.board.adjacent_positions(my_base)
               if p in game.board.grid and game.board.grid[p].owner != player]
    if not threats:
        return []

    forced = []
    for action in actions:
        if action[0] == "place":
            _, kind, pos = action
            reach = _piece_reach(kind)
            if any(game.board.distance(pos, t) <= reach for t in threats):
                forced.append(action)
        elif action[0] == "move":
            _, src, dst = action
            kind = game.board.grid[src].kind
            reach = _piece_reach(kind)
            if any(game.board.distance(dst, t) <= reach for t in threats):
                forced.append(action)
    return forced

def _ongoing_attack_exists(game, player, vp_spot_positions, rp_spot_positions):
    """自軍の既に盤上にある駒だけで、今のラウンド終了時に自動でダメージが入る
    状態（＝何もしなくても攻撃が継続する状況）が既に成立しているかどうかを返す。

    2026-08-07追加: 「継続中の良い攻撃を、その駒自身を動かして自ら手放してしまう」
    問題（棋譜レビューで報告）への対処として、pass を _top_k_candidates で
    強制候補に加えるかどうかの判定に使う。無条件に pass を強制候補へ加えると、
    盤面がまだ空の対局開始直後（R1）でも pass が候補に混ざり、評価関数側の
    exposure項（新規配置は常に多少の被発見リスクを負う）とかみ合って
    「初手パス」という明らかに不自然な手が選ばれる事例が確認されたため、
    「既に自軍の駒が攻撃を継続している」場合に限定して発動する。"""
    for pos, piece in game.board.grid.items():
        if piece.owner != player:
            continue
        dmg, _kills, _vp_kills, _rp_kills, _base_dmg = _potential_attack(
            game.board, pos, piece.kind, player,
            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_spot_positions)
        if dmg > 0:
            return True
    return False


def _pending_kill_positions(game, player, vp_spot_positions, rp_spot_positions):
    """今このプレイヤーが何もしなければ（＝その駒を動かさなければ）、このラウンドの
    戦闘解決で撃破が確定する自軍の駒の位置集合を返す（2026-08-11追加）。

    経緯: evaluate_state（探索の葉ノード評価）は盤上の全駒について
    _position_hold_value 経由で kill_bonus を「まだ実現していない将来の脅威」として
    毎回加算し直す一方、実際に撃破が成立した場合は駒が盤から消えるだけで
    material値(2〜5点程度)しか評価に乗らない。この結果、「確定した撃破を実行する」
    より「別の場所でも何か攻撃できそうに見える」方が探索から見て魅力的に映る
    ケースがあり、確定キルを放棄する無意味なmoveが選ばれる原因になっていた。
    1手先のスコアリング（hold_valueの減点）だけでは深い探索のこの不整合を
    防ぎきれないため、候補生成の段階でそもそも選択肢から除外する。"""
    positions = set()
    for pos, piece in game.board.grid.items():
        if piece.owner != player:
            continue
        _dmg, kills, _vk, _rk, _bd = _potential_attack(
            game.board, pos, piece.kind, player,
            vp_spot_positions=vp_spot_positions, rp_spot_positions=rp_spot_positions)
        if kills > 0:
            positions.add(pos)
    return positions


def _base_damage_contribution(board, pos, kind, player):
    """そのマスに立った駒(kind)が、今このラウンド終了時の戦闘解決で敵本拠へ
    与える見込みダメージ量だけを取り出す（_potential_attackの戻り値の一部）。"""
    return _potential_attack(board, pos, kind, player)[4]


def _pending_base_kill_candidates(game, player, actions):
    """このラウンド、実際に敵本拠を撃破できる（＝対局を即勝ちで終わらせられる）
    place/move手を検出し、そのまま返す（2026-08-22追加・9章-0/9章-2対応）。

    背景: `base_pressure`重みは3.2節の「本拠特攻の支配戦略化」対策として意図的に
    低く設定されている。この重みは`_score_actions`（候補絞り込み用の1手先評価）
    にも使われているため、「あと一手で確定的に本拠を撃破できる」手までもが
    低評価を受け、上位k件の候補（`_top_k_candidates`が絞り込んだもの）から
    漏れてしまうことがある。漏れた場合、その手はminimaxの本探索（evaluate_state
    が game.winner を検出して ±1e6 を返す経路）まで一度も到達できず、機会そのものを
    逃す（4.18節・0.13節・9章-0で報告された「往復移動バグ」・「決め手を見逃す」
    現象の根本原因）。

    対応方針は`_pending_kill_positions`（確定キルを放棄するmoveの除外）と対称で、
    「除外」ではなく「強制的に候補へ含める」形にした。実際に致死かどうかの判定は
    _score_actions の重み付けを一切経由せず、`_potential_attack`が返す実際の
    base_damage見込みと敵本拠の現在HPを直接比較するため、base_pressureの値に
    関わらず必ず検出できる。"""
    opp = 1 - player
    base_pos = CONFIG["base_positions"][opp]
    board = game.board
    base_piece = board.grid.get(base_pos)
    if base_piece is None or base_piece.kind != "本拠" or base_piece.hp <= 0:
        return []

    baseline_total = 0.0
    per_piece_base_dmg = {}
    for pos, piece in board.grid.items():
        if piece.owner != player:
            continue
        bd = _base_damage_contribution(board, pos, piece.kind, player)
        per_piece_base_dmg[pos] = bd
        baseline_total += bd

    forced = []
    for action in actions:
        if action[0] == "place":
            _, kind, pos = action
            total = baseline_total + _base_damage_contribution(board, pos, kind, player)
        elif action[0] == "move":
            _, src, dst = action
            kind = board.grid[src].kind
            old_bd = per_piece_base_dmg.get(src, 0.0)
            new_bd = _base_damage_contribution(board, dst, kind, player)
            total = baseline_total - old_bd + new_bd
        else:
            continue
        if total >= base_piece.hp:
            forced.append(action)
    return forced


def _top_k_candidates(game, player, actions, k, weights=HEURISTIC_WEIGHTS):
    # 2026-09-07最適化（3.18節）: 同上。ここはvp_spot_positions/rp_ptsしか
    # 使わないが、キャッシュ関数の戻り値をそのまま分解して使う。
    _vp_stars, _vp_tengen, vp_spot_positions, rp_pts, _midgame_pts = _spot_position_sets()

    # 2026-08-11追加: 確定している撃破を放棄するmoveを、そもそも候補から除外する。
    pending_kill_positions = _pending_kill_positions(game, player, vp_spot_positions, rp_pts)
    if pending_kill_positions:
        filtered = [
            a for a in actions
            if not (a[0] == "move" and a[1] in pending_kill_positions)
        ]
        # 除外した結果、合法手が空になる事態はほぼ想定されないが、安全のため保険を残す
        # （place/pass等、確定キルの駒を動かさない手は必ず残るはずのため）。
        if filtered:
            actions = filtered

    scored = _score_actions(game, player, actions, weights=weights)
    scored.sort(key=lambda t: t[0], reverse=True)
    top = [a for _, a in scored[:k]]

    forced = _threat_response_candidates(game, player, actions)
    for a in forced:
        if a not in top:
            top.append(a)

    # 2026-08-22追加（9章-0/9章-2対応）: 本拠を確定的に撃破できる手は、
    # base_pressureの低評価に関わらず必ず候補へ強制的に含める。
    lethal_base_kills = _pending_base_kill_candidates(game, player, actions)
    for a in lethal_base_kills:
        if a not in top:
            top.append(a)

    # 継続中の攻撃がある場合はpassを強制候補に加える（pass自体は-1.0評価で
    # 通常は上位K件から漏れ、「攻撃を継続する唯一の手段が候補にない」事故になるため）
    if _ongoing_attack_exists(game, player, vp_spot_positions, rp_pts):
        pass_action = next((a for a in actions if a[0] == "pass"), None)
        if pass_action is not None and pass_action not in top:
            top.append(pass_action)

    return top


# ============================================================
# 6. αβミニマックス（候補プルーニングつき）
# ============================================================
class _SearchTimeout(Exception):
    """反復深化（iterative deepening）のdeadlineを超えたことを示す内部シグナル。
    2026-08-24追加（0.67節・9章TODO対応: 反復深化への構造変更）。

    このexceptionはminimax()の再帰呼び出しをそのまま素通りしてSearchBot側
    まで伝播する。深さDの探索がこれで打ち切られたということは、その呼び出しは
    兄弟候補の一部しか評価し切れていない（alpha-beta前提が崩れている）ため、
    呼び出し元（SearchBot._choose_with_time_budget）はこの深さDの結果を一切
    採用せず、直前に完了した深さD-1の結果をそのまま使う。「N秒経ったら
    その時点のbest_actionを使う」という単純な壁時計打ち切りは、経路によっては
    手が全く決まっていない状態（best_action=None）や、比較が不完全な状態の
    値を採用してしまう危険があるため、意図的に「完了した深さの結果だけを
    信頼する」設計にしてある。"""
    pass


def minimax(game, player_to_move, root_player, depth, alpha, beta,
            production_bots, candidate_k=10, collect_rows=None, weights=HEURISTIC_WEIGHTS,
            deadline=None):
    """collect_rows: Noneでなければ、この呼び出し自身の候補ループで
    (val, action) を追記していく。2026-08-12追加: 再帰呼び出しには渡さない
    （collect_rows=collect_rowsとしない）ことで、最上位（ルート）呼び出しの
    候補一覧だけを集める。これにより、choose()のジッター経路や候補ログ表示
    (debug_choose_candidates)で「候補ごとに独立にminimaxをやり直す」という
    高コストな処理（兄弟間でalpha-beta枝刈りが効かず、実測で1手あたり数倍〜
    十倍近く遅くなっていた）が不要になり、通常の高速経路と同じ1回のminimax
    呼び出しだけで済む。

    weights: このminimax呼び出し全体（葉のevaluate_state・各深さでの候補
    絞り込み_top_k_candidatesの両方）で一貫して使う重み。2026-08-16追加:
    チャンピオン制検証（tune_balance_search.py）でcandidate/championという
    重みの異なる2体を対戦させられるようにするための変更。1回のchoose()呼び
    出しは常に「呼び出し元のボット自身の重み」で自分の手・相手の応手（を
    自分の重みで近似したもの）を評価する（＝相手の本当の重みを覗き見ない、
    通常のminimaxの前提と同じ）。production_bots辞書の中身（相手の応手時の
    生産シミュレーション）も従来通りself（＝この重み）を使う。

    deadline: 2026-08-24追加。Noneなら従来通り時間制限なし（固定深さ探索、
    tune_balance_search.py等の既存呼び出し元はdeadlineを渡さないため挙動・
    速度とも完全に不変）。time.monotonic()の値を渡すと、そのepochを過ぎた
    時点でこの呼び出し（および再帰的な子呼び出し）が_SearchTimeoutを送出して
    即座に巻き戻る。呼び出しのたびに時刻を読むオーバーヘッドはあるが、1ノード
    あたりcandidate_k件程度までしか展開しないため実測上は無視できる小ささ。"""
    if deadline is not None and time.monotonic() > deadline:
        raise _SearchTimeout()

    if game.winner is not None or depth <= 0:
        return evaluate_state(game, root_player, weights=weights), None

    actions = game.valid_actions(player_to_move)
    candidates = _top_k_candidates(game, player_to_move, actions, candidate_k, weights=weights)

    maximizing = (player_to_move == root_player)
    best_val = float("-inf") if maximizing else float("inf")
    best_action = candidates[0] if candidates else None

    for action in candidates:
        child = clone_game(game)
        next_player, round_ended, game_over = simulate_action(child, player_to_move, action, production_bots)

        if game_over:
            val = evaluate_state(child, root_player, weights=weights)
        else:
            # TODO: ラウンドを跨いだ時だけ depth を消費する設計。
            #       depthの単位を「手数」にしたい場合はここを毎回 depth-1 にする。
            next_depth = depth - 1 if round_ended else depth
            val, _ = minimax(child, next_player, root_player, next_depth,
                              alpha, beta, production_bots, candidate_k, weights=weights,
                              deadline=deadline)

        if collect_rows is not None:
            collect_rows.append((val, action))

        if maximizing:
            if val > best_val:
                best_val, best_action = val, action
            alpha = max(alpha, best_val)
        else:
            if val < best_val:
                best_val, best_action = val, action
            beta = min(beta, best_val)

        if alpha >= beta:
            break  # αβ枝刈り

    return best_val, best_action


# ============================================================
# 7. SearchBot 本体
# ============================================================
PRODUCTION_CANDIDATE_K_DEFAULT = 6
# choose_production()の全マス×全駒種フル評価はO(P^2)で盤面密度依存の重さの
# 原因だった（実測150秒超）。軽量な事前スコアリング(O(P))で候補マスを絞り込み、
# 上位K件（デフォルト6）だけをフル評価する2段階方式に変更。


# ============================================================
# 7.5 choose_production の差分計算化（根本対応）
# ============================================================
# 上記の2段階絞り込みでも68秒まで改善したのみ。choose_production自体が
# 候補ごとにevaluate_stateをフル再計算する構造が対局時間の8割超を占めていた。
#
# 方針: 1駒の追加による評価値変化は「隣接」or「射程内」という有限範囲にしか
# 及ばない設計のため、影響範囲を理論上限（PRODUCTION_DELTA_RADIUS）で確定でき、
# それより遠い駒は評価値が変化しない。
#
# 影響半径の導出: 隣接判定(半径1)、射程内攻撃(半径<=MAX_REACH)、二次的な合算
# ダメージ経路(三角不等式で2*MAX_REACH以内)の3経路のみ。上限は2*MAX_REACHとし、
# ハードコードせずCONFIGから算出する（詳細は検証ハンドブック参照）。
#
# RP/reserve変化に連動する_new_placement_threatは位置に依存しないため、半径の
# 外側でも起こりうる。_production_enemy_threat_shift()で駒種ごとに分離計算する。
#
# 検証方針: 新実装(choose_production)と旧実装(_choose_production_reference)を
# 実戦棋譜由来の複数盤面で突き合わせ、評価値が一致することを確認済み。

def _max_piece_reach():
    """現在のCONFIGにおける駒の最大「攻撃が届く距離」（隣接のみなら1）。
    駒種のrange/atk/hpが将来チューニングで変わっても、影響半径の計算が
    自動的に追従するよう、マジックナンバーではなく都度CONFIGから算出する。"""
    reach = 1
    for cfg in CONFIG["pieces"].values():
        r = (1 + cfg["range"]) if cfg["ranged"] else 1
        if r > reach:
            reach = r
    return reach


def _production_delta_radius():
    """choose_productionの差分計算で「新規配置の影響が理論上及びうる」と
    確定できる安全側のマンハッタン距離の上限。導出根拠は本セクション冒頭の
    コメント参照（要点: 直接の射程による影響が最大MAX_REACH、それを介した
    2次的な合算ダメージ判定の影響がさらにMAX_REACH分乗るため2倍）。"""
    return 2 * _max_piece_reach()


# 「半径内座標の事前列挙」ヘルパーはscoring_common.pyに一本化
# (_positions_within_manhattan_radius)し、ここではimportして使う。

def _reserve_afford_best_atk(reserve_list, rp):
    """reserve_list（駒種名のリスト）のうちrp以下のcostで配置可能なものの中で
    最大のATKを返す（無ければNone）。_new_placement_threatの内部計算と
    同じ定義（affordable/best_atk）をここで独立に再現し、O(E)の全駒再計算を
    行う前に「そもそも配置可能な最大ATKが変わったか」だけをO(reserveサイズ)
    で先に判定するために使う（_production_enemy_threat_shift参照）。"""
    best = None
    for k in reserve_list:
        if CONFIG["pieces"][k]["cost"] <= rp:
            atk = CONFIG["pieces"][k]["atk"]
            if best is None or atk > best:
                best = atk
    return best


class _ProductionEvalContext:
    """choose_production()の「実際に配置して評価する」分岐（重い方）で使う、
    盤面全体の下ごしらえをまとめたヘルパー（0.36節）。1回のchoose_production
    呼び出し（=1手番のアップキープ判断）につき最大1つ生成し、複数の駒種・
    複数マスの候補評価で使い回す。

    保持するもの:
      - baseline_ranged_by_owner: 各ownerの射程駒一覧（evaluate_stateと同じ
        _ranged_enemy_pieces()の結果）。新規駒がranged=Trueの場合、候補ごとに
        「持ち主側のリストへ新規駒のエントリだけ追加」した一時辞書を作る
        （全件再スキャンしない。0.35節のO(1)化の考え方を踏襲）。
      - material/hold/threat/contribution: 現盤面（駒を何も追加していない
        状態）での各駒（本拠を除く）の寄与の内訳。evaluate_state内側ループの
        1駒分の計算と完全に同じ式・同じ引数（count_vp_spot_bonus=False等）。
      - old_best_atk: 現在のRP/reserveでの_reserve_afford_best_atk結果
        （_enemy_threat_shiftで使う。「実際に配置する」分岐ではreserveが
        変化しないため、choose_production呼び出し中ずっと不変）。
    """

    def __init__(self, game, player, weights=None):
        self.game = game
        self.player = player
        self.board = game.board

        # 2026-09-07最適化（3.18節）: 同上のキャッシュ済みset群を使う。
        # choose_production呼び出しごとに最大1回しか生成されないインスタンスだが、
        # production_phase()は探索の全ラウンド境界（内部ノード含む）で両プレイヤー分
        # 呼ばれるため、1局あたりの生成回数自体は非常に多い（実測4731回/局）。
        (self.vp_stars, self.vp_tengen, self.vp_spot_positions,
         self.rp_pts, self.midgame_pts) = _spot_position_sets()
        # 呼び出し元(SearchBot)は自分のself.weightsを明示的に渡せる（省略時はグローバル）
        self.weights = weights if weights is not None else HEURISTIC_WEIGHTS
        self.radius = _production_delta_radius()
        # 2026-09-04追加: evaluate_state/_score_actionsと同じbase_pressureラウンド
        # 立ち上がり倍率。生産判断（choose_production）でも同じ基準を使う。
        self.bp_mult = _base_pressure_ramp_multiplier(game.round_number, self.weights["base_pressure_ramp_rounds"], self.weights["rush_opening_pressure"])

        self.baseline_ranged_by_owner = {
            0: _ranged_enemy_pieces(self.board, 0),
            1: _ranged_enemy_pieces(self.board, 1),
        }

        self.material = {}
        self.hold = {}
        self.threat = {}
        self.contribution = {}
        for pos, piece in self.board.grid.items():
            if piece.kind == "本拠":
                continue
            m = _piece_current_material_value(piece)
            h = _position_hold_value(
                self.board, pos, piece.kind, piece.owner, self.weights,
                self.vp_stars, self.vp_tengen, self.rp_pts, self.vp_spot_positions,
                count_vp_spot_bonus=False, midgame_pts=self.midgame_pts,
                ranged_enemies=self.baseline_ranged_by_owner[1 - piece.owner],
                ranged_friends=self.baseline_ranged_by_owner[piece.owner],
                # 2026-09-04追加: evaluate_stateと同じ理由でbase_pressureの二重役割を解消。
                apply_base_pressure=(piece.owner == player),
                base_pressure_multiplier=self.bp_mult,
            )
            t = _new_placement_threat(game, self.board, pos, piece.kind, piece.owner)
            sign = 1 if piece.owner == player else -1
            self.material[pos] = m
            self.hold[pos] = h
            self.threat[pos] = t
            self.contribution[pos] = sign * (m + h + t * self.weights["exposure"])

        self.reserve_list = list(game.reserve[player])
        self.old_best_atk = _reserve_afford_best_atk(self.reserve_list, game.econ.rp[player])
        self.baseline_score = evaluate_state(game, player, weights=self.weights)

        # 2026-09-07発見・修正（3.18節、3.17節の副作用検証中に発覚した第2の既存バグ）:
        # evaluate_stateのmy_base_threat項（_own_base_threat経由、weights["base_defense"]
        # ×_base_hp_panic_multiplier）は、「自陣本拠を狙っている敵駒が、本拠以外にも
        # 何体のターゲットを持っているか」（呼吸点分割の分母）に依存する。新規に自軍の
        # 駒をその敵の隣接/射程内に配置すると、敵からすればターゲットが1体増えるため
        # 分割が変わり、本拠へ届く被害見積もりが変化する（＝迎撃・身代わりの効果が
        # 本拠側にも波及する）。ところがlocal_delta（近傍再評価ループ）は本拠を明示的に
        # スキップしており（"本拠"はpiece.kind=="本拠"で除外。_position_hold_value側も
        # 敵駒自身のhold値からbase_dmg分をapply_base_pressure=Falseで意図的に除外して
        # 二重計上を避けている＝本拠への被害はここではなく_own_base_threatが唯一の
        # 計上経路）、この本拠への波及効果を拾う項がbest_value_for_placements全体に
        # 一つも無かった。my_base_threat_before/my_base_panicをここで1回だけ計算し、
        # 配置後にmy_base_threatを再計算した差分をvalへ明示的に足す
        # （_own_base_threatは本拠隣接マス+射程駒リストのみを見るO(1)相当の軽い関数
        # なので、他の項と違って「関係あるposだけ早期判定」の最適化はせず常に計算する）。
        opp = 1 - player
        self.base_defense_weight = self.weights["base_defense"]
        self.my_base_panic = _base_hp_panic_multiplier(
            self.board, player, self.weights["base_hp_panic_threshold"])
        self.my_base_threat_before = _own_base_threat(
            self.board, player, ranged_enemies=self.baseline_ranged_by_owner[opp])

        # 2026-09-07追加（3.17節の副作用として発見・3.18節で修正）: rp_income_margin用の
        # 「今の盤面のままなら来期入るRP収入見積もり」のbaseline。production前は
        # game.econ.rp[player]自身には依存しない（_estimate_next_round_incomeは
        # board.gridの占有状況とeconomy.spot_owned_sinceだけを見る）ため、この
        # コンテキスト生成時に1回だけ計算すれば全kind・全posで使い回せる。
        # income_relevant_positionsに含まれないposへの配置はこの項が絶対に動かない
        # （_estimate_next_round_incomeがrp_spots/midgame_spotsの8マスしか見ないため）
        # ので、best_value_for_placements側でこの集合に含まれるposだけ再計算する
        # ことで、無関係な配置候補への追加コストをゼロに保つ。
        self.income_relevant_positions = self.rp_pts | self.midgame_pts
        self.income_before = _estimate_next_round_income(game, player)

    def _enemy_threat_shift(self, rp_after):
        """新規配置プレイヤーのRPが rp_after に変化したとき、"敵駒"
        （player以外のowner）のthreat寄与がどれだけ変化するかをまとめて計算
        する（マスに依存しない、駒種ごとに1回だけの計算）。

        戻り値: (global_delta, rp_only_threat)
          - global_delta: 全敵駒について (新threat-旧threat)*exposure*(-1) を
            合計したもの（符号は評価値がplayer視点のため、敵駒の寄与は-1倍）。
          - rp_only_threat: 敵駒posごとの「RPだけ変えて、駒はまだ配置しない」
            場合のthreat値の辞書。半径内の敵駒をposごとの局所再計算に組み込む
            際、この項目との二重計上を避けるために「引くべき旧値」として使う
            （self.thresholdではなくこちらを使う。best_value_for_placements参照）。

        new_best_atk == old_best_atk の場合、_new_placement_threatが返す値は
        どの敵駒についても変化しないことが式から直接わかる（affordable集合が
        変わらなければrelative/best_atkも不変なため）。この場合はO(1)で
        global_delta=0・rp_only_threat=self.threat（そのまま参照）で済ませ、
        全敵駒を再走査するO(E)のループを避ける（実戦ではRP減少が閾値を
        跨がないことの方が多く、この早期終了が頻繁に効く）。"""
        new_best_atk = _reserve_afford_best_atk(self.reserve_list, rp_after)
        if new_best_atk == self.old_best_atk:
            return 0.0, self.threat

        game = self.game
        player = self.player
        board = self.board
        original_rp = game.econ.rp[player]
        game.econ.rp[player] = rp_after
        try:
            rp_only_threat = {}
            global_delta = 0.0
            w_exposure = self.weights["exposure"]
            for pos, piece in board.grid.items():
                if piece.kind == "本拠" or piece.owner == player:
                    continue
                t = _new_placement_threat(game, board, pos, piece.kind, piece.owner)
                rp_only_threat[pos] = t
                global_delta += -1.0 * w_exposure * (t - self.threat[pos])
        finally:
            game.econ.rp[player] = original_rp
        return global_delta, rp_only_threat

    def best_value_for_placements(self, kind, positions, rp_after):
        """kind(駒種)をrp_after（配置後のRP）でpositions各マスに置いた場合の
        評価値のうち最良のものを返す（差分計算。旧実装のbest_pos_valと同じ
        意味）。positionsが空ならNoneを返す。"""
        if not positions:
            return None

        game = self.game
        player = self.player
        board = self.board
        weights = self.weights
        cfg = CONFIG["pieces"][kind]
        radius = self.radius

        # evaluate_state内の「RP項」の変化分。weights["rp_differential"]を使う（VP項は生産で不変）
        rp_term_delta = (rp_after - game.econ.rp[player]) * weights["rp_differential"]

        # 2026-09-07追加: production_diversity_pref（3.16節対応）。self.baseline_score
        # はこのkindを生産する前の状態のevaluate_stateであり、production後の
        # 駒種構成の偏り変化を反映していないため、rp_term_delta等と同じ形で
        # 明示的に差分を足す（kindは全positionsで共通なので1回だけ計算すればよい）。
        diversity_delta = _production_diversity_marginal_delta(
            board, self.reserve_list, player, kind, weights)

        # 2026-09-09追加: signature_unit_affinity（3.20節#6）。kindを1体追加する
        # ことによる評価値の変化はkind・positionによらず常に一定（evaluate_state側の
        # 実装がown_kind_counts[kind]の単純な線形加算のため）。diversity_deltaと
        # 同じ理由で、全positions共通の値として1回だけ計算する。
        signature_delta = weights[SIGNATURE_AFFINITY_KEY[kind]]

        # 2026-09-09追加: opening_tempo_pref（3.20節#1）。RPスポットへの配置
        # (pos in self.rp_pts)の場合のみ、own_rp_spots_ownedが1増える分の
        # 変化をtempo_mult*weightで加算する。pos依存のためpositionsのループ内で
        # 個別に加える（下記val=の直前参照）。
        tempo_mult = _opening_tempo_multiplier(game.round_number)
        opening_tempo_weight = weights["opening_tempo_pref"]

        enemy_delta, rp_only_threat = self._enemy_threat_shift(rp_after)

        original_rp = game.econ.rp[player]
        best_val = None

        for pos in positions:
            new_piece = Piece(kind=kind, owner=player, hp=cfg["hp"], max_hp=cfg["hp"],
                               atk=cfg["atk"], movable=True)

            ranged_by_owner = self.baseline_ranged_by_owner
            if cfg["ranged"]:
                ranged_by_owner = dict(self.baseline_ranged_by_owner)
                ranged_by_owner[player] = self.baseline_ranged_by_owner[player] + [(pos, new_piece, cfg)]

            # --- 新規駒自身の寄与（自分は自分の隣接/射程判定対象に含まれないため
            #     盤面へ実際に置かなくても正しく計算できる） ---
            p_material = _piece_material_value(kind)
            p_hold = _position_hold_value(
                board, pos, kind, player, weights,
                self.vp_stars, self.vp_tengen, self.rp_pts, self.vp_spot_positions,
                count_vp_spot_bonus=False, midgame_pts=self.midgame_pts,
                ranged_enemies=ranged_by_owner[1 - player],
                ranged_friends=ranged_by_owner[player],
                base_pressure_multiplier=self.bp_mult,
            )
            p_threat = _new_placement_threat(game, board, pos, kind, player)
            p_contribution = 1.0 * (p_material + p_hold + p_threat * weights["exposure"])

            # --- 近傍への影響（半径内の既存駒だけ再評価） ---
            # 全駒スキャン(O(盤上駒数))ではなく_positions_within_manhattan_radiusで
            # 半径内座標のみ事前列挙しO(1)参照する方式（ループ本体はO(半径²)で密度非依存）
            board.grid[pos] = new_piece
            game.econ.rp[player] = rp_after
            local_delta = 0.0
            # 2026-09-07発見・2026-09-07(3)修正（3.17節の副作用として発覚した既存バグ、
            # 3.18節参照）: evaluate_state本体はrp_income_margin項
            # （_estimate_next_round_income由来。自軍がRP/中盤スポットを保有している
            # ほど「来期の名目RP収入」が増える分を評価に織り込む）を持つが、この差分
            # 計算経路(best_value_for_placements)は従来この項の変化を一切計算していな
            # かった（rp_term_delta等の他の差分項と同じ形で明示的に足す必要があるのに
            # 抜けていた）。_estimate_next_round_incomeはrp_spots/midgame_spotsの
            # 8マスの占有状況しか見ないため、pos自身がこの8マスに含まれない場合は
            # 配置の前後でこの項が絶対に変化しない。income_relevant_positionsで
            # 早期にゼロ判定することで、無関係な大多数の配置候補への追加コストを
            # ゼロに保っている（オンライン対戦のレイテンシに直結するホットパスの
            # ため、正しさと同時に速度も維持する）。
            income_delta = 0.0
            w_income_margin = weights["rp_income_margin"]
            if w_income_margin and pos in self.income_relevant_positions:
                # board.grid[pos]は既に新規駒に置き換えている（このtryブロックの直前）
                # ため、_estimate_next_round_incomeは工兵ボーナスの有無も含めて
                # 「production後」の状態をそのまま正しく見る。opp側のRP収入は、
                # posが元々必ず空マス（_candidate_positions_for_productionが返す
                # 候補は常にboard.grid未占有のマスのみ）だったため、この配置で
                # 変化しない（他プレイヤーの保有スポットを横取りするわけではない）。
                income_delta = (
                    _estimate_next_round_income(game, player) - self.income_before
                ) * w_income_margin

            # 2026-09-07修正（3.18節）: my_base_threat（本拠防衛）の波及分。
            # board.grid[pos]は既に新規駒に置き換えている（上記income_delta同様、
            # この時点の盤面で計算する必要がある）。_own_base_threatは本拠の隣接
            # マス+射程駒リストしか見ないため、posが本拠から遠くても敵の射程駒
            # （弓兵等）経由で影響しうる。早期ゼロ判定はせず常に再計算する。
            my_base_threat_after = _own_base_threat(
                board, player, ranged_enemies=ranged_by_owner[1 - player])
            base_threat_delta = (
                my_base_threat_after - self.my_base_threat_before
            ) * self.base_defense_weight * self.my_base_panic
            try:
                for ypos in _positions_within_manhattan_radius(board.size, pos, radius):
                    ypiece = board.grid.get(ypos)
                    if ypiece is None or ypiece.kind == "本拠":
                        continue
                    y_material = self.material[ypos]
                    y_hold = _position_hold_value(
                        board, ypos, ypiece.kind, ypiece.owner, weights,
                        self.vp_stars, self.vp_tengen, self.rp_pts, self.vp_spot_positions,
                        count_vp_spot_bonus=False, midgame_pts=self.midgame_pts,
                        ranged_enemies=ranged_by_owner[1 - ypiece.owner],
                        ranged_friends=ranged_by_owner[ypiece.owner],
                        # 2026-09-04追加: 同上（本関数はplayer以外の駒(ypiece)も再評価するため必要）。
                        apply_base_pressure=(ypiece.owner == player),
                        base_pressure_multiplier=self.bp_mult,
                    )
                    y_threat = _new_placement_threat(game, board, ypos, ypiece.kind, ypiece.owner)
                    sign = 1 if ypiece.owner == player else -1
                    new_contribution = sign * (y_material + y_hold + y_threat * weights["exposure"])

                    if ypiece.owner != player:
                        # 敵駒: _enemy_threat_shiftとの二重計上を避けrp_only_threatを使う
                        old_relevant_threat = rp_only_threat.get(ypos, self.threat[ypos])
                        old_relevant_contribution = sign * (
                            y_material + self.hold[ypos] + old_relevant_threat * weights["exposure"])
                    else:
                        old_relevant_contribution = self.contribution[ypos]

                    local_delta += new_contribution - old_relevant_contribution
            finally:
                del board.grid[pos]
                game.econ.rp[player] = original_rp

            # 2026-09-09追加: opening_tempo_pref。posがRPスポットの場合のみ、
            # own_rp_spots_ownedが1増える分をtempo_mult*weightで加算する
            # （それ以外のposでは0。income_deltaと同じ「無関係な配置候補への
            # 追加コストをゼロに保つ」設計）。
            opening_tempo_delta = (
                tempo_mult * opening_tempo_weight if pos in self.rp_pts else 0.0
            )

            val = (self.baseline_score + rp_term_delta + enemy_delta + p_contribution
                   + local_delta + diversity_delta + income_delta + base_threat_delta
                   + signature_delta + opening_tempo_delta)

            if best_val is None or val > best_val:
                best_val = val

        return best_val


class SearchBot:
    def __init__(self, depth=2, candidate_k=10, debug=False, rng=None,
                 production_candidate_k=PRODUCTION_CANDIDATE_K_DEFAULT, weights=None,
                 time_budget=None, max_depth=None, record_debug_kifu=False):
        self.depth = depth            # 「ラウンド」単位の先読み深さ（既定はdepth2。0.95節でdepth3から変更。
                                       # 0.77節でdepth3を基準値と定めていたが、非同期対戦の運用コスト
                                       # （0.95節の実測）を踏まえてdepth2へ引き下げ。base_hp_panic_threshold
                                       # の発火ライン0.8はdepth2の探索地平線に収まるよう設計済みのため
                                       # （0.90節）、防御性能はdepth2でも維持される）
        self.candidate_k = candidate_k
        self.production_candidate_k = production_candidate_k
        self.debug = debug
        # weights省略時はグローバルのHEURISTIC_WEIGHTSを使う（後方互換）。明示的に
        # 渡すとcandidate/championボットに別々の重みを持たせられる（チャンピオン制評価向け）
        # 符号統一方針（weights.py参照）: ここでto_engine_signed()を一度だけ通し、
        # 以降self.weightsを参照する全ての評価式（ThreatMap等）は変更しない。
        # 2026-09-12修正: 以前はweightsをそのままto_engine_signed()に渡していたため、
        # play_vs_aiの対戦相手選択で新しい重みキー新設より前の世代（champion_history/
        # gen_XXXX.jsonの古いスナップショット）を選ぶと、そのキーが辞書に存在せず
        # 評価式がKeyErrorで落ちていた（resolve_opponent_weights()のdocstring参照）。
        # 現在のデフォルトで欠けたキーを補うresolve_opponent_weights()を経由させる。
        self.weights = to_engine_signed(
            resolve_opponent_weights(weights) if weights is not None else HEURISTIC_WEIGHTS
        )
        # 探索中の生産判断はself自身のchoose_production()を使う（HeuristicBot.choose_production
        # は「生産しない」を選べない欠陥があり、以前これを使っていたのがpassバグの原因だった）
        #
        # rng=None（既定）: 完全決定論・高速経路（自動検証向け）。
        # rng=random.Random()等: 最終選択にジッターを載せ対局ごとに展開を変える（低速経路、
        # main.py・play_vs_ai向け。自動検証では使わないこと）。
        self.rng = rng
        # time_budget=None（既定）: self.depthの固定深さでminimax1回（決定論・高速、既存呼び出し元は無変更）
        # time_budget=秒数: 反復深化に切り替え、予算内で完了した最深の結果を採用
        #     （難易度を時間予算そのもので設定できる。「インポッシブル=30秒」等）
        # max_depth: 反復深化時の深さ上限（None=事実上無制限）
        self.time_budget = time_budget
        self.max_depth = max_depth

        # 2026-09-06追加: game.py Game.run()はhasattr(bot, "choose_with_debug")で
        # 自動的に候補ログ記録つきの低速経路へ切り替える設計（generate_kifu.py・
        # main.py・tune_balance_search.pyが共通で使うrun()）。SearchBotは
        # 意図的にこの名前のメソッドを持たない（debug_choose_candidates()の
        # docstring参照。main.py/tune_balance_search.pyの大量自己対戦を
        # 誤って低速化しないため）ため、これまでSearchBot同士の対局を
        # generate_kifu.pyで棋譜化すると、常にcandidates=Noneになっていた
        # （「以前は表示されていたのに見えなくなった」という退行の実体は、
        # この意図的な名前分離がgenerate_kifu.py側の事情を考慮していなかった
        # ことによるもの）。
        # record_debug_kifu=Trueを明示的に渡した場合だけ、インスタンス単位で
        # choose_with_debugをdebug_choose_candidatesの別名として生やし、
        # hasattr()チェックを満たすようにする。既定はFalseのままなので、
        # このフラグを渡さない既存の全呼び出し元（main.py・tune_balance_search.py・
        # web_api.py等）の速度・決定論には一切影響しない。
        # generate_kifu.py側で候補ログ入りの棋譜を残したい対局のbotにだけ
        # SearchBot(..., record_debug_kifu=True) を指定すること。
        if record_debug_kifu:
            self.choose_with_debug = self.debug_choose_candidates

    def choose(self, game, player, actions):
        if self.debug:
            print(f"[R{game.round_number} P{player}] RP={game.econ.rp[player]} "
                f"legal={len(actions)} sample={actions[:5]}")

        if len(actions) == 1:
            return actions[0]
        production_bots = {0: self, 1: self}

        if self.time_budget is not None:
            # 2026-08-24追加: 時間予算ベースの反復深化。既存の固定深さ経路とは
            # 完全に切り離してあり、time_budgetを渡さない既存呼び出し元の速度・
            # 決定論には一切影響しない（詳細は_choose_with_time_budget参照）。
            return self._choose_with_time_budget(game, player, actions, production_bots)

        # rngの有無に関わらず必ず1回のminimax呼び出しで済ませる（候補ごとに
        # minimaxをやり直すとalpha-beta枝刈りが効かず著しく遅くなるため）
        need_rows = self.debug or self.rng is not None
        rows = [] if need_rows else None
        _, best_action = minimax(
            clone_game(game), player, player, self.depth,
            float("-inf"), float("inf"), production_bots, self.candidate_k,
            collect_rows=rows, weights=self.weights,
        )

        if rows:
            rows.sort(key=lambda t: t[0], reverse=True)
            if self.debug:
                print(f"[R{game.round_number} P{player}] top candidates:")
                for val, action in rows[:8]:
                    print(f"    {val:8.2f}  {action}")
            if self.rng is not None:
                chosen = self._select_best(game, player, rows)
                if chosen is not None:
                    return chosen

        return best_action if best_action is not None else actions[-1]

    def _choose_with_time_budget(self, game, player, actions, production_bots):
        """反復深化（iterative deepening）: depth=1から順に完全に探索し、
        self.time_budget秒の予算内で完了できた最も深い探索の結果を採用する
        （2026-08-24追加。0.67節・9章TODO#: depth難易度階層案の前提となる
        タイムアウト実装方法の見直し対応）。

        なぜ単純な壁時計タイムアウトではなく反復深化にするのか:
        固定深さのminimax呼び出し中に「N秒経ったら強制終了」するだけだと、
        打ち切られた時点でbest_actionが全く決まっていない（あるいは兄弟候補の
        一部しか比較できていない、alpha-beta前提が崩れた不完全な値になっている）
        可能性がある。反復深化なら「深さDの探索が最後まで完了した」ことを
        _SearchTimeoutが送出されなかったことで保証できるため、常に「その時点で
        安全に採用できる、直近に完了した深さの結果」を返せる。

        - depth=1の探索にはdeadlineを渡さない（＝どれだけtime_budgetが短くても
          最低限、手が全く決まらない事態を避けるため、深さ1だけは無条件で
          完了させる）。
        - depth=2以降はdeadline付きで探索し、_SearchTimeoutで打ち切られたら
          その深さの結果は破棄し、直前に完了した深さの結果をそのまま使う。
        - self.max_depthを超えて深さを増やすことはしない（Noneなら事実上
          無制限で、時間切れになるまで深さを増やし続ける）。
        """
        deadline = time.monotonic() + self.time_budget
        max_depth = self.max_depth if self.max_depth is not None else float("inf")

        best_action = actions[0]
        best_rows = None
        completed_depth = 0
        depth = 1
        while depth <= max_depth:
            rows = []
            this_deadline = None if depth == 1 else deadline
            try:
                _, action_at_depth = minimax(
                    clone_game(game), player, player, depth,
                    float("-inf"), float("inf"), production_bots, self.candidate_k,
                    collect_rows=rows, weights=self.weights, deadline=this_deadline,
                )
            except _SearchTimeout:
                break

            if action_at_depth is not None:
                best_action = action_at_depth
            best_rows = rows
            completed_depth = depth

            if self.debug:
                print(f"[R{game.round_number} P{player}] iterative deepening: "
                      f"depth={depth} completed within budget, best={best_action}")

            if time.monotonic() >= deadline:
                break
            depth += 1

        if self.debug:
            depth_label = "∞" if max_depth == float("inf") else str(max_depth)
            print(f"[R{game.round_number} P{player}] time budget ({self.time_budget}s) "
                  f"exhausted at depth={completed_depth}/{depth_label}")

        if best_rows:
            best_rows.sort(key=lambda t: t[0], reverse=True)
            if self.rng is not None:
                chosen = self._select_best(game, player, best_rows)
                if chosen is not None:
                    return chosen

        return best_action if best_action is not None else actions[-1]

    def _select_best(self, game, player, rows):
        """rows: [(value, action), ...]（価値の降順ソート済み前提）から1つ選ぶ。
        self.rngがNoneなら単純に最良値。self.rngがあればHEURISTIC_WEIGHTS["jitter"]幅の
        ノイズを載せてから選び直す（choose()とdebug_choose_candidates()の両方から
        呼ばれる。選択ロジックを一箇所にまとめることで、呼び出し元による選択のズレを防ぐ）。

        2026-09-09追加: flourish_tiebreak_pref（3.20節#7）。SearchBotの評価値は
        深い探索の積み上げのため、HeuristicBotの1手先評価と違って完全な同値は
        実務上ほぼ起きない。そこで「ごく僅かな差（EPS）に収まる、実質的な同点」を
        HeuristicBot側の「完全な同点」と同じ扱いにする。EPSはjitter幅より
        はるかに小さく固定してあり、実際の評価差がある候補同士の順位を覆すことは
        ない（＝この特徴のバランス影響は無視できるほど小さい設計を維持する）。"""
        if not rows:
            return None
        flourish_w = self.weights.get("flourish_tiebreak_pref", 0.0)
        if flourish_w > 0 and len(rows) > 1:
            rows = _apply_flourish_tiebreak(game, player, rows)
        if self.rng is None:
            return rows[0][1]
        jitter = self.weights["jitter"]
        return max(rows, key=lambda t: t[0] + self.rng.uniform(-jitter, jitter))[1]

    def _score_candidates(self, game, player, actions, production_bots):
        """choose()と全く同じ1回のminimax呼び出しをcollect_rows付きで実行し、
        ルート直下の候補評価値一覧を返す。2026-08-12修正: 以前は候補ごとに
        独立でminimaxをやり直しており、兄弟間のalpha-beta枝刈りが効かず
        choose()の高速経路より大幅に遅かった（play_vs_ai.htmlで1手10秒以上に
        達する原因になっていた）。今はchoose()と同じ経路なので同じ速さで、
        かつ選ばれるactionも常にchoose()と一致する。"""
        rows = []
        _, best_action = minimax(
            clone_game(game), player, player, self.depth,
            float("-inf"), float("inf"), production_bots, self.candidate_k,
            collect_rows=rows, weights=self.weights,
        )
        rows.sort(key=lambda t: t[0], reverse=True)
        chosen = self._select_best(game, player, rows)
        return (chosen if chosen is not None else best_action), rows

    def debug_choose_candidates(self, game, player, actions):
        """選ばれたactionと、候補ごとの評価値を game.py の run() /
        HeuristicBot.choose_with_debug と同じ辞書形式（action/candidates/
        random_pick）で返す。play_vs_ai.html側の「AIの候補手ログを表示」機能が
        web_api.py経由でこれを呼ぶ。

        重要: 意図的に "choose_with_debug" という名前を避けている。
        game.py の run() は hasattr(bot, "choose_with_debug") で自動的に
        低速な候補ログ記録経路へ切り替える設計になっており、この名前で
        SearchBotに生やすと main.py・tune_balance_search.py の自己対戦が
        （candidate_kごとに独立にminimaxし直す分）大幅に遅くなってしまう
        （実測: 1局あたり数秒 → 15秒程度に悪化することを確認済み）。
        play_vs_ai.html専用の機能として明確に切り離すため、この名前にした。
        通常のchoose()より遅いのでデバッグ・UI表示用途に限定して使うこと。
        SearchBotはepsilonランダムを行わないため random_pick は常にFalse。"""
        if len(actions) == 1:
            return {"action": actions[0], "candidates": [{"action": actions[0], "value": 0.0}], "random_pick": False}
        production_bots = {0: self, 1: self}
        best_action, rows = self._score_candidates(game, player, actions, production_bots)
        best_action = best_action if best_action is not None else actions[-1]
        candidates = [{"action": a, "value": round(v, 2)} for v, a in rows[:8]]
        return {"action": best_action, "candidates": candidates, "random_pick": False}

    def choose_production(self, game, player, affordable_kinds):
        """9章TODO#9・0.36節: clone_game+フルevaluate_stateの繰り返しをやめ、
        差分計算（影響が及ぶ近傍だけを再評価）で候補を評価する高速版。
        「reserveに積むだけ」の分岐（1駒種につき1回しかevaluate_stateを
        呼ばない、軽い分岐）は従来通りclone_game+evaluate_stateのままにして
        ある（頻度・コストともに支配的ではないため、変更範囲とリスクを
        絞った）。重い方（実際に配置してマスごとに評価する分岐）だけを
        _score_place_candidates_fast() に置き換える。

        正しさの担保: 0.35節と同じ「OLD/NEW比較テスト」の方針に基づき、
        本実装は _choose_production_reference（旧: clone_game+フル
        evaluate_state版。削除せず保持）と実戦棋譜由来の盤面で突き合わせ、
        種類・マスごとの評価値が一致することを大量サンプルで検証済み
        （検証ハンドブック0.36節参照）。"""
        baseline = evaluate_state(game, player, weights=self.weights)
        best_kind, best_val = None, baseline
        jitter = self.weights["jitter"] if self.rng is not None else 0.0

        # game.winner確定済みの場合、evaluate_stateは常に同じ±1e6を返すため
        # 差分計算に入らず「生産しない」を早期確定する（旧実装と同じ挙動）
        if game.winner is not None:
            return None

        ctx = None  # 生産候補の評価に必要な「盤面全体の下ごしらえ」。遅延生成。

        for kind in affordable_kinds:
            cost = CONFIG["pieces"][kind]["produce_cost"]
            place_cost = CONFIG["pieces"][kind]["cost"]
            rp_after_produce = game.econ.rp[player] - cost

            if rp_after_produce < place_cost:
                # 今すぐ置けないなら、reserveに置いた場合の簡易評価に留める
                # （1回のみのevaluate_state呼び出しなので従来通りclone_gameで良い）。
                child = clone_game(game)
                child.econ.rp[player] = rp_after_produce
                child.reserve[player].append(kind)
                val = evaluate_state(child, player, weights=self.weights)
            else:
                if ctx is None:
                    ctx = _ProductionEvalContext(game, player, weights=self.weights)
                positions = self._pruned_production_positions(game, player, kind)
                val = ctx.best_value_for_placements(kind, positions, rp_after_produce - place_cost)
                if val is None:
                    val = baseline

            if self.rng is not None:
                val += self.rng.uniform(-jitter, jitter)

            if val > best_val:
                best_val, best_kind = val, kind

        return best_kind

    def _choose_production_reference(self, game, player, affordable_kinds):
        """choose_production() の旧実装（clone_game+フルevaluate_state版）。
        差分計算版（0.36節）の正しさを検証するための基準実装として残して
        ある。本番の探索経路からは呼ばれない（削除しないのは、将来
        evaluate_state側の評価式を変更した際に、差分計算側(_ProductionEvalContext)
        への反映漏れを同じ盤面での突き合わせテストで即座に検出できるようにする
        ため。0.27節・0.35節で繰り返し発生した「複数箇所に実装が分散していて
        反映漏れが起きる」事故の再発防止と同じ考え方）。"""
        baseline = evaluate_state(game, player, weights=self.weights)
        best_kind, best_val = None, baseline
        jitter = self.weights["jitter"] if self.rng is not None else 0.0

        for kind in affordable_kinds:
            cost = CONFIG["pieces"][kind]["produce_cost"]
            place_cost = CONFIG["pieces"][kind]["cost"]
            child = clone_game(game)
            child.econ.rp[player] -= cost

            if child.econ.rp[player] < place_cost:
                child.reserve[player].append(kind)
                val = evaluate_state(child, player, weights=self.weights)
            else:
                positions = self._pruned_production_positions(game, player, kind)
                best_pos_val = None
                for pos in positions:
                    grandchild = clone_game(child)
                    cfg = CONFIG["pieces"][kind]
                    grandchild.board.grid[pos] = Piece(kind=kind, owner=player,
                                                        hp=cfg["hp"], max_hp=cfg["hp"], atk=cfg["atk"],
                                                        movable=True)
                    grandchild.econ.rp[player] -= place_cost
                    v = evaluate_state(grandchild, player, weights=self.weights)
                    if best_pos_val is None or v > best_pos_val:
                        best_pos_val = v
                val = best_pos_val if best_pos_val is not None else baseline

            if self.rng is not None:
                val += self.rng.uniform(-jitter, jitter)

            if val > best_val:
                best_val, best_kind = val, kind

        return best_kind

    def _pruned_production_positions(self, game, player, kind):
        """_candidate_positions_for_production() が返す候補マスを、軽量な
        事前スコアリングで上位 production_candidate_k 件に絞り込む
        （2026-08-13追加。9章TODO#9・0.29節/4.24節の対応）。

        事前スコアには _position_hold_value を使う。この関数は clone_game も
        盤面への実配置も行わず、「もし今このマスにこの駒を置いたら」という
        仮定のもとで support/danger/資源orVPスポット占有/攻撃見込みだけを
        見積もる（board.grid にposのエントリが無くても動作する設計になって
        いるため、そのまま使い回せる）。単体呼び出しのコストはおおよそO(盤上駒数)で、
        本評価（clone_game + evaluate_state、実質O(盤上駒数^2)）よりずっと軽い。

        候補数が production_candidate_k 以下なら従来通り全件そのまま返す
        （プルーニングによる挙動変化を、絞り込みが実際に発生する局面だけに限定するため）。
        """
        all_positions = self._candidate_positions_for_production(game, player)
        k = self.production_candidate_k
        if k is None or len(all_positions) <= k:
            return all_positions

        # 2026-09-07最適化（3.18節）: 同上のキャッシュ済みset群を使う。
        vp_stars, vp_tengen, vp_spot_positions, rp_pts, midgame_pts = _spot_position_sets()
        bp_mult = _base_pressure_ramp_multiplier(game.round_number, self.weights["base_pressure_ramp_rounds"], self.weights["rush_opening_pressure"])

        prelim = [
            (_position_hold_value(
                game.board, pos, kind, player, self.weights,
                vp_stars, vp_tengen, rp_pts, vp_spot_positions,
                count_vp_spot_bonus=True, midgame_pts=midgame_pts,
                base_pressure_multiplier=bp_mult,
             ), pos)
            for pos in all_positions
        ]
        prelim.sort(key=lambda t: t[0], reverse=True)
        return [pos for _, pos in prelim[:k]]

    def _candidate_positions_for_production(self, game, player):
        """全マスではなく、意味がありそうな場所だけに絞る（速度対策）。
        2026-08-07: midgame_spots(隅の中盤解禁スポット)を候補に追加。これが無いと、
        新規生産した駒を隅に置く選択肢自体がSearchBotの探索から漏れてしまう。"""
        # 2026-09-07最適化（3.18節）: 同上のキャッシュ済みset群を使う。
        _vp_stars, _vp_tengen, vp, eco, midgame = _spot_position_sets()
        my_base = CONFIG["base_positions"][player]
        threats = [p for p in game.board.adjacent_positions(my_base)
                if p in game.board.grid and game.board.grid[p].owner != player]
        candidates = vp | eco | midgame
        for t in threats:
            candidates |= set(game.board.adjacent_positions(t))
        return [p for p in candidates if p not in game.board.grid]


# ============================================================
# 8. 動作確認の入り口（まずは小規模から）
# ============================================================
if __name__ == "__main__":
    from heuristic_bot import run_matchup

    print("### SearchBot(depth=1) vs HeuristicBot: まず10戦で疎通確認 ###")
    # ここは検証の基本値(depth3)ではなくあえてdepth=1固定（疎通確認のみが目的の
    # 最速経路。実際の強さ検証にはdepth3で他のコマンドを使うこと）。
    run_matchup(lambda: SearchBot(depth=1, candidate_k=999, debug=True), HeuristicBot,
                n_games=10, label="SearchBot(先手) vs Heuristic(後手) [動作確認]")

    # TODO: n_games/depth/candidate_kを調整して勝率・実行時間のバランスを検証












