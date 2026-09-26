# -*- coding: utf-8 -*-
"""
verify_lab.py
==========================================================
Sprigling（ステータスが固定でない駒）時代の検証環境。3つの性質を満たすことを目的にする:

  再現できる  : 対局は全てシード文字列から決まる（ボットの乱数・RandomBotの乱数とも）。
                PYTHONHASHSEED=0で自分自身を起動し直すので、駒種名のset反復順も固定。
                実行ごとに runs/*.json へ git commit・未コミット変更の有無・CONFIGのハッシュ・
                引数・全ペアの結果を保存する。`repro` で同じ対局が同じ結果になるかを確認できる。
  統計的に正しい: 先後入れ替えの「ペア」を1標本とし（lab_stats.py）、95%CIとSPRTで判定する。
                探索（exploit）で選ばれた候補は、選抜に使ったのとは別のシードで再検定する
                （選抜に使った対局で評価すると、たまたま勝った候補を過大評価するため）。
  壊れたビルドを探す: `exploit` が Whittlewisp のビルド空間を進化的に探索し、
                「同じくらいの動員コストの標準ビルド」を持つ相手に勝ち越すビルドを探す。

サブコマンド:
  calibrate        Spriglingのステータス式の静的チェック（階級ごとの強さ、既存駒との1対1）
  compare          AI同士の比較。--random-rosters でペアごとに違うSprigling構成を両者に配る
  classes          重量階級ごとのバランス（vs-base: 既存5種のみの相手と / bring: 階級総当たり）
  exploit          壊れたビルドの自動探索
  placement-sweep  配置コスト比率ごとの、Spriglingの採用率と勝率
  aa-test          両側同条件の対局でスコアが0.5になるか（検証環境そのもののバグ検出）
  shape-split      攻撃寄りが強いのは式かAIかの切り分け
  repro            同じ対局を2回流して結果が一致するかの確認

例:
  python3 verify_lab.py calibrate
  python3 verify_lab.py compare --a heuristic --b random --pairs 100
  python3 verify_lab.py compare --a heuristic --b heuristic --random-rosters --pairs 200
  python3 verify_lab.py exploit --weight-class middle --pop 12 --gens 6
  python3 verify_lab.py placement-sweep --ratios 0.5,0.75,1.0 --pairs 100
"""

import argparse
import copy
import datetime
import hashlib
import json
import multiprocessing as mp
import os
import random
import subprocess
import sys
import time

import lab_stats
import sprigling as S
from config import CONFIG, apply_config_overrides
from game import Game, RandomBot, type_multiplier
# ボット関連は先に読み込んでおく（ワーカーはforkで親のメモリを引き継ぐので、
# 長時間の実行中にファイルを編集しても実行中の検証には混ざらない）
from heuristic_bot import HeuristicBot
from search_bot_skeleton import SearchBot
from weights import resolve_weights

HERE = os.path.dirname(os.path.abspath(__file__))
RUNS_DIR = os.path.join(HERE, "runs")
CORE_KINDS = ("歩兵", "騎兵", "重装兵")


# ============================================================
# 再現性: ハッシュシード固定・実行記録
# ============================================================
def _ensure_hash_seed():
    if os.environ.get("PYTHONHASHSEED") != "0":
        env = dict(os.environ, PYTHONHASHSEED="0")
        os.execve(sys.executable, [sys.executable] + sys.argv, env)


def _git(*args):
    try:
        return subprocess.check_output(["git", *args], cwd=HERE, stderr=subprocess.DEVNULL,
                                       text=True).strip()
    except Exception:
        return None


def config_hash():
    blob = json.dumps(CONFIG, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def save_record(command, args, results):
    os.makedirs(RUNS_DIR, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path = os.path.join(RUNS_DIR, f"{stamp}_{command}.json")
    apply_config_overrides(None)
    record = {
        "command": command,
        "argv": sys.argv[1:],
        "args": vars(args),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "python": sys.version.split()[0],
        "pythonhashseed": os.environ.get("PYTHONHASHSEED"),
        "base_config_hash": config_hash(),
        "results": results,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=1, default=str)
    return path


# ============================================================
# シナリオ（1回の対局設定）
# scenario = {
#   "config_overrides": dict|None,                   apply_config_overrides()に渡す
#   "spriglings": {name: {"genome": g, "seed": int}}, 登録するSprigling（駒種名は"S:<name>"）
#   "rosters": {"A": [kind...]|None, "B": ...},       None=登録済み全駒種
#   "bots": {"A": "heuristic", "B": "heuristic"},  （{"type": "heuristic", "weights": {...}} も可）
#   "start_extra": {"A": [kind...], "B": ...},        最初の手駒に追加する駒（省略可）
# }
# 側A/Bは先後入れ替えに追従する（A側のロースター・ボットはA側が先手でも後手でも同じ）。
# ============================================================
BASE_KINDS = ("歩兵", "騎兵", "重装兵", "弓兵", "工兵")

_applied_key = None


def apply_scenario(sc):
    """CONFIGをシナリオの状態にする（同じシナリオなら何もしない）。"""
    global _applied_key
    key = json.dumps({k: sc.get(k) for k in ("config_overrides", "spriglings")}, sort_keys=True)
    if key == _applied_key:
        return
    apply_config_overrides(sc.get("config_overrides"))
    for name, spec in (sc.get("spriglings") or {}).items():
        S.register_sprigling(name, spec["genome"], spec.get("seed", 0))
    _applied_key = key


def make_bot(spec, rng_seed):
    """spec: "heuristic" / "search" / "random"、または {"type": ..., "weights": {重みの上書き}}。"""
    weights = None
    kind = spec
    if isinstance(spec, dict):
        kind = spec["type"]
        if spec.get("weights"):
            weights = resolve_weights(spec["weights"])
    if kind == "heuristic":
        return HeuristicBot(weights=weights, rng=random.Random(rng_seed))
    if kind == "search":
        return SearchBot(weights=weights)  # rng=None: 完全決定論
    if kind == "random":
        return RandomBot()
    raise ValueError(kind)


def play_game(sc, seed, a_seat):
    """1局。A側の結果（1/0.5/0）と集計用の情報を返す。"""
    apply_scenario(sc)
    random.seed(f"game-{seed}-{a_seat}")  # RandomBotはグローバル乱数を使う
    b_seat = 1 - a_seat
    rosters = sc.get("rosters") or {}
    extra = sc.get("start_extra") or {}
    game = Game(roster={a_seat: rosters.get("A"), b_seat: rosters.get("B")},
                extra_reserve={a_seat: extra.get("A"), b_seat: extra.get("B")})
    bots = {a_seat: make_bot(sc["bots"]["A"], f"bot-{seed}-{a_seat}-A"),
            b_seat: make_bot(sc["bots"]["B"], f"bot-{seed}-{a_seat}-B")}
    res = game.run(bots)
    produced = {"A": [], "B": []}
    for rec in game.kifu:
        for p, k in rec.get("produced", {}).items():
            produced["A" if p == a_seat else "B"].append(k)
    if res["winner"] is None:
        score = 0.5
    else:
        score = 1.0 if res["winner"] == a_seat else 0.0
    return {"score": score, "reason": res["reason"], "rounds": res["rounds"],
            "a_seat": a_seat, "produced": produced}


def play_pair(task):
    sc, seed = task
    g1 = play_game(sc, seed, 0)
    g2 = play_game(sc, seed, 1)
    return {"seed": seed, "pair_score": (g1["score"] + g2["score"]) / 2, "games": [g1, g2]}


def run_pairs(tasks, workers):
    """[(scenario, seed), ...] を並列に消化する（結果の順序は入力順で固定）。"""
    if workers <= 1:
        return [play_pair(t) for t in tasks]
    with mp.get_context("fork").Pool(workers) as pool:
        return pool.map(play_pair, tasks, chunksize=1)


def adoption(pair_results, side="A"):
    """side側の動員のうちSpriglingが占める割合。"""
    total = spr = 0
    for pr in pair_results:
        for g in pr["games"]:
            for k in g["produced"][side]:
                total += 1
                spr += S.is_sprigling(k)
    return round(spr / total, 4) if total else 0.0


# ============================================================
# 標準ビルド（比較対照）
# ============================================================
def _with_material(genome, material):
    g = copy.deepcopy(genome)
    for p in g.values():
        p["material"] = material
    return g


def reference_pool():
    """対照群。R/H/W（中量級）と固定シードで引いた軽量級・重量級3体ずつを、
    全部位bind/pierce/crushの3通りの材質で作ったもの（重量級上限を超えるものは除く）。
    材質を揃えるのは、対照と候補の属性を一致させて「属性相性のメタ」ではなく
    「コストあたりの性能」だけを比べるため。"""
    bases = dict(S.REFERENCE_BUILDS)
    rng = random.Random("reference-pool-v1")
    for wc, tag in (("light", "L"), ("heavy", "X")):
        for i in range(3):
            bases[f"{tag}{i + 1}"] = S.random_genome(rng, wc, material="bind")
    pool = {}
    for name, g in bases.items():
        for m in ("bind", "pierce", "crush"):
            gm = _with_material(g, m)
            if S.weight_class_of(S.raw_stats(gm, 0)["weight"]) is not None:
                pool[f"{name}-{m}"] = {"genome": gm, "seed": 0}
    return pool


def nearest_reference(stats, pool_stats):
    """動員コストが最も近い、同じ属性の対照ビルド名。"""
    same = [n for n in pool_stats if pool_stats[n]["attribute"] == stats["attribute"]]
    return min(same, key=lambda n: abs(pool_stats[n]["produce_cost"] - stats["produce_cost"]))


# ============================================================
# calibrate: ステータス式の静的チェック
# ============================================================
def _hits(a, b):
    cb = CONFIG["combat"]
    if not cb["multi_attack"]:
        return 1
    base = cb["base_speed"]
    ratio = a.get("speed", base) / max(b.get("speed", base), 1e-9)
    n = 1
    for i, th in enumerate(cb["multi_attack_thresholds"]):
        if ratio >= th:
            n = i + 2
    return n


def duel(a, b, mult_ab=1.0, mult_ba=1.0):
    """隣接1対1の殴り合い（同時ダメージ。連撃ルールが有効なら素早さ比の回数を掛ける）。
    aの勝ち=1, 引き分け=0.5, 負け=0。"""
    mult_ab *= _hits(a, b)
    mult_ba *= _hits(b, a)
    ha, hb = a["hp"], b["hp"]
    for _ in range(10000):
        ha, hb = ha - b["atk"] * mult_ba, hb - a["atk"] * mult_ab
        if ha <= 0 or hb <= 0:
            break
    if ha > 0 >= hb:
        return 1.0
    if hb > 0 >= ha:
        return 0.0
    return 0.5


def cmd_calibrate(args):
    apply_config_overrides(None)
    core = {k: CONFIG["pieces"][k] for k in CORE_KINDS}
    ref_power = {k: v["hp"] * v["atk"] for k, v in core.items()}
    results = {"reference_builds": {}, "classes": {}}
    print("== 基準ビルド（Whittlewisp R/H/W）")
    for name, g in S.REFERENCE_BUILDS.items():
        st = S.derive_stats(g, 0)
        results["reference_builds"][name] = st
        print(f"  {name}: HP{st['hp']} ATK{st['atk']} 移動{st['move']} 属性{st['attribute']} "
              f"動員{st['produce_cost']} 配置{st['cost']} 重量{st['sprigling']['weight']}")
    rng = random.Random(f"calibrate-{args.seed}")
    print(f"== ランダムビルド各{args.samples}体: 既存駒との1対1（属性相性なし）で勝つ割合")
    for wc in ("light", "middle", "heavy"):
        stats = [S.derive_stats(S.random_genome(rng, wc), 0) for _ in range(args.samples)]
        row = {}
        for k, c in core.items():
            outcomes = [duel(st, c) for st in stats]
            row[k] = round(outcomes.count(1.0) / len(stats), 3)          # 勝ち
            row[k + "_draw"] = round(outcomes.count(0.5) / len(stats), 3)  # 相打ち
        power = sorted(st["hp"] * st["atk"] / 7200 for st in stats)
        row["power_vs_core_median"] = round(power[len(power) // 2], 3)
        row["power_vs_core_max"] = round(power[-1], 3)
        results["classes"][wc] = row
        print(f"  {wc:6s} 勝率 苔兵に{row['歩兵']:.0%} 棘走に{row['騎兵']:.0%} 岩守に{row['重装兵']:.0%} "
              f"| HP×ATKは既存駒の 中央値{row['power_vs_core_median']:.2f}倍 最大{row['power_vs_core_max']:.2f}倍")
    # 軽量級が既存駒に1対1で勝つのは1%以下（連撃で稀に勝つ個体は許容）
    checks = {
        "light_rarely_beats_core_neutral": all(results["classes"]["light"][k] <= 0.01 for k in CORE_KINDS),
    }
    if CONFIG["sprigling_stats"]["model"] == "cost":
        # cost式のみ: 中量級の基準ビルド(R/H/W)のHP×攻撃力の平均が既存駒よりやや強い。
        # physical式では「HP×攻撃力」が強さの物差しにならない（shape-split参照）ので
        # 実対局の classes で判定する。
        checks["reference_builds_slightly_stronger"] = 1.0 <= sum(
            st["hp"] * st["atk"] for st in results["reference_builds"].values()
        ) / len(results["reference_builds"]) / 7200 <= 1.4
    results["checks"] = checks
    for k, v in checks.items():
        print(f"  [{'OK' if v else 'NG'}] {k}")
    print("record:", save_record("calibrate", args, results))
    return 0 if all(checks.values()) else 1


# ============================================================
# compare: AI同士の比較
# ============================================================
def _random_roster_scenario(seed, n_spriglings):
    rng = random.Random(f"roster-{seed}")
    spr = {}
    for i in range(n_spriglings):
        wc = rng.choice(("light", "middle", "heavy"))
        roll_seed = rng.randint(0, 10**6)  # 個体差ロール。重量判定も同じロールで行う
        spr[f"r{i}"] = {"genome": S.random_genome(rng, wc, seed=roll_seed), "seed": roll_seed}
    return spr


def cmd_compare(args):
    tasks = []
    for i in range(args.pairs):
        seed = f"{args.seed}-{i}"
        sc = {"config_overrides": None, "rosters": {"A": None, "B": None},
              "bots": {"A": args.a, "B": args.b},
              "spriglings": _random_roster_scenario(seed, args.roster_size) if args.random_rosters else {}}
        tasks.append((sc, seed))
    t0 = time.time()
    pairs = _run_with_sprt(tasks, args)
    summary = lab_stats.summarize([p["pair_score"] for p in pairs])
    summary["elapsed_sec"] = round(time.time() - t0, 1)
    print(f"{args.a} vs {args.b}: {summary}")
    print("record:", save_record("compare", args, {"summary": summary, "pairs": pairs}))


def _run_with_sprt(tasks, args):
    """args.sprtが指定されていれば、args.batchペアごとにSPRTを判定して早期終了する。"""
    if not getattr(args, "sprt", None):
        return run_pairs(tasks, args.workers)
    s0, s1 = (float(x) for x in args.sprt.split(","))
    done = []
    for i in range(0, len(tasks), args.batch):
        done += run_pairs(tasks[i:i + args.batch], args.workers)
        verdict, llr = lab_stats.sprt_decision([p["pair_score"] for p in done], s0, s1,
                                               alpha=getattr(args, "alpha", 0.05))
        print(f"  {len(done)} pairs: LLR={llr:.2f} verdict={verdict}")
        if verdict:
            break
    return done


# ============================================================
# classes: 重量階級ごとのバランス
# ============================================================
CLASS_PAIRS = (("light", "middle"), ("middle", "heavy"), ("light", "heavy"))


def _class_genomes(seed, wc, n):
    rng = random.Random(f"class-{seed}-{wc}")
    return [S.random_genome(rng, wc) for _ in range(n)]


def class_scenarios(mode, seed, overrides, bot, per_class=3):
    """1ペア分のシナリオを {行ラベル: scenario} で返す。ビルドはシードから決まるので、
    overrides（ステータス式・戦闘ルール）だけを変えた比較は同じビルドどうしの対応比較になる。"""
    base = list(BASE_KINDS)
    out = {}
    if mode == "vs-base":
        for wc in ("light", "middle", "heavy"):
            spr = {f"{wc}{i}": {"genome": g, "seed": 0}
                   for i, g in enumerate(_class_genomes(seed, wc, per_class))}
            out[wc] = {"config_overrides": overrides, "spriglings": spr,
                       "rosters": {"A": base + [f"S:{n}" for n in spr], "B": base},
                       "bots": {"A": bot, "B": bot}}
    else:  # bring: 両者が1体ずつ持ち込み（最初の手駒）、同じ駒を追加動員もできる
        for wa, wb in CLASS_PAIRS:
            ga = _class_genomes(seed, wa, 1)[0]
            gb = _class_genomes(seed, wb, 1)[0]
            spr = {"a": {"genome": ga, "seed": 0}, "b": {"genome": gb, "seed": 0}}
            out[f"{wa}_vs_{wb}"] = {"config_overrides": overrides, "spriglings": spr,
                                    "rosters": {"A": base + ["S:a"], "B": base + ["S:b"]},
                                    "start_extra": {"A": ["S:a"], "B": ["S:b"]},
                                    "bots": {"A": bot, "B": bot}}
    return out


def run_classes(mode, pairs, overrides, bot, workers, seed="0"):
    tasks, labels = [], []
    for j in range(pairs):
        for label, sc in class_scenarios(mode, f"{seed}-{j}", overrides, bot).items():
            tasks.append((sc, f"cls-{seed}-{j}"))
            labels.append(label)
    res = run_pairs(tasks, workers)
    rows = {}
    for label in dict.fromkeys(labels):
        rs = [r for l, r in zip(labels, res) if l == label]
        rows[label] = dict(lab_stats.summarize([r["pair_score"] for r in rs]),
                           adoption=adoption(rs), rounds=round(sum(
                               g["rounds"] for r in rs for g in r["games"]) / (2 * len(rs)), 1))
    return rows


def cmd_classes(args):
    overrides = json.loads(args.overrides) if args.overrides else None
    rows = run_classes(args.mode, args.pairs, overrides, args.bot, args.workers, args.seed)
    for label, r in rows.items():
        print(f"  {label:16s} score={r['score']:.3f} CI{r['ci95']} 採用率{r['adoption']:.0%} 平均{r['rounds']}R")
    print("record:", save_record("classes", args, {"rows": rows}))


# ============================================================
# exploit: 壊れたビルドの自動探索
# ============================================================
def mutate(genome, rng, weight_class=None, tries=200):
    for _ in range(tries):
        g = copy.deepcopy(genome)
        for _ in range(rng.choice((1, 1, 2, 3))):
            pt = rng.choice(S.PART_TYPES)
            p = g[pt]
            field = rng.choice(("size", "length", "count", "material", "angle"))
            if field in ("size", "length"):
                p[field] = max(S.PARAM_MIN, min(S.PARAM_MAX, p[field] + rng.choice((-1, 1))))
            elif field == "count" and pt not in S.VITAL:
                p["count"] = max(0, min(S.COUNT_MAX[pt], p["count"] + rng.choice((-1, 1))))
            elif field == "material":
                p["material"] = rng.choice(("bind", "pierce", "crush"))
            elif field == "angle" and pt in S.ANGLE_TYPES:
                p["angle"] = max(0, min(3, p["angle"] + rng.choice((-1, 1))))
        if S.validate_genome(g):
            continue
        w = S.raw_stats(g, 0)["weight"]
        wc = S.weight_class_of(w)
        if wc is None or (weight_class and wc != weight_class):
            continue
        return g
    return genome


def _exploit_scenario(genome, ref_name, ref_spec, bots):
    return {
        "config_overrides": None,
        "spriglings": {"cand": {"genome": genome, "seed": 0}, ref_name: ref_spec},
        "rosters": {"A": list(BASE_KINDS) + ["S:cand"], "B": list(BASE_KINDS) + [f"S:{ref_name}"]},
        "bots": {"A": bots, "B": bots},
    }


def cmd_exploit(args):
    rng = random.Random(f"exploit-{args.seed}")
    apply_config_overrides(None)
    pool = reference_pool()
    pool_stats = {n: S.derive_stats(s["genome"], s["seed"]) for n, s in pool.items()}

    def evaluate(population, gen):
        tasks, index = [], []
        for ci, g in enumerate(population):
            ref = nearest_reference(S.derive_stats(g, 0), pool_stats)
            sc = _exploit_scenario(g, ref, pool[ref], args.bot)
            for j in range(args.pairs):
                # 同世代の全候補で同じシード列を使う（共通乱数法: 候補間の比較の分散を下げる）
                tasks.append((sc, f"x{args.seed}-g{gen}-{j}"))
                index.append(ci)
        res = run_pairs(tasks, args.workers)
        scores = [[] for _ in population]
        adopt = [[] for _ in population]
        for ci, r in zip(index, res):
            scores[ci].append(r["pair_score"])
            adopt[ci].append(r)
        return [(sum(s) / len(s), adoption(a)) for s, a in zip(scores, adopt)]

    population = [S.random_genome(rng, args.weight_class) for _ in range(args.pop)]
    fitness = evaluate(population, 0)
    history = []
    for gen in range(1, args.gens + 1):
        ranked = sorted(zip(fitness, population), key=lambda t: -t[0][0])
        elite = ranked[: max(2, args.pop // 3)]
        best = ranked[0]
        history.append({"gen": gen - 1, "best_score": round(best[0][0], 4), "best_adoption": best[0][1]})
        print(f"gen {gen - 1}: best={best[0][0]:.3f} (adoption {best[0][1]:.0%})")
        children = [mutate(rng.choice(elite)[1], rng, args.weight_class)
                    for _ in range(args.pop - len(elite))]
        child_fit = evaluate(children, gen)
        # 親の適応度は前世代のシードで測った値のままにせず、エリートも今世代のシードで測り直す
        elite_fit = evaluate([g for _, g in elite], gen)
        population = [g for _, g in elite] + children
        fitness = elite_fit + child_fit

    ranked = sorted(zip(fitness, population), key=lambda t: -t[0][0])
    alpha = 0.05 / max(1, args.confirm)  # 複数候補の検定なのでBonferroni補正（全体で5%）
    print(f"== 上位{args.confirm}候補を、選抜に使っていない新しいシードでSPRT再検定 "
          f"(H0: score={args.s0} / H1: score={args.s1} / 各α={alpha:.3f})")
    findings = []
    for rank, ((score, adopt), g) in enumerate(ranked[: args.confirm]):
        st = S.derive_stats(g, 0)
        ref = nearest_reference(st, pool_stats)
        sc = _exploit_scenario(g, ref, pool[ref], args.bot)
        tasks = [(sc, f"confirm{args.seed}-{rank}-{j}") for j in range(args.max_confirm_pairs)]
        cargs = argparse.Namespace(sprt=f"{args.s0},{args.s1}", batch=args.batch, workers=args.workers,
                                   alpha=alpha)
        pairs = _run_with_sprt(tasks, cargs)
        samples = [p["pair_score"] for p in pairs]
        verdict, llr = lab_stats.sprt_decision(samples, args.s0, args.s1, alpha=alpha)
        label = {"H1": "BROKEN", "H0": "OK"}.get(verdict, "INCONCLUSIVE")
        summary = lab_stats.summarize(samples)
        print(f"  #{rank + 1} {label}: {summary} vs 対照{ref}({pool_stats[ref]['produce_cost']}RP) | "
              f"HP{st['hp']} ATK{st['atk']} 移動{st['move']} {st['attribute']} 動員{st['produce_cost']}RP "
              f"採用率{adoption(pairs):.0%}")
        findings.append({"rank": rank + 1, "verdict": label, "llr": round(llr, 3), "summary": summary,
                         "selection_score": round(score, 4), "adoption": adoption(pairs),
                         "control": ref, "stats": st, "genome": g})
    path = save_record("exploit", args, {"history": history, "findings": findings,
                                         "reference_pool": {n: pool_stats[n] for n in pool_stats}})
    print("record:", path)


# ============================================================
# placement-sweep: 配置コスト比率の検討
# ============================================================
def cmd_placement_sweep(args):
    apply_config_overrides(None)
    pool = reference_pool()
    names = [f"S:{n}" for n in pool]
    out = []
    for ratio in (float(x) for x in args.ratios.split(",")):
        sc = {"config_overrides": {"sprigling_stats": {"placement_cost_ratio": ratio}},
              "spriglings": pool,
              "rosters": {"A": list(BASE_KINDS) + names, "B": list(BASE_KINDS)},
              "bots": {"A": args.bot, "B": args.bot}}
        pairs = run_pairs([(sc, f"place{args.seed}-{j}") for j in range(args.pairs)], args.workers)
        summary = lab_stats.summarize([p["pair_score"] for p in pairs])
        row = {"ratio": ratio, "summary": summary, "sprigling_adoption": adoption(pairs)}
        out.append(row)
        print(f"ratio {ratio:.2f}: Sprigling込み側のスコア {summary['score']:.3f} "
              f"CI{summary['ci95']} | 動員に占めるSprigling {row['sprigling_adoption']:.0%}")
    print("record:", save_record("placement-sweep", args, {"rows": out}))


# ============================================================
# shape-split: 「攻撃寄りが強い」のは式（ゲームの仕組み）かAIの評価か
# ============================================================
def _shaped_piece(power, atk_hp_ratio, cost=450):
    import math
    return {"hp": round(math.sqrt(power / atk_hp_ratio)), "atk": round(math.sqrt(power * atk_hp_ratio)),
            "move": 2, "cost": round(cost * 0.75), "produce_cost": cost, "upkeep": 0,
            "ranged": False, "range": 0, "attribute": "crush", "role": "重装兵"}


def cmd_shape_split(args):
    """同コスト・同HP×攻撃力で形だけ違う2駒（攻撃寄り vs HP寄り）を、
    (a) 既存5種と一緒に選べる / (b) それしか動員できない（強制）
    × AIの駒評価が 従来式 / 苔兵の比率に合わせた式（hp_valuation_bonus=0.8）
    の4条件で戦わせる。(b)で差が消えれば「AIの選り好み」、残れば「ゲームの仕組み」。"""
    atk_p = _shaped_piece(8100, 0.25)
    hp_p = _shaped_piece(8100, 0.12)
    base = list(BASE_KINDS)
    consistent = {"type": "heuristic", "weights": {"hp_valuation_bonus": 0.8}}
    conds = [
        ("mixed / default AI", base + ["S:atk"], base + ["S:hp"], "heuristic"),
        ("mixed / consistent AI", base + ["S:atk"], base + ["S:hp"], consistent),
        ("forced / default AI", ["S:atk"], ["S:hp"], "heuristic"),
        ("forced / consistent AI", ["S:atk"], ["S:hp"], consistent),
    ]
    print(f"攻撃寄り HP{atk_p['hp']} ATK{atk_p['atk']} / HP寄り HP{hp_p['hp']} ATK{hp_p['atk']} "
          f"（どちらも450RP・HP×ATK≈8100・crush）")
    rows = {}
    for name, ra, rb, bot in conds:
        sc = {"config_overrides": {"pieces": {"S:atk": atk_p, "S:hp": hp_p}}, "spriglings": {},
              "rosters": {"A": ra, "B": rb}, "bots": {"A": bot, "B": bot}}
        res = run_pairs([(sc, f"split{args.seed}-{j}") for j in range(args.pairs)], args.workers)
        summ = lab_stats.summarize([r["pair_score"] for r in res])
        rows[name] = dict(summ, adoption_atk=adoption(res, "A"), adoption_hp=adoption(res, "B"))
        print(f"  {name:24s} 攻撃寄り側 {summ['score']:.3f} CI{summ['ci95']} "
              f"採用率 攻撃寄り{rows[name]['adoption_atk']:.0%} / HP寄り{rows[name]['adoption_hp']:.0%}",
              flush=True)
    print("record:", save_record("shape-split", args, {"rows": rows}))


# ============================================================
# repro: 再現性チェック
# ============================================================
def cmd_repro(args):
    pool = reference_pool()
    sc = {"config_overrides": None, "spriglings": pool,
          "rosters": {"A": None, "B": list(BASE_KINDS)}, "bots": {"A": args.bot, "B": args.bot}}
    tasks = [(sc, f"repro-{j}") for j in range(args.pairs)]
    first = run_pairs(tasks, args.workers)
    second = run_pairs(tasks, 1)
    same = first == second
    print("identical:", same)
    return 0 if same else 1


# ============================================================
# aa-test: 環境そのものの検証（A/Aテスト）
# ============================================================
def cmd_aa_test(args):
    """両側を完全に同じ条件（同じボット・同じロースター）で戦わせる。スコアの95%CIが
    0.5を含まなければ、検証環境のどこかに側A/Bの非対称（バグ）がある。"""
    sc = {"config_overrides": None, "spriglings": reference_pool() if args.with_spriglings else {},
          "rosters": {"A": None, "B": None}, "bots": {"A": args.bot, "B": args.bot}}
    pairs = run_pairs([(sc, f"aa{args.seed}-{j}") for j in range(args.pairs)], args.workers)
    summary = lab_stats.summarize([p["pair_score"] for p in pairs])
    ok = summary["ci95"][0] <= 0.5 <= summary["ci95"][1]
    print(f"A/A: {summary} -> {'OK' if ok else 'NG（側の非対称を疑う。ただし5%は偶然でもNGになる）'}")
    print("record:", save_record("aa-test", args, {"summary": summary, "ok": ok}))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workers", type=int, default=max(1, os.cpu_count() or 1))
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("calibrate")
    p.add_argument("--samples", type=int, default=300)
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_calibrate)

    p = sub.add_parser("compare")
    p.add_argument("--a", default="heuristic", choices=("heuristic", "search", "random"))
    p.add_argument("--b", default="random", choices=("heuristic", "search", "random"))
    p.add_argument("--pairs", type=int, default=100)
    p.add_argument("--seed", default="0")
    p.add_argument("--random-rosters", action="store_true",
                   help="ペアごとにランダムなSprigling構成を両者共通で追加する")
    p.add_argument("--roster-size", type=int, default=3)
    p.add_argument("--sprt", help="例 0.5,0.55 （H0,H1の期待スコア）。指定するとbatchごとに早期終了判定")
    p.add_argument("--batch", type=int, default=20)
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("classes")
    p.add_argument("--mode", choices=("vs-base", "bring"), default="vs-base")
    p.add_argument("--pairs", type=int, default=100)
    p.add_argument("--overrides", help='CONFIGの上書き(JSON) 例: \'{"sprigling_stats":{"model":"physical"}}\'')
    p.add_argument("--bot", default="heuristic", choices=("heuristic", "search"))
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_classes)

    p = sub.add_parser("exploit")
    p.add_argument("--weight-class", choices=("light", "middle", "heavy"))
    p.add_argument("--bot", default="heuristic", choices=("heuristic", "search"))
    p.add_argument("--pop", type=int, default=12)
    p.add_argument("--gens", type=int, default=6)
    p.add_argument("--pairs", type=int, default=16, help="選抜段階の1候補あたりペア数")
    p.add_argument("--confirm", type=int, default=3, help="再検定する上位候補数")
    p.add_argument("--max-confirm-pairs", type=int, default=300)
    p.add_argument("--batch", type=int, default=25)
    p.add_argument("--s0", type=float, default=0.5)
    p.add_argument("--s1", type=float, default=0.6)
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_exploit)

    p = sub.add_parser("placement-sweep")
    p.add_argument("--ratios", default="0.5,0.75,1.0")
    p.add_argument("--bot", default="heuristic", choices=("heuristic", "search"))
    p.add_argument("--pairs", type=int, default=100)
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_placement_sweep)

    p = sub.add_parser("aa-test")
    p.add_argument("--bot", default="heuristic", choices=("heuristic", "search"))
    p.add_argument("--pairs", type=int, default=400)
    p.add_argument("--with-spriglings", action="store_true")
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_aa_test)

    p = sub.add_parser("shape-split")
    p.add_argument("--pairs", type=int, default=400)
    p.add_argument("--seed", default="0")
    p.set_defaults(func=cmd_shape_split)

    p = sub.add_parser("repro")
    p.add_argument("--bot", default="heuristic", choices=("heuristic", "search"))
    p.add_argument("--pairs", type=int, default=6)
    p.set_defaults(func=cmd_repro)

    args = ap.parse_args(argv)
    return args.func(args) or 0


if __name__ == "__main__":
    _ensure_hash_seed()
    sys.exit(main())
