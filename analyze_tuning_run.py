# -*- coding: utf-8 -*-
"""
analyze_tuning_run.py
==========================================================
tune_balance_search.py の実行結果（Optunaのsqlite storageとchampion_history/
ディレクトリ）を読み込み、コンソール出力の生ログを貼り付ける代わりに、
「チャンピオンの切り替わり頻度の異常」「評価スコアの偏り」「どの重みが
悪さをしているか」を人間・AI双方が短時間で把握できる、丸め済み・構造化済みの
分析レポート（Markdown）を1ファイルにまとめて出力するツール。

背景（2026-09-04追記予定、handbook.md 0.96.1節参照）:
  コンソール出力をそのまま貼り付けると、小数第二位に丸められていない重みが
  trialごとに大量に列挙され、データ量が肥大化して分析効率が悪い。本スクリプトは
  tune_balance_search.py実行後に**追加で1回実行するだけ**で、以下を1つの
  Markdownファイルにまとめる。
    1. チャンピオン昇格の時系列（世代・trial番号・間隔・勝敗）と、
       間隔が異常に短い（不安定・ノイズで昇格している疑い）/長い
       （停滞している疑い）世代の自動フラグ付け
    2. 全trialのスコア分布（平均・中央値・標準偏差・前半/後半比較によるトレンド）
       と、スコアを構成する各ペナルティ項目の平均値（どのペナルティが
       支配的か）
    3. 重みごとの現チャンピオン値・探索レンジ内での位置（%）・スコアとの
       相関・世代間の変化量（丸めた差分のみ）。探索レンジの上限/下限に
       張り付いている重みは「レンジ自体が実態に対して狭すぎる/構造的な
       問題を重み1つで無理に相殺しようとしている」兆候として自動フラグ付けする
       （2026-09-04のbase_pressureの件と同種の問題を早期発見する狙い）。
    4. （2026-09-05(3)追加）多様性パネル（DIVERSITY_PANEL_ARCHETYPES）関連の
       新指標。championへの昇格行に多様性平均/最低勝率・base_score
       （champion_term抜きのスコア）列を追加し、極端な対champion勝率
       （>=0.9または<=0.1）を自動フラグ付けする。加えて、championには
       勝ち越したが確認戦または多様性チェックで見送られたtrialの一覧（従来は
       コンソール出力にしか残らなかった情報）を専用セクションにまとめる
       （2026-09-07修正: 2026-09-06(2)で追加された確認戦チェックを反映し、
       「却下段階」列で確認戦落ちと多様性チェック落ちを区別するようにした。
       それ以前はこの2つが区別されず一律「多様性チェックで見送り」と
       表示されていた）。
    5. （2026-09-05(3)追加）champion_history_dirが複数のOptuna study間で
       使い回された場合に、対象study以外の昇格記録がレポートに混入しない
       ようフィルタする（study_nameで突き合わせ、一致しない/記録の無い
       エントリは除外した上でその旨を明示する）。
    6. （2026-09-06追加）score（championモード時はbase_score+champion_term）が
       低い順のtrial一覧。champion_history/には昇格したtrialしか残らないため、
       戦績が悪いtrial（championに惨敗した等、探索の失敗事例として原因を
       追いたいtrial）を見つける手段がこれまで無かった。ここに挙がったtrial番号は
       generate_kifu.py（2026-09-06追加の--trial-a/-b）でそのまま棋譜を
       再生成して調べられる。
    7. （2026-09-07追加）セクション2のスコア分布に、base_score単体の前半/後半
       トレンドを追加。従来はscore（championモード時はbase_score+champion_term）の
       前半/後半比較しか無く、championの強さの変化やchampion_bonus_scaleの
       乗り方の違いで生じる見かけの改善と、ゲームバランス自体（base_score）の
       改善を区別できなかった（引き継ぎ資料4節チェックリスト5番）。

前提:
  - tune_balance_search.py・weights.py と同じフォルダに置く
  - tune_balance_search.pyの実行時に使った --storage / --study-name と
    同じ値をこのスクリプトにも渡す（デフォルトはtune_balance_search.py側の
    デフォルトと同じ）
  - champion_history/ ディレクトリ（--champion-games 0 で実行していた場合は
    生成されないため、その場合はチャンピオン関連のセクションのみ省略される）
  - 多様性パネル関連の列・セクション、base_score列、study混入防止フィルタは
    いずれも2026-09-05(3)以降にtune_balance_search.pyが保存したデータでのみ
    値が入る。それより前のchampion_history/gen_XXXX.jsonにはstudy_name
    フィールド自体が無いため、安全側に倒して自動的にレポート対象から除外される
    （除外された世代はレポート冒頭に一覧表示される）。

使い方:
  $ python3 analyze_tuning_run.py \\
        --storage sqlite:///balance_tuning_search.db \\
        --study-name search_balance_weights_d2k10 \\
        --champion-history-dir champion_history

  省略時は tune_balance_search.py のデフォルト値（--storage
  sqlite:///balance_tuning_search.db、--champion-history-dir ./champion_history）
  にフォールバックするが、--study-name は省略不可（1つのdbファイルに複数studyが
  同居しうるため、他のstudyの実行結果を誤って混ぜて分析しないための安全策）。

出力:
  tuning_report_<study_name>_<実行時刻>.md をカレントディレクトリに保存する。
  このファイル1つをClaudeに渡せば、生ログを貼り付けるより大幅に少ない分量で
  同等以上の分析ができる設計。
"""
import argparse
import datetime
import glob
import json
import math
import os
import statistics
import sys

try:
    import optuna
except ImportError:
    print("optunaがインストールされていません（tune_balance_search.pyと同じ環境で実行してください）。",
          file=sys.stderr)
    raise

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# tune_balance_search.py側のWEIGHT_SEARCH_SPACE（探索レンジ）をそのまま流用する。
# 別途ここに転記して二重管理にすると、tune_balance_search.py側でレンジを変更した
# 際にこのスクリプトだけ古いレンジのまま「レンジ外」と誤検知する事故が起きるため、
# 必ずimportで参照する（WEIGHT_SEARCH_SPACEへの追加漏れは0.86節で一度実際に
# 起きているので、二重管理は避ける）。
try:
    from tune_balance_search import WEIGHT_SEARCH_SPACE
except ImportError as e:
    print(f"tune_balance_search.pyのimportに失敗しました（同じフォルダに置いてください）: {e}",
          file=sys.stderr)
    raise


DEGENERATE_METRIC_KEYS = [
    # evaluate_matchup()のmetrics辞書のうち、値が大きいほど「望ましくない」項目。
    # 平均値をレポートし、支配的なペナルティ項目を見つけるために使う。
    "tiebreak_ratio", "kills_per_game", "first_pass_rate",
    "bad_engagements_per_game", "avg_rp_spot_neglect", "rp_spot_neglect_penalty",
    "engagement_abandonment_per_game", "engagement_abandonment_penalty",
    "premature_decisive_rate", "premature_decisive_penalty", "round_length_penalty",
]

BOUNDARY_HIT_MARGIN_RATIO = 0.05  # レンジ幅の何%以内なら「境界に張り付いている」とみなすか

# 2026-09-06(2)追加: 「有意水準ぎりぎりの昇格」を検出するための片側二項検定p値。
# tune_balance_search.py の _promotion_is_significant() / rematch_eval.py の
# _one_sided_binom_p() と完全に同じ計算式を、analyze_tuning_run.py側でも
# 独立に複製している（analyze_tuning_run.pyは過去のstudy・別バージョンの
# tune_balance_search.pyが生成したchampion_history/も読む可能性があるため、
# 実行時のtune_balance_search.pyの内部関数に依存させたくない）。
# promotion_marginはchampion_history/gen_XXXX.jsonのmetricsに保存されていない
# ため、既定値0.5（tune_balance_search.pyの--promotion-margin既定と同じ）を
# 常に使う前提とする。--promotion-marginを0.5以外に変更して運用している場合、
# ここで表示するp値は実際の昇格判定に使われた値と一致しない点に注意。
PROMOTION_MARGIN_ASSUMED = 0.5


def _one_sided_binom_p(wins, n_games, threshold=PROMOTION_MARGIN_ASSUMED):
    """勝率がthresholdを片側検定で上回っているかのp値（二項分布の片側裾確率）。"""
    if not n_games:
        return None
    return sum(
        math.comb(n_games, k) * (threshold ** k) * ((1 - threshold) ** (n_games - k))
        for k in range(wins, n_games + 1)
    )


# 2026-09-06(2)追加: 「有意ではあるがぎりぎり」を示すp値の帯。tune_balance_search.py
# の--promotion-alpha既定は2026-09-06(2)に0.1→0.05へ引き下げたが、0.1のまま
# 運用している過去のstudy・現行studyの両方を拾えるよう、新旧どちらの既定alphaで
# 見ても「際どい」と言える範囲（0.03〜0.1）をボーダーラインとして扱う。
# search_balance_weights_260905_v3の世代8（p=0.097）・世代11（p=0.067）は
# いずれもこの帯に入り、実際にrematch_eval.pyで大サンプル再戦させたところ
# （世代8 vs 世代7: win_rate=0.497, p=0.57／世代10 vs 世代7: win_rate=0.543,
# p=0.074）、どちらも直前の"連続昇格"が統計的な裏付けを欠いていたことが
# 裏付けられている（詳細はanalyze_tuning_run_handoff.mdの該当節参照）。
BORDERLINE_P_LOW = 0.03
BORDERLINE_P_HIGH = 0.10


def _detect_noisy_cascades(rows, short_threshold):
    """短間隔昇格が連続する『連鎖』を検出し、win_rateだけが上昇してbase_scoreは
    改善していない（＝直前championに対する見かけ上の勝率上昇が、大元まで遡った
    実力の裏付けを伴っていない疑いがある）パターンをフラグ化する。

    2026-09-06(2)追加。search_balance_weights_260905_v3の事後検証がきっかけ:
    世代8→9→10（間隔93→2→2trial、対前champion勝率0.57→0.66→0.73と一見
    『連続して強くなっている』ように見えた）を実際にrematch_eval.pyで大元の
    世代7と300局ずつ直接対戦させたところ、世代8 vs 世代7はwin_rate=0.497
    （ほぼコイントス、p=0.57で有意差なし）、世代8→9→10を経た世代10でさえ
    vs 世代7でwin_rate=0.543（p=0.074、--promotion-alpha 0.05では有意差なし）
    という結果だった。つまり3回の"昇格"が示す見かけの勝率上昇
    （57%→66%→73%）は、実際には大元からほとんど強くなっていない対象を
    ノイズで断続的に上書きしていただけだった可能性が高い。base_score
    （-14.55→36.34→-52.6）もwin_rateの上昇と全く連動しておらず、事前に
    このシグナルだけからでも疑うことができた。

    検出方法: 「前世代からの間隔がshort_threshold以下」の行が連続する区間
    （その直前の"起点"行を含む）を1つの連鎖とみなし、長さ3行以上
    （＝短間隔の昇格が2回以上連続）の連鎖について、
      - 対前champion勝率が連鎖の最初の行より最後の行の方が高い
      - base_scoreは連鎖の最初の行より最後の行の方が低い（悪化している）
    の両方を満たす場合にフラグを立てる。統計的に厳密な検定ではなく、
    あくまで「rematch_eval.pyで大元と直接対戦させて確認すべき」という
    調査の優先順位付けのためのヒューリスティックである点に注意。
    """
    flags = []
    if short_threshold is None:
        return flags

    chains = []
    current_chain = []
    for row in rows:
        iv = row["interval_trials"]
        if iv is not None and iv <= short_threshold and current_chain:
            current_chain.append(row)
        else:
            if len(current_chain) >= 3:
                chains.append(current_chain)
            current_chain = [row]
    if len(current_chain) >= 3:
        chains.append(current_chain)

    for chain in chains:
        win_rates = [r["win_rate_vs_prev"] for r in chain]
        base_scores = [r["base_score"] for r in chain]
        if any(v is None for v in win_rates) or any(v is None for v in base_scores):
            continue
        win_rate_up = win_rates[-1] > win_rates[0]
        base_score_down = base_scores[-1] < base_scores[0]
        if win_rate_up and base_score_down:
            gens = [r["generation"] for r in chain]
            trials_in_chain = [r["trial_number"] for r in chain]
            # 連鎖の起点（gens[0]）が世代交代した"直後"の行なので、その1つ前の
            # 世代番号（gens[0]-1）が連鎖に入る前の"大元"のchampionにあたる
            # （generationは昇格のたびに1ずつ増える単純な連番のため）。
            if isinstance(gens[0], int):
                anchor_gen = gens[0] - 1
                anchor_note = (
                    f"連鎖が始まる前のchampion（世代{anchor_gen}。"
                    f"champion_history/gen_{anchor_gen:04d}.jsonがあればそれ）"
                )
            else:
                anchor_note = "連鎖が始まる前のchampion"
            flags.append(
                f"世代{gens[0]}〜{gens[-1]}（trial {trials_in_chain[0]}〜{trials_in_chain[-1]}）は"
                f"前世代からの間隔が{short_threshold:.1f}trial以下の昇格が{len(chain) - 1}回連続する"
                f"『連鎖』です。対前champion勝率は{win_rates[0]}→{win_rates[-1]}と上昇していますが、"
                f"base_scoreは{base_scores[0]}→{base_scores[-1]}とむしろ悪化しており、両者が逆行して"
                f"います。これは『直前championに対する勝率上昇』が、大元まで遡った実力の裏付けを"
                f"伴わない統計的ノイズ（停止則・多重検定の影響）である疑いを示す典型パターンです。"
                f"rematch_eval.pyでこの連鎖の最終的なchampion（世代{gens[-1]}）と、{anchor_note}を"
                f"300局程度で直接対戦させ、有意な差が実際にあるか確認することを強く推奨します。"
            )
    return flags


def _round(v, nd=2):
    if isinstance(v, bool) or v is None:
        return v
    if isinstance(v, (int,)):
        return v
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return v
        return round(v, nd)
    return v


def load_champion_history(history_dir, study_name=None):
    """champion_history/gen_XXXX.json を世代順に読み込む。ディレクトリが
    無い/空なら空リストを返す（--champion-games 0 運用時に対応）。

    2026-09-05(3)追加: champion_history_dirはchampionを引き継いで複数のstudyを
    跨いで使い回されることがある（改善後に別studyとして再検証を始める運用）。
    generation番号だけをキーに全ファイルをそのまま読み込むと、「今回の
    study_nameでは起きていない、別（多くは過去の）studyの昇格記録」が
    レポートに混入し分析を誤らせる（実際に発生した事故:
    search_balance_weights_260905_v1のtrial10・100戦100勝の昇格記録が、
    search_balance_weights_260905_v2のレポートに混入していた）。

    study_nameを指定した場合、各エントリの"study_name"フィールドが一致する
    ものだけをレポート対象として返す。一致しない、またはフィールド自体が
    無い（この対策より前に保存された旧形式ファイル）エントリは、安全側に
    倒して除外し、2つ目の戻り値（excluded、理由付きのリスト）として返す。
    study_nameを指定しない場合（後方互換）は従来通りフィルタなしで返す。
    """
    if not history_dir or not os.path.isdir(history_dir):
        return [], []
    entries = []
    for path in sorted(glob.glob(os.path.join(history_dir, "gen_*.json"))):
        with open(path, "r", encoding="utf-8") as f:
            entries.append(json.load(f))
    entries.sort(key=lambda e: e.get("generation", 0))

    if not study_name:
        return entries, []

    kept, excluded = [], []
    for e in entries:
        entry_study = e.get("study_name")
        if entry_study == study_name:
            kept.append(e)
        elif entry_study is None:
            excluded.append({
                "generation": e.get("generation"),
                "trial_number": e.get("trial_number"),
                "reason": "study_name未記録（2026-09-05(3)より前の旧形式ファイル）",
            })
        else:
            excluded.append({
                "generation": e.get("generation"),
                "trial_number": e.get("trial_number"),
                "reason": f"別study（'{entry_study}'）の記録",
            })
    return kept, excluded


def analyze_promotion_frequency(history):
    """世代間のtrial間隔を計算し、異常に短い/長い間隔にフラグを立てる。"""
    if len(history) < 2:
        return {
            "generations": len(history),
            "rows": [],
            "mean_interval": None,
            "stdev_interval": None,
            "flags": ["世代数が2未満のため間隔の分析はできません（もっとtrialを回してください）。"]
                     if len(history) < 2 else [],
        }
    rows = []
    intervals = []
    for prev, cur in zip(history[:-1], history[1:]):
        prev_t = prev.get("trial_number")
        cur_t = cur.get("trial_number")
        interval = (cur_t - prev_t) if (prev_t is not None and cur_t is not None) else None
        if interval is not None:
            intervals.append(interval)
        metrics = cur.get("metrics") or {}
        wins = metrics.get("champion_wins")
        losses = metrics.get("champion_losses")
        draws = metrics.get("champion_draws")
        n_games = (wins + losses + draws) if None not in (wins, losses, draws) else None
        p_value = _one_sided_binom_p(wins, n_games) if (wins is not None and n_games) else None
        # 2026-09-06(2)追加: tune_balance_search.pyの確認戦（B案）が記録した
        # 独立2回目のchampion戦の結果。旧バージョンのtune_balance_search.pyで
        # 生成されたchampion_history/には無いためNone（表では"-"）になる。
        confirm_win_rate = metrics.get("win_rate_vs_champion_confirm")
        confirmation_ok = metrics.get("confirmation_ok")
        rows.append({
            "generation": cur.get("generation"),
            "trial_number": cur_t,
            "interval_trials": interval,
            "win_rate_vs_prev": _round(metrics.get("win_rate_vs_champion")),
            "wins": wins,
            "losses": losses,
            "draws": draws,
            # 2026-09-06(2)追加: 対前champion勝率の片側二項検定p値（PROMOTION_MARGIN_ASSUMED
            # =0.5仮定。上のモジュールdocstring参照）と、それがボーダーライン
            # （BORDERLINE_P_LOW〜BORDERLINE_P_HIGH）に入るかどうか。
            "p_value_vs_prev": _round(p_value, 4),
            "borderline": (p_value is not None and BORDERLINE_P_LOW <= p_value <= BORDERLINE_P_HIGH),
            # 2026-09-06(2)追加: 確認戦（tune_balance_search.py 2026-09-06(2)版のB案）の結果。
            "confirm_win_rate": _round(confirm_win_rate),
            "confirmation_ok": confirmation_ok,
            # 2026-09-05(3)追加: 多様性パネル導入（同日）に伴う新指標。
            # champion_history/gen_XXXX.jsonのmetricsには元々入っていたが、
            # レポート側で未表示だったギャップへの対応（引き継ぎ資料3.4節）。
            # 多様性パネル導入前の世代や--disable-diversity-check運用では
            # 記録が無いためNone（表では"-"）になる。
            "diversity_avg_win_rate": _round(metrics.get("diversity_avg_win_rate")),
            "diversity_min_win_rate": _round(metrics.get("diversity_min_win_rate")),
            "diversity_min_win_rate_archetype": metrics.get("diversity_min_win_rate_archetype"),
            # 2026-09-05(3)追加: champion_term加算前のbase_score（ゲームバランス
            # 自体の質）。スコアの絶対値がchampion_termに支配されているだけの
            # 昇格（引き継ぎ資料2節）を見分けるための列。tune_balance_search.py
            # 側がbase_scoreを記録するようになった以降の昇格のみ値が入る。
            "base_score": _round(metrics.get("base_score")),
        })

    flags = []
    # 2026-09-05(3)追加: 勝率が0.9台/0.1台など極端な昇格行を自動検出する
    # （引き継ぎ資料4節チェックリスト1番「セクション1で勝率が極端な昇格行が
    # ないか」の手動確認を、レポート生成時点で先回りしてフラグ化する）。
    # search_balance_weights_260905_v1のtrial10（100戦100勝）が好例で、
    # 真の勝率90%と仮定しても100/100が起きる確率は約0.0027%しかない
    # （二項分布 0.9**100 ≈ 2.66e-5）。
    EXTREME_WIN_RATE_HIGH = 0.9
    EXTREME_WIN_RATE_LOW = 0.1
    for row in rows:
        wr = row["win_rate_vs_prev"]
        if wr is None:
            continue
        if wr >= EXTREME_WIN_RATE_HIGH or wr <= EXTREME_WIN_RATE_LOW:
            flags.append(
                f"世代{row['generation']}（trial {row['trial_number']}）: "
                f"対前champion勝率が{wr}と極端です（{row['wins']}勝{row['losses']}敗"
                f"{row['draws']}分）。真の実力差が大きい場合でもこの結果は統計的に"
                f"起きにくく、直前championの弱点だけを突いた非推移的カウンター"
                f"（じゃんけん昇格）の疑いがあります。"
                + (f" 多様性パネル: 平均{row['diversity_avg_win_rate']}/"
                   f"最低{row['diversity_min_win_rate']}@{row['diversity_min_win_rate_archetype']}"
                   f"（基準を満たしたため昇格していますが、僅差でないか確認してください）。"
                   if row["diversity_avg_win_rate"] is not None
                   else " 多様性パネルの記録が無いため（旧世代または--disable-diversity-check運用）、"
                        "champion_history/gen_XXXX.jsonを直接確認することを推奨します。")
            )

    # 2026-09-06(2)追加: p値がボーダーライン（BORDERLINE_P_LOW〜BORDERLINE_P_HIGH）の
    # 昇格行を検出する。上のEXTREME_WIN_RATE系フラグとは独立の視点（勝率そのものは
    # 極端でなくても、その勝率を記録した局数次第では有意性がぎりぎりなことがある）。
    for row in rows:
        if not row["borderline"]:
            continue
        confirm_note = ""
        if row["confirm_win_rate"] is not None:
            confirm_note = (
                f" 確認戦（独立2回目のchampion戦）はwin_rate={row['confirm_win_rate']}で、"
                f"{'有意な勝ち越しを再現できました' if row['confirmation_ok'] else '有意な勝ち越しを再現できませんでした（この昇格はノイズだった疑いが強いです）'}。"
            )
        else:
            confirm_note = (" この昇格の記録には確認戦（2026-09-06(2)以降のtune_balance_search.py）の"
                             "結果が無いため、rematch_eval.pyで直接検証することを推奨します。")
        flags.append(
            f"世代{row['generation']}（trial {row['trial_number']}）: 対前champion勝率"
            f"{row['win_rate_vs_prev']}（{row['wins']}勝{row['losses']}敗{row['draws']}分）の"
            f"片側二項検定p値は{row['p_value_vs_prev']}で、有意水準ぎりぎりです"
            f"（promotion_margin=0.5想定。実際の判定に使われた--promotion-alphaと一致しない"
            f"場合があります）。" + confirm_note
        )

    if intervals:
        mean_interval = statistics.mean(intervals)
        stdev_interval = statistics.pstdev(intervals) if len(intervals) > 1 else 0.0
        # 「異常に短い」= 平均の半分未満かつ絶対値でも小さい（3trial未満）。
        # 昇格判定自体は二項検定（有意水準alpha）を通っているはずだが、
        # champion_gamesが少ない設定だと有意水準を満たしても偶然性が残るため、
        # 短期間で連続昇格している場合はサンプル数（champion_games）を
        # 疑う材料として提示する。
        short_threshold = max(3, mean_interval / 2)
        long_threshold = mean_interval * 3
        for row in rows:
            iv = row["interval_trials"]
            if iv is None:
                continue
            if iv <= short_threshold:
                flags.append(
                    f"世代{row['generation']}（trial {row['trial_number']}）: "
                    f"前世代からわずか{iv}trialで昇格（平均{mean_interval:.1f}trialの半分未満）。"
                    f"champion_gamesが少なすぎてノイズで昇格している可能性があるため、"
                    f"--champion-games を増やすか --promotion-alpha を厳しくすることを検討してください。"
                )
            elif iv >= long_threshold and long_threshold > mean_interval:
                flags.append(
                    f"世代{row['generation']}（trial {row['trial_number']}）: "
                    f"前世代から{iv}trialも掛かって昇格（平均{mean_interval:.1f}trialの3倍以上）。"
                    f"探索が停滞していた可能性があります。"
                )
        # 2026-09-06(2)追加: 短間隔昇格が連続する『連鎖』のうち、win_rateだけが
        # 上昇しbase_scoreが逆行しているものを検出する（モジュール冒頭の
        # _detect_noisy_cascadesのdocstring・search_balance_weights_260905_v3の
        # 実例参照）。
        flags.extend(_detect_noisy_cascades(rows, short_threshold))
    else:
        mean_interval = None
        stdev_interval = None

    return {
        "generations": len(history),
        "rows": rows,
        "mean_interval": _round(mean_interval),
        "stdev_interval": _round(stdev_interval),
        "flags": flags,
    }


def analyze_score_distribution(trials):
    """全trialの生スコア(value)の分布を集計する。前半/後半で平均を比較し、
    トレンド（改善しているか、悪化・停滞しているか）を大まかに見る。

    2026-09-05(3)追加: championモード時のvalueは base_score + champion_term
    （引き継ぎ資料2節）であり、champion_termの支配度が高いtrialが混じると
    「scoreは良いがbase_score（ゲームバランス自体の質）は平凡」という乖離が
    起きうる。tune_balance_search.py側がmetrics["base_score"]を記録するように
    なった以降のtrialについては、base_score単体の分布も合わせて集計し、
    score平均とbase_score平均の差でchampion_termの押し上げ／押し下げの度合いを
    大まかに把握できるようにする。base_score未記録のtrialが混在する運用
    （新機能導入前の続きから分析する場合等）ではNoneを返す。
    """
    values = [t.value for t in trials if t.value is not None]
    if not values:
        return {"n": 0}
    n = len(values)
    half = n // 2
    first_half = values[:half] if half > 0 else values
    second_half = values[half:] if half > 0 else values

    base_scores = [
        t.user_attrs["base_score"] for t in trials
        if t.value is not None and isinstance(t.user_attrs.get("base_score"), (int, float))
    ]
    base_score_stats = None
    if base_scores:
        # 2026-09-07追加: scoreの前半/後半比較と同じ理由で、base_score単体でも
        # 前半/後半トレンドを見られるようにする。championモードでは前半/後半の
        # score改善がchampion_bonus_scaleの乗り方の違い（世代交代のタイミングや
        # championの強さの変化）だけで生まれることがあり、scoreの改善傾向
        # （上のfirst_half_mean/second_half_mean/trend）だけでは「ゲームバランス
        # 自体（base_score）が本当に良くなっているか」を見誤る恐れがあるため
        # （引き継ぎ資料4節チェックリスト5番）。base_scoreが記録されているtrialだけを
        # 元のtrial順（trial番号順）のまま前半/後半に分割する。base_score未記録の
        # trialが間に混じっていても、記録があるtrialだけを詰めて等分するため、
        # scoreの前半/後半分割（全trialを単純に半分）とは対象trial数がずれうる点に
        # 注意（base_score_stats["n"]を参照）。
        bs_n = len(base_scores)
        bs_half = bs_n // 2
        bs_first_half = base_scores[:bs_half] if bs_half > 0 else base_scores
        bs_second_half = base_scores[bs_half:] if bs_half > 0 else base_scores
        base_score_stats = {
            "n": bs_n,
            "mean": _round(statistics.mean(base_scores)),
            "median": _round(statistics.median(base_scores)),
            "first_half_mean": _round(statistics.mean(bs_first_half)),
            "second_half_mean": _round(statistics.mean(bs_second_half)),
            "trend": _round(statistics.mean(bs_second_half) - statistics.mean(bs_first_half)),
        }

    return {
        "n": n,
        "mean": _round(statistics.mean(values)),
        "median": _round(statistics.median(values)),
        "stdev": _round(statistics.pstdev(values)) if n > 1 else 0.0,
        "min": _round(min(values)),
        "max": _round(max(values)),
        "first_half_mean": _round(statistics.mean(first_half)),
        "second_half_mean": _round(statistics.mean(second_half)),
        "trend": _round(statistics.mean(second_half) - statistics.mean(first_half)),
        "base_score_stats": base_score_stats,
    }


def analyze_metric_averages(trials):
    """evaluate_matchup()のmetrics（trial.user_attrs）のうち、値が大きいほど
    望ましくない項目の平均値を集計する。どのペナルティが支配的かを見るため。"""
    sums = {}
    counts = {}
    for t in trials:
        for key in DEGENERATE_METRIC_KEYS:
            if key in t.user_attrs:
                v = t.user_attrs[key]
                if isinstance(v, (int, float)):
                    sums[key] = sums.get(key, 0.0) + v
                    counts[key] = counts.get(key, 0) + 1
    return {k: _round(sums[k] / counts[k]) for k in sums if counts.get(k)}


def analyze_diversity_rejections(trials):
    """championには（二項検定で有意に）勝ち越したが、確認戦または多様性パネルの
    基準を満たさず昇格を見送られたtrialの一覧を作る。

    2026-09-05追加の多様性パネルチェックは、championに勝ち越したcandidateに
    ついてのみ評価を実行し、その結果（diversity_avg_win_rate等）と
    beats_champion_only/promoted_championフラグをtrial.user_attrsへ記録する
    （昇格したかどうかに関わらず、tune_balance_search.py側で無条件に記録済み）。
    このため、"beats_champion_only=True かつ promoted_champion=False" な
    trialを抽出すれば、champion_history/gen_XXXX.json（昇格したものだけ）には
    残らない「見送られたtrial」を、tune_balance_search.py側の追加保存なしに
    Optunaのtrialデータだけから再構成できる。

    従来はこの手の見送りイベントはコンソール出力にしか残らず
    （引き継ぎ資料付属のtrial12の例のように、手動でコピペして伝えるしかなかった）、
    「championの世代交代が長期間起きていない」ことが本当の探索停滞なのか、
    このパネルによる健全なフィルタリングなのかを後から区別できない問題が
    あった。このセクションはその区別を可能にする。

    2026-09-07修正: この関数は元々「多様性パネル」しか無かった時期に書かれており、
    2026-09-06(2)で追加された確認戦（B案、tune_balance_search.py側の
    confirmation_check_enabled）を考慮していなかった。tune_balance_search.pyは
    beats_champion（1回目の勝ち越し）の後、まず確認戦を実行し、それに失敗した
    candidateは多様性パネルへ進ませない（コストの軽い方を先に弾く設計）ため、
    "beats_champion_only=True かつ promoted_champion=False" には実際には
    「確認戦で有意な勝ち越しを再現できなかった」trialと「確認戦は通ったが多様性
    パネルの基準を満たさなかった」trialの2種類が混在している。前者は
    diversity_avg_win_rate等がそもそも計算されず記録もされない（多様性パネルへ
    到達していないため）ため、user_attrsのconfirmation_okがFalseかどうかで
    2種類を判別できる（旧バージョンのtune_balance_search.py実行分やconfirmation_ok
    未記録のtrialではNoneになるため、Noneは「多様性パネル起因」側として扱う＝
    従来の判定を後方互換で維持する）。
    """
    rejections = []
    for t in trials:
        ua = t.user_attrs
        if ua.get("beats_champion_only") is True and ua.get("promoted_champion") is False:
            confirmation_ok = ua.get("confirmation_ok")
            # confirmation_ok is False の場合のみ「確認戦で落ちた」と判定する。
            # None（確認戦機能が無い旧バージョン、または--disable-confirmation-check）や
            # True（確認戦は通過した）の場合は、多様性パネル起因として扱う。
            rejected_at = "confirmation" if confirmation_ok is False else "diversity"
            rejections.append({
                "trial_number": t.number,
                "rejected_at": rejected_at,
                "win_rate_vs_champion": _round(ua.get("win_rate_vs_champion")),
                "win_rate_vs_champion_confirm": _round(ua.get("win_rate_vs_champion_confirm")),
                "champion_generation_faced": ua.get("champion_generation_faced"),
                "diversity_avg_win_rate": _round(ua.get("diversity_avg_win_rate")),
                "diversity_min_win_rate": _round(ua.get("diversity_min_win_rate")),
                "diversity_min_win_rate_archetype": ua.get("diversity_min_win_rate_archetype"),
                "diversity_win_rates": ua.get("diversity_win_rates") or {},
            })
    rejections.sort(key=lambda r: r["trial_number"])
    return rejections


def _pearson_corr(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    cov = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    var_x = sum((x - mean_x) ** 2 for x in xs)
    var_y = sum((y - mean_y) ** 2 for y in ys)
    if var_x <= 0 or var_y <= 0:
        return None
    return cov / math.sqrt(var_x * var_y)


def analyze_weights(trials, current_champion_weights):
    """WEIGHT_SEARCH_SPACEの各重みについて、探索レンジ内での現チャンピオン値の
    位置・trial全体でのスコアとの相関・レンジ境界への張り付きを調べる。

    2026-09-06追加: championモード時のscoreはbase_score+champion_termであり
    （引き継ぎ資料2節）、championが強く大半のtrialがそのchampionに一方的に
    負け続けるような回（win_rate_vs_championの分散がscoreの分散を支配する状況）
    では、「スコアとの相関」が実質「championにどれだけ善戦できたか」との相関に
    近くなり、「その重みが単体でゲームバランスを良くするか」という本来知りたい
    情報とは別物になってしまう（このレポート自体がその一例になりうる。
    セクション2のscore平均とbase_score平均の差が大きい回は特に注意）。
    これを見分けられるよう、base_score記録済みのtrialに限定した相関
    （corr_with_base_score）も別途計算し、scoreとの相関と併記する。
    両者が大きく食い違う重み（例: scoreとの相関は強いがbase_scoreとの相関は
    ほぼ0）は、「championとの相性」にしか効いていない疑いがある。
    """
    rows = []
    boundary_flags = []
    for name, (kind, lo, hi) in WEIGHT_SEARCH_SPACE.items():
        xs, ys = [], []
        xs_base, ys_base = [], []
        for t in trials:
            if name in t.params and t.value is not None:
                xs.append(t.params[name])
                ys.append(t.value)
                base_score = t.user_attrs.get("base_score")
                if isinstance(base_score, (int, float)):
                    xs_base.append(t.params[name])
                    ys_base.append(base_score)
        corr = _pearson_corr(xs, ys)
        corr_base = _pearson_corr(xs_base, ys_base)

        champ_value = None
        if current_champion_weights is not None:
            # base_defense/incoming_damageのような比率合成キーは
            # HEURISTIC_WEIGHTS側では別名（ratio解決後）で保存されているため、
            # 見つからない場合はNoneのまま扱う（レポート上は "-" 表示）。
            champ_value = current_champion_weights.get(name)

        position_pct = None
        boundary_hit = None
        if champ_value is not None and hi > lo:
            position_pct = (champ_value - lo) / (hi - lo)
            margin = (hi - lo) * BOUNDARY_HIT_MARGIN_RATIO
            if champ_value <= lo + margin:
                boundary_hit = "下限側"
            elif champ_value >= hi - margin:
                boundary_hit = "上限側"

        row = {
            "key": name,
            "range": f"{lo:g}〜{hi:g}",
            "champion_value": _round(champ_value),
            "position_in_range": _round(position_pct * 100) if position_pct is not None else None,
            "corr_with_score": _round(corr),
            "corr_with_base_score": _round(corr_base),
            "boundary_hit": boundary_hit,
            "n_trials_observed": len(xs),
        }
        rows.append(row)
        if boundary_hit:
            boundary_flags.append(
                f"`{name}` = {row['champion_value']}（探索レンジ{row['range']}の{boundary_hit}に張り付いています）。"
                f"レンジ自体が狭すぎるか、この重み1つで別の構造的な問題を無理に相殺しようとしている"
                f"可能性があります（2026-09-04のbase_pressureと同種のパターン）。"
            )

    # スコアとの相関が強い（正・負とも）順に並べ替えて上位を目立たせる。
    rows_with_corr = [r for r in rows if r["corr_with_score"] is not None]
    rows_with_corr.sort(key=lambda r: abs(r["corr_with_score"]), reverse=True)

    return rows, rows_with_corr[:10], boundary_flags


def analyze_weight_drift(history):
    """世代間で変化した重みだけを差分表示する（丸め済み・変化なしは省略）。
    「重みが乱立して膨大になる」問題への直接対応。"""
    if len(history) < 2:
        return []
    drift_rows = []
    for prev, cur in zip(history[:-1], history[1:]):
        prev_w = prev.get("weights") or {}
        cur_w = cur.get("weights") or {}
        changed = {}
        for key in cur_w:
            pv = prev_w.get(key)
            cv = cur_w.get(key)
            if not isinstance(cv, (int, float)) or not isinstance(pv, (int, float)):
                continue
            if abs(cv - pv) >= 0.005:  # 丸め後に差が出ない程度のノイズは無視
                changed[key] = (_round(pv), _round(cv))
        drift_rows.append({
            "from_generation": prev.get("generation"),
            "to_generation": cur.get("generation"),
            "changed": changed,
        })
    return drift_rows


def analyze_worst_trials(trials, n=10):
    """scoreが低い順にtrialを並べ、戦績調査の取っ掛かりにする一覧を作る。

    2026-09-06追加: champion_history/gen_XXXX.jsonには昇格したtrialしか
    残らないため、「戦績が極端に悪いtrial」を探す手段がこれまで無く
    （コンソールのスクロールバックを遡るか記憶に頼るしかなかった）、
    generate_kifu.py側に--trial-a/-b（DBから直接trialの重みを読み込んで
    棋譜を作る機能、同じく2026-09-06追加）を実装しても、肝心の「どのtrial
    番号を調べればいいか」が分からなければ活用できなかった。この一覧が
    その入口になる。
    """
    rows = []
    for t in trials:
        if t.value is None:
            continue
        ua = t.user_attrs
        rows.append({
            "trial_number": t.number,
            "score": _round(t.value),
            "base_score": _round(ua.get("base_score")),
            "win_rate_vs_champion": _round(ua.get("win_rate_vs_champion")),
            "champion_wins": ua.get("champion_wins"),
            "champion_losses": ua.get("champion_losses"),
            "champion_draws": ua.get("champion_draws"),
            "champion_generation_faced": ua.get("champion_generation_faced"),
        })
    rows.sort(key=lambda r: r["score"])
    return rows[:n] if n and n > 0 else rows


def build_report(args, study, history, excluded_history=None):
    trials = [t for t in study.trials if t.value is not None]
    excluded_history = excluded_history or []
    lines = []
    now = datetime.datetime.now().isoformat(timespec="seconds")
    lines.append(f"# tune_balance_search.py 実行結果レポート（{args.study_name}）")
    lines.append("")
    lines.append(f"生成日時: {now}　/　storage: `{args.storage}`　/　完了trial数: {len(trials)}")
    lines.append("")
    lines.append("このファイルはClaudeへそのまま渡す想定の、丸め済み・構造化済みレポートです。"
                  "生のコンソール出力を貼り付けるよりデータ量が少なく分析に適しています。")
    lines.append("")
    if excluded_history:
        lines.append(f"**注意（study混入防止フィルタ）:** `{args.champion_history_dir}` 内に、"
                      f"study '{args.study_name}' のものではないchampion_history記録が"
                      f"{len(excluded_history)}件見つかったため、以下のセクション1・4・5・付録の"
                      f"対象からは除外しています（championを引き継いで別studyとして再検証する運用では、"
                      f"古いstudyの記録が同じディレクトリに残っていることがあります）。")
        lines.append("")
        for e in excluded_history:
            lines.append(f"- 世代{e['generation']}（trial {e['trial_number']}）: {e['reason']}")
        lines.append("")

    # ---------------- 1. チャンピオン昇格の時系列 ----------------
    lines.append("## 1. チャンピオン昇格の時系列（切り替わり頻度の異常検知）")
    lines.append("")
    if not history:
        lines.append("このstudyに属するchampion_historyが見つかりませんでした"
                      "（`--champion-games 0` で実行した場合や、まだ一度も昇格が起きていない"
                      "場合はこのセクションは対象外です。上の除外一覧が非空の場合は、"
                      "そちらに表示されている別studyの記録のみが見つかった状態です）。")
    else:
        promo = analyze_promotion_frequency(history)
        lines.append(f"- 総世代数: {promo['generations']}　/　平均昇格間隔: "
                      f"{promo['mean_interval']} trial（標準偏差 {promo['stdev_interval']}）")
        lines.append("")
        lines.append("| 世代 | 昇格trial | 前世代からの間隔(trial) | 対前champion勝率 | p値 | 勝-敗-分 | "
                      "確認戦勝率 | 多様性平均勝率 | 多様性最低勝率(相手) | base_score |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for row in promo["rows"]:
            div_min = (f"{row['diversity_min_win_rate']}@{row['diversity_min_win_rate_archetype']}"
                       if row["diversity_min_win_rate"] is not None else "-")
            p_display = row["p_value_vs_prev"]
            if p_display is not None:
                p_display = f"**{p_display}⚠**" if row["borderline"] else f"{p_display}"
            else:
                p_display = "-"
            if row["confirm_win_rate"] is not None:
                confirm_display = f"{row['confirm_win_rate']}" + ("✓" if row["confirmation_ok"] else "✗")
            else:
                confirm_display = "-"
            lines.append(
                f"| {row['generation']} | {row['trial_number']} | {row['interval_trials']} | "
                f"{row['win_rate_vs_prev']} | {p_display} | {row['wins']}-{row['losses']}-{row['draws']} | "
                f"{confirm_display} | "
                f"{row['diversity_avg_win_rate'] if row['diversity_avg_win_rate'] is not None else '-'} | "
                f"{div_min} | {row['base_score'] if row['base_score'] is not None else '-'} |"
            )
        lines.append("")
        lines.append("（多様性平均/最低勝率・base_scoreが\"-\"の行は、2026-09-05(3)の指標追加より前の"
                      "世代か、`--disable-diversity-check`運用の世代です。多様性列は"
                      "`meta_diversity_check.py`のDIVERSITY_PANEL_ARCHETYPES"
                      "（turtle_vp/rush_kill/economy_boom/vp_spot_hunter）に対する追加対戦の結果、"
                      "base_scoreはchampion_term加算前のスコア（引き継ぎ資料2節）です。"
                      "2026-09-06(2)追加: p値は対前champion勝率の片側二項検定p値"
                      "（promotion_margin=0.5想定。実際の--promotion-marginと異なる場合は不一致）で、"
                      "⚠は有意水準ぎりぎり（p=0.03〜0.10）だったことを示す。確認戦勝率は"
                      "tune_balance_search.py 2026-09-06(2)版以降のB案（独立2回目のchampion戦）の"
                      "結果で、✓/✗はその確認戦単体で有意な勝ち越しを再現できたか。いずれも"
                      "旧バージョンで生成されたchampion_history/には記録が無いため\"-\"になる。）")
        lines.append("")
        if promo["flags"]:
            lines.append("**フラグ（異常の疑いがある世代）:**")
            for f in promo["flags"]:
                lines.append(f"- {f}")
        else:
            lines.append("昇格間隔・勝率に明確な異常（極端な短周期・長期停滞・極端な勝率）は"
                          "検出されませんでした。")
    lines.append("")

    # ---------------- 2. スコア分布の偏り ----------------
    lines.append("## 2. 評価スコアの分布・偏り")
    lines.append("")
    dist = analyze_score_distribution(trials)
    if dist.get("n"):
        lines.append(f"- n={dist['n']}　平均={dist['mean']}　中央値={dist['median']}　"
                      f"標準偏差={dist['stdev']}　最小={dist['min']}　最大={dist['max']}")
        lines.append(f"- 前半平均={dist['first_half_mean']}　後半平均={dist['second_half_mean']}　"
                      f"差分(後半-前半)={dist['trend']}"
                      f"（{'改善傾向' if dist['trend'] > 0 else '悪化・停滞傾向' if dist['trend'] < 0 else '横ばい'}）")
        bs = dist.get("base_score_stats")
        if bs:
            lines.append(f"- base_score（champion_term抜き）: n={bs['n']}　平均={bs['mean']}　"
                          f"中央値={bs['median']}　（scoreの平均との差分={_round(dist['mean'] - bs['mean'])}。"
                          f"差が大きいほど、championモードの勝敗ボーナスがscore全体を"
                          f"押し上げ/押し下げている度合いが大きいことを示唆します）")
            lines.append(f"- base_score 前半平均={bs['first_half_mean']}　後半平均={bs['second_half_mean']}　"
                          f"差分(後半-前半)={bs['trend']}"
                          f"（{'改善傾向' if bs['trend'] > 0 else '悪化・停滞傾向' if bs['trend'] < 0 else '横ばい'}）。"
                          "scoreのトレンド（1つ上の行）と方向が食い違う場合、scoreの改善/悪化は"
                          "championの強さの変化やchampion_bonus_scaleの乗り方の違いによるもので、"
                          "ゲームバランス自体（base_score）は必ずしも同じ方向に動いていない疑いがあります。")
        else:
            lines.append("- base_score: このstudyのtrialにはbase_score未記録のものしかありません"
                          "（2026-09-05(3)のtune_balance_search.py更新より前に実行されたtrialのみ、"
                          "またはchampionモード自体を使っていない可能性があります）。")
    else:
        lines.append("完了したtrialがありません。")
    lines.append("")
    lines.append("### ペナルティ項目別の平均値（値が大きいほど問題を示唆）")
    lines.append("")
    metric_avgs = analyze_metric_averages(trials)
    if metric_avgs:
        lines.append("| 指標 | 全trial平均 |")
        lines.append("|---|---|")
        for k, v in sorted(metric_avgs.items(), key=lambda kv: -kv[1] if isinstance(kv[1], (int, float)) else 0):
            lines.append(f"| `{k}` | {v} |")
    else:
        lines.append("記録された指標がありません。")
    lines.append("")

    # ---------------- 3. 重み分析 ----------------
    lines.append("## 3. 重みごとの分析（どの重みが悪さをしているか）")
    lines.append("")
    current_champion_weights = history[-1]["weights"] if history else None
    weight_rows, top_corr_rows, boundary_flags = analyze_weights(trials, current_champion_weights)

    lines.append("### 探索レンジ境界に張り付いている重み（要注意）")
    lines.append("")
    if boundary_flags:
        for f in boundary_flags:
            lines.append(f"- {f}")
    else:
        lines.append("境界に張り付いている重みはありません。")
    lines.append("")

    lines.append("### スコアとの相関が強い重み トップ10（|相関係数|降順）")
    lines.append("")
    lines.append("| 重み | 現チャンピオン値 | レンジ | レンジ内位置(%) | スコアとの相関 | "
                  "base_scoreとの相関 | 観測trial数 |")
    lines.append("|---|---|---|---|---|---|---|")
    for r in top_corr_rows:
        lines.append(
            f"| `{r['key']}` | {r['champion_value']} | {r['range']} | {r['position_in_range']} | "
            f"{r['corr_with_score']} | "
            f"{r['corr_with_base_score'] if r['corr_with_base_score'] is not None else '-'} | "
            f"{r['n_trials_observed']} |"
        )
    lines.append("")
    lines.append("（相関は「その重みの値を単独で動かした時にスコアがどちらに動く傾向があるか」の"
                  "大まかな目安であり、他の重みとの交互作用までは捉えられない点に注意。"
                  "全重みの一覧は本レポート末尾の付録を参照。"
                  "2026-09-06追加: 「base_scoreとの相関」はchampion_termを含まない生の"
                  "ゲームバランス指標との相関。championが強く大半のtrialが一方的に負けている回"
                  "（セクション2のscore平均とbase_score平均の差が大きい回）では、"
                  "「スコアとの相関」の順位は「championとの相性の良さ」を反映しているだけの"
                  "場合がある。両者が大きく食い違う重み（スコアとの相関は強いのにbase_scoreとの"
                  "相関はほぼ0など）は、汎用的なバランス改善ではなく特定championへの対策に"
                  "なっている疑いがあるため注意。）")
    lines.append("")

    # ---------------- 4. 昇格見送りtrial一覧（確認戦落ち／多様性チェック落ち） ----------------
    lines.append("## 4. 昇格見送りtrial一覧（確認戦落ち／多様性チェック落ち）")
    lines.append("")
    lines.append("championには（二項検定で有意に）勝ち越したものの、確認戦"
                  "（3.12節のB案。独立2回目のchampion戦）または多様性パネル"
                  "（DIVERSITY_PANEL_ARCHETYPES）のいずれかの基準を満たさず昇格を"
                  "見送られたtrialです。champion_history/gen_XXXX.jsonには昇格した"
                  "ものしか残らないため、従来はこのイベントはコンソール出力にしか残らず、"
                  "後から一覧できませんでした。"
                  "**2026-09-07修正:** 以前は多様性パネル落ちだけを想定した表でしたが、"
                  "2026-09-06(2)で確認戦（多様性パネルより先に実行される、より安いゲート）が"
                  "追加されて以降は、ここに挙がるtrialの一部は確認戦の時点で弾かれており"
                  "多様性パネル自体に到達していません（`却下段階`列が`confirmation`のもの。"
                  "この場合`多様性平均勝率`等は`-`のまま＝未実施であり、基準未達を意味しません）。"
                  "`却下段階`が`diversity`のものだけが、実際に多様性パネルで弾かれたtrialです。"
                  "このセクションが空の場合や件数が少ない場合でも、必ずしも探索が順調とは限りません"
                  "（確認戦・多様性チェックの一方または両方が無効化されている、championにまだ"
                  "誰も勝ち越していない、等の可能性があるため、セクション1の昇格間隔と合わせて"
                  "確認してください）。逆に、このセクションに該当trialが多い期間は、セクション1で"
                  "「昇格間隔が長く停滞している」ように見えても、実際には確認戦・多様性チェックが"
                  "非推移的なカウンターやノイズ由来の見かけの勝ち越しを継続的に弾いている健全な"
                  "状態である可能性があります。")
    lines.append("")
    rejections = analyze_diversity_rejections(trials)
    if not rejections:
        lines.append("該当するtrialはありませんでした（確認戦・多様性チェックの両方が"
                      "無効化されている場合も含みます。`--disable-confirmation-check`/"
                      "`--disable-diversity-check`を使っていないか確認してください）。")
    else:
        n_confirmation = sum(1 for r in rejections if r["rejected_at"] == "confirmation")
        n_diversity = len(rejections) - n_confirmation
        lines.append(f"該当trial数: {len(rejections)}（うち確認戦落ち: {n_confirmation}件　"
                      f"多様性チェック落ち: {n_diversity}件）")
        lines.append("")
        lines.append("| trial | 却下段階 | 対champion勝率(1回目) | 確認戦勝率 | 対戦した世代 | "
                      "多様性平均勝率 | 多様性最低勝率(相手) | 内訳(archetype:勝率) |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for r in rejections:
            div_min = (f"{r['diversity_min_win_rate']}@{r['diversity_min_win_rate_archetype']}"
                       if r["diversity_min_win_rate"] is not None else "-")
            breakdown = ", ".join(
                f"{name}:{_round(wr)}" for name, wr in sorted(r["diversity_win_rates"].items())
            ) or "-"
            confirm_wr = r["win_rate_vs_champion_confirm"] if r["win_rate_vs_champion_confirm"] is not None else "-"
            stage_label = "confirmation" if r["rejected_at"] == "confirmation" else "diversity"
            lines.append(
                f"| {r['trial_number']} | {stage_label} | {r['win_rate_vs_champion']} | "
                f"{confirm_wr} | {r['champion_generation_faced']} | {r['diversity_avg_win_rate']} | "
                f"{div_min} | {breakdown} |"
            )
    lines.append("")

    # ---------------- 5. 世代間の重み差分 ----------------
    lines.append("## 5. 世代間の重み差分（変化した重みのみ・丸め済み）")
    lines.append("")
    drift_rows = analyze_weight_drift(history)
    if args.recent_generations and args.recent_generations > 0 and len(drift_rows) > args.recent_generations:
        omitted = len(drift_rows) - args.recent_generations
        drift_rows = drift_rows[-args.recent_generations:]
        lines.append(f"（直近{args.recent_generations}遷移のみ表示。それ以前の{omitted}遷移は"
                      f"`{args.champion_history_dir}/gen_XXXX.json`を直接参照してください。"
                      f"`--recent-generations 0` で全件表示できます。）")
        lines.append("")
    if not drift_rows:
        lines.append("世代が2未満のため差分はありません。")
    else:
        for d in drift_rows:
            lines.append(f"### 世代{d['from_generation']} → 世代{d['to_generation']}")
            if not d["changed"]:
                lines.append("（数値として意味のある変化があった重みはありません）")
            else:
                lines.append("")
                lines.append("| 重み | 変化前 | 変化後 |")
                lines.append("|---|---|---|")
                for key, (pv, cv) in sorted(d["changed"].items()):
                    lines.append(f"| `{key}` | {pv} | {cv} |")
            lines.append("")

    # ---------------- 6. 戦績が悪いtrialの一覧 ----------------
    lines.append("## 6. 戦績が悪いtrialの一覧（棋譜調査の候補）")
    lines.append("")
    lines.append("scoreが低い順のtrialです。`champion_history/`には昇格したtrialしか残らないため、"
                  "戦績が悪いtrial（championに惨敗した等）をこれまで一覧する手段がありませんでした。"
                  "気になるtrialは `generate_kifu.py --storage <このレポートのstorage> "
                  f"--study-name {args.study_name} --trial-a <trial番号>` "
                  "で当時の重み設定のまま棋譜を再生成して調べられます"
                  "（`--tune pieces`/`both`運用でCONFIG[\"pieces\"]もチューニングしていた場合は"
                  "自動で反映されます）。")
    lines.append("")
    worst_n = getattr(args, "worst_trials", 10)
    worst_rows = analyze_worst_trials(trials, n=worst_n)
    if not worst_rows:
        lines.append("（`--worst-trials 0` が指定されたため省略、または完了trialがありません）")
    else:
        lines.append("| trial | score | base_score | 対champion勝率 | 勝-敗-分 | 対戦した世代 |")
        lines.append("|---|---|---|---|---|---|")
        for r in worst_rows:
            lines.append(
                f"| {r['trial_number']} | {r['score']} | "
                f"{r['base_score'] if r['base_score'] is not None else '-'} | "
                f"{r['win_rate_vs_champion'] if r['win_rate_vs_champion'] is not None else '-'} | "
                f"{r['champion_wins']}-{r['champion_losses']}-{r['champion_draws']} | "
                f"{r['champion_generation_faced'] if r['champion_generation_faced'] is not None else '-'} |"
            )
        lines.append("")
        lines.append("（base_score/対champion勝率が\"-\"の行はchampionモード未使用、または"
                      "2026-09-05(3)以前の旧形式trialです。）")
    lines.append("")

    # ---------------- 付録: 全重みの一覧 ----------------
    lines.append("## 付録: 探索対象の全重み一覧（丸め済み）")
    lines.append("")
    lines.append("| 重み | 現チャンピオン値 | レンジ | レンジ内位置(%) | スコアとの相関 | base_scoreとの相関 |")
    lines.append("|---|---|---|---|---|---|")
    for r in sorted(weight_rows, key=lambda r: r["key"]):
        lines.append(
            f"| `{r['key']}` | {r['champion_value']} | {r['range']} | {r['position_in_range']} | "
            f"{r['corr_with_score']} | "
            f"{r['corr_with_base_score'] if r['corr_with_base_score'] is not None else '-'} |"
        )
    lines.append("")

    return "\n".join(lines)


def _resolve_study_name(args):
    """--study-name省略時に、--storage内のstudy一覧から自動選択・案内する。

    2026-09-05追加: tune_balance_search.py側は--study-nameを省略すると
    f"search_balance_{tune}_d{depth}k{candidate_k}" 形式の名前を自動生成して
    使う（同ファイルのstudy_name変数参照）が、analyze_tuning_run.py側は
    「1つのdbに複数studyが同居しうるため、他studyの結果を誤って混ぜない」
    安全策として--study-nameを必須にしていた。この2つの組み合わせにより、
    tune_balance_search.pyを--study-name省略で実行した人がanalyze_tuning_run.py
    を実行しようとすると、自動生成された名前を知る手段がなく
    argparseの「required」エラーだけが表示されて行き詰まる（今回の報告）。
    ここでは「必須」自体は維持しつつ（安全策の意図は正しいので撤回しない）、
    storage内に実在するstudy名を自動で调べて案内する。study が1件だけなら
    それを自動選択し、複数あれば一覧を出して選ばせる。"""
    if args.study_name:
        return args.study_name

    try:
        names = optuna.get_all_study_names(storage=args.storage)
    except Exception as e:
        print(f"--study-name が指定されておらず、--storage（{args.storage}）内のstudy一覧の"
              f"取得にも失敗しました: {e}\n"
              f"--study-name を明示的に指定してください。", file=sys.stderr)
        sys.exit(1)

    if not names:
        print(f"--study-name が指定されておらず、--storage（{args.storage}）内にstudyが"
              f"1件も見つかりませんでした。\n"
              f"tune_balance_search.pyをまだ実行していないか、--storageのパスが"
              f"tune_balance_search.py実行時と異なっている可能性があります。",
              file=sys.stderr)
        sys.exit(1)

    if len(names) == 1:
        print(f"--study-name が指定されなかったため、--storage内で唯一見つかったstudy "
              f"'{names[0]}' を自動的に使用します。")
        return names[0]

    print(f"--study-name が指定されておらず、--storage（{args.storage}）内に複数のstudyが"
          f"見つかりました。--study-name で以下のいずれかを指定してください:",
          file=sys.stderr)
    for summary in optuna.get_all_study_summaries(storage=args.storage):
        print(f"  - {summary.study_name}（trial数: {summary.n_trials}）", file=sys.stderr)
    sys.exit(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--storage", type=str, default="sqlite:///balance_tuning_search.db")
    parser.add_argument("--study-name", type=str, default=None,
                         help="tune_balance_search.py実行時に使ったstudy名"
                              "（省略時の自動生成名は search_balance_{tune}_d{depth}k{candidate_k} 形式）。"
                              "本スクリプト側で省略した場合、--storage内のstudyを自動検出して"
                              "1件なら自動選択、複数あれば一覧を表示する。")
    parser.add_argument("--champion-history-dir", type=str, default="champion_history")
    parser.add_argument("--recent-generations", type=int, default=15,
                         help="セクション5（世代間の重み差分）に表示する直近の世代遷移数。"
                              "長期間の実行では世代数が多くなり差分表が肥大化するため、"
                              "既定では直近15遷移のみ表示する（0以下で全件表示）。")
    parser.add_argument("--worst-trials", type=int, default=10,
                         help="セクション6（戦績が悪いtrial一覧）に表示するworst-N件数"
                              "（scoreの低い順）。既定10件、0以下で全trial表示。"
                              "2026-09-06追加: generate_kifu.pyの--trial-a/-bで棋譜を"
                              "再生成して調べるtrialを見つけるための一覧。")
    parser.add_argument("--out", type=str, default=None,
                         help="出力ファイルパス。省略時は tuning_report_<study_name>_<時刻>.md")
    args = parser.parse_args()

    args.study_name = _resolve_study_name(args)

    study = optuna.load_study(study_name=args.study_name, storage=args.storage)
    # 2026-09-05(3)追加: champion_history_dirは複数studyで使い回されることがあるため、
    # args.study_nameでフィルタし、他studyの記録はexcluded_historyへ分離する
    # （build_report側でレポート本文に注意書きとして出力する）。
    history, excluded_history = load_champion_history(args.champion_history_dir, args.study_name)

    report = build_report(args, study, history, excluded_history)

    out_path = args.out or f"tuning_report_{args.study_name}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"{out_path} に保存しました（このファイルをそのままClaudeに渡してください）")


if __name__ == "__main__":
    main()
