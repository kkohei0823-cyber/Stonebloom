# -*- coding: utf-8 -*-
"""
tune_sprigling_ai.py
==========================================================
Sprigling時代のAI（HeuristicBotの重み）をOptunaで最適化する。
tune_balance_search.py（固定5駒種・SearchBot自己対戦）のSprigling版で、
検証環境 verify_lab.py の仕組み（先後入れ替えペア・シード固定・実行記録）をそのまま使う。

1 trial の評価:
  候補の重み(A) vs 現在の重み(B) を --pairs ペア。各ペアでランダムなSpriglingを配る
  （ペアごとに構成が変わるので、特定の構成への過剰適応を防ぐ。verify_lab.ai_eval_scenario）:
    --roster-mode mirror（既定）: 両者が同じ --roster-size 体を動員可能・同じ1体を最初の手駒に持つ
    --roster-mode swap          : 両者が別々の構成。2局目は手番と一緒に構成も入れ替える
    --stratified                : 構成内の階級を軽・中・重で均等にする（無指定なら階級も完全ランダム）
  既存5種はどちらも常に動員できる。
  目的関数 = Aのスコア（0.5なら現状と互角）。
  シード列は全trialで共通（共通乱数法: trial間の比較のノイズを減らす）。その代わり
  そのシード列への過剰適応が起きうるので、最後に選抜に使っていない新しいシードで
  上位候補をSPRT再検定し、その結果だけを信じる。

例:
  python3 tune_sprigling_ai.py --trials 40 --pairs 60
  python3 tune_sprigling_ai.py --trials 40 --pairs 60 \\
      --overrides '{"sprigling_stats":{"model":"physical"},"combat":{"initiative":true}}'
  python3 tune_sprigling_ai.py --keys hp_valuation_bonus,swarm_pref --trials 20

出力: best_sprigling_ai.json（再検定を通過した場合のみ "confirmed": true）
      runs/*_tune-ai.json（全trialの記録）
"""

import argparse
import json
import math
import os
import sys

import lab_stats
import verify_lab as V
from weights import HEURISTIC_WEIGHTS, WEIGHT_TIER_BY_KEY

# 探索範囲。Tier5（Sprigling時代の重み）は0からの絶対範囲、既存の重みは現在値の
# 0.25〜4倍（対数スケール）。
TIER5_RANGES = {
    "hp_valuation_bonus": (0.0, 2.0),
    "sprigling_affinity": (0.0, 10.0),
    "speed_edge_pref": (0.0, 10.0),
    "heavy_anchor_pref": (0.0, 20.0),
    "swarm_pref": (0.0, 5.0),
    "elite_pref": (0.0, 5.0),
}
DEFAULT_KEYS = list(TIER5_RANGES) + [
    "efficiency", "attack", "kill_bonus", "production_diversity_pref",
    "favorable_matchup_bonus", "unfavorable_matchup_penalty",
]


def suggest(trial, keys):
    out = {}
    for k in keys:
        if k in TIER5_RANGES:
            lo, hi = TIER5_RANGES[k]
            out[k] = trial.suggest_float(k, lo, hi)
        else:
            base = HEURISTIC_WEIGHTS[k]
            if base > 0:
                out[k] = trial.suggest_float(k, base * 0.25, base * 4.0, log=True)
            else:  # 既定0のTier重み
                out[k] = trial.suggest_float(k, 0.0, 10.0)
    return out


ROSTER = {"size": 3, "stratified": False, "mode": "mirror"}  # main()で引数から上書き


def roster_scenario(seed, weights, overrides):
    return V.ai_eval_scenario(seed, {"type": "heuristic", "weights": weights}, "heuristic", overrides,
                              ROSTER["size"], ROSTER["stratified"], ROSTER["mode"])


def evaluate(weights, seeds, overrides, workers):
    tasks = [(roster_scenario(s, weights, overrides), s) for s in seeds]
    res = V.run_pairs(tasks, workers)
    return [r["pair_score"] for r in res]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--pairs", type=int, default=60, help="1 trialあたりのペア数")
    ap.add_argument("--keys", help="探索する重み（カンマ区切り）。省略時はTier5+主要6種")
    ap.add_argument("--overrides", help="CONFIGの上書き(JSON)。ステータス式・戦闘ルールを固定して調整する")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--seed", default="0")
    ap.add_argument("--study-name", default="sprigling_ai")
    ap.add_argument("--storage", help="例 sqlite:///sprigling_ai.db（中断・再開・並列実行用）")
    ap.add_argument("--confirm", type=int, default=3, help="再検定する上位trial数")
    ap.add_argument("--max-confirm-pairs", type=int, default=400)
    ap.add_argument("--s1", type=float, default=0.55, help="SPRTのH1（改善とみなす期待スコア）")
    ap.add_argument("--out", default="best_sprigling_ai.json")
    ap.add_argument("--roster-size", type=int, default=3, help="1ペアあたりのSpriglingの数（各側）")
    ap.add_argument("--stratified", action="store_true", help="構成内の階級を軽・中・重で均等に割り当てる")
    ap.add_argument("--roster-mode", choices=("mirror", "swap"), default="mirror",
                    help="mirror=両者同じ構成 / swap=両者別構成（2局目で構成も入れ替え）")
    args = ap.parse_args(argv)
    ROSTER.update(size=args.roster_size, stratified=args.stratified, mode=args.roster_mode)

    import optuna
    keys = args.keys.split(",") if args.keys else DEFAULT_KEYS
    for k in keys:
        if k not in HEURISTIC_WEIGHTS:
            raise SystemExit(f"未知の重みキー: {k}")
    overrides = json.loads(args.overrides) if args.overrides else None
    seeds = [f"tune{args.seed}-{j}" for j in range(args.pairs)]

    def objective(trial):
        w = suggest(trial, keys)
        samples = evaluate(w, seeds, overrides, args.workers)
        m = sum(samples) / len(samples)
        trial.set_user_attr("ci95", lab_stats.mean_ci(samples)[1:])
        print(f"trial {trial.number}: score={m:.3f}", flush=True)
        return m

    study = optuna.create_study(
        direction="maximize", study_name=args.study_name, storage=args.storage,
        load_if_exists=bool(args.storage),
        sampler=optuna.samplers.TPESampler(seed=int.from_bytes(args.seed.encode(), "little") % (2**31)))
    # 現在の重み（=Bと同じ）を最初のtrialとして入れておく（0.5付近の基準点）
    study.enqueue_trial({k: HEURISTIC_WEIGHTS[k] if k not in TIER5_RANGES
                         else max(TIER5_RANGES[k][0], HEURISTIC_WEIGHTS[k]) for k in keys})
    study.optimize(objective, n_trials=args.trials)

    done = [t for t in study.trials if t.value is not None]
    # 現在の重みと同一の候補（最初に入れた基準trial等）は再検定しない
    # （A/A比較なので「改善」になりえず、偶然の偽陽性の元になるだけ）
    baseline = {k: HEURISTIC_WEIGHTS[k] for k in keys}
    candidates = [t for t in done
                  if any(abs(t.params[k] - baseline[k]) > 1e-9 for k in keys)]
    top = sorted(candidates, key=lambda t: -t.value)[: args.confirm]
    # 複数候補を検定するので、有意水準をBonferroni補正する（全体で5%）
    alpha = 0.05 / max(1, len(top))
    print(f"== 上位{len(top)}trialを、選抜に使っていない新しいシードでSPRT再検定 "
          f"(H0: 0.5 / H1: {args.s1} / 各α={alpha:.3f})")
    confirmed = []
    for t in top:
        w = {k: t.params[k] for k in keys}
        held = [f"held{args.seed}-{t.number}-{j}" for j in range(args.max_confirm_pairs)]
        samples = []
        verdict = None
        for i in range(0, len(held), 50):
            samples += evaluate(w, held[i:i + 50], overrides, args.workers)
            verdict, llr = lab_stats.sprt_decision(samples, 0.5, args.s1, alpha=alpha)
            if verdict:
                break
        summ = lab_stats.summarize(samples)
        label = {"H1": "IMPROVED", "H0": "NOT_BETTER"}.get(verdict, "INCONCLUSIVE")
        print(f"  trial {t.number}: 選抜時{t.value:.3f} → 再検定 {summ} {label}")
        confirmed.append({"trial": t.number, "selection_score": t.value, "verdict": label,
                          "held_out": summ, "weights": w})

    best = next((c for c in confirmed if c["verdict"] == "IMPROVED"), None)
    out = {"confirmed": best is not None, "overrides": overrides, "keys": keys,
           "weights": (best or confirmed[0])["weights"] if confirmed else {},
           "candidates": confirmed,
           "labels": {k: WEIGHT_TIER_BY_KEY[k]["label"] for k in keys if k in WEIGHT_TIER_BY_KEY}}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"→ {args.out} (confirmed={out['confirmed']})")
    ns = argparse.Namespace(**vars(args))
    print("record:", V.save_record("tune-ai", ns, {
        "trials": [{"number": t.number, "value": t.value, "params": t.params} for t in done],
        "confirm": confirmed}))


if __name__ == "__main__":
    V._ensure_hash_seed()
    sys.exit(main())
