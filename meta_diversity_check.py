# -*- coding: utf-8 -*-
"""
meta_diversity_check.py
==========================================================
「特定の極端な重み設定だけが常に正解になってしまわないか」を高速に
検証するための総当たりツール。

背景・目的:
  AI作成をメインコンテンツにする以上、"全パラメータを守備寄り+VP最優先"
  のような単一の合理的解が常に勝ち続けてしまうと、プレイヤー間で工夫の
  余地がなくなり「メタが回らない」。理想は「歩兵ビルドが流行れば重装兵
  ビルドが台頭する」のようなじゃんけん構造（相性関係）がプレイヤーの
  ビルド選択にも存在すること。

  この検証にはSearchBot（探索あり、1局が数秒〜数十秒）ではなく、
  HeuristicBot（探索なし、1局が数十ms）を使う。理由: 「戦術的に最善の
  一手を指せるか」ではなく「戦略アーキタイプ同士の総当たり」を知りたい
  だけなので、低コストなHeuristicBotで十分かつ高速（数千戦を数分で回せる）。
  SearchBotでの追試が必要な場合は、ここで疑わしいと分かったアーキタイプ
  同士のペアだけをtune_balance_search.pyのチャンピオン戦モードで深掘りする。

使い方:
  python3 meta_diversity_check.py --games 60
"""

import argparse
import copy
import random
from collections import defaultdict

import weights as W
from game import Game
from heuristic_bot import HeuristicBot


# ============================================================
# アーキタイプ定義（ベース"balanced"からの上書き差分のみ。値は意図的に極端化）
# ============================================================
ARCHETYPES = {
    "balanced": {},  # weights.py の BASE_WEIGHTS（現行チャンピオン相当）そのまま

    # ユーザーが懸念していた「全部守備寄り+VP最優先」ビルド。
    # 2026-08-27: 符号統一方針により、danger/incoming_damage/exposure/
    # base_defense/rp_cost は常に0以上の大きさで指定する（内部の符号反転は
    # weights.to_engine_signed()がエンジン投入直前に行う）。
    "turtle_vp": {
        "vp_star": 15.0, "vp_tengen": 20.0,
        #"danger": 20.0,
        "support": 8.0,
        "base_defense": 3.0, "base_hp_weight": 5.0,
        #"incoming_damage": 2.5,
        "exposure": 3.0,
        #"attack": 0.1,
        "advance": 0.1, "kill_bonus": 3.0,
        "rp_spot":21.0, "midgame_spot": 3.0,
        "rp_cost": 0.3,
        # 2026-09-02追加: base_hp_panic_threshold（0.86節で導入。同日中にTier4
        # champion_index=48からBASE_WEIGHTSへ昇格し、主要重み＝新規プレイヤーの
        # AIでも最初から有効な重みになった）。
        # 本拠HPが危険水域(<80%。2026-09-02(2)にdepth=2探索の地平線に収まる
        # よう0.5→0.8へ引き上げ済み)に入った際にbase_defense項の倍率を
        # 引き上げる、まさに「守備寄りビルド」が使うべき重み。default=0.0の
        # ままだとこのアーキタイプが実装後も新設された防衛挙動を一切踏まない
        # 状態だったため、10.0（HP比40%で倍率6.0相当）を設定した。
        "base_hp_panic_threshold": 10.0,
        # 2026-09-02追加検討: base_approach_defense_priority（敵接近時点で
        # 早期に本拠へ帰還させる新設重み）も併用を試したが、実測ベンチマークで
        # base_hp_panic_threshold単体（本拠速攻相手に勝率15.0%）より悪化する
        # （併用で26.7〜35.0%）結果が出たため、ここではあえて設定しない
        # （既定値0.0のまま）。詳細はweights.py内のコメント、および
        # 引き継ぎ資料の応答メッセージ参照。
    },

    "rush_kill": {
        "attack": 1.8, "kill_bonus": 40.0, "advance": 4.0,
        "danger": 1.0, "base_pressure": 3.0,
        "incoming_damage": 0.1, "base_defense": 0.05,
        "exposure": 0.1, "vp_star": 2.0, "vp_tengen": 3.0,
        "rp_spot": 5.0,
    },

    "economy_boom": {
        "rp_spot": 70.0, "engineer_econ_bonus": 15.0,
        "midgame_spot": 25.0, "rp_differential": 1.5, "rp_cost": 0.3,
        "vp_star": 3.0, "vp_tengen": 4.0, "attack": 0.2, "kill_bonus": 5.0,
        "danger": 3.0,
    },

    "vp_spot_hunter": {
        "vp_spot_kill_bonus": 60.0, "rp_spot_kill_bonus": 30.0,
        "kill_bonus": 25.0, "attack": 1.0, "advance": 2.5,
        "vp_star": 20.0, "vp_tengen": 25.0,
        "danger": 3.0,
    },
}


def build_weights(overrides):
    return W.resolve_weights(overrides)


def run_match(w_a, w_b, n_games, seed_base):
    """w_a(先手/後手を半々) vs w_b でn_games戦し、aの勝率を返す。"""
    a_wins, b_wins, draws = 0, 0, 0
    for i in range(n_games):
        rng_a = random.Random(seed_base + i * 2)
        rng_b = random.Random(seed_base + i * 2 + 1)
        bot_a = HeuristicBot(weights=w_a, rng=rng_a)
        bot_b = HeuristicBot(weights=w_b, rng=rng_b)
        game = Game()
        if i % 2 == 0:
            bots = {0: bot_a, 1: bot_b}
            a_player = 0
        else:
            bots = {0: bot_b, 1: bot_a}
            a_player = 1
        result = game.run(bots)
        winner = result["winner"]
        if winner is None:
            draws += 1
        elif winner == a_player:
            a_wins += 1
        else:
            b_wins += 1
    total = a_wins + b_wins + draws
    return a_wins, b_wins, draws, total


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--games", type=int, default=60,
                     help="各カード(組み合わせ)ごとの対戦数（既定60、偶数推奨=先後半々）")
    ap.add_argument("--dominance-threshold", type=float, default=0.65,
                     help="この勝率を『全対戦相手に対して』上回ったら支配的アーキタイプと判定する")
    args = ap.parse_args()

    names = list(ARCHETYPES.keys())
    resolved = {name: build_weights(ARCHETYPES[name]) for name in names}

    print("=" * 70)
    print(f"メタ多様性チェック: アーキタイプ{len(names)}種の総当たり "
          f"(各カード{args.games}戦, HeuristicBot)")
    print("=" * 70)

    win_rate = {}  # (a, b) -> aの勝率
    seed_counter = 1000
    for i, a in enumerate(names):
        for b in names:
            if a == b:
                continue
            a_wins, b_wins, draws, total = run_match(
                resolved[a], resolved[b], args.games, seed_counter)
            seed_counter += args.games * 2
            wr = a_wins / total if total else 0.0
            win_rate[(a, b)] = wr
            print(f"  {a:14s} vs {b:14s}: {a}勝率 {wr*100:5.1f}%  "
                  f"({a_wins}勝{b_wins}敗{draws}分 / {total}戦)")

    print()
    print("-" * 70)
    print("アーキタイプ別・平均勝率（他全アーキタイプに対して）:")
    print("-" * 70)
    overall = {}
    for a in names:
        opp_rates = [win_rate[(a, b)] for b in names if b != a]
        avg = sum(opp_rates) / len(opp_rates)
        overall[a] = avg
        worst = min(opp_rates)
        print(f"  {a:14s}: 平均勝率 {avg*100:5.1f}%  (最も苦手な相手への勝率 {worst*100:5.1f}%)")

    print()
    print("-" * 70)
    print("判定:")
    print("-" * 70)
    dominant = [a for a in names
                if min(win_rate[(a, b)] for b in names if b != a) >= args.dominance_threshold]
    if dominant:
        print(f"  [要注意] 以下のアーキタイプは、全ての他アーキタイプに対して "
              f"勝率{args.dominance_threshold*100:.0f}%以上を記録しました:")
        for d in dominant:
            print(f"    - {d}")
        print("  → 単一の合理的な極端解が支配戦略化している可能性が高いです。"
              "下記のルール変更案を検討してください。")
    else:
        best = max(overall, key=overall.get)
        worst = min(overall, key=overall.get)
        print(f"  [健全] 全アーキタイプに対して一方的に勝ち続けるものはありませんでした。")
        print(f"  平均勝率が最も高いのは {best}({overall[best]*100:.1f}%)、"
              f"最も低いのは {worst}({overall[worst]*100:.1f}%) でした。")
        spread = overall[best] - overall[worst]
        if spread > 0.20:
            print(f"  ただし平均勝率の差が{spread*100:.0f}ptあり、{best}がやや強すぎる可能性があります。"
                  f"閾値を下げて({args.dominance_threshold-0.1:.2f}等)再確認することを推奨します。")


if __name__ == "__main__":
    main()
