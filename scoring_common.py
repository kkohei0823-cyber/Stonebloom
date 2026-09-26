"""
scoring_common.py
==========================================================
heuristic_bot.py / search_bot_skeleton.py の両方から使われる、
self に依存しない低レベル評価関数群をまとめたモジュール。

2026-08-22 新設（検証ハンドブック 3.4節・0.27節、9章-10 対応）。
方針は 0.27 節で合意済みだったが実装は未着手だった:
  「HeuristicBot クラス自体は削除しない。ただし search_bot_skeleton.py が
  依存する低レベル関数群（_potential_attack 等）は独立モジュールへ切り出す」

対象は以下の8関数（いずれも heuristic_bot.py 側で `self` を使わない
モジュール関数だったもの。ハンドブックの記述は6関数
+ 2026-08-13追加の_ranged_enemy_pieces で7関数としていたが、
実際には search_bot_skeleton.py が import している
_own_base_threat / _hypothetical_own_base_threat も同種の
自己完結した関数のため、あわせてこちらに移設した）:

  - _new_placement_threat
  - _existing_incoming_damage
  - _ranged_enemy_pieces
  - _potential_incoming_damage
  - _own_base_threat
  - _hypothetical_own_base_threat
  - _potential_attack
  - _neighbors_count
  - _position_hold_value

移設に伴い、呼び出し元の変更は以下の通り:
  - heuristic_bot.py: 本体からこれらの定義を削除し、
    `from scoring_common import (...)` で再importする（HeuristicBotクラス側の
    呼び出しコードは無変更で動くようにするため）。
  - search_bot_skeleton.py: `from heuristic_bot import (...)` を
    `from scoring_common import (...)` に変更。
  - test_invariants.py / test_invariants.js が存在する場合、そちらの
    import文も同様に scoring_common 経由に変更が必要（本セッションでは
    test_invariants.py 自体が引き継ぎ資料に含まれていなかったため未対応。
    次回、ファイルを受け取り次第 import 元を確認・修正すること）。

ロジック自体は heuristic_bot.py から一切変更していない（コピーのみ）。
"""

from collections import Counter
from functools import lru_cache

from config import CONFIG, config_version
from game import Piece, type_multiplier, is_damage_nullified


# ============================================================
# CONFIG由来のスポット座標setのキャッシュ（AI思考速度対応。3.18節参照）
# ============================================================
# 2026-09-07追加: evaluate_state（探索リーフの最内周、1局あたり数万回呼ばれる
# ホットパス）や_score_actions・_top_k_candidates・_ProductionEvalContext.__init__・
# _pruned_production_positions・_candidate_positions_for_productionは、いずれも
# 呼ばれるたびに vp_stars/vp_tengen/rp_pts/midgame_pts という4つのsetを
# `set(CONFIG[...])`で毎回新規に作り直していた。これらの座標はCONFIGの
# reset_config()/apply_config_overrides()（＝Optunaのtrial切り替え時）以外では
# 変化しないにもかかわらず、探索中の全ノード・全リーフで無条件に再構築されており、
# プロファイラ（cProfile）で計測したところ実測1局あたりのevaluate_state呼び出し
# だけで数万回のset構築が発生していた。config.pyのconfig_version()（reset_config系
# 関数が呼ばれた回数）をキャッシュキーにすることで、trialが変わらない限り一度
# 計算したsetをそのまま使い回せるようにする（探索の判断・評価値には一切影響しない、
# 純粋なメモ化による高速化）。
_spot_position_sets_cache = {"version": None, "sets": None}


def _spot_position_sets():
    """(vp_stars, vp_tengen, vp_spot_positions, rp_pts, midgame_pts) を返す。
    CONFIGの世代が変わらない限り初回計算結果をそのまま返す（frozensetなので
    呼び出し側が誤って書き換えても他の呼び出し元に影響しない）。"""
    version = config_version()
    if _spot_position_sets_cache["version"] != version:
        vp_stars = frozenset(CONFIG["vp_spots"]["stars"])
        vp_tengen = frozenset(CONFIG["vp_spots"]["tengen"])
        vp_spot_positions = vp_stars | vp_tengen
        rp_pts = frozenset(CONFIG["rp_spots"]["points"])
        midgame_pts = frozenset(CONFIG["midgame_spots"]["points"])
        _spot_position_sets_cache["version"] = version
        _spot_position_sets_cache["sets"] = (vp_stars, vp_tengen, vp_spot_positions, rp_pts, midgame_pts)
    return _spot_position_sets_cache["sets"]


@lru_cache(maxsize=None)
def _manhattan_radius_offsets(radius):
    """マンハッタン距離が1以上radius以下となる(dx,dy)オフセット一覧
    （中心自身(0,0)は含まない）。2026-08-24追加（9章TODO#9:
    choose_production()の「盤面密度に応じて最悪ケースで68秒かかる」
    バグの根本対応の一部）。

    radiusの実質的な種類数は少ない（弓兵等のreach値・choose_productionの
    差分計算半径の組み合わせ程度）ため、game.pyの_adjacent_positions_cached
    と同じ考え方でmaxsize=Noneのlru_cacheをプロセス全体・全対局を通じて
    使い回してよい。"""
    offsets = []
    for dx in range(-radius, radius + 1):
        remaining = radius - abs(dx)
        for dy in range(-remaining, remaining + 1):
            if dx == 0 and dy == 0:
                continue
            offsets.append((dx, dy))
    return offsets


@lru_cache(maxsize=None)
def _positions_within_manhattan_radius(board_size, pos, radius):
    """(board_size, pos, radius)の組だけで一意に決まる、盤内に収まる
    マンハッタン距離radius以内（中心posは含まない）の座標一覧。
    2026-08-24追加。

    【背景・9章TODO#9「choose_production()が盤面密度に応じて最悪ケースで
    68秒かかる」バグの根本対応】
    _existing_incoming_damage / _potential_incoming_damage の射程駒（弓兵等）
    に対する「呼吸点分割の母数（同じ敵を他に何体が狙っているか）」の計算が、
    従来 `[p for p, pc in board.grid.items() if ... board.distance(src_pos, p)
    <= reach ...]` という形でboard.grid全体を毎回フルスキャンしていた。
    半径で対象を絞り込む条件自体は正しかったが、スキャン対象そのものが
    盤上の全駒だったため、ループのコストはO(盤上駒数)のままだった。
    search_bot_skeleton.py側でchoose_production()の候補評価を近傍だけ再評価
    する差分計算（0.36節）に切り替えた後も、この内側の呼吸点分割計算だけは
    全駒スキャンのまま残っており、実測プロファイルで「最悪ケース68秒」
    （0.35節/0.36節）の残存する主要因になっていたことが今回判明した。

    本関数は「reach以内に存在しうる座標」を事前に列挙し、各座標を
    board.grid.get()でO(1)参照する方式に切り替えるためのもの。ダイヤモンドの
    マス数は盤面サイズと半径にのみ依存する定数で、盤上に何個駒があっても
    不変であるため、この置き換えにより「盤面密度に応じて重くなる」構造的な
    問題を解消できる。choose_production()の差分計算（search_bot_skeleton.py
    _positions_within_radius_cached、旧実装）と全く同じ発想のため、実装を
    二重管理しないようこちら（下位モジュール）に一本化し、
    search_bot_skeleton.py側はこの関数をimportして使う。"""
    x, y = pos
    result = []
    for dx, dy in _manhattan_radius_offsets(radius):
        nx, ny = x + dx, y + dy
        if 0 <= nx < board_size and 0 <= ny < board_size:
            result.append((nx, ny))
    return result


def _new_placement_threat(game, board, pos, kind, player):
    """次の相手ターンの新規配置によって、この駒が脅かされるリスクを見積もる。

    2026-08-06 修正: exposure_factor が min(空き隣接マス数,4)/4.0 という、
    常に4マスを基準にした割合になっていた。盤端・角のマスは物理的に隣接
    マス数が2〜3しかないため、実際の危険度に関係なく「空きマスが少ない」
    という理由だけで exposure が過小評価され、盤端（＝敵本拠付近もここに
    該当する）が不自然に安全に見えるバイアスが生まれていた。そのマス自身が
    持ちうる隣接マス数を分母にして正規化する。"""
    opp = 1 - player
    empty_adjacent = [p for p in board.adjacent_positions(pos) if p not in board.grid]
    if not empty_adjacent:
        return 0.0
    opp_rp = game.econ.rp[opp]
    affordable = [k for k in game.reserve[opp] if CONFIG["pieces"][k]["cost"] <= opp_rp]
    if not affordable:
        return 0.0
    best_atk = max(CONFIG["pieces"][k]["atk"] for k in affordable)
    my_hp = CONFIG["pieces"][kind]["hp"]
    relative = min(best_atk / my_hp, 1.0)
    max_neighbors = len(board.adjacent_positions(pos))
    exposure_factor = len(empty_adjacent) / max_neighbors if max_neighbors else 0.0
    return relative * best_atk * exposure_factor


def _existing_incoming_damage(board, target_pos, player, exclude_positions=(), ranged_friends=None):
    """既に盤上にいる自軍の駒(exclude_positionsを除く)が、target_posの敵に対して
    今のラウンドで既に与えている合計ダメージを、game.resolve_combat と同じ
    「呼吸点方式」で計算して返す（2026-08-07追加）。

    経緯: _potential_attack はこれまで「今評価している1体だけ」の与ダメージで
    キル判定(kill_targets)をしており、複数の味方が同じ敵に同時攻撃している
    （合算すれば倒せる）ケースを一切考慮していなかった。実際の game.py の
    resolve_combat は複数駒の与ダメージを同じターゲットに対して加算するため、
    「2対1で挟んでいるのに、片方だけでは倒せないので"攻撃"として評価されず、
    その駒が離脱してもスコア上ほとんど損失に見えない」というズレが生じていた
    （2vs1の有利対面をAIが放棄する一因）。

    2026-08-08修正: 駒種相性（三すくみ）を反映。resolve_combat と同じ順序
    （①呼吸点分割 -> ②対象ごとの相性倍率）で計算しないと、三すくみ導入後は
    ここで見積もる被ダメージと実際の被ダメージがズレ、AIのキル判定が不正確になる。

    2026-08-12修正: 工兵の弓兵ダメージ無効化（0.32節）を反映。resolve_combat と同じく、
    is_damage_nullified()で無効化される対象は piece_targets（呼吸点方式の分割母数）
    から除外する。target_pos自身が無効化対象なら piece_targets に含まれなくなり、
    直後の「target_pos not in piece_targets」判定で自然に0扱いになる。

    2026-08-13修正（0.29節/4.24節対応、O(駒数^2)解消）: _potential_incoming_damageと
    同じ理由・同じ要領で、近接の自軍駒は board.grid.items() の全件スキャンではなく
    board.adjacent_positions(target_pos) からO(1)で直接判定する。射程駒だけは
    ranged_friends（_ranged_enemy_pieces()の結果。呼び出し元で「player側の射程駒
    一覧」として1回だけ作って使い回す想定）を受け取る。Noneならその場で計算するため
    後方互換。
    """
    total = 0.0
    target_piece = board.grid.get(target_pos)
    target_kind = target_piece.kind if target_piece is not None else None

    # --- 近接: target_posの隣接マスだけをO(1)で見る ---
    for src_pos in board.adjacent_positions(target_pos):
        if src_pos in exclude_positions:
            continue
        piece = board.grid.get(src_pos)
        if piece is None or piece.owner != player:
            continue
        cfg = CONFIG["pieces"][piece.kind]
        if cfg["ranged"] or cfg["atk"] <= 0:
            continue
        piece_targets = [p for p in board.adjacent_positions(src_pos)
                          if p in board.grid and board.grid[p].owner != player
                          and not is_damage_nullified(piece.kind, board.grid[p].kind)]
        if target_pos not in piece_targets:
            continue
        dmg_each = cfg["atk"] / len(piece_targets)
        total += dmg_each * type_multiplier(piece.kind, target_kind)

    # --- 射程: 事前計算済みリストを使い回す ---
    if ranged_friends is None:
        ranged_friends = _ranged_enemy_pieces(board, player)

    for src_pos, piece, cfg in ranged_friends:
        if src_pos in exclude_positions:
            continue
        reach = 1 + cfg["range"]
        # 全件スキャン+距離足切り(O(盤上駒数))ではなく、reach以内座標を事前列挙し
        # O(1)参照する方式（O(reach²)、盤面密度非依存）
        piece_targets = [p for p in _positions_within_manhattan_radius(board.size, src_pos, reach)
                          if p in board.grid and board.grid[p].owner != player
                          and not is_damage_nullified(piece.kind, board.grid[p].kind)]
        if target_pos not in piece_targets:
            continue
        dmg_each = cfg["atk"] / len(piece_targets)
        total += dmg_each * type_multiplier(piece.kind, target_kind)

    return total


def _ranged_enemy_pieces(board, owner):
    """board上でownerが所有する「射程持ち」駒のリストを [(pos, piece, cfg), ...] で返す
    （2026-08-13追加。0.29節/4.24節対応、O(駒数^2)解消の一環）。

    _potential_incoming_damage の従来実装は、呼ばれるたびに board.grid.items() を
    フルスキャンして「射程内の敵」を探していた。近接駒は本来 board.adjacent_positions(pos)
    でO(1)に判定できるにもかかわらず、この一括スキャンに巻き込まれて毎回全駒と
    比較されていたのが主なムダ。evaluate_state 側で1回だけこのリストを作り、
    盤上の全駒についてのforループ全体で使い回すことで、「敵の射程駒だけ」を
    対象にした比較に絞り込む（近接駒同士の判定はこの関数を経由せず、
    呼び出し側でboard.adjacent_positionsを直接使うようにした）。
    """
    result = []
    for pos, piece in board.grid.items():
        if piece.owner != owner:
            continue
        cfg = CONFIG["pieces"][piece.kind]
        if cfg["atk"] <= 0 or not cfg["ranged"]:
            continue
        result.append((pos, piece, cfg))
    return result


def _potential_incoming_damage(board, pos, kind, player, exclude=None, ranged_enemies=None):
    """
    そのマスに立った場合、次の戦闘解決でこの駒が敵から受けるダメージ総量を返す
    （2026-08-09追加）。

    経緯: これまで _potential_attack で「与ダメージ1につき加点」(attack重み)を
    評価していたのに対し、対になるはずの「被ダメージ1につき減点」が一切
    存在しなかった。三すくみ導入前は駒種を問わずATK/HPの比があまり極端で
    なかったため実害が目立たなかったが、三すくみ導入後は「歩兵に騎兵を
    ぶつける」（騎兵→歩兵は無印1.0倍だが、歩兵→騎兵は2.0倍で返ってくる、
    という非対称な不利交換）のような、与ダメージだけ見れば一見prettyだが
    実際には損な突撃をボットが選んでしまう挙動が確認された。

    game.resolve_combat と同じ「呼吸点分割 → 相性倍率」の順序で、このマスに
    隣接（弓兵の場合は射程内）する敵ユニットそれぞれの攻撃力を、その敵から
    見た「今回の全ターゲット数」で割ってから、相性倍率(type_multiplier(敵の種類, kind))
    を掛けて合算する。exclude は移動の場合の移動元座標（敵の他ターゲット判定
    から、移動によって空になるこのマスを除外するため）。

    2026-08-12修正: 工兵の弓兵ダメージ無効化（0.32節）を反映。is_damage_nullified()で
    このマスに立つ駒(kind)が該当する敵駒(piece.kind)からの無効化対象と判定される
    場合は、そもそもその敵駒からの被ダメージ計算自体をスキップする（距離を問わず
    常に無効化、という仕様のため in_range 判定より前に早期リターンする）。
    また、他の実際の被害対象(enemy_targets)についても同様の無効化対象を分割母数
    から除外し、resolve_combat と矛盾しないようにする。

    2026-08-13修正（0.29節/4.24節対応、O(駒数^2)解消）: 従来は board.grid.items() を
    フルスキャンして敵駒を1つずつ判定していたが、近接駒はposに隣接しているか
    どうかしか見ないため、board.adjacent_positions(pos) からO(1)で直接判定できる。
    射程駒（弓兵等）だけは全駒スキャンが必要なので、そちらは呼び出し側から
    ranged_enemies（_ranged_enemy_pieces()の結果、evaluate_state側で1回だけ作って
    使い回す想定）を受け取る。ranged_enemies=None の場合は従来通りその場で
    計算するため、他の呼び出し元（choose_production等）は変更なしでそのまま動く。
    """
    enemy = 1 - player
    total = 0.0

    # --- 近接駒: 全駒スキャンではなく、隣接マスだけをO(1)で見る ---
    for src_pos in board.adjacent_positions(pos):
        piece = board.grid.get(src_pos)
        if piece is None or piece.owner != enemy:
            continue
        cfg = CONFIG["pieces"][piece.kind]
        if cfg["ranged"] or cfg["atk"] <= 0:
            continue  # 射程駒は下のranged_enemiesループ側でまとめて扱う
        if is_damage_nullified(piece.kind, kind):
            continue
        enemy_targets = [p for p in board.adjacent_positions(src_pos)
                          if p in board.grid and board.grid[p].owner == player and p != exclude
                          and not is_damage_nullified(piece.kind, board.grid[p].kind)]
        if pos not in enemy_targets:
            enemy_targets.append(pos)
        dmg_each = cfg["atk"] / len(enemy_targets)
        total += dmg_each * type_multiplier(piece.kind, kind)

    # --- 射程駒: 全駒スキャンが必要なので、事前計算済みリストを使い回す ---
    if ranged_enemies is None:
        ranged_enemies = _ranged_enemy_pieces(board, enemy)

    for src_pos, piece, cfg in ranged_enemies:
        atk = cfg["atk"]
        if is_damage_nullified(piece.kind, kind):
            continue
        reach = 1 + cfg["range"]
        if board.distance(src_pos, pos) > reach:
            continue
        # 2026-08-24修正（同上）: board.grid.items()全件スキャンをやめ、
        # reach以内の座標だけを事前列挙してO(1)参照する。
        enemy_targets = [p for p in _positions_within_manhattan_radius(board.size, src_pos, reach)
                          if p in board.grid and board.grid[p].owner == player and p != exclude
                          and not is_damage_nullified(piece.kind, board.grid[p].kind)]
        if pos not in enemy_targets:
            enemy_targets.append(pos)
        dmg_each = atk / len(enemy_targets)
        total += dmg_each * type_multiplier(piece.kind, kind)

    return total


def _estimate_next_round_income(game, player):
    """次ラウンドにplayerへ入るRP収入の名目見積もり（RP上限による切り捨て前）。

    2026-08-28新設: heuristic_bot.choose_production()のoverflow_if_skip見積もりが
    base_income固定1のみで、rp_spot/midgame_spot保有分（工兵ボーナス込み）を
    完全に無視していたため実収入より大幅に過小評価されていた問題への対応として
    導入した。

    2026-08-30時点の注記: 一時的にrp_hoarding_pref/rp_overflow_avoidance削除に
    伴い未使用になっていたが、同日中の再改修でchoose_production()の「配置待ちの
    駒を置くためのRPを、来期収入込みでどこまであてにするか」(income_confidence_ratio)
    の判断に再び使うようになった。

    Economy.compute_income()/_accrue_spot_income()（game.py）とほぼ同じ判定条件を
    使うが、あくまで「今の盤面のまま何も変わらなかったら」という1手先だけの
    簡易見積もりであり、以下は意図的に省略している:
      - 今回のターンでこれから起きる移動・撃破・スポット争奪（1手前の時点の
        判断に使う値なので、それらの結果までは織り込まない）
      - RP上限自体（ここでの返り値は名目収入であり、上限適用は呼び出し側で行う）
    """
    cfg = CONFIG["economy"]
    econ = game.econ
    board = game.board
    next_round = game.round_number + 1
    total = cfg["base_income"]
    for spot_cfg in (CONFIG["rp_spots"], CONFIG["midgame_spots"]):
        start_round = spot_cfg.get("start_round", 0)
        if next_round < start_round:
            continue
        for pos in spot_cfg["points"]:
            piece = board.grid.get(pos)
            if piece is None or piece.owner != player:
                continue
            owned_since = econ.spot_owned_since.get(pos, game.round_number)
            # 次ラウンド時点で「保有継続ラウンド数」が1つ進む前提で判定する
            held_next_round = next_round - owned_since
            if held_next_round < spot_cfg["capture_income_delay"]:
                continue
            base = spot_cfg["income"]
            if piece.kind == "工兵":
                base += cfg["engineer_bonus"]
            total += base
    return total


def _hypothetical_next_round_income_margin(game, player, new_pos=None, new_kind=None, exclude_pos=None):
    """1手先ヒューリスティック（HeuristicBot._score_actions）専用: 「この配置/移動を
    実行した後」の (自分の来期RP収入見積もり - 相手の来期RP収入見積もり) を返す
    （2026-09-04追加）。

    _hypothetical_own_base_threat と全く同じ理由・同じ挿入/削除パターンを使う：
    _score_actionsは「まだ実行していない仮の1手」を評価する関数であり、board.gridを
    実際には書き換えていない。しかし_estimate_next_round_income()はboard.gridを
    直接見てRP/中盤解禁スポットの保有駒を判定するため、この効果を正しく評価するには
    一時的にboard.gridへ仮の駒を追加（または移動元を削除）してから計算し、直後に
    必ず元に戻す必要がある。

    経緯: RPスポットを確保せずに敵本拠へ特攻する手は、初手時点ではdanger=0・
    incoming=0でリスクが見えず、advance/base_pressure/efficiencyの加点だけが
    確定で入ってしまう（企画書9.1節）。一方で、その手が「RPスポットを空ける」
    「RPスポットへ向かわない」ことによる中長期的な経済的不利（＝相手にRP収入で
    上回られて息切れする）は、1手先読みのHeuristicBotの視野には本来入らない。
    本関数は_estimate_next_round_income()（既存だが従来は呼び出し元がなかった
    関数）を使い、「この候補手を実行した結果、来期のRP収入で自分と相手のどちらが
    有利になるか」を1手先の評価にその場で織り込むことで、数手先を読まなくても
    息切れリスクを評価値に反映できるようにする。

    注意（近似の限界）: _estimate_next_round_income内部のcapture_income_delay判定は
    econ.spot_owned_since（実際に保有し始めたラウンド）を参照するが、本関数は
    board.gridだけを一時的に書き換え、econ.spot_owned_sinceは更新しない。そのため
    「このマスに今置いたばかりなのに、あたかも既に保有継続していたかのように」
    収入を見積もってしまう場合がある（＝新規確保の効果をやや過大評価する近似）。
    1手先ヒューリスティックの候補手同士を相対比較する用途では実用上問題ないと
    判断しているが、絶対値としての精度を求める用途には使わないこと。
    """
    board = game.board
    inserted_pos = None
    removed_piece = None
    removed_pos = None
    try:
        if exclude_pos is not None and exclude_pos in board.grid:
            removed_piece = board.grid.pop(exclude_pos)
            removed_pos = exclude_pos
        if new_pos is not None and new_pos not in board.grid:
            cfg = CONFIG["pieces"][new_kind]
            board.grid[new_pos] = Piece(kind=new_kind, owner=player, hp=cfg["hp"], max_hp=cfg["hp"],
                                         atk=cfg["atk"], movable=True, uid=-1)
            inserted_pos = new_pos
        opp = 1 - player
        return _estimate_next_round_income(game, player) - _estimate_next_round_income(game, opp)
    finally:
        if inserted_pos is not None:
            del board.grid[inserted_pos]
        if removed_pos is not None:
            board.grid[removed_pos] = removed_piece


def _own_base_threat(board, player, ranged_enemies=None):
    """自陣本拠が、現在の盤面のまま次の戦闘解決を迎えた場合に受けるであろう
    被ダメージ見積もりを返す（2026-08-14追加）。

    base_pressure（敵本拠への攻撃機会を評価する重み）と対になる、防御側の指標。
    _potential_incoming_damage は「そのマスに kind の駒が立っていたら」という
    仮想の被弾見積もりを計算する汎用関数だが、本拠の位置(CONFIG["base_positions"])
    と本拠自身のkind("本拠")を渡すだけで、既存の呼吸点分割・三すくみ・ダメージ
    無効化のロジックをそのまま流用して「本拠への見込み被害」を計算できる
    （本拠はCONFIG["pieces"]["本拠"]にhp/atk/move等が定義済みのため、この関数が
    要求するcfg参照は問題なく成立する）。

    味方の駒が、本拠を狙っている敵と同じ相手の隣接/射程内に入ると、呼吸点分割に
    よりその敵の攻撃力が複数ターゲットに分散され、結果として本拠への被害が薄まる
    （＝身代わり・迎撃の効果が自然に反映される）。この関数は与えられたboardの
    状態をそのまま見るだけなので、探索木の各ノード（clone_gameされた実際の盤面）
    で呼べば、その時点の駒配置を正しく反映した値になる。
    """
    my_base_pos = CONFIG["base_positions"][player]
    return _potential_incoming_damage(board, my_base_pos, "本拠", player, ranged_enemies=ranged_enemies)


def _hypothetical_own_base_threat(board, player, new_pos=None, new_kind=None, exclude_pos=None):
    """1手先ヒューリスティック（_score_actions）専用: 「この配置/移動を実行した後」の
    自陣本拠への被ダメージ見積もりを計算する（2026-08-14追加）。

    evaluate_state（探索の葉ノード）は実際にclone_game+simulate_actionされた後の
    本物の盤面を見るため_own_base_threat()をそのまま呼べばよいが、_score_actionsは
    「まだ実行していない仮の1手」を評価する関数であり、盤面(board.grid)を実際には
    書き換えていない。しかし本拠への脅威は、呼吸点分割の性質上「その敵を狙う味方が
    何体いるか」に依存するため（自分の駒を敵の隣接/射程内に割り込ませると、敵の
    攻撃力が複数ターゲットに分散され本拠への被害が薄まる＝迎撃・身代わりの効果）、
    その効果を正しく評価するには一時的に board.grid へ仮の駒を追加してから計算し、
    直後に必ず元に戻す必要がある。board.gridは単純なdictなので、追加/削除のコストは
    無視できるほど小さい。

    new_pos/new_kind: place/moveの結果、この位置にこの駒種の味方が来る場合に指定する。
    exclude_pos: moveの場合の移動元（そこにいた駒はもういないものとして扱う）。
    """
    inserted_pos = None
    removed_piece = None
    removed_pos = None
    try:
        if exclude_pos is not None and exclude_pos in board.grid:
            removed_piece = board.grid.pop(exclude_pos)
            removed_pos = exclude_pos
        if new_pos is not None and new_pos not in board.grid:
            cfg = CONFIG["pieces"][new_kind]
            board.grid[new_pos] = Piece(kind=new_kind, owner=player, hp=cfg["hp"], max_hp=cfg["hp"],
                                         atk=cfg["atk"], movable=True, uid=-1)
            inserted_pos = new_pos
        return _own_base_threat(board, player)
    finally:
        if inserted_pos is not None:
            del board.grid[inserted_pos]
        if removed_pos is not None:
            board.grid[removed_pos] = removed_piece


# base_hp_panic_threshold（2026-09-02にBASE_WEIGHTSへ昇格。導入は0.86節）の実装。
# 詳細な設計背景・経緯は検証ハンドブック0.86節を参照（コード側のコメント肥大化を
# 避けるため、経緯説明はハンドブックへ集約し、ここには要点のみ残す）。
# 要点: 本拠HPが割合BASE_HP_PANIC_ZONE_RATIOを下回ったら、base_defense項に
# 掛かる倍率を(1 + severity * weight)まで引き上げる。危険水域の外・weight<=0の
# 間は常に1.0（無変化）で完全に後方互換。
#
# 2026-09-02(2)修正: 発火ラインを0.5→0.8に引き上げた。本拠HP=600・騎兵ATK=48が
# 3体同時に本拠へ隣接するような本拠速攻に対しては、0.5ラインだと発火が
# ラウンド3（本拠HP比28%、あと1〜2ラウンドで陥落）までずれ込み、事実上手遅れ
# だった（実測：この時点からではweightを17〜20まで振り切っても勝率が改善しない
# チャンピオンが実在した）。さらにtune_balance_search.py実運用のdepth=2探索は
# 2ラウンド先までしか読まないため、0.5ラインの危険水域はその探索の地平線
# （honrizon）にすら入らず、минimaxの葉評価でも一切増幅されていなかった。
# 0.8ラインなら被弾1発目（ラウンド1、HP比76%）から発火し、depth=2の葉評価にも
# 収まるようになる。0.8という値自体は「本拠速攻に対して手遅れにならない」という
# 目的に対する現実的な下限であり、これより下げるとdepth=2の探索地平線から
# 外れて同じ問題が再発するおそれがある。
BASE_HP_PANIC_ZONE_RATIO = 0.8


def _base_hp_panic_multiplier(board, player, weight):
    """本拠HPが危険水域（現在HP/最大HP < BASE_HP_PANIC_ZONE_RATIO）に入っている場合、
    base_defense項に掛け合わせる倍率を返す（危険水域の外・本拠破壊済み・weight<=0
    ならいずれも1.0＝無変化）。設計の経緯はハンドブック0.86節参照。"""
    if weight <= 0:
        return 1.0
    base_pos = CONFIG["base_positions"][player]
    piece = board.grid.get(base_pos)
    if piece is None or piece.kind != "本拠" or piece.hp <= 0:
        return 1.0
    max_hp = piece.max_hp if piece.max_hp else CONFIG["pieces"]["本拠"]["hp"]
    if max_hp <= 0:
        return 1.0
    hp_ratio = piece.hp / max_hp
    if hp_ratio >= BASE_HP_PANIC_ZONE_RATIO:
        return 1.0
    severity = (BASE_HP_PANIC_ZONE_RATIO - hp_ratio) / BASE_HP_PANIC_ZONE_RATIO
    return 1.0 + severity * weight


def _base_pressure_ramp_multiplier(round_number, ramp_rounds, rush_opening_pressure=0.0):
    """base_pressure（本拠への攻撃評価）に掛ける、ラウンド数に応じた立ち上がり倍率
    （2026-09-04追加）。

    背景: base_dmg（本拠へのダメージ）はattackとは既に別の重み(base_pressure)に
    分離されているが、この重み自体は「ラウンド1の丸裸の本拠を攻める」ケースにも
    「ラウンド20で消耗した本拠にとどめを刺す」ケースにも同じ強さで効いてしまう。
    前者はdanger=0・incoming=0でノーリスクなため常に高得点になりがちで淘汰したい
    行動、後者は本来推奨したい行動であり、1つの定数重みでは区別できない
    （企画書9.1節・9.1節フォローアップ参照）。rp_income_marginのような加点で
    対抗する対症療法は、advance/base_pressure/efficiency/kill_bonus等の合算に
    対して桁が小さすぎて実測で機能しなかった（同フォローアップの実測ログ参照）。

    そこで対症療法を追加するのではなく、base_pressureそのものの実効値を
    ラウンド数に応じて0からramp_roundsラウンドかけて線形に立ち上げる。
    ramp_rounds<=0なら常に1.0（無効・後方互換）。round_number>=ramp_roundsに
    達したら1.0（通常のbase_pressureがそのまま効く）。

    rush_opening_pressure: 2026-09-04追加（Tier4）。rush_killというアーキタイプ
    自体を選択肢から完全に消すのではなく、「意図的に序盤の本拠特攻を選ぶ」尖った
    ビルド向けに、ramp期間中（round_number < ramp_rounds）にのみ上乗せする値。
    1.0でramp分をちょうど打ち消して従来のbase_pressureへ戻り、1.0超で
    ramp前より強い特攻を選べる。ramp期間が終わった後（round_number>=ramp_rounds）
    には一切影響しない（中盤・終盤のバランスは変えない、あくまで「序盤の解禁度合い」
    だけを操作するスタイル選択用のノブ）。

    注意: 「ラウンド」はround_number（両者が1手ずつ打ち終えた単位）であり、
    ラウンド1は両者本拠HPが満タン・盤上に駒がほぼ無い状態なので、この倍率が
    低い間はadvance/efficiency等の他の重みは一切変更されない点に注意
    （本拠へのダメージ評価だけを狙い撃ちで抑える）。
    """
    if ramp_rounds <= 0:
        return 1.0
    if round_number >= ramp_rounds:
        return 1.0
    base = max(0.0, round_number / ramp_rounds)
    return base + rush_opening_pressure


# 2026-09-09追加: advance_ramp_roundsはこの関数をそのまま流用する（実装は
# 変更なし、rush_opening_pressure引数を渡さず呼び出すだけ）。advanceが
# base_pressureと違ってramp機構を持たず常にフル値が乗っていたため、
# レンジ下限（0.2、レンジ内位置2.12%）への張り付きという形で同型の問題が
# 顕在化した（analyze_tuning_run_handoff.md 3.19節参照）。呼び出し側
# （heuristic_bot._score_actions / search_bot_skeleton._score_actions）で
# `_base_pressure_ramp_multiplier(game.round_number, weights["advance_ramp_rounds"])`
# として使う。ramp_rounds<=0（既定）なら常に1.0＝従来通り無効。


# ============================================================
# opening_tempo_pref（2026-09-09新設・analyze_tuning_run_handoff.md 3.20節#1）
# ============================================================
# 背景: 3.19節でtrial278/325（戦績が悪いと分かっているtrial）の棋譜candidates
# フィールドを直接確認したところ、ラウンド1はRPスポットへの配置を含む
# あらゆる候補手がpassより評価が低いという縮退が実際の数値として確認できた
# （exposure/dangerによる配置ペナルティが、まだ収益化していないRPスポットの
# 価値をラウンド1時点では上回ってしまう。3.3節の力学の実例）。
# rp_income_marginは「経済的な息切れ」を評価に織り込む対症療法として既に
# 導入済みだが、それでもこの縮退が起きる場合（rp_income_marginが小さい
# ビルド等）に、プレイヤーが直接「序盤はpassを避けろ」と指示できる専用ノブを
# 用意する。base_pressure_ramp_roundsと同じ「ラウンド数に応じて減衰する」
# 構造だが、あちらは特定の重み(base_pressure)を弱めるのに対し、こちらは
# pass忌避・RPスポット確保優先という行動そのものへ直接加点/減点する。
OPENING_TEMPO_ROUNDS = 5  # この期間だけ効果を持つ（rush_opening_pressureの
# ramp期間と揃えた固定値。プレイヤーが期間を調整したい場合は将来的に専用の
# `_rounds`重みを別途新設すること。今回は「優先度高・技術難度低」を優先し、
# 既存のadvance_ramp_rounds/base_pressure_ramp_roundsのように可変長のramp_rounds
# 重みそのものにはしていない）。


def _opening_tempo_multiplier(round_number, tempo_rounds=OPENING_TEMPO_ROUNDS):
    """ラウンド1で1.0、tempo_rounds以降で0.0になるよう線形に減衰する倍率。
    _base_pressure_ramp_multiplier（0→1に立ち上がる）とは向きが逆
    （こちらは「序盤ほど強く効き、中盤以降は無効」という opening_tempo_pref の
    意図そのものを表す）。tempo_rounds<=0なら常に0.0（無効）。"""
    if tempo_rounds <= 0:
        return 0.0
    if round_number >= tempo_rounds:
        return 0.0
    return max(0.0, 1.0 - (round_number - 1) / tempo_rounds)


# ============================================================
# comeback_desperation_pref（2026-09-09新設・3.20節#2）
# ============================================================
# 「VP・本拠HPで劣勢な終盤、通常なら避ける相打ち・飛び込みのようなリスクの
# 高い手を積極的に選ぶ度合い」。純粋なゲーム状態（VP差・ラウンド進行度）だけ
# から[0,1]の「どれだけ捨て身になるべきか」を返す関数と、実際の重み
# (comeback_desperation_pref)を掛けて評価式へ加点するのは呼び出し側
# （各botの_score_actions）に委ねる二段構成にする（opening_tempo_multiplier
# と同じ設計。weightに依存しない状態量として独立にテストできるようにする）。
def _comeback_desperation_fraction(game, player):
    """[0,1]。VPで劣勢なほど・ラウンドが進んでいるほど大きい。
    weight=0のボットで呼んでもコスト以外の副作用はない（呼び出し側で
    weightを掛けるまでは何もスコアに影響しない）。"""
    opp = 1 - player
    vp_deficit = game.econ.cumulative_vp[opp] - game.econ.cumulative_vp[player]
    if vp_deficit <= 0:
        return 0.0
    # VPスポット(kill_bonus_vp)を1回相当奪われた程度の差を「1.0に近づき始める」
    # 目安として100で正規化する（vp_spots.kill_bonus_vpの既定値と同オーダー）。
    vp_deficit_ratio = min(1.0, vp_deficit / 100.0)
    turn_limit = CONFIG["turn_limit_per_player"]
    lateness = min(1.0, game.round_number / max(turn_limit, 1))
    return vp_deficit_ratio * lateness


# ============================================================
# signature_unit_affinity（2026-09-09新設・3.20節#6、5駒種分）
# ============================================================
# 「特定の駒種を偏重して〜特化型のプレイスタイルを演出する」ための、
# production_diversity_prefと対になる重み（あちらは偏りへの減点、こちらは
# 狙った1駒種への直接加点）。KIND_TO_PLACE_PREF/KIND_TO_PRODUCE_PREF
# （Tier1 #1-10、heuristic_bot.py側）が「配置/生産の選び方」という狭い
# 局面判断なのに対し、本重みは「自軍の駒種構成そのもの」に対する恒常的な
# 評価項として、production_diversity_prefと全く同じ場所（evaluate_state・
# choose_production系）に配線する（advance_ramp_rounds/opening_tempo_prefの
# ような「行動選好」レイヤーではなく「状態評価」レイヤーに置くことで、
# HeuristicBotの生産判断とSearchBotの探索評価（choose_production・
# evaluate_stateの両方）に一貫して効かせる）。
SIGNATURE_AFFINITY_KEY = {
    "歩兵": "infantry_signature_affinity",
    "騎兵": "cavalry_signature_affinity",
    "重装兵": "heavy_signature_affinity",
    "弓兵": "archer_signature_affinity",
    "工兵": "engineer_signature_affinity",
}


# 2026-09-02: base_approach_defense_priority（「敵が本拠半径4以内に近づいた
# 時点で本拠側へ帰還する行動を評価する」重み）を試験導入したが、実測ベンチマーク
# （本拠速攻構成 vs turtle_vp系、各60戦、meta_diversity_check.pyのrun_match流用）で
# base_hp_panic_threshold単体（勝率15.0%）より悪化する結果（併用で26.7〜35.0%、
# 重みを上げるほど56〜98%まで悪化）となったため撤回した。「敵が近づいたら
# 無条件で本拠に戻る」という設計は、迎撃したほうが得な場面まで撤退させて
# しまい逆効果だった。撤回に伴い_base_approach_severity/
# _nearest_enemy_distance_to_base/BASE_APPROACH_ALERT_RADIUSも削除済み。


def _potential_attack(board, pos, kind, player, exclude=None, vp_spot_positions=None,
                       rp_spot_positions=None, ranged_friends=None):
    """
    そのマスに立った場合、次の戦闘解決で与えられるダメージ総量と、
    見込める撃破数（うちVPスポット駐留駒の撃破数・RPスポット駐留駒の撃破数）を返す
    (total_damage, kill_count, vp_spot_kill_count, rp_spot_kill_count, base_damage)。
    game.resolve_combat と同じ「呼吸点方式」の分割ダメージ計算に合わせてある。
    exclude: 移動の場合、移動元の座標（自分自身を対象から除外するため）。
    vp_spot_positions: VPスポット（三々+天元）の座標集合。渡された場合のみ
    vp_spot_kill_count を計算する（game.py の kill_bonus_vp ルール対応）。
    rp_spot_positions: RPスポット（4-4点）の座標集合。渡された場合のみ
    rp_spot_kill_count を計算する（2026-08-07追加。rp_spot_kill_bonus対応。
    こちらはgame.py側に対応する実ルールはなく、純粋にヒューリスティック評価用）。

    2026-08-06 修正: 戻り値に base_damage（本拠へのダメージ分）を追加した。
    従来は本拠への削りダメージも通常の attack 重みでそのまま加点していたが、
    本拠はHPが2000と桁違いに大きく、序盤の1〜数ダメージは実質的な脅威に
    ならない。それでも「敵に隣接して攻撃している」という理由だけで通常の
    駒への攻撃と同等以上に高得点になり、結果として初手から敵本拠の隣に
    駒を置く「特攻」がRPスポット確保より常に優先される一因になっていた
    （経済最優先化のための修正、呼び出し側の重み付けは choose()/
    _score_actions() 側で attack と base_pressure に分離した）。

    2026-08-07 修正: kill判定を「自分単独の与ダメージ」だけでなく、盤上に
    既にいる他の味方駒からの合算ダメージ(_existing_incoming_damage)も
    加味するように変更。複数駒での集中攻撃（フォーカスファイア）による
    撃破を正しく評価できるようにするため（詳細はコメント参照）。

    2026-08-12修正: 工兵の弓兵ダメージ無効化（0.32節）を反映。is_damage_nullified()
    で無効化される対象（例: kind=弓兵, 相手=工兵）は targets（呼吸点方式の分割母数）
    から除外する。resolve_combat と同じ「無効化対象はそもそも対象に含めない」方式。
    """
    cfg = CONFIG["pieces"][kind]
    atk = cfg["atk"]
    if atk <= 0:
        return 0.0, 0, 0, 0, 0.0
    if cfg["ranged"]:
        reach = 1 + cfg["range"]
        # 全件スキャン+距離足切りではなくreach以内座標を事前列挙しO(1)参照する方式
        targets = [p for p in _positions_within_manhattan_radius(board.size, pos, reach)
                   if p != exclude and p in board.grid and board.grid[p].owner != player
                   and not is_damage_nullified(kind, board.grid[p].kind)]
    else:
        targets = [p for p in board.adjacent_positions(pos)
                   if p != exclude and p in board.grid and board.grid[p].owner != player
                   and not is_damage_nullified(kind, board.grid[p].kind)]
    if not targets:
        return 0.0, 0, 0, 0, 0.0
    dmg_each = atk / len(targets)  # ①呼吸点分割（相性適用前。game.resolve_combatと同じ順序）
    exclude_positions = {pos}
    if exclude is not None:
        exclude_positions.add(exclude)
    kill_targets = []
    total_dmg = 0.0
    base_dmg = 0.0
    for p in targets:
        defender_kind = board.grid[p].kind
        this_dmg = dmg_each * type_multiplier(kind, defender_kind)  # ②分割後に相性倍率
        total_dmg += this_dmg
        if defender_kind == "本拠":
            base_dmg += this_dmg
        already = _existing_incoming_damage(board, p, player, exclude_positions, ranged_friends=ranged_friends)
        if board.grid[p].hp <= this_dmg + already:
            kill_targets.append(p)
    vp_spot_kills = 0
    if vp_spot_positions:
        vp_spot_kills = sum(1 for p in kill_targets if p in vp_spot_positions)
    rp_spot_kills = 0
    if rp_spot_positions:
        rp_spot_kills = sum(1 for p in kill_targets if p in rp_spot_positions)
    # 三すくみ導入により合計与ダメージはatkから変化しうるため、相性反映後の
    # 実際の合計(total_dmg)を返す（attack/base_pressure評価に三すくみを正しく反映するため必須）。
    return total_dmg, len(kill_targets), vp_spot_kills, rp_spot_kills, base_dmg


def _neighbors_count(board, pos, player, exclude=None):
    friendly, enemy = 0, 0
    for n in board.adjacent_positions(pos):
        if exclude is not None and n == exclude:
            continue
        p = board.grid.get(n)
        if p is None:
            continue
        if p.owner == player:
            friendly += 1
        else:
            enemy += 1
    return friendly, enemy


def _count_adjacent_enemy_kind(board, pos, player, kind, exclude=None):
    """2026-08-25追加（Tier1重み#15 engineer_shield_bonus用）。
    pos に隣接する「指定した種類の敵駒」の数を返す。_neighbors_countの
    種類限定版。射程を問わず隣接マスのみを見る（毒無効化は距離を問わず
    発動するが、ここでは「盾として弓兵の隣に居続ける」構図を評価したい
    ため隣接に限定している）。"""
    count = 0
    for n in board.adjacent_positions(pos):
        if exclude is not None and n == exclude:
            continue
        p = board.grid.get(n)
        if p is not None and p.owner != player and p.kind == kind:
            count += 1
    return count


def _count_matchup_adjacent(board, pos, player, own_kind, exclude=None):
    """2026-08-25追加（Tier2重み#16/#17 favorable/unfavorable_matchup用）。
    posに隣接する敵駒のうち、三すくみ(type_advantage)上「own_kindが有利」な
    数と「own_kindが不利」な数をそれぞれ返す ((favorable_count, unfavorable_count))。
    弓兵・工兵はtype_advantage["pairs"]に登場しないため常にどちらにも
    カウントされない（1.0倍固定）。"""
    favorable, unfavorable = 0, 0
    for n in board.adjacent_positions(pos):
        if exclude is not None and n == exclude:
            continue
        p = board.grid.get(n)
        if p is None or p.owner == player:
            continue
        if type_multiplier(own_kind, p.kind) > 1.0:
            favorable += 1
        elif type_multiplier(p.kind, own_kind) > 1.0:
            unfavorable += 1
    return favorable, unfavorable


def _distance_to_center(board, pos):
    """2026-08-25追加（Tier2重み#23 center_control用）。盤面中央からの
    マンハッタン距離。"""
    center = (board.size // 2, board.size // 2)
    return board.distance(pos, center)


def _nearest_enemy_distance(board, pos, player, exclude=None):
    """2026-08-25追加（Tier1重み#13 archer_kiting_pref用）。posから最も
    近い敵駒までのマンハッタン距離。敵駒が盤上に存在しない場合はNoneを返す。
    9x9の小盤面なので全駒を舐める素朴な実装で十分（1手あたりの呼び出し回数は
    弓兵の移動候補手のみに限定して使う想定）。"""
    best = None
    for p, piece in board.grid.items():
        if exclude is not None and p == exclude:
            continue
        if piece.owner == player:
            continue
        d = board.distance(pos, p)
        if best is None or d < best:
            best = d
    return best


def _distance_to_nearest_corner(board, pos):
    """2026-08-25追加（Tier2重み#24 corner_edge_avoidance用）。4隅のうち
    最も近いものへのマンハッタン距離。"""
    size = board.size
    corners = [(0, 0), (0, size - 1), (size - 1, 0), (size - 1, size - 1)]
    return min(board.distance(pos, c) for c in corners)


def _position_hold_value(board, pos, kind, player, weights, vp_stars, vp_tengen, rp_pts,
                          vp_spot_positions, count_vp_spot_bonus=True, midgame_pts=None,
                          ranged_enemies=None, ranged_friends=None, apply_base_pressure=True,
                          base_pressure_multiplier=1.0):
    """
    「そのマスに居続けた場合」の価値（support/danger/RPorVPスポット占有/攻撃・撃破）を返す。
    advance・efficiencyは「配置/移動という行為そのもの」の評価であり位置の保持価値ではないため含めない。
    移動アクションのスコア計算で、移動元(src)を離れることの機会費用を見積もるのに使う。

    count_vp_spot_bonus: Falseにすると vp_star/vp_tengen の占有ボーナスを加算しない
    （2026-08-07追加。「案A」。詳細は evaluate_state 側のコメント参照）。
    rp_spot（RPスポット）側のボーナスは、この関数の呼び出し元に関わらず常に加算する
    （こちらは「まだ確定していない将来のRP収入」を見積もる正当な項目であり、
    evaluate_state 側で実測RP差と二重計上にはならないため）。
    midgame_pts: 隅の中盤解禁スポット座標集合（2026-08-07追加）。Noneなら判定しない。
    末尾にキーワード専用で追加することで、既存の位置引数呼び出しを壊さないようにしている。

    apply_base_pressure: 2026-09-04追加。この駒がkind=="本拠"の敵に与える見込み
    ダメージ(base_dmg)にweights["base_pressure"]を乗せるかどうかを切り替える。
    既定Trueで従来通り（1手ヒューリスティック側の呼び出しは常に「自分がこれから
    打とうとしている手」＝自分視点の評価であり、意味は変わらない）。

    base_pressure_multiplier: 2026-09-04追加。base_dmg*weights["base_pressure"]に
    さらに掛ける倍率（_base_pressure_ramp_multiplier参照）。序盤の本拠特攻を
    抑えるためのラウンド依存の立ち上がり倍率を渡す想定。既定1.0（無効）。

    背景: evaluate_state / _ProductionEvalContext は盤上の「敵の駒」もこの関数で
    評価し、符号を反転して合算する（sign = 1 if piece.owner == player else -1）。
    このとき敵駒側の呼び出しでも同じweights["base_pressure"]がbase_dmgに掛かる
    ため、1つの重みが「自分の駒が敵本拠を脅かす価値（進めたい）」と「敵の駒が
    自陣本拠を脅かす危険度（下げたい）」という逆方向の2役を同時に背負っていた。
    Optunaはこの重みを動かすと両方が同時に動いてしまうため、片方だけを狙って
    調整できず、rp_spot確保を伴わない本拠特攻を安全に抑制できない一因になって
    いた（詳細は企画書9.1節・0.86節参照）。
    自陣本拠が脅かされている危険度は、evaluate_state側で別途_own_base_threat
    （呼吸点分割込みの正確な集計）をweights["base_defense"]で評価済みのため、
    敵駒側の呼び出しでapply_base_pressure=Falseを渡してこの項をゼロにしても、
    防御シグナル自体は失われない（二重計上をやめるだけ）。これによりbase_pressure
    は「自分の駒を敵本拠へ向かわせる価値」だけを表す純粋な攻撃側の重みになる。
    """
    score = 0.0
    friendly, enemy = _neighbors_count(board, pos, player)
    score += friendly * weights["support"]
    score += enemy * weights["danger"]
    if count_vp_spot_bonus:
        if pos in vp_tengen:
            score += weights["vp_tengen"]
        elif pos in vp_stars:
            score += weights["vp_star"]
        elif pos in rp_pts:
            score += weights["rp_spot"]
            if kind == "工兵":
                score += weights["engineer_econ_bonus"]
        elif midgame_pts and pos in midgame_pts:
            score += weights["midgame_spot"]
            if kind == "工兵":
                score += weights["engineer_econ_bonus"]
    elif pos in rp_pts:
        score += weights["rp_spot"]
        if kind == "工兵":
            score += weights["engineer_econ_bonus"]
    elif midgame_pts and pos in midgame_pts:
        score += weights["midgame_spot"]
        if kind == "工兵":
            score += weights["engineer_econ_bonus"]
    dmg, kills, vp_spot_kills, rp_spot_kills, base_dmg = _potential_attack(
        board, pos, kind, player, vp_spot_positions=vp_spot_positions,
        rp_spot_positions=rp_pts, ranged_friends=ranged_friends)
    score += (dmg - base_dmg) * weights["attack"]
    if apply_base_pressure:
        score += base_dmg * weights["base_pressure"] * base_pressure_multiplier
    score += kills * weights["kill_bonus"]
    score += vp_spot_kills * weights["vp_spot_kill_bonus"]
    score += rp_spot_kills * weights["rp_spot_kill_bonus"]
    # 2026-08-09追加: 被ダメージ側の減点（attackの対）。ここに入れておかないと、
    # 移動元(src)に留まる価値の見積もりだけ被ダメージが考慮されず、
    # 移動先(dst)の評価とだけ非対称になってしまう。
    score += _potential_incoming_damage(board, pos, kind, player, ranged_enemies=ranged_enemies) * weights["incoming_damage"]
    return score


# ============================================================
# 生産多様性（production_diversity_pref）のSearchBot側配線
# ============================================================
# 2026-09-07追加（analyze_tuning_run_handoff.md 3.16節対応）。
#
# 経緯: 工兵(engineer)への生産一極集中がAI同士の対局で繰り返し観測されていた。
# ソース調査の結果、production_diversity_pref（生産の偏りを抑えるために
# 2026-08-31新設された重み。WEIGHT_SEARCH_SPACEにも登録済み）は
# heuristic_bot.py の choose_production() にしか実装されておらず、実際に
# チューニング・対戦評価に使われている search_bot_skeleton.py の SearchBot側
# には一切配線されていなかった（grep -rn "production_diversity_pref" *.py の
# ヒットはheuristic_bot.pyの1箇所のみ）。一方 _position_hold_value() は
# RP/中盤スポットにいる工兵に対して engineer_econ_bonus を無条件・無上限に
# 加算する（上のif kind == "工兵"の4箇所参照）。つまりSearchBotの評価式には
# 「工兵を選ぶと得」という項はあっても「工兵ばかりだと損」という項が
# 存在せず、生産の偏りを止める仕組みが構造的に欠けていた。
#
# 対応: SearchBot側（search_bot_skeleton.pyのevaluate_state /
# _ProductionEvalContext.best_value_for_placements の両方）から参照する
# 「自軍の駒種構成が偏っているほど損をする」評価項を、production_diversity_pref
# で重み付けしてここに新設する。
#
# heuristic_bot.choose_production() 内の同名の重み参照（盤上の敵駒構成との
# 三すくみ相性エッジ、composition_edge）とは、狙いは同じ「生産の偏りを
# 是正する」でも具体的な計算式は別物である点に注意（あちらは敵構成に対する
# 有利/不利の相性、こちらは自軍の駒種構成そのものの偏り）。工兵のように
# type_multiplierの三すくみに一切関与しない駒種では、heuristic_bot側の
# composition_edgeは常に0になり実質無効という別の限界も抱えているため
# （favorable/unfavorableが両方0になるため）、こちらの実装はheuristic_bot側の
# 計算をそのまま移植せず、駒種構成の集中度を直接見る独立した式にした。

_PRODUCIBLE_KINDS = tuple(k for k in CONFIG["pieces"] if k != "本拠")
N_PRODUCIBLE_KINDS = len(_PRODUCIBLE_KINDS)


def _own_army_kind_counts(board, reserve_list, player):
    """自軍（本拠を除く盤上の駒＋reserve）の駒種ごとの数をCounterで返す。
    「盤上にいるか手駒(reserve)のままか」は生産の偏りそのものには関係しない
    （どちらも既に生産して確保済みの戦力という点で同じ）ため合算する。

    盤面を毎回自前でフルスキャンする（O(盤上駒数)）ため、evaluate_state
    のようにplayer/opp両方の集中度を毎リーフノードで求めるホットパスから
    直接呼ぶと、evaluate_state本体が既に持つ1回のボードスキャンに対して
    余分な2回分のスキャンが追加されコストが跳ね上がる（実測: depth2の
    1局が約8秒→13秒に悪化）。そのため evaluate_state 側は本関数を呼ばず、
    既存のボードスキャンへ_add_board_piece_to_counts()でインラインに
    数え上げる（_production_diversity_penalty_from_counts参照）。
    本関数は、盤面を1回もスキャンしない他の呼び出し元
    （_production_diversity_marginal_delta。choose_productionの候補kindごとに
    高々5回呼ばれるだけで、evaluate_stateほどの呼び出し頻度ではないため
    許容できる）向けに残してある。"""
    counts = Counter()
    for piece in board.grid.values():
        if piece.owner == player and piece.kind != "本拠":
            counts[piece.kind] += 1
    _add_reserve_kind_counts(counts, reserve_list)
    return counts


def _add_reserve_kind_counts(counts, reserve_list):
    """reserve_list（駒種名のリスト）の中身をcounts(Counter)へ加算する。
    reserveは保有上限9体までの小さなリストのため、呼び出しコストは無視できる。"""
    for kind in reserve_list:
        counts[kind] += 1


def _production_concentration_index(counts, n_kinds=N_PRODUCIBLE_KINDS):
    """自軍の駒種構成の偏り度合いを返す（0=全n_kinds種が均等、値が大きいほど
    特定の駒種への集中が強い）。総数が0（まだ何も生産していない）の場合は
    0.0を返す。

    tune_balance_search.py の _production_diversity_ratio()（対局後にShannon
    エントロピーで集計し[0,1]に正規化する同種の指標）とは別実装。本関数は
    choose_production()の差分計算（1駒追加した場合の増分だけを見たい）で
    使うため、対数を含まずO(1)で差分計算できるSimpson指数（二乗和）ベースに
    している。

    2026-09-07（実装時に発覚した設計ミスの訂正）: 当初は二乗和を total^2 で
    正規化し[0,1]に収める版（1種類に完全集中していれば駒数に関わらず常に1.0）
    にしていたが、これだと「1種類だけ生産した」時点で既に指標が上限1.0に
    張り付き、そこからさらに同じ駒種を追加してもindexが変化しない
    （＝production_diversity_marginal_deltaが実質ゼロになり、まさに今回
    直したかった『工兵を際限なく追加しても評価上ノーペナルティ』という
    バグを別の場所で再現してしまう）ことが実装後の検証で判明した
    （下記テスト参照）。そのため total（二乗ではなく1乗）で正規化する式に
    変更した。この式は「1種類に完全集中」の場合 total*(1-1/n_kinds) という
    total に比例する値になり、駒を追加するたび一定のペナルティ
    ((1-1/n_kinds)*production_diversity_pref)が積み上がり続ける
    （＝生産すればするほど際限なく損が増える、が正しい狙い）。
    """
    total = sum(counts.values())
    if total <= 0 or n_kinds <= 1:
        return 0.0
    sum_sq = sum(c * c for c in counts.values())
    total_sq = total * total
    min_sum_sq = total_sq / n_kinds  # 全n_kinds種に均等分配した場合の理論最小値（sum_sqの下限）
    # Cauchy-Schwarzによりsum_sq >= min_sum_sqが常に成り立つため理論上は
    # 負にならないが、浮動小数の丸め誤差対策としてmax(0.0, ...)を通す。
    return max(0.0, (sum_sq - min_sum_sq) / total)


def _production_diversity_penalty(board, reserve_list, player, weights, n_kinds=N_PRODUCIBLE_KINDS):
    """evaluate_state用: 自軍の駒種構成の偏りに応じた「引くべき」ペナルティ
    （0以上）を返す。呼び出し側で `score -= この値` として使うことを想定
    （production_diversity_prefは他の「大きさ」系重みと同じくPENALTY_WEIGHT_KEYS
    には含まれず常に正の値のため、符号は呼び出し側で明示的に反転する）。

    盤面を自前でスキャンする版（_own_army_kind_counts経由）。呼び出し頻度が
    低い経路（choose_productionの「reserveに積むだけ」分岐等、production後の
    子ゲーム状態に対して1回だけevaluate_stateを呼ぶような箇所）向け。
    evaluate_state本体のようにリーフノードごとに毎回呼ばれるホットパスでは、
    既存のボードスキャンループ内で数え上げたcountsをそのまま
    _production_diversity_penalty_from_counts()へ渡すこと（二重スキャンを
    避けるため）。"""
    counts = _own_army_kind_counts(board, reserve_list, player)
    return _production_concentration_index(counts, n_kinds) * weights["production_diversity_pref"]


def _production_diversity_penalty_from_counts(counts, weights, n_kinds=N_PRODUCIBLE_KINDS):
    """_production_diversity_penaltyの「countsを既に持っている」版。呼び出し側
    （evaluate_state）が自前のボードスキャンループ内で既に数え上げたCounterを
    渡すことで、盤面の再スキャンを避ける（2026-09-07追加。ホットパス最適化。
    実測: これが無いとevaluate_state呼び出しごとに2回（player分・opp分）の
    余分な盤面フルスキャンが発生し、depth2の自己対戦1局が約8秒→13秒に悪化
    することを確認した）。"""
    return _production_concentration_index(counts, n_kinds) * weights["production_diversity_pref"]


def _production_diversity_marginal_delta(board, reserve_list, player, kind, weights,
                                          n_kinds=N_PRODUCIBLE_KINDS):
    """choose_production用: kindを1体追加生産した場合の集中度ペナルティの
    増分を、評価値へそのまま加算できる符号（集中が悪化するほど負の値）で返す。

    _ProductionEvalContext.best_value_for_placements() のように「production
    前のbaseline_score（evaluate_state）」に対する差分だけを積み上げる評価
    経路では、production後の新しい駒種構成をevaluate_stateが自動的に
    再評価してくれないため、この関数で明示的に差分を足す必要がある
    （rp_term_delta等、他の差分項と同じ理由・同じパターン）。

    一方、choose_production()の「今すぐ置けないのでreserveに積むだけ」の
    分岐や _choose_production_reference()（旧実装、突き合わせテスト用）は
    production後のboard/reserveをそのままevaluate_stateへ渡す構造のため、
    この関数を呼ばなくても_production_diversity_penaltyが自動的に反映される
    （二重計上ではなく、「差分計算経路にだけ明示的に足す」設計である点に
    注意）。"""
    counts = _own_army_kind_counts(board, reserve_list, player)
    before = _production_concentration_index(counts, n_kinds)
    counts[kind] += 1
    after = _production_concentration_index(counts, n_kinds)
    return -(after - before) * weights["production_diversity_pref"]
