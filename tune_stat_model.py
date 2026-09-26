# -*- coding: utf-8 -*-
"""
tune_stat_model.py
==========================================================
Spriglingの「physical」ステータス式（部位の式×重量、sprigling.py参照）の係数と
素早さルール（先制・連撃）をOptunaで探索し、階級バランスの目標に最も近い組み合わせを探す。

「コストで帳尻を合わせる」のではなく、見た目（部位・重量）から決まる式の係数だけを動かして、
結果として階級間のバランスが取れる点を探す、という位置づけ。係数は少数で、全て意味を持つ:
  atk_scale / hp_scale           : 全体の強さの水準
  atk_weight_exp / hp_weight_exp : 重いほど攻撃力・HPが伸びる度合い
  speed_weight_exp / leg_speed_coef : 軽いほど・脚が長いほど速い度合い
  initiative / multi_attack      : 素早さの効き方（戦闘ルール）

1 trial = verify_lab の classes を vs-base と bring の両モードで --pairs ペアずつ。
損失 = Σ(スコア − 目標)²。目標の既定値:
  vs-base（Sprigling込み vs 既存5種のみ）: 軽量0.45 / 中量0.55 / 重量0.55
  bring（両者1体持ち込み・階級総当たり）   : 全て0.50（どの階級を持ち込んでも互角）
ビルドはシードから決まり全trial共通（共通乱数法）。最後に最良trialを新しいシードで再計測する。

例:
  python3 tune_stat_model.py --trials 40 --pairs 60
  python3 tune_stat_model.py --trials 30 --fix-combat '{"initiative":true,"multi_attack":true}'
"""

import argparse
import copy
import json
import os
import sys

import verify_lab as V

DEFAULT_TARGETS = {
    "vs-base": {"light": 0.45, "middle": 0.55, "heavy": 0.55},
    "bring": {"light_vs_middle": 0.5, "middle_vs_heavy": 0.5, "light_vs_heavy": 0.5},
}


def overrides_for(params, fix_combat):
    ph = {k: params[k] for k in ("atk_scale", "hp_scale", "atk_weight_exp", "hp_weight_exp",
                                  "speed_weight_exp", "leg_speed_coef")}
    combat = dict(fix_combat) if fix_combat is not None else {
        "initiative": params["initiative"], "multi_attack": params["multi_attack"]}
    return {"sprigling_stats": {"model": "physical", "physical": ph}, "combat": combat}


def measure(overrides, pairs, seed, workers):
    return {mode: V.run_classes(mode, pairs, overrides, "heuristic", workers, seed)
            for mode in ("vs-base", "bring")}


def loss(result, targets):
    total = 0.0
    for mode, rows in targets.items():
        for label, target in rows.items():
            total += (result[mode][label]["score"] - target) ** 2
    return total


def show(result):
    return " | ".join(f"{l}={r['score']:.2f}" for m in ("vs-base", "bring") for l, r in result[m].items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trials", type=int, default=40)
    ap.add_argument("--pairs", type=int, default=60)
    ap.add_argument("--confirm-pairs", type=int, default=200)
    ap.add_argument("--fix-combat", help='戦闘ルールを固定する(JSON) 例 {"initiative":true,"multi_attack":false}')
    ap.add_argument("--targets", help="目標値(JSON)。DEFAULT_TARGETSと同じ形")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--seed", default="0")
    ap.add_argument("--storage")
    ap.add_argument("--study-name", default="sprigling_stat_model")
    ap.add_argument("--out", default="best_stat_model.json")
    args = ap.parse_args(argv)

    import optuna
    targets = json.loads(args.targets) if args.targets else DEFAULT_TARGETS
    fix_combat = json.loads(args.fix_combat) if args.fix_combat else None

    def objective(trial):
        p = {
            "atk_scale": trial.suggest_float("atk_scale", 0.5, 4.0, log=True),
            "hp_scale": trial.suggest_float("hp_scale", 0.4, 3.0, log=True),
            "atk_weight_exp": trial.suggest_float("atk_weight_exp", 0.0, 2.0),
            "hp_weight_exp": trial.suggest_float("hp_weight_exp", 0.0, 2.0),
            "speed_weight_exp": trial.suggest_float("speed_weight_exp", 0.0, 2.0),
            "leg_speed_coef": trial.suggest_float("leg_speed_coef", 0.0, 0.3),
        }
        if fix_combat is None:
            p["initiative"] = trial.suggest_categorical("initiative", [False, True])
            p["multi_attack"] = trial.suggest_categorical("multi_attack", [False, True])
        res = measure(overrides_for(p, fix_combat), args.pairs, f"stat{args.seed}", args.workers)
        trial.set_user_attr("result", {m: {l: r["score"] for l, r in rows.items()} for m, rows in res.items()})
        value = loss(res, targets)
        print(f"trial {trial.number}: loss={value:.4f} {show(res)}", flush=True)
        return value

    study = optuna.create_study(direction="minimize", study_name=args.study_name,
                                storage=args.storage, load_if_exists=bool(args.storage),
                                sampler=optuna.samplers.TPESampler(seed=0))
    # 現行の既定係数を基準点として最初に評価する
    from config import CONFIG
    base = dict(CONFIG["sprigling_stats"]["physical"])
    base.pop("weight_ref", None)
    if fix_combat is None:
        base.update(initiative=False, multi_attack=False)
    study.enqueue_trial(base)
    study.optimize(objective, n_trials=args.trials)

    best = study.best_trial
    p = dict(best.params)
    ov = overrides_for(p, fix_combat)
    print("== 最良trialを新しいシードで再計測")
    held = measure(ov, args.confirm_pairs, f"held{args.seed}", args.workers)
    held_loss = loss(held, targets)
    print(f"  選抜時 loss={best.value:.4f} → 再計測 loss={held_loss:.4f}  {show(held)}")
    out = {"params": p, "overrides": ov, "targets": targets,
           "selection_loss": best.value, "held_out_loss": held_loss,
           "held_out": {m: {l: {k: r[k] for k in ("score", "ci95", "adoption")} for l, r in rows.items()}
                        for m, rows in held.items()}}
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"→ {args.out}")
    print("record:", V.save_record("tune-stat-model", args, {
        "trials": [{"number": t.number, "value": t.value, "params": t.params,
                    "result": t.user_attrs.get("result")} for t in study.trials if t.value is not None],
        "best": out}))


if __name__ == "__main__":
    V._ensure_hash_seed()
    sys.exit(main())
