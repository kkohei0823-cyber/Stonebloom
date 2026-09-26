"""
analyze_weight_regression.py
==========================================================
（2026-09-07: analyze_engineer_econ_bonus.pyを改名・汎用化。旧ファイルは
このスクリプトに統合されたため削除して構わない。）

「scoreとの相関は強いのにbase_scoreとの相関は弱い」重み（例: engineer_econ_bonus）や、
逆に「相関の符号が study 間で反転する」重み（例: production_diversity_pref）が、
本当に汎用的なバランス改善に効いているのか、それとも他の重み（rp_spot等）と
一緒に動く多重共線性で相関係数だけ膨らんでいるだけなのかを、追加対局なしで
Optuna DBの既存trialデータだけから切り分けるための簡易スクリプト。

2026-09-06版（analyze_engineer_econ_bonus.py）はengineer_econ_bonus専用の
決め打ちだったが、「気になる重みが出るたびに個別スクリプトを作る」運用は
続かないため、任意の重みを`--weight`で指定して調べられる汎用ツールに
作り替えた（引き継ぎ資料 5節「今後もanalyze_重み設定は必要になってくる」
を受けた対応）。回帰そのものの中身（標準化重回帰・偏回帰係数・単純相関との
比較・多重共線性フラグ）は旧スクリプトから変更していない。

pandasには依存しない（optuna + numpyのみ）。

考え方:
  ペアワイズ相関係数（レポートに出ている「スコアとの相関」）は、他の重みとの
  同時変動を無視した単純な2変数間の相関でしかない。rp_spotとengineer_econ_bonusの
  ように「一緒に上がっていく」重み同士があると、片方の効果をもう片方が
  借りて相関係数が実際より大きく見えることがある。
  重回帰（多変量線形回帰）で全重みを同時に説明変数に入れて解くと、
  「他の重みを一定に保った場合の、その重みだけの効き目」（偏回帰係数）が
  得られるため、この見かけ上の相関を排除しやすい。

使い方:
  # 上位20件のランキングだけを見る（従来の使い方）
  python3 analyze_weight_regression.py --study-name search_balance_weights_260905_v3

  # 特定の重み（複数可、カンマ区切りまたは--weightを複数回）に注目する
  python3 analyze_weight_regression.py --study-name <study名> \\
      --storage sqlite:///balance_tuning_search.db \\
      --weight engineer_econ_bonus --weight production_diversity_pref

  # 目的変数をbase_score以外に変える
  python3 analyze_weight_regression.py --study-name <study名> --target score
"""

import argparse
from collections import Counter

import numpy as np
import optuna


def _parse_weight_args(raw_list):
    """--weight は複数回指定・カンマ区切りのどちらでも受け付ける。"""
    names = []
    for raw in raw_list or []:
        for part in raw.split(","):
            part = part.strip()
            if part:
                names.append(part)
    return names


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--storage", default="sqlite:///balance_tuning_search.db",
                         help="tune_balance_search.py実行時と同じOptuna storage URL")
    parser.add_argument("--study-name", required=True, help="分析対象のstudy名")
    parser.add_argument("--target", default="base_score",
                         help="回帰の目的変数。trial.user_attrsのキー名（既定base_score）。"
                              "見つからないtrialはtrial.value(=score)にフォールバックする。")
    parser.add_argument("--top", type=int, default=20, help="表示する重みの数（既定20）")
    parser.add_argument("--weight", action="append", default=None,
                         help="注目したい重み名。複数回指定可、またはカンマ区切りで複数指定可"
                              "（例: --weight engineer_econ_bonus,production_diversity_pref）。"
                              "指定するとランキング上位に入っていなくても、その重みの"
                              "偏回帰係数・単純相関・多重共線性フラグを個別セクションに表示する。")
    args = parser.parse_args()
    focus_names = _parse_weight_args(args.weight)

    study = optuna.load_study(study_name=args.study_name, storage=args.storage)
    trials = [t for t in study.trials if t.value is not None]
    if not trials:
        raise SystemExit("valueが確定しているtrialが見つかりませんでした（全trialが中断/失敗している可能性）。")

    # 全trialに共通するパラメータ名だけを対象にする。
    # --tune weights/pieces/both を混在させて同じdbに保存している場合、trialごとに
    # paramsの構成が異なることがあるため、構成が一致するtrialだけを使う
    # （重み(weight)パラメータと駒(piece)パラメータが混在していても、
    #  ここでは区別せずまとめて回帰に投入する）。
    key_sets = Counter(tuple(sorted(t.params.keys())) for t in trials)
    common_keys, _ = key_sets.most_common(1)[0]
    param_names = list(common_keys)

    rows, ys, skipped = [], [], 0
    for t in trials:
        if tuple(sorted(t.params.keys())) != common_keys:
            skipped += 1
            continue
        y = t.user_attrs.get(args.target)
        if y is None:
            y = t.value
        rows.append([t.params[name] for name in param_names])
        ys.append(y)

    if skipped:
        print(f"[warning] パラメータ構成が異なる{skipped}件のtrialを除外しました"
              f"（最多構成の{len(param_names)}パラメータに揃えたtrialのみ使用: {len(ys)}件）。")

    X = np.array(rows, dtype=float)
    y = np.array(ys, dtype=float)
    n = len(y)
    print(f"n={n} trials / target={args.target!r} / params={len(param_names)}\n")

    # 標準化してから回帰する（各重みのスケールがバラバラなので、係数の大きさを
    # 「1標準偏差動かした時にtargetが何ポイント動くか」という揃った単位で比較するため）。
    X_std = X.std(axis=0)
    X_std[X_std == 0] = 1.0  # 全trialで値が同じ（探索されていない）重みによる0除算を防ぐ
    Xn = (X - X.mean(axis=0)) / X_std
    Xn_with_intercept = np.column_stack([Xn, np.ones(n)])

    coef, _residuals, _rank, _sv = np.linalg.lstsq(Xn_with_intercept, y, rcond=None)
    coefs = coef[:-1]  # 最後の1個は切片項なので除く

    # 比較用に、従来通りの単純なペアワイズ相関係数も並べて出す。
    pairwise = []
    for i in range(len(param_names)):
        col = X[:, i]
        pairwise.append(0.0 if col.std() == 0 else np.corrcoef(col, y)[0, 1])

    def _collinearity_flag(c, r):
        # 「単純相関は大きいのに偏回帰係数はその4割未満」＝他の重みに便乗して
        # 相関係数だけ膨らんでいた疑いが強い、という簡易フラグ。
        return abs(r) > 0.3 and abs(c) < abs(r) * 0.4

    ranked = sorted(zip(param_names, coefs, pairwise), key=lambda r: -abs(r[1]))

    print(f"{'weight':30s} {'偏回帰係数(標準化)':>18s} {'単純相関(参考)':>14s}")
    print("-" * 66)
    for name, c, r in ranked[: args.top]:
        flag = "  <- 相関は強いが偏回帰は弱い(要注意)" if _collinearity_flag(c, r) else ""
        print(f"{name:30s} {c:+18.4f} {r:+14.3f}{flag}")

    if not focus_names:
        return

    # rank_by_nameは1始まり（1位=最も|偏回帰係数|が大きい重み）。
    rank_by_name = {name: i + 1 for i, (name, _c, _r) in enumerate(ranked)}
    coef_by_name = {name: c for name, c, _r in ranked}
    corr_by_name = {name: r for name, _c, r in ranked}

    print()
    print("=" * 66)
    print("注目重みの詳細")
    print("=" * 66)
    for name in focus_names:
        if name not in rank_by_name:
            print(f"\n[warning] `{name}` はこのstudyのtrialパラメータに見つかりませんでした"
                  f"（WEIGHT_SEARCH_SPACE/CONFIG_PIECE_SEARCH_SPACEへの登録漏れ、"
                  f"綴りの誤り、または`--tune weights/pieces/both`の構成違いで"
                  f"除外された可能性があります。3.2節参照）。")
            continue
        rank = rank_by_name[name]
        c = coef_by_name[name]
        r = corr_by_name[name]
        flag = _collinearity_flag(c, r)
        print(f"\n`{name}`")
        print(f"  順位: {rank}/{len(param_names)}（|偏回帰係数|降順）")
        print(f"  偏回帰係数(標準化): {c:+.4f}")
        print(f"  単純相関(参考)    : {r:+.3f}")
        if flag:
            print("  判定: 単純相関(|r|>0.3)に対して偏回帰係数がその4割未満です。"
                  "他の重み（rp_spot等、一緒に動きやすい経済系の重みが典型）に"
                  "便乗して相関係数だけが膨らんでいる多重共線性の疑いが強いため、"
                  "この重み単体をweights.pyへ恒久的に強く反映する根拠は薄いです。"
                  "個別に効果を確認したい場合は、この重みだけを固定/変化させた"
                  "before/after比較（generate_kifu.py --trial-a/-bで該当trial同士を"
                  "直接対戦させる等）を行ってください。")
        elif abs(r) <= 0.3:
            print("  判定: 単純相関自体が弱く（|r|<=0.3）、独立した効果があるとも"
                  "多重共線性で膨らんでいるとも言い切れません。探索レンジが狭い"
                  "重みは標準偏差が小さくなり係数が過小評価されることがあるため、"
                  "レンジ内位置（付録参照）と合わせて判断してください。")
        else:
            print("  判定: 単純相関・偏回帰係数の両方が同程度に大きく、他の重みとの"
                  "同時変動を除いても独立した説明力を持つ可能性が高いです。"
                  "汎用的なバランス改善に効いている重みの候補として優先度を"
                  "上げてよいと考えられます。")


if __name__ == "__main__":
    main()
