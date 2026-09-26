"""
web_api.py
==========
play_vs_ai.html (Pyodide経由) がブラウザから呼び出すための薄いファサード。

設計方針（最重要）:
- ここにゲームロジック・AI評価ロジックは一切書かない。すべて game.py /
  search_bot_skeleton.py / heuristic_bot.py / config.py への委譲のみ。
- JS側とはJSON化可能な素のdict/list/文字列/数値だけでやり取りする
  （PythonオブジェクトをそのままJSに渡すとPyodideのproxy経由になり扱いが
  面倒になるため、この層で明示的に辞書へシリアライズする）。
- これにより「Python版とJS版でロジックが分岐する」という事故が構造的に
  起こらなくなる（JSは常にこのAPI経由でPythonの実装をそのまま呼ぶだけ）。
"""

import json
import random
import sys

sys.path.insert(0, ".")

from game import Game, CONFIG
from search_bot_skeleton import SearchBot


_games = {}
_bots = {}
_next_id = [0]

# 2026-09-06追加: play_vs_ai経由（human vs AI）の対局でg.kifuが常に空になる
# バグの修正用。web_api.pyはGame.run()を呼ばず、begin_round/apply_action/
# resolve_round_end/apply_productionをJS側から個別に呼び出す構成のため、
# game.py Game.run()が1ループでturn_snapshots・damage・removed等をまとめて
# 組み立てているのと同じことを、ラウンドをまたいで少しずつ集める形で行う。
# game_id -> {"round_actions": {...}, "turn_snapshots": [...],
#             "removed": [...], "damage": {...}, "vp_kill_bonus": [...],
#             "rp_wasted": [...], "board_after_combat": [...]}
_pending_round = {}

# 2026-09-06追加: 上記のkifu空バグ修正時、apply_action()が候補手情報
# (candidates/random_pick)を持たず常にNoneを書き込んでいたため、
# 「AIの候補手ログを表示」チェックボックスがONの対局でも、ダウンロードした
# 棋譜をlocal_viewerで再生すると常に「候補ログなし」になってしまう退行が
# あった。ai_choose_with_debug_json()はAIの手を選ぶ時点で候補情報を
# 計算済みなので、それを直後のapply_action()に橋渡しするためのバッファ。
# game_id -> {"player": int, "action": action_tuple,
#             "candidates": [...], "random_pick": bool}
_pending_debug = {}


def _serialize_action(action):
    if action[0] == "place":
        return {"type": "place", "kind": action[1], "pos": list(action[2])}
    if action[0] == "move":
        return {"type": "move", "src": list(action[1]), "dst": list(action[2])}
    return {"type": "pass"}


# 2026-09-06追加: 棋譜（g.kifu / kifu_json()の出力）専用のaction直列化。
#
# 背景: 上の_serialize_action()はJS側とのワイヤープロトコル用に
# {"type": "place", "kind": ..., "pos": [...]} という辞書形式を返す
# （JS側がaction.type/action.kind/action.posというプロパティでそのまま
# 読むための形式で、valid_actions_json()やai_choose_json()等ではこの形式
# のままで正しい）。
#
# ところがapply_action()はこの辞書形式を、そのままpending["round_actions"]
# とturn_snapshots[...]["action"]（＝最終的にg.kifuのactions/turns[].action
# フィールドになる）にも書き込んでいた。一方、g.kifuの本来のスキーマは
# game.py Game.run()内のローカル_serialize_action()が使っている
# ["place", kind, [x,y]] という配列形式であり（generate_kifu.pyが吐く
# kifu_data_*.jsonはすべてこの配列形式）、local_viewer_template.htmlの
# actionText()もa[0]/a[1]/a[2]という配列インデックスでしか読まない。
#
# この形式の食い違いにより、play_vs_ai経由でダウンロードした棋譜をlocal_viewer
# で開くと、actionText()がa[0]==="place"/"move"のいずれにも一致せず（辞書には
# 数値インデックスが無いため常にundefined）、全ての手が無条件にフォールバック
# 先の「パス」表示になってしまっていた。
#
# 修正方針: ワイヤープロトコル用の_serialize_action()はJS側の既存の使い方
# （action.type等）を壊さないよう一切変更せず、棋譜のactions/turns[].action
# フィールドの組み立てにのみ、この配列形式の関数を新設して使う。
def _serialize_action_for_kifu(action):
    if action[0] == "place":
        return [action[0], action[1], list(action[2])]
    if action[0] == "move":
        return [action[0], list(action[1]), list(action[2])]
    return list(action)


def _deserialize_action(obj):
    if obj["type"] == "place":
        return ("place", obj["kind"], tuple(obj["pos"]))
    if obj["type"] == "move":
        return ("move", tuple(obj["src"]), tuple(obj["dst"]))
    return ("pass",)


def _board_snapshot(g):
    """game.py Game.run()内の_board_snapshot()と全く同じ形式（kifu用）。
    state_json()用の_serialize_piece()とはキー名も対象（HPなど）も異なるため、
    棋譜スキーマ互換のためにあえて別関数として持つ。"""
    return [
        {"pos": list(pos), "kind": p.kind, "owner": p.owner,
         "hp": round(p.hp, 1), "max_hp": p.max_hp}
        for pos, p in g.board.grid.items()
    ]


def _serialize_piece(pos, piece):
    return {
        "pos": list(pos),
        "owner": piece.owner,
        "kind": piece.kind,
        "hp": piece.hp,
        "maxHp": piece.max_hp,
        "atk": piece.atk,
        "movable": piece.movable,
    }


def state_json(game_id):
    """描画に必要な情報一式を返す（JS側はこれだけ見れば盤面を再現できる）。"""
    g = _games[game_id]
    pieces = [_serialize_piece(pos, p) for pos, p in g.board.grid.items()]
    bans = [
        {"pos": list(pos), "owner": owner, "round": round_}
        for pos, (owner, round_) in g.placement_ban.items()
    ]
    return json.dumps({
        "round": g.round_number,
        "rp": list(g.econ.rp),
        "vp": list(g.econ.cumulative_vp),
        "reserve": {0: list(g.reserve[0]), 1: list(g.reserve[1])},
        "pieces": pieces,
        "placementBan": bans,
        "winner": g.winner,
        "winReason": g.win_reason,
        # 2026-09-12追加: 投了機能用。win_reason=="resigned"のとき、実際に
        # 投了したプレイヤー番号（winnerはその相手なので単独では区別できない）。
        "resignedPlayer": getattr(g, "resigned_player", None),
        "gameOver": g.winner is not None,
    })


def config_json():
    """CONFIG全体をそのままJSへ渡す。build_play_vs_ai.pyの数値抜き出しが不要になる
    （数値もロジックも同じconfig.pyを直接読んでいるだけなので、キーの追加漏れが起こり得ない）。"""
    return json.dumps(CONFIG)


def new_game():
    gid = _next_id[0]
    _next_id[0] += 1
    _games[gid] = Game()
    return gid


def new_bot(depth=2, candidate_k=10, weights_json=None):
    # 0.95節によりdepth2が既定（play_vs_ai側のUIから明示的に指定できる）。
    # 旧0.77節のdepth3既定は非同期対戦の運用コスト実測を踏まえて撤回。
    # 2026-09-01追加: weights_jsonにweights.resolve_weights()と同じ形状の完全な
    # 重み辞書のJSON文字列（champion_history/gen_XXXX.jsonの"weights"部分）を渡すと、
    # そのAI（対戦相手）の重みとして使う。None/未指定なら従来通りグローバルの
    # HEURISTIC_WEIGHTS（現在のweights.py既定値）を使う。JS側はplay_vs_ai_template.html
    # の対戦相手選択UI（BUILD_OPTIONS.champion_history）から呼び出す。
    bid = _next_id[0]
    _next_id[0] += 1
    weights_override = json.loads(weights_json) if weights_json else None
    # 2026-08-12修正: rng=random.Random()を渡し、対局ごとに展開が変わるようにする
    # （choose()側は候補を独立評価する経路に切り替わるため、決定論の高速経路より
    # 遅くなるが、vs AI戦では速度上の問題にならない想定）。
    _bots[bid] = SearchBot(depth=depth, candidate_k=candidate_k, debug=False, rng=random.Random(),
                            weights=weights_override)
    return bid


def begin_round(game_id):
    g = _games[game_id]
    if g.winner is not None:
        return False
    g.round_number += 1
    if CONFIG["alternate_initiative_each_round"] and g.round_number % 2 == 0:
        order = list(reversed(g.turn_order))
    else:
        order = g.turn_order
    # 2026-09-06追加: このラウンド用の棋譜集計バッファをリセットする
    # （finalize_round_kifu()参照）。前のラウンドで消費されずに残った候補情報
    # （例: 何らかの理由でapply_action()が呼ばれなかった）を次のラウンドへ
    # 誤って引き継がないよう、_pending_debugもここで捨てておく。
    _pending_round[game_id] = {"round_actions": {}, "turn_snapshots": []}
    _pending_debug.pop(game_id, None)
    return {"round": g.round_number, "order": order}


def valid_actions_json(game_id, player):
    g = _games[game_id]
    actions = g.valid_actions(player)
    return json.dumps([_serialize_action(a) for a in actions])


def apply_action(game_id, player, action_obj_json):
    g = _games[game_id]
    action = _deserialize_action(json.loads(action_obj_json))
    g.apply_action(player, action)
    if not g.first_move_done:
        g.first_move_done = True
        g.maybe_resolve_pie_rule(action)
    # 2026-09-06追加: game.py Game.run()のturn_snapshotsと同じ形式でこの1手を記録する
    # （kifu空バグ修正）。
    # 2026-09-06修正: 上の初回修正時はdebug候補手(candidates/random_pick)を
    # ここで持っておらず常にNoneとしていたが、これだと「候補手ログを表示」
    # チェックボックスがONの対局でもダウンロードした棋譜には候補が一切残らず、
    # local_viewerで再生すると常に「候補ログなし」になってしまっていた
    # （AIの候補手ログ自体は表示用にaddLog()へは出ているが、棋譜には載らない
    # ままだった）。ai_choose_with_debug_json()が直前に呼ばれていれば、
    # _pending_debugに残した候補情報をこのターンのスナップショットへ引き継ぐ。
    pending = _pending_round.get(game_id)
    if pending is not None:
        # 2026-09-06修正: 棋譜(g.kifu)のactions/turns[].actionには、JS向け
        # ワイヤープロトコル用の辞書形式(_serialize_action)ではなく、
        # game.py/generate_kifu.py/local_viewerが期待する配列形式
        # (_serialize_action_for_kifu)を使う（詳細は_serialize_action_for_kifu()
        # のコメント参照。これが「棋譜の全ての手がパス表示になる」バグの修正）。
        serialized = _serialize_action_for_kifu(action)
        pending["round_actions"][player] = serialized

        debug = _pending_debug.pop(game_id, None)
        if debug is not None and debug["player"] == player and debug["action"] == action:
            candidates = debug["candidates"]
            random_pick = debug["random_pick"]
        else:
            candidates = None
            random_pick = None

        pending["turn_snapshots"].append({
            "player": player,
            "action": serialized,
            "board": _board_snapshot(g),
            "rp": list(g.econ.rp),
            "reserve": {0: list(g.reserve[0]), 1: list(g.reserve[1])},
            "candidates": candidates,
            "random_pick": random_pick,
        })
    else:
        _pending_debug.pop(game_id, None)


def ai_choose_json(game_id, bot_id, player):
    """AIの着手を選ばせ、選ばれたactionだけを返す（軽量版。デバッグ表示なし）。"""
    g = _games[game_id]
    bot = _bots[bot_id]
    actions = g.valid_actions(player)
    action = bot.choose(g, player, actions)
    return json.dumps({"action": _serialize_action(action)})


def ai_choose_with_debug_json(game_id, bot_id, player):
    """AIの着手を選ばせ、選ばれたactionと上位候補＋評価値も返す
    （「AIの候補手ログを表示」チェックボックス用。SearchBot.choose_with_debugへ委譲するだけ）。
    game.py の run() / HeuristicBot.choose_with_debug と同じ辞書形式
    （action/candidates/random_pick）で返ってくるので、そのままJSON化するだけでよい。

    2026-09-06修正: 計算した候補情報を_pending_debugにも残しておく。直後に
    このactionでapply_action()が呼ばれたとき、それを棋譜のturns[].candidatesへ
    引き継ぐため（apply_action()側のコメント参照。以前はここで計算した候補情報が
    棋譜に一切残らず、ダウンロードした棋譜をlocal_viewerで再生すると常に
    「候補ログなし」になってしまっていた）。"""
    g = _games[game_id]
    bot = _bots[bot_id]
    actions = g.valid_actions(player)
    debug_info = bot.debug_choose_candidates(g, player, actions)
    # JS側の「AIの候補手ログを表示」パネル向け（formatActionForLog()がaction.type/
    # .kind/.pos等のプロパティで読むため、辞書形式のままにする必要がある）。
    candidates_for_js = [{"action": _serialize_action(c["action"]), "value": c["value"]}
                         for c in debug_info["candidates"]]
    # 2026-09-06追加: 上のcandidates_for_jsとは別に、棋譜(turn_snapshots[].candidates)
    # 用に配列形式で複製しておく。以前はcandidates_for_jsをそのまま_pending_debug
    # 経由でturn_snapshotsにも書き込んでいたため、local_viewerのactionText(c.action)
    # が候補手についても常に「パス」表示になっていた（apply_action()側の
    # _serialize_action_for_kifu()のコメントも参照）。
    candidates_for_kifu = [{"action": _serialize_action_for_kifu(c["action"]), "value": c["value"]}
                           for c in debug_info["candidates"]]
    _pending_debug[game_id] = {
        "player": player,
        "action": debug_info["action"],
        "candidates": candidates_for_kifu,
        "random_pick": debug_info["random_pick"],
    }
    return json.dumps({
        "action": _serialize_action(debug_info["action"]),
        "candidates": candidates_for_js,
        "randomPick": debug_info["random_pick"],
    })


def resign(game_id, player):
    """2026-09-12追加: play_vs_ai.htmlの投了ボタン用の薄いファサード。
    ロジック自体はgame.Game.resign()に実装済みで、ここではgame_idから
    Gameインスタンスを引いて委譲するだけ（web_api.pyの設計方針どおり）。"""
    g = _games[game_id]
    g.resign(player)


def resolve_round_end(game_id):
    from search_bot_skeleton import resolve_round_end as _resolve
    g = _games[game_id]
    # production_botsはNone: production_phaseは別途JS側からapply_production/
    # ai_choose_productionで呼ぶ（2026-09-06: 戻り値がbase_destroyedだけでなく
    # removed/damage/vp_kill_bonus/rp_wastedも返すようになった。これらはこの
    # ラウンドの棋譜を組み立てるのに必要なため_pending_roundに保存しておき、
    # 生産フェーズ完了後にfinalize_round_kifu()が使う）。
    base_destroyed, removed, damage, vp_kill_bonus, rp_wasted = _resolve(g, None)
    game_over = base_destroyed or g.round_number >= CONFIG["turn_limit_per_player"]
    if game_over and not base_destroyed:
        g.score_and_finish()
    pending = _pending_round.get(game_id)
    if pending is not None:
        pending["removed"] = removed
        pending["damage"] = damage
        pending["vp_kill_bonus"] = vp_kill_bonus
        pending["rp_wasted"] = rp_wasted
        # production_phaseは駒を盤に置かない（reserveへ加えるだけ）ので、盤面の
        # スナップショットはこの時点（戦闘解決直後）のもので確定してよい
        # （game.py Game.production_phase()参照）。
        pending["board_after_combat"] = _board_snapshot(g)
        # 2026-09-06追加: game.py Game.run()と同様、生産フェーズ直前（戦闘解決・
        # 収入計算は終わっているがまだ何も生産していない時点）のRP・持ち駒を
        # 記録しておく。_resolve(g, None)はproduction_bots=Noneのため
        # production_phase()自体を呼ばずにここまで進んでいるので、この時点の
        # g.econ.rp/g.reserveがちょうどその値になっている
        # （local_viewer側で「戦闘解決」と「生産」を別コマとして再生するための
        # データ。finalize_round_kifu()参照）。
        pending["rp_after_combat"] = list(g.econ.rp)
        pending["reserve_after_combat"] = {0: list(g.reserve[0]), 1: list(g.reserve[1])}
    return json.dumps({"baseDestroyed": base_destroyed, "gameOver": game_over})


def finalize_round_kifu(game_id, produced_json="{}"):
    """このラウンドの棋譜エントリ（game.py Game.run()のself.kifu.append(...)と同一
    スキーマ）を組み立ててg.kifuに追記する。2026-09-06追加。

    呼び出しタイミング: JS側でresolve_round_end()を呼んだ後、そのラウンドの
    生産フェーズ（AI・人間の両方）が完了した直後（次のラウンドを開始する前、
    または対局終了(gameOver)が確定した直後）に呼ぶこと。
    ゲーム終了で生産フェーズ自体を行わなかった場合はproduced_jsonを省略
    （"{}"のまま）してよい。

    produced_json: {"0": kind文字列またはnull, "1": kind文字列またはnull} という
    形式のJSON文字列（JSのオブジェクトキーは文字列になるため）。
    """
    g = _games[game_id]
    pending = _pending_round.pop(game_id, None)
    if pending is None:
        # begin_round()を経由せずに呼ばれた等の異常系。棋譜が1ラウンド分
        # 欠けるだけで対局の進行自体には影響しないため、例外を投げず無視する。
        return

    produced_raw = json.loads(produced_json) if produced_json else {}
    produced = {int(p): kind for p, kind in produced_raw.items() if kind}

    removed = pending.get("removed", [])
    damage = pending.get("damage", {})
    vp_kill_bonus = pending.get("vp_kill_bonus", [0, 0])
    rp_wasted = pending.get("rp_wasted", [0, 0])
    board_snapshot = pending.get("board_after_combat")
    if board_snapshot is None:
        board_snapshot = _board_snapshot(g)
    # 2026-09-06追加: 生産フェーズ直前のRP・持ち駒（resolve_round_end()参照）。
    # gameOverでresolve_round_end()自体が呼ばれていない異常系に備え、無ければ
    # 現在値にフォールバックする（この場合rp_after_combat==rpとなるだけで、
    # local_viewer側の「戦闘解決／生産」2ステップ表示は壊れない）。
    rp_after_combat = pending.get("rp_after_combat")
    if rp_after_combat is None:
        rp_after_combat = list(g.econ.rp)
    reserve_after_combat = pending.get("reserve_after_combat")
    if reserve_after_combat is None:
        reserve_after_combat = {0: list(g.reserve[0]), 1: list(g.reserve[1])}

    g.kifu.append({
        "round": g.round_number,
        "actions": pending["round_actions"],
        "turns": pending["turn_snapshots"],
        "damage": {f"{pos[0]},{pos[1]}": round(d, 1) for pos, d in damage.items()},
        "deaths": [{"pos": list(pos), "owner": owner, "kind": kind} for pos, owner, kind in removed],
        "board": board_snapshot,
        "rp": list(g.econ.rp),
        "vp": list(g.econ.cumulative_vp),
        "reserve": {0: list(g.reserve[0]), 1: list(g.reserve[1])},
        "rp_after_combat": rp_after_combat,
        "reserve_after_combat": reserve_after_combat,
        "rp_wasted": list(rp_wasted),
        "produced": produced,
        "vp_kill_bonus": {p: v for p, v in enumerate(vp_kill_bonus) if v > 0},
    })


def kifu_json(game_id, bot0_label="you", bot1_label="AI"):
    """対局終了後、g.kifu（Gameオブジェクトが対局中ずっと蓄積している棋譜。
    generate_kifu.pyのg.run(bots)経由でも、apply_action/resolve_round_end/
    finalize_round_kifu()を手動で呼び出すこのAPI越しの対局でも、同じGame.kifuに
    同じ形式で追記される。2026-09-06修正: 以前はこのAPI越しの対局ではどこも
    g.kifuに追記していなかったため、ここが常に空リストを返し、ダウンロードした
    棋譜がbuild_tools.py viewerで再生できないバグがあった。原因は
    web_api.pyがGame.run()を一切呼ばない構成なのに、g.kifuはGame.run()の中でしか
    構築されないことだった。finalize_round_kifu()を新設し、JS側がラウンドの
    生産フェーズ完了後に呼ぶことで、このAPI経由の対局でもg.kifuが正しく
    埋まるようにした）を、generate_kifu.pyが書き出す
    kifu_data_<タイムスタンプ>.jsonと全く同じスキーマ（1要素のリスト）で返す。
    JS側はこれをそのままBlobにしてダウンロードさせるだけでよく、
    build_tools.pyのビューアーやanalyze_tuning_run.py系のツールにもそのまま渡せる。

    bot0_label/bot1_labelはgenerate_kifu.pyのbot0_label/bot1_labelと同じ用途の
    表示ラベル（例: "human", "AI(champion_weights)"）。JS側でhumanPlayerと
    対戦相手の重み設定選択（BUILD_OPTIONS.champion_history）から組み立てて渡す。
    """
    g = _games[game_id]
    result = {
        "winner": g.winner,
        "reason": g.win_reason,
        "rounds": g.round_number,
        "cumulative_vp": list(g.econ.cumulative_vp),
        # 2026-09-12追加: 投了機能用（local_viewer側で「誰が投了したか」を
        # 表示するために必要。reason=="resigned"のときのみ意味を持つ）。
        "resigned_player": getattr(g, "resigned_player", None),
    }
    game_data = {
        "seed": None,  # 人間が操作した対局のため、再現用のseedは存在しない
        "result": result,
        "kifu": getattr(g, "kifu", []),
        "base_positions": CONFIG["base_positions"],
        "vp_spots": CONFIG["vp_spots"],
        "rp_spots": CONFIG["rp_spots"],
        "midgame_spots": CONFIG["midgame_spots"],
        "board_size": CONFIG["board_size"],
        "bot0_label": bot0_label,
        "bot1_label": bot1_label,
        # 2026-09-06追加: local_viewer/play_vs_aiの表示統一のため、駒種ごとの
        # コスト・HP・ATK等（CONFIG["pieces"]）も棋譜に含める。local_viewer側は
        # これを使って持ち駒パネルをplay_vs_aiの生産選択パネルと同じ見た目
        # （駒名＋コスト＋HP/ATK）で表示する。このキーを持たない古い棋譜でも
        # 単純な駒名リスト表示にフォールバックするだけでエラーにはならない。
        "pieces": CONFIG["pieces"],
    }
    return json.dumps([game_data], ensure_ascii=False)


def affordable_production_kinds_json(game_id, player):
    g = _games[game_id]
    kinds = [k for k in CONFIG["pieces"] if k != "本拠"
             and CONFIG["pieces"][k]["produce_cost"] <= g.econ.rp[player]]
    return json.dumps(kinds)


def apply_production(game_id, player, kind):
    g = _games[game_id]
    if kind is None:
        return None
    cfg = CONFIG["pieces"][kind]
    if cfg["produce_cost"] > g.econ.rp[player]:
        return None
    g.econ.rp[player] -= cfg["produce_cost"]
    g.reserve[player].append(kind)
    return kind


def ai_choose_production(game_id, bot_id, player):
    g = _games[game_id]
    bot = _bots[bot_id]
    kinds = [k for k in CONFIG["pieces"] if k != "本拠"
             and CONFIG["pieces"][k]["produce_cost"] <= g.econ.rp[player]]
    if not kinds:
        return None
    return bot.choose_production(g, player, kinds)

