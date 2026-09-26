# -*- coding: utf-8 -*-
"""
tune_stat_model.py
==========================================================
Spriglingのステータス式の係数と戦闘ルール（連撃・攻城）をOptunaで探索する。

  --model physical : 部位の式×重量（旧既定）
  --model mass     : 体の質量が総合力、部位の作りが攻撃/耐久の配分を決める（sprigling._mass_model）

■ 1対1の絶対条件（--model mass のとき。違反したtrialは対局せずに棄却する）
  verify_lab.duel_report（隣接1対1・同時ダメージ・連撃込み・属性相性なし）で
    重量級 vs 軽量級 / 重量級 vs 中量級 : 負け 0%
    苔兵・棘走・岩守・毒舞 vs 軽量級      : 負け 0%
    動員コストが根張(300)以上のSprigling vs 根張 : 負け 0%
  相打ちは負けに数えない。

■ 実対局の目標（損失 = Σ(スコア − 目標)²。値は左側＝軽い側の勝率。--targets で変更）
  pure（対人戦の形: 各側5体がすべてその階級のSprigling）: 軽vs中0.30 / 中vs重0.30 / 軽vs重0.15（暫定）
  shape: 同じ重量で攻撃寄り vs 耐久寄り（強制動員）が 0.50
  （既存5種込みの旧目標は TARGETS["mass_with_base"]）
  探索対象には配置コスト比（placement_cost_ratio）も含む。
ビルドはシードから決まり全trial共通（共通乱数法）。最後に最良trialを新しいシードで再計測する。

例:
  python3 tune_stat_model.py --model mass --trials 60 --pairs 60 --storage sqlite:///runs/mass.db
"""

import argparse
import json
import os
import sys

import verify_lab as V

TARGETS = {
    "physical": {
        "vs-base": {"light": 0.45, "middle": 0.55, "heavy": 0.55},
        "bring": {"light_vs_middle": 0.5, "middle_vs_heavy": 0.5, "light_vs_heavy": 0.5},
    },
    # 既定は対人戦の形（pure: 各側5体がすべてその階級のSprigling）。値は左側（軽い側）の勝率の目標。
    # 暫定値: 重いほうがはっきり勝つが全勝ではない。--targets で上書きする前提。
    "mass": {
        "pure": {"light_vs_middle": 0.30, "middle_vs_heavy": 0.30, "light_vs_heavy": 0.15},
        "shape": {"forced": 0.5},
    },
    # 旧来の既存5種込みの目標（--targets に渡せば使える）
    "mass_with_base": {
        "vs-base": {"light": 0.45, "middle": 0.55, "heavy": 0.55},
        "bring": {"light_vs_middle": 0.45, "middle_vs_heavy": 0.45, "light_vs_heavy": 0.40},
        "shape": {"forced": 0.5},
    },
}


def suggest(trial, model):
    """(overrides, params) を返す。"""
    if model == "physical":
        ph = {
            "atk_scale": trial.suggest_float("atk_scale", 0.5, 4.0, log=True),
            "hp_scale": trial.suggest_float("hp_scale", 0.4, 3.0, log=True),
            "atk_weight_exp": trial.suggest_float("atk_weight_exp", 0.0, 2.0),
            "hp_weight_exp": trial.suggest_float("hp_weight_exp", 0.0, 2.0),
            "speed_weight_exp": trial.suggest_float("speed_weight_exp", 0.0, 2.0),
            "leg_speed_coef": trial.suggest_float("leg_speed_coef", 0.0, 0.3),
        }
        combat = {"initiative": trial.suggest_categorical("initiative", [False, True]),
                  "multi_attack": trial.suggest_categorical("multi_attack", [False, True])}
        return {"sprigling_stats": {"model": "physical", "physical": ph}, "combat": combat}
    half = trial.suggest_float("sigma_half_range", 0.1, 0.3)
    ms = {
        "atk0": trial.suggest_float("atk0", 24.0, 48.0),
        "hp0": trial.suggest_float("hp0", 140.0, 280.0),
        "atk_mass_exp": trial.suggest_float("atk_mass_exp", 0.2, 1.2),
        "hp_mass_exp": trial.suggest_float("hp_mass_exp", 0.2, 1.2),
        "atk_shape_exp": trial.suggest_float("atk_shape_exp", 0.3, 0.8),
        "hp_shape_exp": trial.suggest_float("hp_shape_exp", 0.3, 1.2),
        "sigma_min": 0.5 - half, "sigma_max": 0.5 + half,
        "speed_mass_exp": trial.suggest_float("speed_mass_exp", 0.0, 1.5),
        "leg_speed_coef": trial.suggest_float("leg_speed_coef", 0.0, 0.3),
        "siege_mass_exp": trial.suggest_float("siege_mass_exp", 0.0, 1.0),
        "class_step": trial.suggest_float("class_step", 0.0, 0.15),
    }
    # 配置コスト比（配置コスト = 動員コスト × これ）。対人戦の形では持ち込んだ駒の
    # ゲーム内の値段はこれだけなので、重いほど展開が遅れる度合いを直接決める。
    placement = trial.suggest_float("placement_cost_ratio", 0.1, 0.8)
    multi = trial.suggest_categorical("multi_attack", [False, True])
    combat = {"multi_attack": multi, "initiative": False,
              "siege": trial.suggest_categorical("siege", [False, True]),
              "guard_ratio": trial.suggest_float("guard_ratio", 0.0, 0.9)}
    if multi:
        combat["max_hits"] = trial.suggest_int("max_hits", 2, 4)
        combat["extra_hit_efficiency"] = trial.suggest_float("extra_hit_efficiency", 0.2, 1.0)
    return {"sprigling_stats": {"model": "mass", "mass": ms, "placement_cost_ratio": placement},
            "combat": combat}


def constraint_violation(overrides, samples):
    """1対1の絶対条件の違反量（負け率の合計）。0なら合格。"""
    V.apply_config_overrides(overrides)
    rep = V.duel_report(samples, "constraint", attribute=False)
    v = (rep["class"]["heavy_vs_light"]["loss"] + rep["class"]["heavy_vs_middle"]["loss"]
         + rep["class"]["pricier_vs_root"]["loss"])
    v += sum(r["loss"] for r in rep["core_vs_light"].values())
    return v, rep


def measure(overrides, pairs, seed, workers, model, targets=None):
    """targetsに含まれる検証（pure / vs-base / bring / shape）だけを実行する。"""
    targets = targets or TARGETS[model]
    res = {mode: {l: r["score"] for l, r in
                  V.run_classes(mode, pairs, overrides, "heuristic", workers, seed).items()}
           for mode in targets if mode in ("pure", "vs-base", "bring")}
    if "shape" in targets:
        res["shape"] = {"forced": V.run_shape_forced(overrides, pairs, workers, seed)["score"]}
    return res


def loss(result, targets):
    return sum((result[m][l] - t) ** 2 for m, rows in targets.items() for l, t in rows.items())


def show(result):
    return " | ".join(f"{l}={v:.2f}" for rows in result.values() for l, v in rows.items())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", choices=("physical", "mass"), default="mass")
    ap.add_argument("--trials", type=int, default=60)
    ap.add_argument("--pairs", type=int, default=60)
    ap.add_argument("--confirm-pairs", type=int, default=200)
    ap.add_argument("--duel-samples", type=int, default=120, help="1対1条件の判定に使う各階級のビルド数")
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    ap.add_argument("--seed", default="0")
    ap.add_argument("--storage")
    ap.add_argument("--study-name")
    ap.add_argument("--out", default="best_stat_model.json")
    ap.add_argument("--targets", help='目標(JSON)。例 \'{"pure":{"light_vs_middle":0.3,"middle_vs_heavy":0.3,"light_vs_heavy":0.15},"shape":{"forced":0.5}}\'')
    ap.add_argument("--warm-start", help="前回のbest_*.jsonのparamsを最初のtrialとして評価する")
    ap.add_argument("--warm-extra", help="warm-startのparamsに追加・上書きする値(JSON)")
    args = ap.parse_args(argv)

    import optuna
    targets = json.loads(args.targets) if args.targets else TARGETS[args.model]
    check = args.model == "mass"

    def objective(trial):
        ov = suggest(trial, args.model)
        if check:
            viol, _ = constraint_violation(ov, args.duel_samples)
            trial.set_user_attr("violation", viol)
            if viol > 0:
                print(f"trial {trial.number}: 1対1条件違反 {viol:.3%} → 棄却", flush=True)
                return 10.0 + viol
        res = measure(ov, args.pairs, f"stat{args.seed}", args.workers, args.model, targets)
        trial.set_user_attr("result", res)
        value = loss(res, targets)
        print(f"trial {trial.number}: loss={value:.4f} {show(res)}", flush=True)
        return value

    study = optuna.create_study(direction="minimize",
                                study_name=args.study_name or f"sprigling_stat_{args.model}",
                                storage=args.storage, load_if_exists=bool(args.storage),
                                sampler=optuna.samplers.TPESampler(seed=0))
    if args.warm_start:
        with open(args.warm_start, encoding="utf-8") as f:
            warm = dict(json.load(f)["params"])
        warm.update(json.loads(args.warm_extra) if args.warm_extra else {})
        study.enqueue_trial(warm, skip_if_exists=True)
    study.optimize(objective, n_trials=args.trials)

    best = study.best_trial
    if best.value >= 10.0:
        raise SystemExit("1対1条件を満たすtrialが無かった。--trialsを増やすか探索範囲を見直すこと。")
    ov = suggest(optuna.trial.FixedTrial(best.params), args.model)
    print("== 最良trialを新しいシードで再計測")
    held = measure(ov, args.confirm_pairs, f"held{args.seed}", args.workers, args.model, targets)
    held_loss = loss(held, targets)
    out = {"model": args.model, "params": best.params, "overrides": ov, "targets": targets,
           "selection_loss": best.value, "held_out_loss": held_loss, "held_out": held}
    if check:
        viol, rep = constraint_violation(ov, 300)  # 大きめのサンプルで1対1条件を再確認
        out["duel_check"] = {"violation": viol, "report": rep}
        print(f"  1対1条件（各階級300体）: 違反 {viol:.3%}")
        if viol > 0:
            print("  [警告] 大きめのサンプルでは1対1条件を満たしていない。--duel-samples を増やして"
                  "再探索するか、class_step を少し上げて duels で再確認すること。")
    print(f"  選抜時 loss={best.value:.4f} → 再計測 loss={held_loss:.4f}  {show(held)}")
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"→ {args.out}")
    print("record:", V.save_record("tune-stat-model", args, {
        "trials": [{"number": t.number, "value": t.value, "params": t.params,
                    "result": t.user_attrs.get("result"), "violation": t.user_attrs.get("violation")}
                   for t in study.trials if t.value is not None],
        "best": out}))


if __name__ == "__main__":
    V._ensure_hash_seed()
    sys.exit(main())
