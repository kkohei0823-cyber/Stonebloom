# -*- coding: utf-8 -*-
"""
champion_tools.py
==========================================================
2026-08-22統合: champion_battle.py と import_champion_log.py を1本化した。
どちらも「champion_history/（tune_balance_search.pyの案A、gen_XXXX.json）を
読み書きする」という同じデータを対象にした専用ツールだったため、サブコマンド
形式にまとめた。

  champion_battle.py         → サブコマンド list / show / battle / all-pairs
  import_champion_log.py     → サブコマンド import-log

前提:
  - tune_balance_search.py / config.py / game.py / search_bot_skeleton.py と
    同じフォルダに置く
  - tune_balance_search.py を --champion-games > 0 で一度以上実行し、
    champion_history/ に gen_XXXX.json が最低1つ保存されていること
    （通常は gen_0000.json = 初代が必ず存在するはず）

使い方:
  # 保存されている世代の一覧と、各世代の主要スコアを確認する
  $ python3 champion_tools.py list

  # ある世代の診断指標（昇格時点のmetrics）を丸ごと確認する
  $ python3 champion_tools.py show 50

  # 世代0（初代）と世代50（最新）を100局戦わせる
  $ python3 champion_tools.py battle --gen-a 0 --gen-b 50 --games 100

  # 複数世代の総当たり戦（相性のじゃんけん構造がないか確認する用途）
  $ python3 champion_tools.py all-pairs --gens 0,10,20,30,40,50 --games 30

  # 【2026-08-23新規】先後勝率差チェック: 世代50のミラー戦で先手/後手の勝率差を見る
  #（ルールそのものの手番有利不利を、AIの強さと切り離して判定できる）
  $ python3 champion_tools.py firstmover --gen-a 50 --games 60

  # 【2026-08-23新規】トーナメント戦: 総当たり戦の結果を順位表にし、
  # 世代番号と強さの相関・非推移的な循環（相性じゃんけん）を自動検出する
  $ python3 champion_tools.py tournament --gens 51,52,53,54,55,56,57 --games 30 --save-results t.json

  # 【2026-08-23新規】重みタイプ（プレイスタイル）の検出: 上のtournament結果を
  # クラスタリングし、クラスタ間に相性じゃんけん構造がないか調べる
  $ python3 champion_tools.py archetypes --tournament-results t.json --k 3

  # 過去のテキストログから世代を復元する（まずは--dry-runで確認）
  $ python3 champion_tools.py import-log --log old_run_log.txt --dry-run
  $ python3 champion_tools.py import-log --log old_run_log.txt

旧スクリプトからの移行:
  旧 `python3 champion_battle.py --list`        → `python3 champion_tools.py list`
  旧 `python3 champion_battle.py --gen-a A --gen-b B ...`
                                                  → `python3 champion_tools.py battle --gen-a A --gen-b B ...`
  旧 `python3 champion_battle.py --all-pairs ...` → `python3 champion_tools.py all-pairs ...`
  旧 `python3 champion_battle.py --show N`       → `python3 champion_tools.py show N`
  旧 `python3 import_champion_log.py --log F ...` → `python3 champion_tools.py import-log --log F ...`
  （フラグ自体の意味・既定値は変更していない。サブコマンド化しただけ）

ゲームエンジン本体（config.py / game.py / search_bot_skeleton.py）と、
1局を非対称対戦させる仕組み（ExecutorHolder / evaluate_vs_champion /
_promotion_is_significant）は tune_balance_search.py のものをそのまま
importして再利用する（対戦ロジックを二重実装すると、片方だけ直された
ときに結果がずれるため）。
"""

import argparse
import ast
import csv
import json
import math
import os
import re
import sys
from concurrent.futures import TimeoutError as FutureTimeoutError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config  # noqa: E402
import weights  # noqa: E402  2026-08-25: resolve_weightsはweights.pyに分離した
from tune_balance_search import (  # noqa: E402
    ExecutorHolder,
    evaluate_vs_champion,
    _promotion_is_significant,
    _resolve_weight_overrides,
    save_champion_history_entry,
    GAME_TIMEOUT_SECONDS_DEFAULT,
    _run_one_asymmetric_game,
)

GEN_FILE_RE = re.compile(r"^gen_(\d+)\.json$")


def default_history_dir():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "champion_history")


# ------------------------------------------------------------
# 共通: champion_history/ の読み書き（旧champion_battle.py由来）
# ------------------------------------------------------------

def list_generations(history_dir):
    """history_dir内のgen_XXXX.jsonを走査し、{generation: filepath}を返す
    （generation昇順）。"""
    if not os.path.isdir(history_dir):
        return {}
    found = {}
    for name in os.listdir(history_dir):
        m = GEN_FILE_RE.match(name)
        if m:
            found[int(m.group(1))] = os.path.join(history_dir, name)
    return dict(sorted(found.items()))


def load_generation(history_dir, generation):
    path = os.path.join(history_dir, f"gen_{generation:04d}.json")
    if not os.path.exists(path):
        available = sorted(list_generations(history_dir).keys())
        available_desc = str(available) if available else (
            "(なし。まだtune_balance_search.pyを案A対応版で実行していない可能性があります)"
        )
        raise SystemExit(
            f"[エラー] 世代{generation}のスナップショットが見つかりません ({path})。\n"
            f"保存済みの世代: {available_desc}"
        )
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _parse_gen_list(s):
    """世代番号指定文字列をパースしてintのリスト（重複排除・昇順）を返す。

    対応フォーマット:
      - カンマ区切り: "50,52,55"
      - 範囲指定（両端含む）: "50-57"
      - 混在: "0,10-20,30,45-50"
    "-" は範囲区切りとしてのみ使う想定のため、負の世代番号はサポートしない
    （このツールで負の世代番号は使わないため問題ない）。
    """
    if s is None:
        return None
    gens = set()
    for part in s.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo_s, sep, hi_s = part.partition("-")
            lo_s, hi_s = lo_s.strip(), hi_s.strip()
            if not lo_s or not hi_s:
                raise SystemExit(f"[エラー] 世代範囲指定が不正です: '{part}'（例: 50-57）")
            try:
                lo, hi = int(lo_s), int(hi_s)
            except ValueError:
                raise SystemExit(f"[エラー] 世代範囲指定が不正です: '{part}'（例: 50-57）")
            if lo > hi:
                raise SystemExit(f"[エラー] 世代範囲指定の開始が終了より大きいです: '{part}'")
            gens.update(range(lo, hi + 1))
        else:
            try:
                gens.add(int(part))
            except ValueError:
                raise SystemExit(f"[エラー] 世代番号として解釈できません: '{part}'")
    return sorted(gens)


def _fmt_metric(metrics, key, fmt="{:.2f}"):
    if not metrics or key not in metrics or metrics[key] is None:
        return "-"
    try:
        return fmt.format(metrics[key])
    except (ValueError, TypeError):
        return str(metrics[key])


# ------------------------------------------------------------
# サブコマンド: list（旧 champion_battle.py --list）
# ------------------------------------------------------------

def cmd_list(args):
    """歴代チャンピオンの一覧と、その昇格時点のスコア（診断指標）を表示する。
    「歴代の評価スコアを確認したい」という要望に対応する機能。"""
    history_dir = args.history_dir
    generations = list_generations(history_dir)
    if not generations:
        print(f"[情報] {history_dir} に保存済みの世代がありません。")
        return

    rows = []
    for gen, path in generations.items():
        data = load_generation(history_dir, gen)
        metrics = data.get("metrics") or {}
        rows.append({
            "generation": gen,
            "trial_number": data.get("trial_number"),
            "source": data.get("source"),
            "saved_at": data.get("saved_at"),
            "win_rate_vs_champion": _fmt_metric(metrics, "win_rate_vs_champion"),
            "win_balance_info": _fmt_metric(metrics, "win_balance_info"),
            "diversity_ratio": _fmt_metric(metrics, "diversity_ratio"),
            "rp_spot_neglect_penalty": _fmt_metric(metrics, "rp_spot_neglect_penalty"),
            "premature_decisive_rate": _fmt_metric(metrics, "premature_decisive_rate"),
        })

    header = ["世代", "trial", "由来", "vs前champion勝率", "先後差", "多様性",
              "経済放置pen", "早期決着率", "保存日時"]
    widths = [4, 6, 16, 16, 8, 8, 12, 10, 20]
    print("  ".join(h.ljust(w) for h, w in zip(header, widths)))
    print("-" * (sum(widths) + 2 * (len(widths) - 1)))
    for r in rows:
        print("  ".join(str(v).ljust(w) for v, w in zip(
            [r["generation"], r["trial_number"], r["source"], r["win_rate_vs_champion"],
             r["win_balance_info"], r["diversity_ratio"], r["rp_spot_neglect_penalty"],
             r["premature_decisive_rate"], r["saved_at"]], widths)))

    if args.csv:
        with open(args.csv, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        print(f"\n{args.csv} にCSVとして書き出しました。")

    print(
        "\n注意: ここでの各指標は「その世代が“前championを破って昇格した瞬間”に"
        "1回だけ記録されたスナップショット」であり、後の世代と直接対戦させた場合の"
        "勝率ではない（例えば世代3のvs前champion勝率は「世代3 vs 世代2」の結果で、"
        "「世代3 vs 世代50」ではない）。世代同士を直接比較したい場合は"
        "battle または all-pairs で実際に対戦させること。"
    )


# ------------------------------------------------------------
# サブコマンド: show（旧 champion_battle.py --show）
# ------------------------------------------------------------

def cmd_show(args):
    """1世代分の診断指標(metrics)を丸ごとダンプする。"""
    data = load_generation(args.history_dir, args.generation)
    print(f"世代{data['generation']} (source={data.get('source')}, "
          f"trial={data.get('trial_number')}, saved_at={data.get('saved_at')})")
    metrics = data.get("metrics")
    if metrics:
        print(json.dumps(metrics, indent=2, ensure_ascii=False))
    else:
        print("[注意] この世代には診断指標が記録されていません"
              "（初代ブートストラップ、または旧バージョンからの補完保存の場合に起こる）。")
    if data.get("config_overrides"):
        print("\n昇格時点の駒パラメータ上書き (config_overrides):")
        print(json.dumps(data["config_overrides"], indent=2, ensure_ascii=False))


# ------------------------------------------------------------
# サブコマンド: battle / all-pairs（旧 champion_battle.py --gen-a/--gen-b/--all-pairs）
# ------------------------------------------------------------

def _resolve_ruleset(args, data_a, data_b):
    """対戦に使う駒パラメータ(config_overrides)を決める。
    2世代の間で駒パラメータのチューニング結果(--tune pieces/both時)が
    異なる場合、対戦は必ずどちらか一方（または素のconfig.py）の
    ルールに統一する必要がある（対局中に片方のプレイヤーだけ駒コストが
    違う、ということはできないため）。"""
    ov_a = data_a.get("config_overrides") or []
    ov_b = data_b.get("config_overrides") or []
    if ov_a != ov_b and args.ruleset != "none":
        print(
            f"[注意] 世代{data_a['generation']}と世代{data_b['generation']}は"
            f"昇格時点の駒パラメータ(config_overrides)が異なります。"
            f"--ruleset {args.ruleset} により、"
            f"{'世代' + str(data_a['generation']) if args.ruleset == 'a' else '世代' + str(data_b['generation'])}"
            f"側のルールに統一して対戦します。"
        )
    if args.ruleset == "a":
        return ov_a
    if args.ruleset == "b":
        return ov_b
    return []


def _run_pair(executor_holder, history_dir, gen_a, gen_b, args):
    data_a = load_generation(history_dir, gen_a)
    data_b = load_generation(history_dir, gen_b)
    config_overrides = _resolve_ruleset(args, data_a, data_b)

    result = evaluate_vs_champion(
        executor_holder, data_a["weights"], data_b["weights"], config_overrides,
        args.games, args.seed, args.depth, args.candidate_k,
        game_timeout=args.game_timeout,
    )
    # evaluate_vs_championは「candidate側(=gen_a)の勝率」を返す設計をそのまま使う。
    significant = _promotion_is_significant(
        result["wins"], result["n_games"], threshold=0.5, alpha=args.alpha
    )
    return data_a, data_b, result, significant


def cmd_battle(args):
    history_dir = args.history_dir
    executor_holder = ExecutorHolder(max_workers=args.workers)
    try:
        data_a, data_b, result, significant = _run_pair(
            executor_holder, history_dir, args.gen_a, args.gen_b, args
        )
    finally:
        executor_holder.shutdown(wait=False)

    print(f"世代{args.gen_a} (source={data_a.get('source')}) "
          f"vs 世代{args.gen_b} (source={data_b.get('source')})")
    print(f"{args.games}局中: 世代{args.gen_a} {result['wins']}勝"
          f"{result['losses']}敗{result['draws']}分 "
          f"(勝率={result['win_rate']:.3f})")
    if significant:
        verdict = (f"世代{args.gen_a}が世代{args.gen_b}より統計的に有意に強い"
                   if result["win_rate"] > 0.5 else
                   f"世代{args.gen_b}が世代{args.gen_a}より統計的に有意に強い")
    else:
        verdict = "有意差なし（この対戦数では実力差を判定できない）"
    print(f"判定 (alpha={args.alpha}): {verdict}")


def cmd_all_pairs(args):
    history_dir = args.history_dir
    if args.gens:
        gens = _parse_gen_list(args.gens)
    else:
        gens = sorted(list_generations(history_dir).keys())
    if len(gens) < 2:
        raise SystemExit("[エラー] all-pairs には2世代以上必要です。--gens で指定するか、"
                          "champion_historyに複数世代を用意してください。")

    executor_holder = ExecutorHolder(max_workers=args.workers)
    matrix = {g: {} for g in gens}
    try:
        for i, ga in enumerate(gens):
            for gb in gens[i + 1:]:
                data_a, data_b, result, significant = _run_pair(
                    executor_holder, history_dir, ga, gb, args
                )
                matrix[ga][gb] = (result["win_rate"], significant)
                matrix[gb][ga] = (1.0 - result["win_rate"], significant)
                mark = "*" if significant else " "
                print(f"世代{ga:>4} vs 世代{gb:>4}: "
                      f"世代{ga}勝率={result['win_rate']:.3f}{mark} "
                      f"({result['wins']}勝{result['losses']}敗{result['draws']}分)")
    finally:
        executor_holder.shutdown(wait=False)

    print("\n勝率マトリクス（行が世代Xの、列の世代に対する勝率。*は有意差あり）:")
    header = "        " + "  ".join(f"g{g:<4}" for g in gens)
    print(header)
    for ga in gens:
        cells = []
        for gb in gens:
            if ga == gb:
                cells.append("  -   ")
                continue
            wr, sig = matrix[ga][gb]
            cells.append(f"{wr:.2f}{'*' if sig else ' '} ")
        print(f"g{ga:<6}  " + " ".join(cells))

    print(
        "\n見方: もし A>B, B>C だが C>A のような非推移的な関係（じゃんけん）が"
        "*付き（有意差あり）で見つかった場合、相性関係で世代交代がぐるぐる回っている"
        "可能性が高いことの直接証拠になる。単なるノイズなら、このマトリクスは"
        "概ね世代番号が新しいほど勝率が高い、という単調な傾向に近くなるはず。"
    )


# ------------------------------------------------------------
# サブコマンド: firstmover（2026-08-23新規: 先後勝率差チェック）
# ------------------------------------------------------------
#
# 用途: ルールの手番順健全性を判定する。同じ重み同士（ミラー戦）で先手/後手を
# 均等に入れ替え、先手(p0)/後手(p1)勝率の差を純粋な手番由来の有利不利とみなす。
# --gen-bで異なる世代を指定すると「強さの差」と混ざるため使わないこと（battleサブコマンドへ）。

def evaluate_first_mover(executor_holder, weights_a, weights_b, config_overrides,
                          n_games, seed_base, depth, candidate_k, game_timeout):
    """先手(p0)・後手(p1)を均等に入れ替えて対戦させ、「座席（先手/後手という立場）」
    ベースで勝率を集計する。

    2026-08-24修正: 以前は「Aが先手のときのAの勝率」と「Aが後手のときのAの勝率」を
    別々の50局サブサンプルとして集計しており、これらは別サンプルからの推定値のため
    合計が100%にならなかった（引き分け0でも100%からずれる）。
    ミラー戦（weights_a == weights_b、この関数の主用途）では、先手・後手どちらの
    座席にAが座ってもBが座っても中身は同じ重みなので、「Aが先手のときの勝率」は
    「先手という座席の勝率」そのものと等価である。そこで全n_games局をまとめて
    「先手が勝ったか／後手が勝ったか」で集計し直すことで、
    (1) 100局すべてを使った1つの推定値になり統計的により効率的になり、
    (2) 引き分けがなければ定義上必ず合計100%になる（同一局の勝者を先手/後手で
        振り分けているだけなので）。
    非ミラー戦（--gen-bで異なる世代を指定した場合）では、座席ベースの集計は
    「先手世代・後手世代のどちらであれ先に動いた側が勝つ率」という意味になり、
    「特定の世代Aが先手のときにどれだけ勝てるか」という質問には直接答えない
    （Aが先手の50局とBが先手の50局が混ざるため）。そのためこの場合は座席ベースの
    集計に加えて、従来通りの「Aが先手のときのA視点勝率」「Aが後手のときのA視点
    勝率」も参考値として別途返す（こちらは意図的に合計100%にならない値）。
    """
    args_list = []
    for i in range(n_games):
        a_is_p0 = (i % 2 == 0)
        args_list.append((weights_a, weights_b, config_overrides,
                           seed_base + i, depth, candidate_k, a_is_p0))

    futures = [executor_holder.executor.submit(_run_one_asymmetric_game, a) for a in args_list]
    p0_seat_wins = p1_seat_wins = draws = timeout_count = 0
    cand_p0_wins = cand_p0_games = cand_p1_wins = cand_p1_games = 0
    for a, fut in zip(args_list, futures):
        a_is_p0 = a[-1]
        try:
            r = fut.result(timeout=game_timeout)
        except FutureTimeoutError:
            timeout_count += 1
            r = {"candidate_won": False, "draw": False}

        # 参考値: 従来通りの「Aが先手/後手それぞれのときのA視点勝率」
        if a_is_p0:
            cand_p0_games += 1
            cand_p0_wins += 1 if r["candidate_won"] else 0
        else:
            cand_p1_games += 1
            cand_p1_wins += 1 if r["candidate_won"] else 0

        if r["draw"]:
            draws += 1
            continue
        # 座席ベース集計（candidate_won × a_is_p0 の組み合わせでp0/p1勝敗を判定）
        p0_seat_won = r["candidate_won"] if a_is_p0 else (not r["candidate_won"])
        if p0_seat_won:
            p0_seat_wins += 1
        else:
            p1_seat_wins += 1

    if timeout_count:
        print(f"[警告] {n_games}局中{timeout_count}局がタイムアウトしました。"
              f"次の実行のためexecutorを作り直します。", file=sys.stderr)
        executor_holder.recycle()

    decided_games = n_games - draws
    return {
        # 主指標: 座席（先手/後手）ベース。引き分けがなければ合計100%になる。
        "p0_seat_wins": p0_seat_wins, "p1_seat_wins": p1_seat_wins,
        "decided_games": decided_games, "n_games": n_games, "draws": draws,
        "p0_win_rate": (p0_seat_wins / decided_games) if decided_games else 0.0,
        "p1_win_rate": (p1_seat_wins / decided_games) if decided_games else 0.0,
        "p0_games": decided_games, "p1_games": decided_games,  # 表示用: 引き分けを除いた分母
        "p0_wins": p0_seat_wins, "p1_wins": p1_seat_wins,
        # 参考値: 非ミラー戦のときだけ意味を持つ、Aが各座席のときのA視点勝率
        # （こちらは意図的に合計100%にならない）
        "candidate_p0_wins": cand_p0_wins, "candidate_p0_games": cand_p0_games,
        "candidate_p1_wins": cand_p1_wins, "candidate_p1_games": cand_p1_games,
        "candidate_p0_win_rate": (cand_p0_wins / cand_p0_games) if cand_p0_games else 0.0,
        "candidate_p1_win_rate": (cand_p1_wins / cand_p1_games) if cand_p1_games else 0.0,
    }


def _run_firstmover_one(history_dir, gen_a, gen_b_raw, args, executor_holder):
    gen_b = gen_b_raw if gen_b_raw is not None else gen_a
    is_mirror = (gen_a == gen_b)

    data_a = load_generation(history_dir, gen_a)
    data_b = load_generation(history_dir, gen_b)
    config_overrides = (data_a.get("config_overrides") or []) if is_mirror \
        else _resolve_ruleset(args, data_a, data_b)

    result = evaluate_first_mover(
        executor_holder, data_a["weights"], data_b["weights"], config_overrides,
        args.games, args.seed, args.depth, args.candidate_k,
        game_timeout=args.game_timeout,
    )

    label = f"世代{gen_a}" if is_mirror else f"世代{gen_a} vs 世代{gen_b}"
    print(f"{label} ／ {args.games}局中{result['decided_games']}局が決着"
          f"（引き分け{result['draws']}局）")
    print(f"  先手(p0)勝率: {result['p0_win_rate']:.3f} "
          f"({result['p0_wins']}/{result['p0_games']})")
    print(f"  後手(p1)勝率: {result['p1_win_rate']:.3f} "
          f"({result['p1_wins']}/{result['p1_games']})")

    if is_mirror:
        sig = _promotion_is_significant(result["p0_wins"], result["p0_games"],
                                         threshold=0.5, alpha=args.alpha)
        print("  ミラー戦（同一重み同士）のため、この先手/後手の勝率差は"
              "純粋にルール（手番順）由来の有利不利とみなせます。")
        if sig and result["p0_win_rate"] > 0.5:
            verdict = "先手側に統計的に有意な手番有利があります → ルールの見直しを検討してください。"
        elif sig and result["p0_win_rate"] < 0.5:
            verdict = "後手側に統計的に有意な手番有利があります → ルールの見直しを検討してください。"
        else:
            verdict = "先手/後手で統計的に有意な差は検出されませんでした（この対戦数では）。"
        print(f"  判定 (alpha={args.alpha}): {verdict}")
    else:
        print(f"  [注意] 世代{gen_a}と世代{gen_b}は重みが異なるため、上記の"
              "先手/後手勝率は「先に動いた側が勝つ率」であり、"
              "「強さの差」と「手番の有利不利」が混在しています。")
        print(f"  参考（世代{gen_a}視点）: 先手のとき{result['candidate_p0_win_rate']:.3f} "
              f"({result['candidate_p0_wins']}/{result['candidate_p0_games']})"
              f" / 後手のとき{result['candidate_p1_win_rate']:.3f} "
              f"({result['candidate_p1_wins']}/{result['candidate_p1_games']})"
              "  ※こちらは別々のサブサンプルからの推定のため合計は100%になりません")
        print("  純粋な手番差だけを見たい場合は --gen-b を省略する"
              "（ミラー戦になる）か、--gen-a と同じ値を指定してください。")


def cmd_firstmover(args):
    history_dir = args.history_dir
    gens_a = _parse_gen_list(args.gen_a)
    gens_b = _parse_gen_list(args.gen_b) if args.gen_b is not None else None

    if gens_b is None:
        pairs = [(g, None) for g in gens_a]
    elif len(gens_b) == 1:
        pairs = [(g, gens_b[0]) for g in gens_a]
    elif len(gens_b) == len(gens_a):
        pairs = list(zip(gens_a, gens_b))
    else:
        raise SystemExit(
            f"[エラー] --gen-b の指定数（{len(gens_b)}）が --gen-a の指定数"
            f"（{len(gens_a)}）と一致しません。--gen-b は省略・単一世代・"
            f"--gen-aと同数のいずれかで指定してください。"
        )

    executor_holder = ExecutorHolder(max_workers=args.workers)
    try:
        for i, (gen_a, gen_b) in enumerate(pairs):
            if i > 0:
                print()
            _run_firstmover_one(history_dir, gen_a, gen_b, args, executor_holder)
    finally:
        executor_holder.shutdown(wait=False)


# ------------------------------------------------------------
# サブコマンド: tournament（2026-08-23新規: トーナメント戦・順位付け）
# ------------------------------------------------------------
#
# 用途: all-pairsと同じ総当たり戦に、①順位表のスピアマン順位相関、②非推移的循環
# (A>B>C>A)の自動検出を追加。--save-resultsでJSON保存しarchetypesサブコマンドで再利用可。

def _run_tournament_core(history_dir, gens, args, executor_holder=None):
    """gens内の全ペアを総当たりで対戦させ、{(gen_a, gen_b): 結果dict}を返す。
    gen_a, gen_bはgens内での出現順（i<jのペアのみ）。"""
    own_executor = executor_holder is None
    if own_executor:
        executor_holder = ExecutorHolder(max_workers=args.workers)
    pairwise = {}
    try:
        for i, ga in enumerate(gens):
            for gb in gens[i + 1:]:
                data_a, data_b, result, significant = _run_pair(
                    executor_holder, history_dir, ga, gb, args
                )
                pairwise[(ga, gb)] = {
                    "wins_a": result["wins"], "losses_a": result["losses"],
                    "draws": result["draws"], "games": result["n_games"],
                    "win_rate_a": result["win_rate"], "significant": significant,
                }
                mark = "*" if significant else " "
                print(f"世代{ga:>4} vs 世代{gb:>4}: "
                      f"世代{ga}勝率={result['win_rate']:.3f}{mark} "
                      f"({result['wins']}勝{result['losses']}敗{result['draws']}分)")
    finally:
        if own_executor:
            executor_holder.shutdown(wait=False)
    return pairwise


def _tournament_standings(gens, pairwise):
    """各世代の総合成績（全対戦を合算した勝敗・勝率）を計算し、勝率降順で返す。"""
    totals = {g: {"wins": 0, "losses": 0, "draws": 0} for g in gens}
    for (ga, gb), r in pairwise.items():
        totals[ga]["wins"] += r["wins_a"]
        totals[ga]["losses"] += r["losses_a"]
        totals[ga]["draws"] += r["draws"]
        totals[gb]["wins"] += r["losses_a"]
        totals[gb]["losses"] += r["wins_a"]
        totals[gb]["draws"] += r["draws"]
    standings = []
    for g in gens:
        t = totals[g]
        decided = t["wins"] + t["losses"]
        win_rate = (t["wins"] / decided) if decided else 0.5
        standings.append({"generation": g, "win_rate": win_rate, **t})
    standings.sort(key=lambda s: s["win_rate"], reverse=True)
    return standings


def _beats_graph(gens, pairwise, sig_only=False):
    """ノード=世代、辺=「多数決で勝っている」の有向グラフを{gen: set(勝っている相手)}で返す。
    sig_only=Trueなら二項検定で有意差ありと判定されたペアのみを辺として扱う。"""
    beats = {g: set() for g in gens}
    for (ga, gb), r in pairwise.items():
        if sig_only and not r["significant"]:
            continue
        if r["win_rate_a"] > 0.5:
            beats[ga].add(gb)
        elif r["win_rate_a"] < 0.5:
            beats[gb].add(ga)
    return beats


def _find_cycles(beats):
    """beats（{node: set(勝っている相手)}）から3ノードの非推移的循環
    （A>B>C>A）を総当たりで検出する（対象ノード数が数十程度までを想定した
    素朴な実装）。同じ循環を3方向から重複検出しないようfrozensetで重複排除する。"""
    found = {}
    for i in beats:
        for j in beats[i]:
            for l in beats.get(j, ()):
                if l != i and i in beats.get(l, ()):
                    key = frozenset((i, j, l))
                    if key not in found:
                        found[key] = (i, j, l)
    return list(found.values())


def _spearman(xs, ys):
    """依存ライブラリなしのスピアマン順位相関係数（タイは平均順位で処理する簡易版）。"""
    def ranks(vals):
        order = sorted(range(len(vals)), key=lambda idx: vals[idx])
        r = [0.0] * len(vals)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
                j += 1
            avg_rank = (i + j) / 2.0 + 1
            for m in range(i, j + 1):
                r[order[m]] = avg_rank
            i = j + 1
        return r

    n = len(xs)
    if n < 2:
        return None
    rx, ry = ranks(xs), ranks(ys)
    d2 = sum((a - b) ** 2 for a, b in zip(rx, ry))
    return 1 - (6 * d2) / (n * (n * n - 1))


def cmd_tournament(args):
    history_dir = args.history_dir
    if args.gens:
        gens = _parse_gen_list(args.gens)
    else:
        gens = sorted(list_generations(history_dir).keys())
    if len(gens) < 3:
        raise SystemExit("[エラー] tournament には3世代以上を指定してください（--gens）。")

    print(f"=== トーナメント戦（総当たり形式）: 世代 {gens} ===\n")
    pairwise = _run_tournament_core(history_dir, gens, args)
    standings = _tournament_standings(gens, pairwise)

    print("\n順位表（総合勝率順）:")
    print(f"{'順位':>4} {'世代':>6} {'勝':>4} {'敗':>4} {'分':>4} {'勝率':>7}")
    for rank, s in enumerate(standings, start=1):
        print(f"{rank:>4} {s['generation']:>6} {s['wins']:>4} {s['losses']:>4} "
              f"{s['draws']:>4} {s['win_rate']:>7.3f}")

    by_gen = sorted(standings, key=lambda s: s["generation"])
    rho = _spearman([s["generation"] for s in by_gen], [s["win_rate"] for s in by_gen])
    print("\n世代番号と総合勝率の順位相関(Spearman rho): "
          + (f"{rho:.3f}" if rho is not None else "計算不可（世代数不足）"))
    if rho is not None:
        if rho > 0.6:
            note = "世代が新しいほど強い、という健全な右肩上がりの傾向が見られます。"
        elif rho < -0.3:
            note = "世代が新しいほどむしろ弱くなっている、逆転した傾向が見られます（要調査）。"
        else:
            note = "世代番号と強さの間に明確な単調傾向は見られません（相性じゃんけんの可能性）。"
        print(f"  → {note}")

    beats_all = _beats_graph(gens, pairwise, sig_only=False)
    beats_sig = _beats_graph(gens, pairwise, sig_only=True)
    cycles_all = _find_cycles(beats_all)
    cycles_sig_keys = {frozenset(c) for c in _find_cycles(beats_sig)}

    print("\n非推移的な循環（じゃんけん構造）の検出:")
    if cycles_all:
        for i, j, l in cycles_all:
            sig_mark = " (有意差あり)" if frozenset((i, j, l)) in cycles_sig_keys \
                else " (有意差なし・参考程度)"
            print(f"  世代{i} > 世代{j} > 世代{l} > 世代{i}{sig_mark}")
    else:
        print("  検出されませんでした（この世代セットでは、勝敗が推移的でした）。")

    if args.save_results:
        payload = {
            "gens": gens,
            "pairwise": {f"{a}-{b}": r for (a, b), r in pairwise.items()},
            "standings": standings,
        }
        with open(args.save_results, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, ensure_ascii=False)
        print(f"\n結果を保存しました: {args.save_results}"
              f"（archetypesサブコマンドの --tournament-results で再利用できます）")


# ------------------------------------------------------------
# サブコマンド: archetypes（2026-08-23新規: 重みタイプ（プレイスタイル）の検出）
# ------------------------------------------------------------
#
# 用途: tournamentで相性じゃんけんが疑われた場合の分析ツール。生の重みキーを
# 5つの「スタイル軸」に集約し、軸空間上でk-meansクラスタリングして対戦成績を集計する。
#
# 注意: STYLE_AXESは仮の仮説であり実際の支配軸と一致する保証はない。本コマンドの
# 価値は「非推移的循環の有無を機械的に確認できる土台」にあり、軸の妥当性は
# 出力を見て人間が判断しSTYLE_AXESを書き換えて再実行する運用を想定。

STYLE_AXES = {
    "攻撃・キル志向": [("attack", 1), ("kill_bonus", 1), ("advance", 1),
                    ("vp_spot_kill_bonus", 1), ("rp_spot_kill_bonus", 1)],
    "VP速攻志向": [("vp_star", 1), ("vp_tengen", 1)],
    "経済・持久志向": [("rp_spot", 1), ("engineer_econ_bonus", 1),
                    ("midgame_spot", 1), ("efficiency", 1)],
    # 2026-08-27: 符号統一方針によりincoming_damage/danger/exposure/base_defense
    # の生値（HEURISTIC_WEIGHTS上の値）が「常に0以上の大きさ」に変わったため、
    # これらのキーに対する軸係数の符号を反転した（このツールが測る意味自体は
    # 変更前と同一になるようにするための機械的な追従。STYLE_AXES自体の妥当性は
    # 従来通り出力を見て人間が判断すること）。
    "慎重・防御志向": [("incoming_damage", 1), ("danger", 1),
                    ("base_defense", -1), ("base_hp_weight", 1), ("support", 1)],
    "本拠特攻抑制": [("base_pressure", 1), ("exposure", -1)],
}


def _zscore_matrix(gens, weights_by_gen, keys):
    """gens x keysの値を、キーごとに世代間でz-score化して返す
    （{generation: {key: zscore}}）。標準偏差が0に近いキーは全世代0.0にする
    （そのキーで世代間の差が実質ない、という意味）。"""
    stats = {}
    for key in keys:
        vals = [weights_by_gen[g].get(key, 0.0) for g in gens]
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        stats[key] = (mean, math.sqrt(var))
    z = {}
    for g in gens:
        z[g] = {}
        for key in keys:
            mean, std = stats[key]
            v = weights_by_gen[g].get(key, 0.0)
            z[g][key] = ((v - mean) / std) if std > 1e-9 else 0.0
    return z


def _style_vectors(gens, weights_by_gen, axes=STYLE_AXES):
    all_keys = sorted({k for axis in axes.values() for k, _sign in axis})
    z = _zscore_matrix(gens, weights_by_gen, all_keys)
    vectors = {g: [sum(sign * z[g][k] for k, sign in axis) for axis in axes.values()]
               for g in gens}
    return vectors, list(axes.keys())


def _kmeans(vectors, k, iterations=100, seed=0):
    """依存ライブラリなしの単純なk-means（ユークリッド距離、ランダム初期化）。
    vectors: [[float, ...], ...]（すべて同じ長さ）。
    戻り値: (labels, centroids)。labels[i]はvectors[i]の所属クラスタ番号。"""
    import random as _random
    rng = _random.Random(seed)
    n = len(vectors)
    k = max(1, min(k, n))
    dim = len(vectors[0])

    centroids = [list(v) for v in rng.sample(vectors, k)]
    labels = [0] * n

    for _ in range(iterations):
        changed = False
        for i, v in enumerate(vectors):
            best, best_dist = 0, None
            for c_idx, c in enumerate(centroids):
                dist = sum((a - b) ** 2 for a, b in zip(v, c))
                if best_dist is None or dist < best_dist:
                    best, best_dist = c_idx, dist
            if labels[i] != best:
                changed = True
            labels[i] = best
        new_centroids = []
        for c_idx in range(k):
            members = [vectors[i] for i in range(n) if labels[i] == c_idx]
            if not members:
                new_centroids.append(centroids[c_idx])
                continue
            new_centroids.append([sum(m[d] for m in members) / len(members) for d in range(dim)])
        centroids = new_centroids
        if not changed:
            break
    return labels, centroids


def cmd_archetypes(args):
    history_dir = args.history_dir

    if args.tournament_results:
        with open(args.tournament_results, encoding="utf-8") as f:
            payload = json.load(f)
        gens = payload["gens"]
        pairwise = {tuple(int(x) for x in k.split("-")): v
                    for k, v in payload["pairwise"].items()}
        print(f"保存済みトーナメント結果を読み込みました: {args.tournament_results} "
              f"(世代{gens})")
    else:
        if args.gens:
            gens = _parse_gen_list(args.gens)
        else:
            gens = sorted(list_generations(history_dir).keys())
        if len(gens) < 3:
            raise SystemExit("[エラー] archetypes には3世代以上必要です（--gens）。")
        print(f"=== トーナメント戦を実行して重みタイプを分析します: 世代 {gens} ===\n")
        pairwise = _run_tournament_core(history_dir, gens, args)

    weights_by_gen = {g: load_generation(history_dir, g)["weights"] for g in gens}

    if args.use_full_weights:
        all_keys = sorted({k for w in weights_by_gen.values() for k in w.keys() if k != "jitter"})
        z = _zscore_matrix(gens, weights_by_gen, all_keys)
        vectors = {g: [z[g][k] for k in all_keys] for g in gens}
        axis_labels = all_keys
    else:
        vectors, axis_labels = _style_vectors(gens, weights_by_gen)

    print("\n各世代のスタイル軸スコア（z-score合成。正が強いほどその志向が強い）:")
    print("世代".rjust(6) + "".join(label.rjust(14) for label in axis_labels))
    for g in gens:
        print(str(g).rjust(6) + "".join(f"{v:+.2f}".rjust(14) for v in vectors[g]))

    k = min(args.k, len(gens))
    vec_list = [vectors[g] for g in gens]
    labels, centroids = _kmeans(vec_list, k, seed=args.seed)
    clusters = {}
    for g, lab in zip(gens, labels):
        clusters.setdefault(lab, []).append(g)

    print(f"\nクラスタリング結果（k={k}）:")
    for lab in sorted(clusters.keys()):
        members = clusters[lab]
        centroid = centroids[lab]
        top = sorted(zip(axis_labels, centroid), key=lambda x: -abs(x[1]))[:3]
        desc = ", ".join(f"{name}{v:+.2f}" for name, v in top)
        print(f"  クラスタ{lab} (世代{members}): 特徴的な軸 [{desc}]")

    if len(clusters) >= 2:
        cluster_pairwise = {}
        for (ga, gb), r in pairwise.items():
            la, lb = labels[gens.index(ga)], labels[gens.index(gb)]
            if la == lb:
                continue
            key = (la, lb) if la < lb else (lb, la)
            # winsを「番号が小さい方のクラスタ(low)」「大きい方のクラスタ(high)」に振り分ける
            wins_low, wins_high = (r["wins_a"], r["losses_a"]) if la < lb \
                else (r["losses_a"], r["wins_a"])
            entry = cluster_pairwise.setdefault(key, {"wins_low": 0, "wins_high": 0, "games": 0})
            entry["wins_low"] += wins_low
            entry["wins_high"] += wins_high
            entry["games"] += r["games"]

        print("\nクラスタ間の対戦成績:")
        for (la, lb), e in sorted(cluster_pairwise.items()):
            wr = (e["wins_low"] / e["games"]) if e["games"] else 0.5
            print(f"  クラスタ{la} vs クラスタ{lb}: クラスタ{la}勝率={wr:.3f} "
                  f"({e['wins_low']}勝{e['wins_high']}敗, {e['games']}局)")

        cluster_beats = {lab: set() for lab in clusters}
        for (la, lb), e in cluster_pairwise.items():
            wr = (e["wins_low"] / e["games"]) if e["games"] else 0.5
            if wr > 0.5:
                cluster_beats[la].add(lb)
            elif wr < 0.5:
                cluster_beats[lb].add(la)
        cluster_cycles = _find_cycles(cluster_beats)

        print("\nクラスタ間の非推移的な循環（重みタイプのじゃんけん構造）:")
        if cluster_cycles:
            for i, j, l in cluster_cycles:
                print(f"  クラスタ{i} > クラスタ{j} > クラスタ{l} > クラスタ{i}")
                print(f"    クラスタ{i} = 世代{clusters[i]} / "
                      f"クラスタ{j} = 世代{clusters[j]} / クラスタ{l} = 世代{clusters[l]}")
        else:
            print("  検出されませんでした。")
    else:
        print("\n[注意] 全世代が1つのクラスタにまとまってしまい、クラスタ間の比較が"
              "できませんでした。--k を増やすか、対象世代を増やしてください。")

    print(
        "\n[このコマンドの限界について] ここで使っている5つのスタイル軸は"
        "champion_tools.py側で仮に定義した仮説（STYLE_AXES）にすぎず、実際に"
        "ゲームバランスを支配している軸と一致している保証はない。"
        "--use-full-weights を付けると21キー全体を使ったクラスタリングに"
        "切り替えられるが、次元が多い分クラスタの解釈は難しくなる。まずは"
        "この出力（どの世代がどのクラスタに分類されたか、各クラスタの特徴的な軸、"
        "クラスタ間の対戦成績）を見ながら意味のありそうな軸を探し、必要なら"
        "STYLE_AXES（このファイル内）を書き換えて再実行することを推奨する。"
    )


# ------------------------------------------------------------
# サブコマンド: import-log（旧 import_champion_log.py）
# ------------------------------------------------------------

TRIAL_LINE_RE = re.compile(
    r"Trial (\d+) finished with value: [\-\d.]+ and parameters: (\{.*?\})\."
)
PROMOTION_LINE_RE = re.compile(
    r"\[champion\] trial (\d+) が世代(\d+)の新championに昇格しました "
    r"\(vs前champion: (\d+)勝(\d+)敗(\d+)分, win_rate=([\d.]+)\)"
)
BOOTSTRAP_LINE_RE = re.compile(
    r"\[champion\].*初代チャンピオン\(generation=0\)としてブートストラップしました"
)
LOAD_LINE_RE = re.compile(
    r"\[champion\].*を読み込みました \(generation=(\d+), source=(\S+)\)"
)
FINAL_DUMP_HEADER_RE = re.compile(r"最終世代: generation=(\d+) \(source=(\S+)\)")
FINAL_DUMP_LINE_RE = re.compile(r'HEURISTIC_WEIGHTS\["(\w+)"\] = ([\-\d.]+)')


def parse_log(text):
    """ログテキストを走査し、
      trial_params: {trial_number: raw_params_dict}
      promotions: [{"trial_number", "generation", "wins", "losses", "draws", "win_rate"}]
      has_bootstrap: bool
      loads: [(generation, source)]  # 参考情報。復元には使わない
      final_dump: {"generation": int, "source": str, "weights": {...}} または None
    を返す。"""
    trial_params = {}
    for m in TRIAL_LINE_RE.finditer(text):
        trial_num = int(m.group(1))
        try:
            params = ast.literal_eval(m.group(2))
        except (ValueError, SyntaxError):
            print(f"[警告] trial {trial_num} のparameters辞書をパースできませんでした。"
                  f"生の文字列: {m.group(2)[:100]}...")
            continue
        trial_params[trial_num] = params

    promotions = []
    for m in PROMOTION_LINE_RE.finditer(text):
        promotions.append({
            "trial_number": int(m.group(1)),
            "generation": int(m.group(2)),
            "wins": int(m.group(3)),
            "losses": int(m.group(4)),
            "draws": int(m.group(5)),
            "win_rate": float(m.group(6)),
        })

    has_bootstrap = bool(BOOTSTRAP_LINE_RE.search(text))

    loads = [(int(g), s) for g, s in LOAD_LINE_RE.findall(text)]

    final_dump = None
    header_m = FINAL_DUMP_HEADER_RE.search(text)
    if header_m:
        dump_weights = dict(FINAL_DUMP_LINE_RE.findall(text[header_m.end():]))
        dump_weights = {k: float(v) for k, v in dump_weights.items()}
        if dump_weights:
            final_dump = {
                "generation": int(header_m.group(1)),
                "source": header_m.group(2),
                "weights": dump_weights,
            }

    return trial_params, promotions, has_bootstrap, loads, final_dump


def reconstruct_weights(raw_params):
    """optunaの生パラメータ(WEIGHT_SEARCH_SPACEのキー) から、
    HEURISTIC_WEIGHTSと同じ形状の完全な重み辞書を再構成する。
    本体(tune_balance_search.py)の変換ロジックをそのままimportして使うため、
    format_best_params_for_paste()が出す最終ダンプと理論上完全一致するはず
    （--dry-runの検算で実際に突き合わせる）。"""
    resolved_overrides = _resolve_weight_overrides(raw_params)
    return weights.resolve_weights(resolved_overrides)


def validate_against_final_dump(final_dump, reconstructed_by_generation):
    """ログ末尾の「copy-paste用」ダンプと、再構成結果を突き合わせる検算。
    ズレがあれば変換ロジックの想定違いを示すので、そのまま書き込まずに
    知らせる。"""
    gen = final_dump["generation"]
    if gen not in reconstructed_by_generation:
        print(f"[検算スキップ] 最終ダンプの世代{gen}に対応する昇格イベントが"
              f"ログ中に見つからなかったため、検算できませんでした。")
        return
    reconstructed = reconstructed_by_generation[gen]
    mismatches = []
    for key, dump_value in final_dump["weights"].items():
        recon_value = reconstructed.get(key)
        if recon_value is None:
            mismatches.append(f"  {key}: 再構成側に存在しない")
            continue
        if abs(round(recon_value, 2) - dump_value) > 0.011:
            mismatches.append(f"  {key}: ダンプ={dump_value} 再構成={round(recon_value, 2)}")
    if mismatches:
        print(f"[検算NG] 世代{gen}: ログ末尾のcopy-paste用ダンプと再構成結果が"
              f"{len(mismatches)}件で不一致でした（本体の変換ロジックとズレている"
              f"可能性があります。書き込み前に確認してください）:")
        for line in mismatches:
            print(line)
    else:
        print(f"[検算OK] 世代{gen}: ログ末尾のダンプと再構成結果が"
              f"（丸め誤差の範囲内で）完全一致しました。変換ロジックは正しく"
              f"動作しています。")


def cmd_import_log(args):
    history_dir = args.history_dir

    with open(args.log, "r", encoding="utf-8", errors="replace") as f:
        text = f.read()

    trial_params, promotions, has_bootstrap, loads, final_dump = parse_log(text)

    print(f"ログ解析結果: trial行 {len(trial_params)}件 / 昇格イベント {len(promotions)}件 / "
          f"ブートストラップ行 {'あり' if has_bootstrap else 'なし'} / "
          f"読み込み行(参考) {len(loads)}件")
    if final_dump:
        print(f"末尾のcopy-paste用ダンプ: 世代{final_dump['generation']} "
              f"(source={final_dump['source']}) を検算に使用します。")

    existing_generations = set(list_generations(history_dir).keys())

    reconstructed_by_generation = {}
    to_write = []  # (generation, weights, source, trial_number, metrics, champion_generation_faced)

    if has_bootstrap and (0 not in existing_generations or args.overwrite):
        weights0 = weights.resolve_weights({})
        reconstructed_by_generation[0] = weights0
        to_write.append((0, weights0, "bootstrap(config.py baseline, ログから復元)",
                          None, None, None))
    elif has_bootstrap:
        print("[スキップ] 世代0 (bootstrap): 既にchampion_history/gen_0000.jsonが"
              "存在するため上書きしません（--overwriteで強制可能）")

    for promo in sorted(promotions, key=lambda p: p["generation"]):
        gen = promo["generation"]
        trial_num = promo["trial_number"]
        if gen in existing_generations and not args.overwrite:
            print(f"[スキップ] 世代{gen} (trial {trial_num}): 既にファイルが存在するため"
                  f"上書きしません（--overwriteで強制可能）")
            continue
        raw_params = trial_params.get(trial_num)
        if raw_params is None:
            print(f"[警告] 世代{gen} (trial {trial_num}): ログ中に対応する"
                  f"'Trial {trial_num} finished ... parameters: {{...}}' 行が"
                  f"見つからなかったため、重みを復元できません。スキップします。"
                  f"（ログが該当区間だけ切れている可能性があります）")
            continue
        weights = reconstruct_weights(raw_params)
        reconstructed_by_generation[gen] = weights
        metrics = {
            "wins": promo["wins"],
            "losses": promo["losses"],
            "draws": promo["draws"],
            "win_rate_vs_champion": promo["win_rate"],
            "_restored_from_log": True,
            "_note": "ログからの復元のため、rp_spot_neglect等の詳細診断指標は含まれていません。",
        }
        to_write.append((gen, weights, f"trial_{trial_num}(ログから復元)",
                          trial_num, metrics, gen - 1))

    if final_dump:
        validate_against_final_dump(final_dump, reconstructed_by_generation)

    print(f"\n書き込み対象: {len(to_write)}世代 "
          f"({[g for g, *_ in to_write]})")

    if args.dry_run:
        print("[--dry-run] 実際のファイル書き込みは行いません。")
        return

    for gen, weights, source, trial_num, metrics, faced in to_write:
        path = save_champion_history_entry(
            history_dir, gen, weights, config_overrides=[], source=source,
            trial_number=trial_num, metrics=metrics, champion_generation_faced=faced,
        )
        print(f"  書き込み: {path}")

    print(f"\n完了しました。python3 champion_tools.py list --history-dir {history_dir} "
          f"で確認できます。")


# ------------------------------------------------------------
# argparse（サブコマンド）
# ------------------------------------------------------------

def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="champion_history/ に保存された歴代チャンピオンの一覧・対戦・"
                     "テキストログからの復元をまとめて扱うツール",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--history-dir", type=str, default=None,
                         help="tune_balance_search.pyの--champion-history-dirと同じ場所を"
                              "指定する。省略時はスクリプトと同じフォルダのchampion_history/。")

    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="保存済みの世代一覧と、各世代の昇格時点のスコアを表示する")
    p_list.add_argument("--csv", type=str, default=None, help="一覧をCSVファイルにも書き出す")
    p_list.set_defaults(func=cmd_list)

    p_show = sub.add_parser("show", help="指定した世代の診断指標(metrics)を全項目ダンプする")
    p_show.add_argument("generation", type=int, metavar="GEN")
    p_show.set_defaults(func=cmd_show)

    p_battle = sub.add_parser("battle", help="2世代を対戦させる")
    p_battle.add_argument("--gen-a", type=int, required=True, help="対戦させる世代その1")
    p_battle.add_argument("--gen-b", type=int, required=True, help="対戦させる世代その2")
    p_battle.add_argument("--games", type=int, default=30, help="対戦数（既定30。先後半々で戦う）")
    p_battle.add_argument("--depth", type=int, default=2, help="SearchBotの探索深さ（0.95節によりdepth2が既定。0.77節のdepth3既定は運用コスト実測を踏まえて撤回）")
    p_battle.add_argument("--candidate-k", type=int, default=10, help="SearchBotの候補手プルーニング数")
    p_battle.add_argument("--workers", type=int, default=os.cpu_count())
    p_battle.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT)
    p_battle.add_argument("--seed", type=int, default=0, help="対戦のseedのベース値（再現性用）")
    p_battle.add_argument("--alpha", type=float, default=0.1, help="有意差判定の片側有意水準（既定0.1）")
    p_battle.add_argument("--ruleset", choices=["a", "b", "none"], default="b",
                           help="2世代間で駒パラメータが異なる場合にどちらのルールで対戦させるか")
    p_battle.set_defaults(func=cmd_battle)

    p_all = sub.add_parser("all-pairs", help="複数世代の総当たり戦（相性じゃんけん構造の検証）")
    p_all.add_argument("--gens", type=str, default=None,
                        help="世代番号リスト。カンマ区切り（例: 0,10,20,30,50）・"
                             "範囲指定（例: 50-57）・その混在（例: 0,10-20,50）に対応。"
                             "省略時は保存済み全世代")
    p_all.add_argument("--games", type=int, default=30)
    p_all.add_argument("--depth", type=int, default=2)  # 0.95節によりdepth2が既定（旧0.77節のdepth3既定は撤回）
    p_all.add_argument("--candidate-k", type=int, default=10)
    p_all.add_argument("--workers", type=int, default=os.cpu_count())
    p_all.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT)
    p_all.add_argument("--seed", type=int, default=0)
    p_all.add_argument("--alpha", type=float, default=0.1)
    p_all.add_argument("--ruleset", choices=["a", "b", "none"], default="b")
    p_all.set_defaults(func=cmd_all_pairs)

    p_firstmover = sub.add_parser(
        "firstmover",
        help="先後勝率差チェック: 同一（または指定した）世代同士をミラー戦させ、"
             "先手/後手の勝率差からルール自体の健全性（手番有利不利）を判定する",
    )
    p_firstmover.add_argument("--gen-a", type=str, required=True,
                               help="対象世代。カンマ区切り（例: 50,52,55）・範囲指定"
                                    "（例: 50-57）・その混在で複数指定すると、"
                                    "指定した世代それぞれについて連続で検証する。")
    p_firstmover.add_argument("--gen-b", type=str, default=None,
                               help="省略時は各--gen-aと同じ世代とのミラー戦になる"
                                    "（ルール健全性チェックにはこちらを推奨）。"
                                    "単一世代を指定した場合、--gen-aの各世代がその"
                                    "固定世代とそれぞれ対戦する。--gen-aと同数を"
                                    "カンマ区切り・範囲指定で与えた場合は、先頭から"
                                    "1対1で対応付けて対戦する。"
                                    "強さの差と手番差が混在する点に注意。")
    p_firstmover.add_argument("--games", type=int, default=30,
                               help="対戦数（既定30。先手/後手に半々で振り分ける）")
    p_firstmover.add_argument("--depth", type=int, default=2)  # 0.95節によりdepth2が既定（旧0.77節のdepth3既定は撤回）
    p_firstmover.add_argument("--candidate-k", type=int, default=10)
    p_firstmover.add_argument("--workers", type=int, default=os.cpu_count())
    p_firstmover.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT)
    p_firstmover.add_argument("--seed", type=int, default=0)
    p_firstmover.add_argument("--alpha", type=float, default=0.1,
                               help="有意差判定の片側有意水準（既定0.1）")
    p_firstmover.add_argument("--ruleset", choices=["a", "b", "none"], default="b",
                               help="--gen-b指定時、駒パラメータが異なる場合にどちらのルールで対戦させるか")
    p_firstmover.set_defaults(func=cmd_firstmover)

    p_tournament = sub.add_parser(
        "tournament",
        help="トーナメント戦（総当たり）で複数世代の順位付けを行い、"
             "世代番号と強さの相関・非推移的な循環（相性じゃんけん）を自動検出する",
    )
    p_tournament.add_argument("--gens", type=str, default=None,
                               help="世代番号リスト。カンマ区切り・範囲指定（例: 50-57）・"
                                    "その混在に対応。省略時は保存済み全世代")
    p_tournament.add_argument("--games", type=int, default=30)
    p_tournament.add_argument("--depth", type=int, default=2)  # 0.95節によりdepth2が既定（旧0.77節のdepth3既定は撤回）
    p_tournament.add_argument("--candidate-k", type=int, default=10)
    p_tournament.add_argument("--workers", type=int, default=os.cpu_count())
    p_tournament.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT)
    p_tournament.add_argument("--seed", type=int, default=0)
    p_tournament.add_argument("--alpha", type=float, default=0.1)
    p_tournament.add_argument("--ruleset", choices=["a", "b", "none"], default="b")
    p_tournament.add_argument("--save-results", type=str, default=None,
                               help="対戦結果をJSONに保存する（archetypesサブコマンドで再利用可能）")
    p_tournament.set_defaults(func=cmd_tournament)

    p_archetypes = sub.add_parser(
        "archetypes",
        help="トーナメント戦の結果を分析し、重み傾向（プレイスタイル）ごとに"
             "クラスタリングした上で、クラスタ間に相性じゃんけん構造がないか検出する",
    )
    p_archetypes.add_argument("--tournament-results", type=str, default=None,
                               help="tournament --save-resultsで保存したJSONを読み込んで使う"
                                    "（指定時は対戦をやり直さない）。省略時は--gensで新たに対戦する。")
    p_archetypes.add_argument("--gens", type=str, default=None,
                               help="--tournament-results省略時に対象とする世代。"
                                    "カンマ区切り・範囲指定（例: 50-57）・その混在に対応。"
                                    "省略時は保存済み全世代")
    p_archetypes.add_argument("--games", type=int, default=30)
    p_archetypes.add_argument("--depth", type=int, default=2)  # 0.95節によりdepth2が既定（旧0.77節のdepth3既定は撤回）
    p_archetypes.add_argument("--candidate-k", type=int, default=10)
    p_archetypes.add_argument("--workers", type=int, default=os.cpu_count())
    p_archetypes.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT)
    p_archetypes.add_argument("--seed", type=int, default=0)
    p_archetypes.add_argument("--alpha", type=float, default=0.1)
    p_archetypes.add_argument("--ruleset", choices=["a", "b", "none"], default="b")
    p_archetypes.add_argument("--k", type=int, default=3, help="クラスタ数（既定3）")
    p_archetypes.add_argument("--use-full-weights", action="store_true",
                               help="5つのスタイル軸ではなく、21キーの重み全体でクラスタリングする")
    p_archetypes.set_defaults(func=cmd_archetypes)

    p_import = sub.add_parser("import-log", help="過去のテキストログから世代を復元する")
    p_import.add_argument("--log", type=str, required=True,
                           help="tune_balance_search.pyの標準出力を保存したテキストファイル")
    p_import.add_argument("--overwrite", action="store_true",
                           help="既存のgen_XXXX.jsonもログの内容で上書きする（既定は保護してスキップ）")
    p_import.add_argument("--dry-run", action="store_true",
                           help="書き込まず、何が復元できる/できないかだけ表示する")
    p_import.set_defaults(func=cmd_import_log)

    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    args.history_dir = args.history_dir or default_history_dir()
    args.func(args)


if __name__ == "__main__":
    main()
