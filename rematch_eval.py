# -*- coding: utf-8 -*-
"""
rematch_eval.py（2026-09-06(2)新設）
==========================================================
2つの重み設定（championやOptuna trial）を、棋譜を一切保存せずtune_balance_search.py
と同じ並列エンジン（ProcessPoolExecutor, --workers既定16）で大量対戦させ、
勝率・二項検定のp値だけを高速に集計するための検証専用スクリプト。

背景（何が困っていたか）:
  「championのチャンピオン戦での勝率が本当に有意な差なのか、それとももっと
  多くの局数で確かめると崩れるノイズなのか」を検証したい場面（世代7 vs 世代8を
  300局で再戦させる、世代7 vs 世代10を直接対戦させる、等）で、従来は
  generate_kifu.py --trial-a/-b を使うしかなかった。しかしgenerate_kifu.pyは
  「1局ずつ棋譜(kifu)を組み立てて保存し、ビューアーも自動生成する」ことを
  主目的にしたツールであり、かつrun_and_collect()はseedsをforループで1局ずつ
  直列実行する設計（並列化されていない）ため、300局のような大サンプルの検証には
  著しく非効率だった（棋譜の保存・ビューアー生成のI/Oコストもn_games分だけ
  積み上がる）。

  一方tune_balance_search.pyのevaluate_vs_champion()は、まさに「2つの重み設定を
  棋譜を残さず並列に対戦させ、勝率だけを集計する」処理を既に持っている
  （championとの毎trial評価に使っているものと全く同じ関数）。このスクリプトは
  独自の対戦ロジックを新規実装せず、tune_balance_search.py / generate_kifu.py
  から既存の実装をそのままimportして薄く組み合わせただけのラッパーである
  （評価ロジックを2箇所に重複実装すると、どちらかだけ直されて食い違う事故が
  起きやすいため、意図的に再利用に徹している）。

前提（tune_balance_search.py / generate_kifu.pyと同じ）:
  - config.py / weights.py / game.py / heuristic_bot.py / search_bot_skeleton.py /
    meta_diversity_check.py / build_tools.py と同じフォルダに置く
    （generate_kifu.pyのimport時にbuild_tools・meta_diversity_check等が
    読み込まれるため、これらが無いとimport段階で失敗する。generate_kifu.py
    単体を普段から実行できている環境であれば追加で必要なファイルは無い）。
  - tune_balance_search.py・generate_kifu.py 本体（2026-09-06(2)版）も
    同じフォルダに置く（このスクリプトはその2つから関数をimportするだけで、
    対戦ロジック自体は一切再実装していない）。
  - Optunaはインストール済み（--trial-a/-b使用時のみ必要。champion_history/
    のファイルを直接指定する--weights-a/-bだけを使う場合は不要）。

使い方:
  # 世代7 vs 世代8を300局・16並列で再戦（D-1相当）
  $ python3 rematch_eval.py \\
        --weights-a champion_history/gen_0008.json \\
        --weights-b champion_history/gen_0007.json \\
        --games 300 --workers 16

  # 世代7 vs 世代10を直接対戦（中間の世代8・9を飛ばす。D-2相当）
  $ python3 rematch_eval.py \\
        --weights-a champion_history/gen_0010.json \\
        --weights-b champion_history/gen_0007.json \\
        --games 300 --workers 16

  # champion_historyに残っていない（＝昇格しなかった）trialを直接指定することも
  # できる（generate_kifu.py --trial-a/-bと同じOptuna DB読み込み）
  $ python3 rematch_eval.py \\
        --storage sqlite:///balance_tuning_search.db \\
        --study-name search_balance_weights_260905_v3 \\
        --trial-a 94 --weights-b champion_history/gen_0007.json \\
        --games 300 --workers 16

  # --depth/--candidate-kは元のtune_balance_search.py実行時の値に必ず揃えること
  # （既定はtune_balance_search.py既定と同じdepth=2, candidate-k=10。異なる値で
  # チューニングしていた場合はここでも明示指定しないと元の評価を再現しない。
  # generate_kifu.pyの3.8節注意点と同じ理由）。

出力:
  A側から見た勝率・勝敗分・二項検定の片側p値（tune_balance_search.pyの
  _promotion_is_significantと完全に同じ計算式）・alphaに対する有意判定を
  コンソールに表示する。既定で結果をrematch_result_<A>_vs_<B>_<タイムスタンプ>.json
  にも保存する（--no-saveで無効化可能）。棋譜は一切保存しない（保存したい場合は
  従来通りgenerate_kifu.pyを使うこと。少数局・局面確認向けにはgenerate_kifu.py、
  多数局・勝率の統計的検証にはこのスクリプト、と使い分ける）。
"""

import argparse
import json
import math
import os
import sys
import time
from datetime import datetime, timezone

# tune_balance_search.py / generate_kifu.py を実行場所によらずimportできるようにする
# （tune_balance_search.py本体のmain()末尾と同じ対策）。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from tune_balance_search import (
        ExecutorHolder,
        evaluate_vs_champion,
        _promotion_is_significant,
        GAME_TIMEOUT_SECONDS_DEFAULT,
    )
except ImportError as e:
    raise SystemExit(
        f"tune_balance_search.pyのimportに失敗しました（同じフォルダに2026-09-06(2)版を"
        f"置いてください。confirmation_check関連の新規importが必要です）: {e}"
    )

try:
    from generate_kifu import resolve_side_weights, DEFAULT_STORAGE
except ImportError as e:
    raise SystemExit(
        f"generate_kifu.pyのimportに失敗しました（同じフォルダに置き、config.py/weights.py/"
        f"game.py/heuristic_bot.py/search_bot_skeleton.py/meta_diversity_check.py/"
        f"build_tools.pyが揃っている状態にしてください。generate_kifu.py単体を普段"
        f"実行できている環境なら追加のファイルは不要です）: {e}"
    )


def _one_sided_binom_p(wins, n_games, threshold=0.5):
    """勝率がthresholdを片側検定で上回っているかのp値。
    tune_balance_search.py の _promotion_is_significant() と完全に同じ計算式
    （二項分布の片側裾確率）を、判定結果(bool)だけでなく生のp値として見たい
    ケース向けに複製している。ロジックの実体（何をもって「有意」とするか）は
    _promotion_is_significant()側にのみ持たせ、こちらは表示用の値取得に徹する
    （有意判定そのものは必ず_promotion_is_significant()を呼んで求め、ここでの
    p値計算とロジックが将来食い違わないようにする）。"""
    if n_games == 0:
        return 1.0
    return sum(
        math.comb(n_games, k) * (threshold ** k) * ((1 - threshold) ** (n_games - k))
        for k in range(wins, n_games + 1)
    )


def _unique_result_filename(label_a, label_b):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    safe_a = label_a.replace("/", "_").replace(":", "_")
    safe_b = label_b.replace("/", "_").replace(":", "_")
    return f"rematch_result_{safe_a}_vs_{safe_b}_{ts}.json"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    group = parser.add_argument_group("重み設定の指定（generate_kifu.pyと同じ枠。片側ごとに排他）")
    group.add_argument("--weights-a", metavar="PATH",
                        help="A側の重み設定ファイル（champion_weights.json / "
                             "champion_history/gen_XXXX.json / 素の上書きJSONいずれも可）")
    group.add_argument("--weights-b", metavar="PATH", help="B側の重み設定ファイル（同上）")
    group.add_argument("--archetype-a", metavar="NAME",
                        help="A側をmeta_diversity_check.pyのARCHETYPES名で指定する")
    group.add_argument("--archetype-b", metavar="NAME", help="B側（同上）")

    db_group = parser.add_argument_group("Optuna DBからtrialを直接指定する（generate_kifu.pyと同じ）")
    db_group.add_argument("--trial-a", type=int, metavar="N", help="A側をtrial番号Nで指定する")
    db_group.add_argument("--trial-b", type=int, metavar="N", help="B側をtrial番号Nで指定する")
    db_group.add_argument("--storage", type=str, default=DEFAULT_STORAGE,
                           help=f"--trial-a/-b用のOptuna storage URL（既定: {DEFAULT_STORAGE}）")
    db_group.add_argument("--study-name", type=str, default=None,
                           help="--trial-a/-b用のstudy名（--trial-a/-bを使う場合は必須）")

    parser.add_argument("--games", type=int, default=300,
                         help="対戦局数（先後を必ず半々に自動で振り分ける。既定300。"
                              "多いほど勝率推定の信頼区間が狭くなるが時間も伸びる）")
    parser.add_argument("--workers", type=int, default=16,
                         help="並列ワーカー数（tune_balance_search.pyと同じProcessPoolExecutorベース。"
                              "既定16。マシンの論理コア数を超えて増やしても頭打ちになりやすい）")
    parser.add_argument("--depth", type=int, default=2,
                         help="SearchBotの探索深さ（既定2。元のtune_balance_search.py実行時に"
                              "--depthを変えていた場合は必ず同じ値を指定すること）")
    parser.add_argument("--candidate-k", type=int, default=10,
                         help="SearchBotの候補手プルーニング数（既定10。上記--depthと同様、"
                              "元の実行時の値に揃えること）")
    parser.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT,
                         help=f"1局あたりのタイムアウト秒数（既定{GAME_TIMEOUT_SECONDS_DEFAULT}、"
                              f"tune_balance_search.pyと同じ既定値）")
    parser.add_argument("--seed-base", type=int, default=0,
                         help="対戦に使うseedの起点（既定0）。同じ2つの重み設定・同じseed-baseなら"
                              "結果は再現可能")
    parser.add_argument("--alpha", type=float, default=0.05,
                         help="有意判定に使う片側有意水準（既定0.05。tune_balance_search.pyの"
                              "2026-09-06(2)改定後の既定--promotion-alphaと合わせてある。"
                              "従来既定の0.1と比較したい場合は明示指定すること）")
    parser.add_argument("--margin", type=float, default=0.5,
                         help="有意判定の閾値勝率（既定0.5=単純多数決。"
                              "tune_balance_search.pyの--promotion-marginに相当）")
    parser.add_argument("--no-save", action="store_true",
                         help="結果をJSONファイルへ保存しない（既定は保存する）")
    return parser.parse_args(argv)


def main():
    args = _parse_args(sys.argv[1:])

    if args.trial_a is not None or args.trial_b is not None:
        if not args.study_name:
            raise SystemExit("--trial-a/-bを使う場合は--study-nameの指定が必須です"
                              "（generate_kifu.pyの制約と同じ）。")

    try:
        weights_a, label_a, config_overrides_a = resolve_side_weights(
            args.archetype_a, args.weights_a, args.trial_a, args.storage, args.study_name)
        weights_b, label_b, config_overrides_b = resolve_side_weights(
            args.archetype_b, args.weights_b, args.trial_b, args.storage, args.study_name)
    except (ValueError, OSError, json.JSONDecodeError) as e:
        raise SystemExit(f"重み設定の解決に失敗しました: {e}")

    if label_a == "default(balanced)" and label_b == "default(balanced)":
        raise SystemExit(
            "A側・B側とも未指定です（両方balancedのミラー戦になってしまいます）。"
            "--weights-a/-b、--archetype-a/-b、--trial-a/-bのいずれかで両側を指定してください。"
        )

    # generate_kifu.pyのmain()末尾と同じ優先順位: 駒設定(CONFIG["pieces"])が両側で
    # 異なる場合はA側を優先し警告する（駒設定はゲーム側に1つしか持てないため）。
    if config_overrides_a and config_overrides_b and config_overrides_a != config_overrides_b:
        print(f"[警告] A側（{label_a}）とB側（{label_b}）でCONFIG['pieces']のチューニング値が"
              f"異なりますが、駒設定はゲーム側で1つしか持てないため、A側の設定を採用し、"
              f"B側の駒設定は無視します。", file=sys.stderr)
        config_overrides = config_overrides_a
    else:
        config_overrides = config_overrides_a or config_overrides_b

    print(f"[rematch] A={label_a} vs B={label_b} / games={args.games} / workers={args.workers} / "
          f"depth={args.depth} candidate_k={args.candidate_k}")
    if config_overrides:
        print(f"[rematch] CONFIG['pieces']に{len(config_overrides)}件の上書きを適用します: {config_overrides}")

    executor_holder = ExecutorHolder(max_workers=args.workers)
    start = time.time()
    try:
        # evaluate_vs_champion(candidate, champion, ...) は「candidate視点の勝率」を返す。
        # ここではA側をcandidate、B側をchampion役として渡す（先後は関数内部で自動的に
        # 半々交互になる。tune_balance_search.pyの毎trial評価と全く同じ経路）。
        result = evaluate_vs_champion(
            executor_holder, weights_a, weights_b, config_overrides,
            args.games, args.seed_base, args.depth, args.candidate_k,
            game_timeout=args.game_timeout,
        )
    finally:
        executor_holder.shutdown(wait=True)
    elapsed = time.time() - start

    wins, losses, draws, n = result["wins"], result["losses"], result["draws"], result["n_games"]
    win_rate = result["win_rate"]
    p_value = _one_sided_binom_p(wins, n, threshold=args.margin)
    is_significant = _promotion_is_significant(wins, n, threshold=args.margin, alpha=args.alpha)

    print("\n" + "=" * 60)
    print(f"A: {label_a}")
    print(f"B: {label_b}")
    print(f"{n}局中 A {wins}勝{losses}敗{draws}分 (win_rate={win_rate:.4f})")
    print(f"片側二項検定 p値 = {p_value:.4f}（帰無仮説: 真の勝率<={args.margin}）")
    print(f"alpha={args.alpha} での有意判定: "
          f"{'有意にAが優位（Aの方が強いと言えそう）' if is_significant else '有意差なし（このサンプル数では判定不能）'}")
    print(f"所要時間: {elapsed:.1f}秒（{n}局 / {args.workers}並列）")
    print("=" * 60)

    if not args.no_save:
        out = {
            "label_a": label_a,
            "label_b": label_b,
            "games": n,
            "wins_a": wins,
            "losses_a": losses,
            "draws": draws,
            "win_rate_a": win_rate,
            "p_value_one_sided": p_value,
            "alpha": args.alpha,
            "margin": args.margin,
            "significant": is_significant,
            "depth": args.depth,
            "candidate_k": args.candidate_k,
            "seed_base": args.seed_base,
            "config_overrides": config_overrides,
            "elapsed_seconds": elapsed,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        filename = _unique_result_filename(label_a, label_b)
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)
        print(f"saved {filename}")


if __name__ == "__main__":
    main()
