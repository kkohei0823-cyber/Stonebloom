
# -*- coding: utf-8 -*-
"""
tune_balance_search.py
==========================================================
tune_balance.py の改良版。HeuristicBot同士ではなく、SearchBot(depth, candidate_k)
同士の自己対戦をOptunaの目的関数として使う。

背景:
  HeuristicBot同士で「スコア設計上の天井」に到達したパラメータでも、depth=2の
  SearchBotで対局させると評価が食い違う（RPスポット回避・後手優位など）ことが
  複数確認された。tune_balance.py 末尾のコメント自身が警告している通り、
  HeuristicBot向けに過学習したパラメータの可能性があるため、最初から
  SearchBot同士で調整し直す。

★重要な実装上の注意（tune_balance.pyからの変更点）:
  tune_balance.py は HeuristicBot(weights=weights) のように「明示的にコピーした
  辞書」をボットへ渡すことでOptunaの提案値を反映していた。しかし
  search_bot_skeleton.py の evaluate_state / _score_actions / minimax は内部で
  `weights=HEURISTIC_WEIGHTS`（従来は heuristic_bot.py モジュールのデフォルト引数）を
  直接参照しており、呼び出し側から明示的に上書きする経路が無かった。

  2026-08-10: CONFIGとHEURISTIC_WEIGHTSの実体をconfig.pyに一元化し、上書き・
  リセットのロジックも config.py の apply_config_overrides() / 
  apply_weight_overrides() に一本化した。このスクリプトは両関数を呼ぶだけで、
  「その場書き換え(.clear()+.update())」の実装詳細を自前で持たない。
  game.py / heuristic_bot.py / search_bot_skeleton.py はいずれもconfig.pyから
  同じdictオブジェクトをimportしているため、config.py側で上書きすれば
  すべてのモジュールに自動的に反映される（反映漏れが構造的に起こらない）。

前提:
  - game.py / heuristic_bot.py / search_bot_skeleton.py と同じフォルダに置く
  - Optuna はインストール済み

使い方（段階的にチューニングする場合。tune_balance.pyと同じ運用方針）:
  # --depth省略時はdepth2が既定（0.95節。旧0.77節のdepth3既定は運用コスト実測を
  # 踏まえて撤回した）。depth3はdepth2の約4.3倍遅い上、base_hp_panic_threshold
  # 導入後は並列実行時のCPU競合で1局あたりの実時間がさらに跳ね上がり
  # （0.95節実測: 6並列でdepth3は1局168〜209秒までタイムアウト既定値240秒に
  # 接近する一方、depth2は同条件でも最大16.5秒に収まる）ため、大量試行の
  # Optuna探索・非同期対戦本番ともdepth2を基準とする。depth3で検証し直したい
  # 場合は--depth 3を明示すること。
  # フェーズ1: 駒パラメータだけ探索（重みは現在のheuristic_bot.pyの値で固定）
  $ python3 tune_balance_search.py --tune pieces --trials 100 --games 30 --workers 16 \
        --depth 2 --candidate-k 10

  # フェーズ2: フェーズ1の結果を土台に重みも探索対象に追加
  $ python3 tune_balance_search.py --tune both --seed-from best_params_search_pieces.json \
        --trials 150 --games 30 --workers 16 --depth 2 --candidate-k 10

  既存のHeuristicBot版で調整したbest_params.jsonをウォームスタートの土台にしても良い
  （--seed-from にそのまま渡せる。キーの命名規則は同じ）。

  # 2026-09-10追加（3.22.4節・3.23節）: advance/advance_ramp_roundsが両方とも
  # レンジ下限付近に張り付いたまま動かない問題を切り分けるため、稼働中の既存
  # studyに「advance高め×ramp長め」の組み合わせを意図的に6trialだけ注入して
  # 続行する場合（--study-name/--storageは既存studyと同じ値を指定すること）:
  $ python3 tune_balance_search.py --tune weights --trials 30 --games 30 --workers 16 \
        --depth 2 --candidate-k 10 --enqueue-advance-ramp-combos \
        --study-name search_balance_weights_260905_v3 --storage <既存studyのstorage>
  # --enqueue-advance-ramp-combosはstudy.user_attrsに完了フラグを記録するため、
  # 同じstudyに対して再実行しても6trialが二重投入されることはない。

  # 2026-09-10(3)追加（3.24節）: 設定ミスに気付いてCtrl+Cで中断した場合、同じ
  # コマンドに--resumeを追加して再実行すると、--trialsで指定した目標trial数に
  # 届くまでの「残り」だけを実行する（既に完了した分は数え直さない）。設定
  # （--tune・探索レンジ・championオプション等）を変えて再実行した場合は
  # 安全側に倒して新しいセッションとして--trials全件を実行する:
  $ python3 tune_balance_search.py --tune weights --trials 30 --games 30 --workers 16 \
        --depth 2 --candidate-k 10 --resume \
        --study-name search_balance_weights_260910_v4

  # 2026-09-10(3)追加（3.24節）: Optuna探索を一切行わず、1つの重み設定を自分自身と
  # jitterありで対戦させて先後（手番）差だけを単独で計測する診断モード。champion_
  # weights.jsonが対象なら--first-move-weightsは省略可（--champion-fileの既定値を使う）:
  $ python3 tune_balance_search.py --measure-first-move-advantage --first-move-games 300 \
        --first-move-weights champion_history/gen_0028.json --workers 16 --depth 2 --candidate-k 10

  1トライアルあたりの時間の目安: depth=2, candidate_k=10のSearchBot同士の対局は
  実測で1戦あたり約1.5〜3秒程度（盤面・乱数次第で変動）。games=30なら
  1トライアル分は16並列で数秒〜十数秒程度、trials=100なら全体で15分前後が目安
  （マシン・設定により変わるため、まず --trials 5 程度で試走することを推奨）。
  --depth 3を明示して回す場合、0.77節の実測（depth3はdepth2の約4.3倍）から
  上記の目安時間もおよそ4倍で見積もること。加えて0.95節の実測の通り、並列度が
  上がるほどdepth3の実時間はこの単純倍率よりさらに悪化するため、depth3での
  大量並列実行は推奨しない。
"""

import argparse
import copy
import datetime
import hashlib
import json
import math
import os
import random
import sys
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import optuna

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import CONFIG, RP_SCALE  # noqa: E402  生産可能な駒種数（多様性指標の分母）取得用のみに使う。
from game import type_multiplier, is_damage_nullified  # noqa: E402  自殺的接近の検出用
N_PRODUCIBLE_KINDS = len(CONFIG["starting_reserve"])  # 現状5種（本拠は生産対象外）

# ------------------------------------------------------------
# 探索対象パラメータの定義
# ------------------------------------------------------------

# HEURISTIC_WEIGHTS側の探索範囲。exposure/incoming_damageも対象に含む
# （固定したまま他だけ最適化すると挙動の食い違いが再発するため）。
#
# 既知バグの再発防止: incoming_damageを独立探索させるとattackと乖離し
# （実測1/6程度）、「相性で不利な接近」がスコア上プラスになる事故があった。
# 以後はincoming_damage_ratio（attackに対する比率、1.0=完全対称中心）として
# 探索し、実値は毎トライアル incoming_damage = attack_value * ratio で計算する
# （2026-08-27: 符号統一方針により、incoming_damageは常に0以上の大きさで
# 持つ。エンジンへ渡す直前にweights.to_engine_signed()が符号を反転する）。
#
# 教訓: 比率化した重み(incoming_damage等)は、実値の直接エントリをWEIGHT_SEARCH_SPACE
# に残さないこと（残すとOptunaが無駄な次元を提案し続け探索効率が落ちる）。
# base_defense/base_defense_ratioも同じ比率化パターンで追加済み。
WEIGHT_SEARCH_SPACE = {
    "exposure":             ("float", 0.2, 10.0),
    "vp_star":              ("float", 1.0, 15.0),
    # 2026-09-09追加: search_balance_weights_260905_v3のレポート分析でレンジ上限
    # （8〜20の上限側、レンジ内位置95.19%）に長期間張り付き続けていることが検出された
    # ため上限を引き上げた（2026-09-04のbase_pressure等と同種の対応）。
    "vp_tengen":            ("float", 8.0, 30.0),
    # 2026-09-05(3)追加: rp_spot/engineer_econ_bonusはsearch_balance_weights_260905_v2の
    # レポートでrp_spot_neglect_penalty（全trial平均115.15、他ペナルティの3〜4倍）が
    # 支配的に高止まりし続け、かつ多様性パネルのeconomy_boomアーキタイプに対して
    # 特に脆弱な候補が繰り返し観測された（経済無視への構造的な弱さの疑い）ことを受け、
    # 経済関連の重みがより強い値を試せるよう上限を仮に引き上げた。境界張り付きの
    # 実測フラグが出ているわけではない実験的な拡張のため、再度レポートを見て
    # 効果がなければ元に戻すか別の対策を検討すること。
    "rp_spot":         ("float", 5.0, 55.0),
    "engineer_econ_bonus":  ("float", 0.2, 15.0),
    "midgame_spot":         ("float", 1.0, 15.0),
    "support":              ("float", 0.2, 10.0),
    "danger":               ("float", 0.2, 10.0),
    "attack":               ("float", 0.2, 1.5),
    "incoming_damage_ratio":("float", 0.7, 1.4),
    "base_pressure":        ("float", 0.2, 10.0),
    "base_defense_ratio":   ("float", 0.3, 3.0),
    "base_hp_weight":       ("float", 0.1, 2.0),
    "kill_bonus":           ("float", 5.0, 30.0),
    "vp_spot_kill_bonus":   ("float", 20.0, 60.0),
    "rp_spot_kill_bonus":   ("float", 10.0, 30.0),
    # 2026-09-07追加: search_balance_weights_260905_v3のレポートで下限
    # （0.5〜5の下限側、レンジ内位置2.37%）に張り付いていることが検出された
    # （3.3節の本拠特攻抑制で「advanceをできるだけ下げたい」圧力がレンジの
    # 下限自体にぶつかっている疑い）ため、下限を引き下げた
    # （2026-09-04のbase_pressure、2026-09-05(3)のefficiency等と同種の対応）。
    "advance":              ("float", 0.2, 5.0),
    # 2026-09-05(3)追加: search_balance_weights_260905_v2のレポートでレンジ下限
    # （0.2〜3の下限側、レンジ内位置3.81%）に張り付いていることが検出されたため
    # 下限を引き下げた（2026-09-04のbase_pressureと同種の対応）。
    "efficiency":           ("float", 0.05, 3.0),
    "rp_cost":              ("float", 0.2, 5.0),
    # 2026-09-09追加: search_balance_weights_260905_v3のレポート分析でレンジ上限
    # （0.05〜3の上限側、レンジ内位置95.9%）に長期間張り付き続けていることが検出された
    # ため上限を引き上げた（2026-09-04のbase_pressure等と同種の対応）。
    "rp_differential":      ("float", 0.05, 6.0),
    # チャンピオン報酬(Tier1/2、実装済み)から選んだ自動検証対象10種
    # （選定基準はweights.py SEARCH_SPACE_KEYS参照）。範囲は近い性質の重みを踏襲した仮値。
    "favorable_matchup_bonus":     ("float", 0.0, 5.0),
    # 2026-09-05(3)追加: search_balance_weights_260905_v2のレポートでレンジ上限
    # （0〜5の上限側、レンジ内位置99.39%）に張り付いていることが検出されたため
    # 上限を引き上げた（2026-09-04のbase_pressureと同種の対応）。
    "unfavorable_matchup_penalty": ("float", 0.0, 8.0),
    "finishing_blow_bonus":        ("float", 0.0, 15.0),
    "vp_spot_guard_bonus":         ("float", 0.0, 10.0),
    "rp_spot_guard_bonus":         ("float", 0.0, 10.0),
    "midgame_spot_guard_bonus":    ("float", 0.0, 10.0),
    "base_proximity_alert":        ("float", 0.0, 5.0),
    # 2026-09-09追加: search_balance_weights_260905_v3のレポート分析でレンジ上限
    # （0〜5の上限側、レンジ内位置95.93%）に長期間張り付き続けていることが検出された
    # ため上限を引き上げた（2026-09-04のbase_pressure等と同種の対応）。
    "center_control":              ("float", 0.0, 10.0),
    "corner_edge_avoidance":       ("float", 0.0, 5.0),
    "archer_immunity_awareness":   ("float", 0.0, 5.0),

    # 2026-08-25にBASE_WEIGHTSへ昇格した生産タイミング判断用3種
    # （2026-08-28追加: WEIGHT_SEARCH_SPACEへの追加漏れがあり、weights.py
    # BASE_WEIGHTS(23種)とずれていた）。
    # 2026-08-30改修: 旧rp_hoarding_pref/rp_overflow_avoidanceは検証の結果
    # 機能しておらず廃止（weights.py BASE_WEIGHTSのコメント参照）。
    # 2026-08-30再改修: 一度導入したpiece_cap_safety_margin/max_pending_reserve
    # の2種（整数のON/OFFガード）も、前者は対症療法的、後者は終盤の高収入下で
    # 一律に生産を止めてしまう副作用があったため廃止し、連続値のincome_
    # confidence_ratio 1種に統合した。1.0を大きく超えるほど「生産頻度＞
    # 配置頻度」の壊れた挙動に近づくため、範囲の上限はあくまで実験用
    # （実運用は既定値0.5前後を推奨、再チューニング要）。
    "placement_priority_bonus":    ("float", 0.0, 50.0),
    "base_threat_counter_priority":("float", 0.0, 10.0),

    # 2026-08-31追加: 生産の歩兵一辺倒是正用（weights.py BASE_WEIGHTSコメント
    # 参照）。base_threat_counter_priorityと同系統（生産スコアへの相性加点）
    # のため近い探索範囲にした。
    # 2026-09-05(3)追加: search_balance_weights_260905_v2でスコアとの相関が
    # +0.24（探索対象重み中トップクラス）とプラス方向に強いにも関わらず、
    # 現チャンピオン値はレンジ内位置15.13%と低い水準に留まっていた。
    # rp_spot_neglect_penaltyの高止まり・多様性パネルのeconomy_boom脆弱性と
    # 合わせて、経済・生産多様性側をより強く評価する構成をOptunaが試せる余地を
    # 広げる狙いで上限を引き上げた。
    "production_diversity_pref":   ("float", 0.0, 15.0),

    # base_hp_panic_threshold（0.86節で導入、2026-09-02にBASE_WEIGHTSへ昇格）。
    # 本拠HP危険水域(<80%。2026-09-02(2)に0.5→0.8へ引き上げ済み。理由は
    # scoring_common.pyのBASE_HP_PANIC_ZONE_RATIOコメント参照)で
    # base_defense項に(1+severity*weight)を掛ける倍率。
    # 上限20.0は仮の範囲、実測して絞り込むこと。
    "base_hp_panic_threshold":     ("float", 0.0, 20.0),
    # 2026-09-05(3)追加: rp_spot/engineer_econ_bonus/production_diversity_prefと
    # 同じ理由（経済無視への構造的脆弱性対策）で上限を引き上げた。
    "rp_income_margin":            ("float", 0.05, 6.0),

    # 2026-09-04追加: base_pressure_ramp_rounds（weights.py BASE_WEIGHTS参照）。
    # rush_opening_pressureは意図的にここへ含めない（Tier4止まりのスタイル選択
    # 用ノブであり、通常ビルドの既定バランス探索対象ではないため）。
    # このキーを追加し忘れると分析対象にも入らず、値がweights.pyのデフォルト(5)に
    # 固定されたまま動かなくなる（2026-09-05指摘）。
    # 下限は1（0は「無効」という特殊な後方互換値であり、rp_income_marginの
    # 0.05下限と同じ理由で連続的な探索空間から意図的に外している）。
    # 上限は元々15（turn_limit_per_player(30)の半分を仮の目安にした値。本拠特攻を
    # 抑えたいのは主に序盤〜中盤前半のため、ゲーム全体の半分より長い
    # 立ち上がりは想定していなかった）だったが、2026-09-07にsearch_balance_
    # weights_260905_v3のレポートで上限（レンジ内位置100.0%）に張り付いている
    # ことが検出されたため20へ引き上げた。turn_limit_per_player(30)の2/3程度まで
    # 許容する形になる。それでも張り付く場合はさらに広げるか、advance（同時に
    # 下限を引き下げ済み）側の効果とあわせて再検討すること。
    "base_pressure_ramp_rounds":   ("int", 1, 20),

    # 2026-09-09追加: advance_ramp_rounds（weights.py BASE_WEIGHTS参照。要新設）。
    # search_balance_weights_260905_v3のレポート分析で、advanceが探索レンジ下限
    # （0.2〜5の下限側、レンジ内位置2.12%）に長期間張り付き続けていることが検出された。
    # base_pressure_ramp_rounds導入前のbase_pressureと同型のパターン（企画書9.1.1節：
    # 「ラウンド1の丸裸の敵本拠へ前進する」ことと「中終盤の正当な前進」を1つの定数重みでは
    # 区別できない）である疑いが強く、Optunaがadvanceの値そのものをほぼゼロへ潰すことで
    # 力技で相殺している可能性がある。base_pressure_ramp_roundsと全く同じ汎用ランプ関数
    # （scoring_common._base_pressure_ramp_multiplier）を流用してadvanceにも立ち上がり
    # 期間を導入する。rush_opening_pressureに相当する「上乗せ専用ノブ」は今回は新設しない
    # （必要になった場合は同じパターンで別途追加を検討）。
    # 下限・上限ともbase_pressure_ramp_roundsと同じレンジを踏襲した仮値。実測して絞り込むこと。
    # このキーを追加し忘れるとbase_pressure_ramp_rounds導入時と同じ事故（分析対象にも入らず、
    # 値がweights.pyのデフォルトに固定されたまま動かない）が起きるので注意。
    "advance_ramp_rounds":         ("int", 1, 20),
}

# CONFIG["pieces"] 側の探索範囲。tune_balance.pyと同じ形式。
# RP100倍化に合わせ、下限・上限とも旧値×RP_SCALEへ変更（比率は維持）。
CONFIG_PIECE_SEARCH_SPACE = {
    ("歩兵", "cost"): ("int", 2 * RP_SCALE, 4 * RP_SCALE),
    ("歩兵", "produce_cost"): ("int", 3 * RP_SCALE, 6 * RP_SCALE),
    ("騎兵", "cost"): ("int", 3 * RP_SCALE, 5 * RP_SCALE),
    ("騎兵", "produce_cost"): ("int", 3 * RP_SCALE, 7 * RP_SCALE),
    ("重装兵", "cost"): ("int", 3 * RP_SCALE, 5 * RP_SCALE),
    ("重装兵", "produce_cost"): ("int", 3 * RP_SCALE, 8 * RP_SCALE),
    ("弓兵", "cost"): ("int", 6 * RP_SCALE, 9 * RP_SCALE),
    ("弓兵", "produce_cost"): ("int", 2 * RP_SCALE, 6 * RP_SCALE),
    ("工兵", "cost"): ("int", 2 * RP_SCALE, 4 * RP_SCALE),
    ("工兵", "produce_cost"): ("int", 3 * RP_SCALE, 6 * RP_SCALE),
    #("歩兵", "atk"): ("int", 12, 36),
    #("弓兵", "atk"): ("int", 12, 48),
    #("工兵", "hp"): ("int", 100, 300),
    #("本拠", "hp"): ("int", 100, 1000),
}

N_GAMES_PER_TRIAL_DEFAULT = 30  # SearchBotは1戦が重いため、HeuristicBot版(60)より少なめを既定にした


# ------------------------------------------------------------
# ワーカープロセス側の処理
# ------------------------------------------------------------
# 上書き・リセットは必ずconfig.py/weights.pyのapply_*_overrides()経由で行う
# （このスクリプト独自のbaseline管理は持たない）。


def _count_bad_engagements(game):
    """相性面で明確に不利な接近（自殺的接近）が何回発生したかを数える。

    2026-08-11追加。実戦棋譜で確認されたバグ（incoming_damage/attackの重みが
    非対称にドリフトし、騎兵が歩兵の隣へ自分から移動する等の悪い接近が
    スコア上プラスになっていた）の回帰検知用。重みを対称に戻した後も
    別の要因で再発しうるため、盤面から直接この現象を検出してスコアに
    反映できるようにしておく。

    判定方法: 各ラウンドの各手番（turns）について、"move"アクションの結果、
    移動先(dst)に相性面で明確に不利な敵（type_multiplierで見て、相手が
    自分を攻撃する倍率 > 自分が相手を攻撃する倍率、かつ実ダメージ期待値も
    相手の方が大きい）が隣接することになった場合を1件としてカウントする。
    自分側も撃破が確定している場合（相打ち上等な状況）は除外する。

    2026-08-12修正: 工兵の弓兵ダメージ無効化（0.32節）を反映。is_damage_nullified()で
    無効化される方向（例: 弓兵→工兵）はoutgoing/incomingをそれぞれ0として扱う。
    これを入れないと、無効化により実際にはノーダメージのはずの接近（例: 工兵が
    弓兵の隣に移動する）を、type_multiplierだけを見て誤って「自殺的接近」として
    過剰カウントしてしまう（このAI隣接判定自体は簡易近似で射程武器を考慮しない旨
    の注記が元々あるが、無効化ルールは距離を問わないためこの隣接判定でも捕捉できる）。
    """
    vs = CONFIG["vp_spots"]
    count = 0
    for entry in game.kifu:
        for turn in entry["turns"]:
            action = turn["action"]
            if action[0] != "move":
                continue
            mover = turn["player"]
            dst = tuple(action[2])
            board_pieces = {tuple(p["pos"]): p for p in turn["board"]}
            moved_piece = board_pieces.get(dst)
            if moved_piece is None or moved_piece["owner"] != mover:
                continue  # 想定外の状態。安全側に無視する
            mover_kind = moved_piece["kind"]
            if mover_kind == "本拠":
                continue
            mover_atk = CONFIG["pieces"][mover_kind]["atk"]
            if mover_atk <= 0:
                continue

            worst_ratio = 0.0  # (相手が受ける倍率 は無視し) 被弾/与ダメージ比の最大値を見る
            for pos, p in board_pieces.items():
                if p["owner"] == mover:
                    continue
                dx = abs(pos[0] - dst[0])
                dy = abs(pos[1] - dst[1])
                if dx + dy != 1:
                    continue  # 隣接のみ対象（簡易近似。射程武器は考慮しない）
                enemy_kind = p["kind"]
                enemy_atk = CONFIG["pieces"][enemy_kind]["atk"]
                if enemy_atk <= 0:
                    continue
                if is_damage_nullified(mover_kind, enemy_kind):
                    outgoing = 0.0
                else:
                    outgoing = mover_atk * type_multiplier(mover_kind, enemy_kind)
                if is_damage_nullified(enemy_kind, mover_kind):
                    incoming = 0.0
                else:
                    incoming = enemy_atk * type_multiplier(enemy_kind, mover_kind)
                if outgoing <= 0:
                    continue
                worst_ratio = max(worst_ratio, incoming / outgoing)

            # 被弾期待値が与ダメージ期待値の1.5倍を超える接近を「自殺的接近」とみなす
            # （閾値は初期値。要チューニング）。
            if worst_ratio > 1.5:
                count += 1
    return count


def _rp_spot_neglect_ratio(game, early_rounds=10):
    """序盤（1〜early_roundsラウンド。中盤解禁スポットが開く10ラウンド目まで）における、
    RPスポット(4-4点・4箇所)の平均「空き」比率を返す（0.0=常に4箇所とも埋まっている、
    1.0=一貫して無視されている）。

    背景: 「序盤からRPスポットを完全に無視し本拠を攻め続ける」という縮退挙動を
    score(目的関数)に組み込みたいという要望への対応。生産数やRP総獲得量は
    ①工兵のような低コスト駒の量産で数字を誤魔化せる、②「どの程度が適正か」の
    基準がゲーム内に存在しない、という弱点があるため採用しなかった（詳細は
    セッションサマリ参照）。代わりに「4箇所のRPスポットというマスに、
    どちらかの駒が実際に置かれ続けているか」という、特定の座標や数値を
    正解と決め打ちしない中立な占有率指標を新設した（0.18節のopening_penalty等と
    同じ設計方針）。

    重要な利点: win0_rate/win1_rateのような勝敗ベースの指標と異なり、
    「両陣営が対称に同じ盲点（RPスポット軽視）を共有している」自己対戦でも
    検出できる。対称自己対戦は「相対的な優劣」しか見えないため、両者が
    同じ間違いを犯すケース自体は原理的に検出できないという構造的な弱点が
    あったが（セッション内の相談参照）、この指標は勝敗を経由せず「マスが
    埋まっているか」という事実だけを見るため、この弱点を回避できる。
    """
    spots = CONFIG["rp_spots"]["points"]
    n_spots = len(spots)
    considered_slots = 0
    empty_slots = 0
    for entry in game.kifu:
        if entry["round"] > early_rounds:
            break
        occupied = {tuple(p["pos"]) for p in entry["board"]}
        empty_slots += sum(1 for s in spots if tuple(s) not in occupied)
        considered_slots += n_spots
    if considered_slots == 0:
        return 0.0
    return empty_slots / considered_slots


def _count_engagement_abandonment(game, hp_ratio_threshold=0.3, favorable_ratio_threshold=1.2):
    """「有利な相性で敵に隣接している駒が、瀕死でもないのに次のラウンドで
    その敵から離脱する」パターンの発生回数を数える。

    背景: 4.21節・0.20節の対称化修正で「有利対面から自分から逃げる」問題は
    大きく改善したと報告されていたが、依然として「配置直後・数ラウンドの
    継続戦闘の後に理由なく離脱する」ケースが観測されるとの報告を受け、
    _count_bad_engagements()（不利な接近を検出する）と対になる指標として新設した。
    「配置した以上、致命的な危険が無い限りは有利対面を維持すべき」という
    設計意図を、_count_bad_engagementsと同じ枠組み（type_multiplier・
    is_damage_nullifiedを考慮した比率判定）で検出する。

    判定方法:
      各ラウンドの戦闘解決後の盤面(entry["board"])を見て、隣接する敵のうち
      少なくとも1体に対しoutgoing/incoming比がfavorable_ratio_threshold以上
      （デフォルト1.2倍。favorable_ratio_threshold未満の互角に近い対面は
      「維持すべき」と決め打ちしないよう対象から除外）の駒を
      「有利に張り付いている駒」として記録する。
      次のラウンドの同じプレイヤーの手番で、その駒がまだそこにいて
      (a) hp/max_hpがhp_ratio_threshold以下ではない（＝瀕死の緊急避難ではない）
      (b) moveで離れ、かつ移動後はどの敵とも隣接しなくなる
      場合、離脱1件としてカウントする。

    注意: 駒に一意なIDが無いため「同じマスにいた駒＝同じ駒」という座標ベースの
    近似で追跡している（_count_bad_engagementsと同じ制約）。極めて稀に、
    たまたま同じマスに別の同種駒が入れ替わるケース等で誤検出しうるが、
    大量対戦での統計指標としては実用上問題ない粒度と判断した。
    """
    count = 0
    kifu = game.kifu
    for i in range(len(kifu) - 1):
        entry = kifu[i]
        next_entry = kifu[i + 1]
        board_pieces = {tuple(p["pos"]): p for p in entry["board"]}

        favorable_positions = {}  # pos -> (owner, hp, max_hp)
        for pos, p in board_pieces.items():
            kind = p["kind"]
            if kind == "本拠":
                continue
            atk = CONFIG["pieces"][kind]["atk"]
            if atk <= 0:
                continue
            owner = p["owner"]
            best_ratio = 0.0
            has_adjacent_enemy = False
            for pos2, p2 in board_pieces.items():
                if p2["owner"] == owner:
                    continue
                if abs(pos2[0] - pos[0]) + abs(pos2[1] - pos[1]) != 1:
                    continue
                has_adjacent_enemy = True
                ek = p2["kind"]
                eatk = CONFIG["pieces"][ek]["atk"]
                outgoing = 0.0 if is_damage_nullified(kind, ek) else atk * type_multiplier(kind, ek)
                incoming = 0.0 if (eatk <= 0 or is_damage_nullified(ek, kind)) else eatk * type_multiplier(ek, kind)
                if outgoing <= 0:
                    continue
                ratio = float("inf") if incoming <= 0 else outgoing / incoming
                best_ratio = max(best_ratio, ratio)
            if has_adjacent_enemy and best_ratio >= favorable_ratio_threshold:
                favorable_positions[pos] = (owner, p["hp"], p["max_hp"])

        if not favorable_positions:
            continue

        for turn in next_entry["turns"]:
            action = turn["action"]
            if action[0] != "move":
                continue
            src = tuple(action[1])
            dst = tuple(action[2])
            if src not in favorable_positions:
                continue
            owner, hp, max_hp = favorable_positions[src]
            if turn["player"] != owner:
                continue
            if max_hp <= 0 or hp / max_hp <= hp_ratio_threshold:
                continue  # 瀕死での正当な撤退とみなし除外

            dst_board = {tuple(pp["pos"]): pp for pp in turn["board"]}
            still_adjacent_enemy = any(
                p2["owner"] != owner and abs(pos2[0] - dst[0]) + abs(pos2[1] - dst[1]) == 1
                for pos2, p2 in dst_board.items()
            )
            if not still_adjacent_enemy:
                count += 1
    return count


def _run_one_game(args):
    """1試合を実行し、集計に必要な情報だけを返す（別プロセスで実行される）"""
    weights_overrides, config_overrides, seed, depth, candidate_k = args
    random.seed(int(seed))

    import config as C
    import weights as W
    C.apply_config_overrides(config_overrides)
    W.apply_weight_overrides(weights_overrides)

    from game import Game
    from search_bot_skeleton import SearchBot

    # SearchBot.choose()/choose_production()は元々完全決定論のため、rngを
    # 渡さないとn_games分の平均化が機能しない（seedを変えても結果が変わらない）。
    #
    # このスクリプトは速度優先で意図的にrngを渡さない（完全決定論・高速経路）。
    # ゲームごとに展開を変えたい場合はmain.py/web_api.py側でrng=random.Random()を渡す。
    bot0 = SearchBot(depth=depth, candidate_k=candidate_k)
    bot1 = SearchBot(depth=depth, candidate_k=candidate_k)

    game = Game()
    bots = {0: bot0, 1: bot1}
    result = game.run(bots)

    produced_counter = Counter()
    kill_count = 0
    for entry in game.kifu:
        for kind in entry["produced"].values():
            produced_counter[kind] += 1
        kill_count += len(entry["deaths"])

    # 初手passの記録: 「先手が初手passで後手を誘ってからRPスポットを確保する」
    # 縮退戦略（後手優位を先手が逆用するパターン）を検出するため。
    first_move_is_pass = False
    if game.kifu:
        first_round_actions = game.kifu[0]["actions"]
        first_mover = game.turn_order[0]
        first_action = first_round_actions.get(first_mover)
        if first_action is not None and first_action[0] == "pass":
            first_move_is_pass = True

    bad_engagements = _count_bad_engagements(game)
    rp_spot_neglect = _rp_spot_neglect_ratio(game)
    engagement_abandonment = _count_engagement_abandonment(game)

    return {
        "winner": result["winner"],
        "reason": result["reason"],
        "rounds": result["rounds"],
        "produced": dict(produced_counter),
        "kills": kill_count,
        "first_move_is_pass": first_move_is_pass,
        "bad_engagements": bad_engagements,
        "rp_spot_neglect": rp_spot_neglect,
        "engagement_abandonment": engagement_abandonment,
    }


DEGENERATE_TIEBREAK_REASONS = {
    "second_player_default",
    "total_hp_tiebreak",
    "base_hp_tiebreak",
    "draw_simultaneous_destruction",
    "timeout",  # 2026-08-12追加: 1局がGAME_TIMEOUT_SECONDS_DEFAULTを超えた場合の擬似reason。
                # 「決着らしい決着がつかなかった対局」という点でdegenerateな結果と同種のため、
                # 既存のtiebreak_penaltyにそのまま乗せる（新しいペナルティ項目を増やさない）。
}

# 極端なパラメータだと1局が数十秒に達することがある（turn_limit_per_player=30で
# 必ず終了はするが、待ち時間の体感が悪い）。1局あたりタイムアウトを設け、
# 超過時は退化的な結果として扱いtrialを進行させる。
#
# 2026-09-03修正: base_hp_panic_thresholdの発火ライン引き上げ(0.5→0.8。
# scoring_common.pyのBASE_HP_PANIC_ZONE_RATIO参照)以降、「[警告] チャンピオン戦の
# 1局が120秒でタイムアウトしました」の頻発が報告された。実測（depth=3,
# candidate_k=10、双方base_hp_panic_threshold=18の鏡合わせ自己対戦）したところ、
# 旧ライン(0.5)では平均2.8秒・平均9.3ラウンドで終わっていた対局が、新ライン(0.8)
# では平均50.3秒・最悪68.2秒・平均23.2ラウンドまで伸びていた（終局時の盤上生存
# 駒数も5.4→13.6に倍増）。これはバグではなく、双方が本拠HPの早い段階から
# パニック的に守りを固めるようになった結果、駒の損耗が減って対局が長引く
# という、防御が正しく機能したことの副作用（互いに強く警戒し合う「相互慎重化」）。
# 特にチャンピオン戦は終盤になるほど両者とも十分にチューニングされた守備を
# 持つ鏡合わせに近い対局になりやすく、この副作用が一番出やすい場面と一致する。
# ratio自体を0.6程度まで下げる案も検証したが、その場合は本拠速攻への対策効果
# （0.8ラインでの検証: 対本拠速攻でdefenderが16/16勝）が0.5ラインと同水準
# （13/16勝）まで後退し、今回の問題の本来の目的を果たせなくなるため採用しなかった。
# 実測の新しい最悪ケース(68秒)に対して十分な安全マージンを確保するため、
# タイムアウトを120秒→240秒に引き上げる。
GAME_TIMEOUT_SECONDS_DEFAULT = 240  # 実測の最悪ケース(0.8ライン、depth=3、鏡合わせ自己対戦で68秒)に安全マージンを乗せた値
# 2026-09-03追記（0.95節）: 上記はdepth=3・単独実行での実測値。実際には非同期対戦の
# 同時実行下ではCPU競合により1局あたりの実時間がさらに悪化し（6並列でdepth3は
# 168〜209秒まで伸び、この240秒に対する安全マージンはほぼ消失することを実測）、
# これが基準depthをdepth2へ引き下げる決め手になった（同条件でdepth2は最大16.5秒）。
# 基準depthをdepth2に変更した後もこの240秒という値自体は変更していない
# （depth2運用では十分すぎる安全マージンになるが、--depth 3を明示指定した検証実行
# の安全網としてこのままにしてある）。


def _run_one_asymmetric_game(args):
    """candidate（検証中の重み）とchampion（現在最強と確認済みの重み）を
    非対称に対戦させる1局を実行する（別プロセスで実行される）。

    2026-08-16追加: 従来の_run_one_game（自己対戦）は両者が常に同じ重みの
    ミラー戦だったため、勝率がAIの実力を一切反映しない（win0/win1は先後の
    手番差しか測れない）という問題があった。この関数はその代わりに、常に
    「現在最も強いと確認されている設定」を固定の相手として使うことで、
    win_rateを実際の強さの指標として使えるようにする（検証ハンドブック
    参照）。

    candidate_weights / champion_weights は両方とも weights.resolve_weights()
    が返す「解決済みの完全なHEURISTIC_WEIGHTS形状の辞書」であること
    （apply_weight_overridesのようなグローバル書き換えは使わない。candidateと
    championの2つを同時に有効な状態で持つ必要があるため）。
    CONFIG（駒コスト等のルール側パラメータ）は両者共通の土俵なので、
    従来通りapply_config_overridesでグローバルに適用する。
    """
    (candidate_weights, champion_weights, config_overrides, seed,
     depth, candidate_k, candidate_is_p0) = args
    random.seed(int(seed))

    import config as C
    C.apply_config_overrides(config_overrides)

    from game import Game
    from search_bot_skeleton import SearchBot

    # チャンピオン戦はjitterあり。seedは必ず明示的に渡す（省略するとOSエントロピーで
    # 独立初期化されseedの影響を受けない）。candidate/championは1つずらしたseedを使う。
    candidate_bot = SearchBot(depth=depth, candidate_k=candidate_k,
                               rng=random.Random(int(seed)), weights=candidate_weights)
    champion_bot = SearchBot(depth=depth, candidate_k=candidate_k,
                              rng=random.Random(int(seed) + 1), weights=champion_weights)

    if candidate_is_p0:
        bots = {0: candidate_bot, 1: champion_bot}
        candidate_player = 0
    else:
        bots = {0: champion_bot, 1: candidate_bot}
        candidate_player = 1

    game = Game()
    result = game.run(bots)

    winner = result["winner"]
    return {
        "candidate_won": winner == candidate_player,
        "draw": winner is None,
        "reason": result["reason"],
        "rounds": result["rounds"],
        # 2026-09-10(3)追加（3.24節）: evaluate_vs_champion側で先後別の勝率を
        # 集計できるよう、この1局でcandidateが先手(p0)だったかを持たせておく。
        "candidate_is_p0": candidate_is_p0,
    }


def _promotion_is_significant(wins, n_games, threshold=0.5, alpha=0.1):
    """winsがn_games中、閾値threshold(通常0.5)を「有意に」上回っているかを
    厳密な二項検定（片側）で判定する。

    2026-08-16追加: n_gamesが小さいと、実力差がなくてもwin_rateが0.5から
    ぶれることは普通に起こる（例: 12/20勝=60%は、実力互角でも珍しくない）。
    ここでのみ勝ち越しただけでchampionを更新してしまうと、championが実力の
    裏付けなくノイズで漂流し続け、結局「検証環境が信用できない」という
    元の問題を形を変えて再発させてしまう。alpha（片側有意水準）は既定0.1と
    やや緩めにしてある——厳しくしすぎるとchampionがなかなか更新されず、
    tuningが停滞するため。より厳格にしたい場合は--promotion-alphaを下げること。
    """
    if n_games == 0:
        return False
    tail = sum(
        math.comb(n_games, k) * (threshold ** k) * ((1 - threshold) ** (n_games - k))
        for k in range(wins, n_games + 1)
    )
    return tail <= alpha


def _seat_gap_z_test(wins_p0, n_p0, wins_p1, n_p1):
    """先手(p0)勝率と後手(p1)勝率の差が統計的に有意かを、2群比率の差の
    z検定（正規近似・プール標準誤差、両側）で判定する（2026-09-10(3)追加、
    3.24節）。scipy等の追加依存を避けるため、正規分布のCDFをmath.erfcだけで
    計算する（_promotion_is_significantがmath.combだけで二項検定を実装している
    のと同じ方針）。

    n_p0またはn_p1が0の場合、あるいは正規近似の精度が低いほど小さいサンプル
    （目安として片側30局未満）では結果の信頼性が落ちる点に注意。

    戻り値: (z, p_value_two_sided)。n_p0またはn_p1が0なら(None, None)。
    """
    if n_p0 == 0 or n_p1 == 0:
        return None, None
    p0 = wins_p0 / n_p0
    p1 = wins_p1 / n_p1
    p_pool = (wins_p0 + wins_p1) / (n_p0 + n_p1)
    se = math.sqrt(p_pool * (1 - p_pool) * (1 / n_p0 + 1 / n_p1))
    if se == 0:
        return 0.0, 1.0
    z = (p0 - p1) / se
    p_value = math.erfc(abs(z) / math.sqrt(2))  # 標準正規分布の両側p値
    return z, p_value


def evaluate_vs_champion(executor_holder, candidate_weights, champion_weights, config_overrides,
                          n_games, seed_base, depth, candidate_k,
                          game_timeout=GAME_TIMEOUT_SECONDS_DEFAULT):
    """candidateとchampionを、先後を必ず交互（半々）にして対戦させ、
    candidateの勝率などを返す。

    先後を厳密に半々に固定するのは、「先後どちらを引いたか」による有利不利を
    候補の強さの評価に混入させないため。主指標のwin_rate・昇格判定は従来通り
    この「先後を相殺した」値のみを使う。

    2026-09-10(3)追加（3.24節。従来「先後の手番差そのものを検知したい場合は
    win_rate_as_p0とwin_rate_as_p1を突き合わせればよい」と書いていたが、実際には
    その2値がどこにも計算・記録されていなかった＝先後差を測る機能が存在しな
    かったため実装した）: 戻り値にwin_rate_as_p0/win_rate_as_p1（candidateが
    先手/後手だった試合だけに絞った勝率）と、両者の差が統計的に有意かどうかの
    両側z検定結果（seat_gap_z/seat_gap_p_value、_seat_gap_z_test参照）を追加した。
    これらはあくまで診断用の追加情報であり、score・昇格判定には一切影響しない。

    candidate_weights == champion_weights（同一重み同士のミラー戦）で呼び出せば、
    実力差が原理的に存在しないため、win_rate_as_p0とwin_rate_as_p1の差がそのまま
    「純粋な先後の手番有利不利」の推定値になる。--measure-first-move-advantage
    （main()参照）はこれを利用した診断専用モード。
    """
    args_list = []
    for i in range(n_games):
        candidate_is_p0 = (i % 2 == 0)
        args_list.append((candidate_weights, champion_weights, config_overrides,
                           seed_base + i, depth, candidate_k, candidate_is_p0))

    futures = [executor_holder.executor.submit(_run_one_asymmetric_game, a) for a in args_list]
    results = []
    timeout_count = 0
    for args, fut in zip(args_list, futures):
        try:
            results.append(fut.result(timeout=game_timeout))
        except FutureTimeoutError:
            timeout_count += 1
            seed = args[3]
            print(f"[警告] チャンピオン戦の1局が{game_timeout}秒でタイムアウトしました "
                  f"(seed={seed})。candidate側の負けとして扱います。", file=sys.stderr)
            results.append({"candidate_won": False, "draw": False, "reason": "timeout",
                             "rounds": CONFIG["turn_limit_per_player"],
                             "candidate_is_p0": args[6]})
    if timeout_count:
        print(f"[警告] チャンピオン戦は{n_games}局中{timeout_count}局がタイムアウトしました。"
              f"次のtrialへ進めるようexecutorを作り直します。", file=sys.stderr)
        executor_holder.recycle()

    total = len(results)
    wins = sum(1 for r in results if r["candidate_won"])
    draws = sum(1 for r in results if r["draw"])
    losses = total - wins - draws

    # 2026-09-10(3)追加（3.24節）: 先後別の内訳。上のwin_rate等（昇格判定・スコア
    # に使う主指標）には一切影響しない、診断専用の追加集計。
    p0_results = [r for r in results if r["candidate_is_p0"]]
    p1_results = [r for r in results if not r["candidate_is_p0"]]
    n_p0, n_p1 = len(p0_results), len(p1_results)
    wins_p0 = sum(1 for r in p0_results if r["candidate_won"])
    wins_p1 = sum(1 for r in p1_results if r["candidate_won"])
    seat_gap_z, seat_gap_p_value = _seat_gap_z_test(wins_p0, n_p0, wins_p1, n_p1)

    return {
        "win_rate": wins / total if total else 0.0,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "n_games": total,
        "win_rate_as_p0": (wins_p0 / n_p0) if n_p0 else None,
        "win_rate_as_p1": (wins_p1 / n_p1) if n_p1 else None,
        "n_games_as_p0": n_p0,
        "n_games_as_p1": n_p1,
        "seat_gap_z": seat_gap_z,
        "seat_gap_p_value": seat_gap_p_value,
    }


def evaluate_vs_diversity_panel(executor_holder, candidate_weights, config_overrides,
                                 games_per_archetype, seed_base, depth, candidate_k,
                                 game_timeout=GAME_TIMEOUT_SECONDS_DEFAULT):
    """candidateを、現チャンピオン1体だけでなくDIVERSITY_PANEL_ARCHETYPES
    （turtle_vp/rush_kill/economy_boom/vp_spot_hunter）全員と対戦させる。

    2026-09-05追加の背景: 「直前の1体のchampionにだけ勝ち越した」という条件は、
    非推移的（じゃんけん的）なハードカウンター――特定の1つの重み設定の弱点だけを
    突く、汎用的な強さを伴わない構成――を弾けない。実際に search_balance_weights_
    260905_v1 のtrial 10（100戦100勝でgeneration4に昇格）は、二項検定こそ
    通ったが、わずか1trial後のgeneration5（勝率57%という平凡な数字）に
    あっさり陥落しており、事後的に見て「gen3という1点だけに刺さる手」だった
    疑いが強い。meta_diversity_check.pyが元々もっていた「複数アーキタイプへの
    総当たり」という考え方を、tune_balance_search.pyの昇格判定そのものに
    追加のゲートとして組み込む。

    呼び出しコストを抑えるため、この関数は「既にchampion単体には勝ち越した
    （昇格しそうな）candidate」に対してのみ呼び出す想定（make_objective参照）。
    evaluate_vs_champion()をそのまま再利用し、各アーキタイプの解決済み重みを
    「championの代わり」として渡すだけで済む（対戦ロジックは完全に共通）。
    """
    per_archetype = {}
    for i, (name, overrides) in enumerate(sorted(DIVERSITY_PANEL_ARCHETYPES.items())):
        archetype_weights = _weights_module.resolve_weights(overrides)
        result = evaluate_vs_champion(
            executor_holder, candidate_weights, archetype_weights, config_overrides,
            games_per_archetype, seed_base + i * games_per_archetype, depth, candidate_k,
            game_timeout=game_timeout,
        )
        per_archetype[name] = result

    win_rates = [r["win_rate"] for r in per_archetype.values()]
    avg_win_rate = sum(win_rates) / len(win_rates) if win_rates else 0.0
    min_name, min_result = min(per_archetype.items(), key=lambda kv: kv[1]["win_rate"]) \
        if per_archetype else (None, {"win_rate": 0.0})
    return {
        "per_archetype": per_archetype,
        "avg_win_rate": avg_win_rate,
        "min_win_rate": min_result["win_rate"],
        "min_win_rate_archetype": min_name,
    }


def load_champion(path):
    """champion_weights.jsonを読み込む。存在しなければNoneを返す
    （呼び出し側でブートストラップ処理をする）。"""
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data


def save_champion(path, weights, source, generation, extra=None):
    """champion_weights.jsonを保存する。

    weights: weights.resolve_weights()と同じ形状（完全なHEURISTIC_WEIGHTS）。
    source: 昇格の由来を人間が追えるようにする文字列
        （例: "bootstrap"、"trial_17_vs_gen3"）。
    generation: 何代目のchampionかを表す通し番号（昇格のたびに+1）。
    """
    data = {
        "weights": weights,
        "source": source,
        "generation": generation,
    }
    if extra:
        data.update(extra)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def save_champion_history_entry(history_dir, generation, weights, config_overrides, source,
                                 trial_number=None, metrics=None, champion_generation_faced=None,
                                 study_name=None):
    """(案A) 昇格のたびに、その世代のchampionを個別ファイルとして
    history_dir/gen_XXXX.json に保存する。

    champion_weights.json は「最新championのみ」を上書き保存する設計のため、
    以前の世代の重みは実行が進むと失われてしまう（過去の全trialパラメータは
    Optunaのstudy dbに残るが、resolve_weights()相当の再構成処理が必要で、かつ
    config_overrides（駒コスト等）は元々どこにも保存されていなかった）。

    このファイルは「歴代championを並べて対戦させる／スコア推移を確認する」
    ための別スクリプト（champion_battle.py）が読み込む前提のフォーマット。
    generationごとに1ファイルなので、後から追記していっても既存世代を
    壊さない（champion_weights.jsonのような上書き事故が起きない）。

    weights: weights.resolve_weights()と同じ形状の完全な重み辞書。
    config_overrides: このchampionが昇格した時点でtrialに適用されていた
        駒パラメータの上書き一覧（[(piece_kind, stat, value), ...]）。
        --tune weights のみの運用では常に空リストになる。
    metrics: 昇格時点の診断指標一式（champion_weights.jsonのextraと同じもの）。
        歴代のスコア推移を確認する用途にはこれが本体。
    champion_generation_faced: このchampionが「前championの何世代目」を
        破って昇格したか（通常はgeneration-1と一致するはずだが、念のため
        記録しておく）。
    study_name: 2026-09-05(3)追加。この昇格を生んだOptuna studyの名前。
        champion_history_dirは複数のstudy実行を跨いで使い回されることが
        あり（championを引き継いで別studyとして再検証を始める運用）、
        generation番号だけをキーにanalyze_tuning_run.pyがレポートを作ると、
        「今回のstudyでは起きていない、別（多くは過去の）studyの昇格記録」が
        レポートに混入し、分析を誤らせる事故が実際に起きた
        （search_balance_weights_260905_v1のtrial10・100戦100勝の記録が
        search_balance_weights_260905_v2のレポートに混入していた件）。
        analyze_tuning_run.py側はこのフィールドで対象studyの実行のみに
        絞り込む。Noneの場合（この変更より前に保存された旧形式のファイル）は
        安全側に倒し、レポートからは除外した上でその旨を明示する。
    """
    os.makedirs(history_dir, exist_ok=True)
    entry = {
        "generation": generation,
        "weights": weights,
        "config_overrides": config_overrides or [],
        "source": source,
        "trial_number": trial_number,
        "metrics": metrics,
        "champion_generation_faced": champion_generation_faced,
        "study_name": study_name,
        "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
    }
    path = os.path.join(history_dir, f"gen_{generation:04d}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(entry, f, indent=2, ensure_ascii=False)
    return path


# ------------------------------------------------------------
# バランススコアの計算（tune_balance.pyと同一。SearchBotでも意味は変わらない）
# ------------------------------------------------------------
class ExecutorHolder:
    """2026-08-12追加: future.result(timeout=...)を諦めても、ProcessPoolExecutorは
    実行中のワーカープロセスを個別にkillする手段を持たない。そのため1局が
    タイムアウトすると、そのワーカーはバックグラウンドで走り続けたまま次の
    タスクを受け付けられなくなる（実測: 全ワーカーがタイムアウトすると、
    次のタスク投入がさらに5秒待っても返ってこないことを確認済み＝
    「タイムアウトを設けたのにtrialをまたいでも詰まる」状態になる）。
    タイムアウトが起きたら executor を作り直し、新しいワーカーで即座に
    次のtrialへ進めるようにする。古いexecutorはwait=Falseでバックグラウンドへ
    shutdownし、中の暴走タスクは自然完了（turn_limit_per_playerで必ず終わる）
    を待って自然消滅させる（プロセス自体は独立したOSプロセスなので、
    poolの管理から外れても実害はない）。"""

    def __init__(self, max_workers):
        self.max_workers = max_workers
        self.executor = ProcessPoolExecutor(max_workers=max_workers)

    def recycle(self):
        old = self.executor
        old.shutdown(wait=False, cancel_futures=True)
        self.executor = ProcessPoolExecutor(max_workers=self.max_workers)

    def shutdown(self, wait=True):
        self.executor.shutdown(wait=wait)


def _production_diversity_ratio(produced_total, n_kinds=N_PRODUCIBLE_KINDS):
    """生産された駒種の分布をShannonエントロピーで評価し、0(1種類に完全集中)〜
    1(全n_kinds種が均等)に正規化して返す（2026-08-11: 旧concentration_ratioの
    「最頻出1種の割合」だけを見る閾値判定から変更）。

    旧実装 `max(0.0, concentration_ratio - 0.5) * 100` は、①最頻出1種しか見ておらず
    残りの分布形状を無視する、②50%以下でありさえすれば一律ゼロ点になり
    「40%と20%の違い」を区別できない、という2つの粗さがあった。
    エントロピーはn_kinds種すべての分布を連続的に評価するため、この2点を同時に解消する。

    「均等（各1/n_kinds）が理論上最良」という前提はあくまで縮退戦略（1〜2種類しか
    作らない）を検出するためのヒューリスティックであり、駒の役割差から見て本当に
    均等が最適とは限らない点には注意（9章の継続監視事項に追記推奨）。
    """
    total = sum(produced_total.values())
    if total == 0 or n_kinds <= 1:
        return 0.0
    probs = [c / total for c in produced_total.values() if c > 0]
    entropy = -sum(p * math.log(p) for p in probs)
    max_entropy = math.log(n_kinds)
    return entropy / max_entropy if max_entropy > 0 else 0.0


def evaluate_matchup(executor_holder, weights_overrides, config_overrides, n_games, seed_base, depth, candidate_k,
                      game_timeout=GAME_TIMEOUT_SECONDS_DEFAULT):
    args_list = [
        (weights_overrides, config_overrides, seed_base + i, depth, candidate_k)
        for i in range(n_games)
    ]
    # 2026-08-12修正: executor.map（全局の完了を無条件に待つ）から、1局ごとに
    # future.result(timeout=...)で待つ形に変更。
    futures = [executor_holder.executor.submit(_run_one_game, a) for a in args_list]
    results = []
    timeout_count = 0
    for args, fut in zip(args_list, futures):
        try:
            results.append(fut.result(timeout=game_timeout))
        except FutureTimeoutError:
            timeout_count += 1
            seed = args[2]
            print(f"[警告] 1局が{game_timeout}秒でタイムアウトしました (seed={seed})。"
                  f"このtrialのパラメータ組み合わせが重すぎる可能性があります。"
                  f"退化的な結果として扱い進行します。", file=sys.stderr)
            results.append({
                "winner": None, "reason": "timeout", "rounds": CONFIG["turn_limit_per_player"],
                "produced": {}, "kills": 0, "first_move_is_pass": False, "bad_engagements": 0,
                # rp_spot_neglectのタイムアウト時既定値は0.0（tiebreak_penalty側で
                # 既に罰しているため、ここで二重に不利側の値を入れない）。
                "rp_spot_neglect": 0.0, "engagement_abandonment": 0,
            })
    if timeout_count:
        print(f"[警告] このtrialは{n_games}局中{timeout_count}局がタイムアウトしました。"
              f"次のtrialへ進めるようexecutorを作り直します。", file=sys.stderr)
        executor_holder.recycle()

    total = len(results)
    win_counts = Counter(r["winner"] for r in results)
    reason_counts = Counter(r["reason"] for r in results)
    produced_total = Counter()
    for r in results:
        produced_total.update(r["produced"])
    total_produced = sum(produced_total.values())
    total_kills = sum(r["kills"] for r in results)
    avg_rounds = sum(r["rounds"] for r in results) / total

    win0_rate = win_counts.get(0, 0) / total
    win1_rate = win_counts.get(1, 0) / total

    # --- 1. 勝率バランス ---
    # ミラー自己対戦のwin0_rateは「AIの強さ」を反映しない（両者同じ重みのため常に50%付近）。
    # 先後の手番差検出用としてmetricsには残すがスコアからは外す
    # （強さの代理指標はevaluate_vs_championによるchampion_termに置き換え済み）。
    win_balance_penalty = 0.0
    win_balance_info = abs(win0_rate - 0.5)  # 参考値としてmetricsに残す

    # --- 2. 退化的タイブレーク比率（連続、変更なし） ---
    tiebreak_ratio = sum(reason_counts.get(r, 0) for r in DEGENERATE_TIEBREAK_REASONS) / total
    tiebreak_penalty = tiebreak_ratio * 150

    # --- 3. 生産の偏り（2026-08-11: 閾値判定→エントロピーによる連続指標に変更） ---
    diversity_ratio = _production_diversity_ratio(produced_total, N_PRODUCIBLE_KINDS)
    concentration_penalty = (1.0 - diversity_ratio) * 100

    # --- 4. 戦闘頻度（現行のまま。膠着・無風対局の検出） ---
    kills_per_game = total_kills / total
    combat_penalty = max(0.0, 2.0 - kills_per_game) * 20

    # --- 5. 初手パスという致命的な縮退戦略の検出 ---
    # 「先手が初手からpassし後手を誘ってからRPスポットを確保する」縮退戦略を、
    # 特定座標を決め打ちしない中立な指標（初手pass頻度）で検出する。
    first_pass_count = sum(1 for r in results if r["first_move_is_pass"])
    first_pass_rate = first_pass_count / total
    opening_penalty = first_pass_rate * 120  # 値は初期値。致命的欠陥として重めに設定。要チューニング。

    # --- 6. 自殺的接近（相性で明確に不利な接近）の検出 ---
    # 1試合あたりの平均発生回数で連続減点。incoming_damage/attackの対称性ドリフト
    # 再発の回帰検知用でもある。
    total_bad_engagements = sum(r["bad_engagements"] for r in results)
    bad_engagements_per_game = total_bad_engagements / total
    bad_engagement_penalty = bad_engagements_per_game * 30  # 2026-08-15: 15→30に変更（ユーザー実測での調整）。

    # --- 6.5 序盤のRPスポット無視（RP軽視）の検出 ---
    # 「序盤からRPスポットを無視し本拠を攻め続ける」実戦バグへの対応。占有率で測る
    # （勝敗を経由しないため、両陣営が同じ盲点を共有するケースも検出できる）。要チューニング。
    avg_rp_spot_neglect = sum(r["rp_spot_neglect"] for r in results) / total
    # scale=140（60から引き上げ）。他のペナルティ項を締めるとOptunaが「経済無視を
    # してでも回避する方が得」という抜け道を見つける現象が実測されたため、
    # rp_spot_neglect_penalty自体を支配的な強さにして構造的に防ぐ方針にした。
    rp_spot_neglect_penalty = avg_rp_spot_neglect * 140.0

    # --- 6.7 決着ラウンドの異常な早期化（両者本拠隣接特攻）の検出 ---
    # avg_rounds（平均値）は二極化（一部が極端に短く残りが長い分布）を検出できないため、
    # 「異常に早く決着したゲームの比率」を別途測る。閾値12はROUND_LENGTH_TARGET_MIN(18)より厳しめ。
    PREMATURE_DECISIVE_ROUND_THRESHOLD = 12
    premature_decisive_rate = sum(1 for r in results if r["rounds"] < PREMATURE_DECISIVE_ROUND_THRESHOLD) / total
    premature_decisive_penalty = premature_decisive_rate * 60.0

    # --- 6.6 有利対面からの離脱（無駄な配置・移動）の検出 ---
    # _count_bad_engagementsと対になる指標。誤検出リスクがやや高いためスケールは
    # bad_engagement_penalty(15)よりやや軽い10に設定。要チューニング。
    total_engagement_abandonment = sum(r["engagement_abandonment"] for r in results)
    engagement_abandonment_per_game = total_engagement_abandonment / total
    engagement_abandonment_penalty = engagement_abandonment_per_game * 20.0  # 2026-08-15: 10→20に変更（ユーザー実測での調整）。

    # --- 7. 決着ラウンドが極端に短い/長いことへのペナルティ ---
    # avg_roundsは記録のみでscoreに未反映だったため追加。極端に早い決着=本拠特攻の
    # 支配戦略化、極端に遅い=膠着を示唆する。target範囲は暫定値（rounds=22等を目安）。
    ROUND_LENGTH_TARGET_MIN = 18
    ROUND_LENGTH_TARGET_MAX = 28
    ROUND_LENGTH_PENALTY_SCALE = 3.0  # 値は初期値。win_balance_penalty(最大100)等と
                                       # 比べて過大にならないよう控えめに設定した。要チューニング。
    round_length_penalty = (
        max(0.0, ROUND_LENGTH_TARGET_MIN - avg_rounds)
        + max(0.0, avg_rounds - ROUND_LENGTH_TARGET_MAX)
    ) * ROUND_LENGTH_PENALTY_SCALE

    score = 100.0 - win_balance_penalty - tiebreak_penalty - concentration_penalty \
        - combat_penalty - opening_penalty - bad_engagement_penalty - round_length_penalty \
        - rp_spot_neglect_penalty - engagement_abandonment_penalty - premature_decisive_penalty

    metrics = {
        "win0_rate": win0_rate,
        "win1_rate": win1_rate,
        "win_balance_info": win_balance_info,  # 参考値。スコアには含めない（2026-08-16）
        "tiebreak_ratio": tiebreak_ratio,
        "diversity_ratio": diversity_ratio,
        "most_common_kind": produced_total.most_common(1)[0][0] if produced_total else None,
        "kills_per_game": kills_per_game,
        "avg_rounds": avg_rounds,
        "round_length_penalty": round_length_penalty,
        "first_pass_rate": first_pass_rate,
        "bad_engagements_per_game": bad_engagements_per_game,
        "avg_rp_spot_neglect": avg_rp_spot_neglect,
        "rp_spot_neglect_penalty": rp_spot_neglect_penalty,
        "engagement_abandonment_per_game": engagement_abandonment_per_game,
        "engagement_abandonment_penalty": engagement_abandonment_penalty,
        "premature_decisive_rate": premature_decisive_rate,
        "premature_decisive_penalty": premature_decisive_penalty,
        # 2026-09-05(3)追加: championモード時はこの後に呼び出し元(make_objective)が
        # champion_termを score へ加算するため、そのままだとtrial.user_attrsや
        # champion_history上には「champion_term込みのscore」しか残らず、
        # 「championにたまたま強かっただけで、ゲームバランス自体(base_score)は
        # 平凡だった」ケースを後から見分けられない（引き継ぎ資料2節で指摘済みの
        # 問題）。champion_term加算前のこの時点のscore（=100.0からの各種penaltyの
        # 引き算のみ）をbase_scoreとしてそのまま記録しておく。
        "base_score": score,
    }
    return score, metrics


# ------------------------------------------------------------
# 段階的チューニングのための仕組み（tune_balance.pyと同一構造）
# ------------------------------------------------------------
import weights as _weights_module  # noqa: E402  attack/base_pressureのデフォルト値参照・resolve_weights用
# 2026-08-25: HEURISTIC_WEIGHTSの実体はweights.pyに分離した（経緯はweights.py
# 冒頭のコメント参照）。このスクリプト内で「config.」経由だった重み関連の
# 呼び出しは全て「_weights_module.」経由に変更している。

# 2026-09-05追加: 昇格判定の「多様性パネル」チェック用（後述の
# evaluate_vs_diversity_panel/_run_diversity_panel_check参照）。
# meta_diversity_check.pyのARCHETYPES（手動で極端化した戦略アーキタイプ定義）を
# そのまま流用する。「balanced」は「weights.pyの現在のベースラインそのもの」
# （固定された1つのスタイルではなく実行時点の値に追従する）ため、パネルの
# 固定リファレンスとしては不適切と判断し除外する。
# 二重管理を避けるため、ここでも必ずimportで参照する
# （WEIGHT_SEARCH_SPACEへの追加漏れが0.86節で一度実際に起きているのと同じ教訓）。
try:
    from meta_diversity_check import ARCHETYPES as _DIVERSITY_ARCHETYPES  # noqa: E402
except ImportError as e:
    print(f"meta_diversity_check.pyのimportに失敗しました（同じフォルダに置いてください）: {e}",
          file=sys.stderr)
    raise
DIVERSITY_PANEL_ARCHETYPES = {
    name: overrides for name, overrides in _DIVERSITY_ARCHETYPES.items() if name != "balanced"
}


def weight_param_names():
    return set(WEIGHT_SEARCH_SPACE.keys())


def piece_param_names():
    return {f"{kind}_{stat}" for (kind, stat) in CONFIG_PIECE_SEARCH_SPACE.keys()}


def _resolve_weight_overrides(raw_overrides):
    """WEIGHT_SEARCH_SPACEのキーをHEURISTIC_WEIGHTSの実キーへ変換する。

    2026-08-11追加: "incoming_damage_ratio" は HEURISTIC_WEIGHTS に存在しない
    合成パラメータで、実際の "incoming_damage" = attack * ratio として計算する。
    "attack" は WEIGHT_SEARCH_SPACE で探索対象に含まれているため、通常はこのtrialが
    提案した値を使う（overrides.get経由）。"attack"が探索対象から外された場合でも
    HEURISTIC_WEIGHTSのデフォルト値にフォールバックする。

    2026-08-14追加: "base_defense_ratio" も同じパターンで、実際の "base_defense"
    = base_pressure * ratio として計算する（config.pyのbase_defenseコメント、
    本ファイル冒頭のWEIGHT_SEARCH_SPACEのコメント参照）。

    2026-08-27追加: 符号統一方針により、incoming_damage/base_defenseは
    ここでも常に0以上の大きさとして計算する（以前は-1を掛けていたが、
    エンジンへ渡す直前のweights.to_engine_signed()で符号を反転するように
    変更したため、この関数の役割は純粋に「比率→絶対値の解決」だけになった）。
    """
    overrides = dict(raw_overrides)
    ratio = overrides.pop("incoming_damage_ratio", None)
    if ratio is not None:
        attack_value = overrides.get("attack", _weights_module.HEURISTIC_WEIGHTS["attack"])
        overrides["incoming_damage"] = attack_value * ratio

    base_defense_ratio = overrides.pop("base_defense_ratio", None)
    if base_defense_ratio is not None:
        base_pressure_value = overrides.get("base_pressure", _weights_module.HEURISTIC_WEIGHTS["base_pressure"])
        overrides["base_defense"] = base_pressure_value * base_defense_ratio

    return overrides


def _format_weight_assignments_for_source(weights_dict):
    """weights_dict（HEURISTIC_WEIGHTSキー→値）を、weights.pyの
    ソースコードに実際に貼り付けて効果のある形の代入文に変換する。

    2026-08-31修正: 従来は無条件に `HEURISTIC_WEIGHTS["key"] = value` という
    代入文を出力していたが、これは weights.py の
    `_BASELINE_WEIGHTS = copy.deepcopy(HEURISTIC_WEIGHTS)` キャプチャより後ろに
    貼り付けても resolve_weights()/apply_weight_overrides()/reset_weights() の
    基準点には一切反映されない（HEURISTIC_WEIGHTSというモジュールレベル変数への
    直接代入が、既にキャプチャ済みの_BASELINE_WEIGHTSに波及しないだけの話。
    weights.py側の注意書きにも明記されている）。よって「人力でデフォルト値を
    恒久的に書き換えたい」という本来の用途に対しては実質的に無意味な出力だった。
    正しくは:
      - BASE_WEIGHT_KEYS（初期解放24種）に属するキー → BASE_WEIGHTS["key"]=value
      - TIER_WEIGHT_KEYS（Tier1-4の56種）に属するキー →
        WEIGHT_TIERS内の該当エントリの "default" フィールドを書き換える
        （フラットな辞書ではなくlist[dict]のため単純な代入文にできない。
        該当エントリを人間が見つけやすいようchampion_index付きコメントで示す）
      - ENGINE_ONLY_WEIGHTS（jitter等） → プレイヤー編集対象外のため対象外。
        値に差分があれば警告コメントを出す。
    なお、通常の運用では champion_weights.json 経由の自動同期
    （_sync_champion_weights_from_file、次回import時）で十分であり、本関数の
    出力を貼り付ける必要があるのは「champion_weights.jsonを持たない新しい
    配布環境向けにデフォルト値そのものを恒久的に更新したい」場合のみ。
    """
    base_keys = set(_weights_module.BASE_WEIGHT_KEYS)
    # Tier1-4は57種あるが、貼り付け出力の対象はWEIGHT_SEARCH_SPACEに含まれる
    # ものだけに絞る（0.86節: 全57種を毎回出力すると未変更の値までノイズとして
    # 混ざり込むバグがあった）。
    tier_keys = set(_weights_module.TIER_WEIGHT_KEYS) & set(WEIGHT_SEARCH_SPACE.keys())
    engine_only_keys = set(_weights_module.ENGINE_ONLY_WEIGHTS)

    lines = []

    base_lines = []
    for key in sorted(weights_dict.keys()):
        if key not in base_keys:
            continue
        value = weights_dict[key]
        if isinstance(value, float):
            value = round(value, 2)
        base_lines.append(f'BASE_WEIGHTS["{key}"] = {value}')
    if base_lines:
        lines.append("# --- BASE_WEIGHTS（初期解放24種。この代入文はそのまま貼り付け可） ---")
        lines.extend(base_lines)

    tier_lines = []
    for key in sorted(weights_dict.keys()):
        if key not in tier_keys:
            continue
        value = weights_dict[key]
        if isinstance(value, float):
            value = round(value, 2)
        entry = _weights_module.WEIGHT_TIER_BY_KEY[key]
        tier_lines.append(
            f'# WEIGHT_TIERS: champion_index={entry["champion_index"]} '
            f'(tier={entry["tier"]}, key="{key}", label="{entry["label"]}") の '
            f'"default" を {value} に変更'
        )
    if tier_lines:
        lines.append("")
        lines.append("# --- WEIGHT_TIERS（Tier1-4の56種。list[dict]なので単純代入不可。"
                      "下記の該当エントリの\"default\"を書き換えること） ---")
        lines.extend(tier_lines)

    engine_only_diffs = []
    for key in sorted(weights_dict.keys()):
        if key not in engine_only_keys:
            continue
        value = weights_dict[key]
        default = _weights_module.ENGINE_ONLY_WEIGHTS[key]
        if value != default:
            engine_only_diffs.append(
                f'# 注意: "{key}" はENGINE_ONLY_WEIGHTS（プレイヤー編集対象外）です。'
                f'現在値={value} はデフォルト({default})と異なりますが、この出力では'
                f'反映していません。変更する場合はweights.py側のENGINE_ONLY_WEIGHTSを'
                f'直接編集してください。'
            )
    if engine_only_diffs:
        lines.append("")
        lines.extend(engine_only_diffs)

    return "\n".join(lines)


def format_best_params_for_paste(params, tune):
    """最良トライアルのparamsを、そのまま貼り付け可能な代入文の形にする
    （BASE_WEIGHTS/WEIGHT_TIERS部分はweights.py、CONFIG["pieces"]部分はconfig.py向け）。

    2026-08-15追加: 「best_defenseの符号を手で転記して間違える」といった事故が
    実際に発生したため、手作業での丸め・比率キーの解決・転記を一切不要にする
    ために新設。やること:
      1. WEIGHT_SEARCH_SPACEの比率キー（*_ratio）を_resolve_weight_overrides()で
         実際のHEURISTIC_WEIGHTSキーへ解決する（符号や計算式を人間が再現しない）。
      2. 小数第2位で四捨五入する。
      3. 実際にweights.pyの基準点(_BASELINE_WEIGHTS)へ反映される形
         （BASE_WEIGHTS["key"]=value / WEIGHT_TIERSの該当エントリ更新）で出力する
         （2026-08-31修正: 従来のHEURISTIC_WEIGHTS["key"]=value形式は
         _BASELINE_WEIGHTSキャプチャより後に貼り付けても無効だったため変更）。
      4. CONFIG_PIECE_SEARCH_SPACE側のパラメータ（--tune pieces/both時）も同様に
         CONFIG["pieces"][駒種]["stat"] = value の形で出力する。
    """
    weight_names = weight_param_names()
    piece_names = piece_param_names()

    raw_weights = {k: v for k, v in params.items() if k in weight_names}
    resolved_weights = _resolve_weight_overrides(raw_weights)

    lines = []
    if resolved_weights:
        lines.append(_format_weight_assignments_for_source(resolved_weights))

    piece_items = {k: v for k, v in params.items() if k in piece_names}
    if piece_items:
        lines.append("# --- CONFIG[\"pieces\"] ---")
        for param_name in sorted(piece_items.keys()):
            # param_name は "{kind}_{stat}" 形式（例: "歩兵_cost", "歩兵_produce_cost"）。
            # 2026-09-06修正: statの方に"produce_cost"のようにアンダースコアを含む
            # ものがあるため、最後の"_"で分割するrsplitだと"歩兵_produce_cost"が
            # kind="歩兵_produce", stat="cost"に誤分割されていた（発行される
            # 貼り付け用コードがCONFIG["pieces"]["歩兵_produce"]["cost"]という
            # 存在しないキーになるバグ）。駒種名(kind)自体にアンダースコアが
            # 含まれないことを利用し、最初の"_"で分割する。
            kind, stat = param_name.split("_", 1)
            value = piece_items[param_name]
            if isinstance(value, float):
                value = round(value, 2)
            lines.append(f'CONFIG["pieces"]["{kind}"]["{stat}"] = {value}')

    return "\n".join(lines)


def format_full_weights_for_paste(weights):
    """format_best_params_for_paste()と違い、既に解決済みの完全なHEURISTIC_WEIGHTS
    形状の辞書（weights.resolve_weights()やchampion_weights.jsonの"weights"）を
    そのまま貼り付け用の代入文にする。*_ratioキーの解決は不要（既に解決済みのため）。
    2026-08-16追加: これをformat_best_params_for_pasteに通すと、"base_defense_ratio"
    等ではなく解決後の"base_defense"というキー名になっているために
    weight_param_names()（探索空間側のキー集合）に一致せずフィルタで落ちてしまう
    （base_defense/incoming_damageが出力から消える）バグになるため、別関数にした。
    2026-08-31修正: 出力形式をBASE_WEIGHTS/WEIGHT_TIERS基準に変更（理由は
    _format_weight_assignments_for_source()のdocstring参照）。
    """
    return _format_weight_assignments_for_source(weights)


def make_objective(executor_holder, n_games, tune, seed_params, depth, candidate_k,
                    game_timeout=GAME_TIMEOUT_SECONDS_DEFAULT,
                    champion_holder=None, champion_games=0, champion_bonus_scale=200.0,
                    champion_winrate_cap=0.85,
                    champion_history_dir=None,
                    promotion_margin=0.5, promotion_alpha=0.1, champion_path=None,
                    diversity_check_enabled=True, diversity_check_games=40,
                    diversity_min_avg_winrate=0.55, diversity_min_winrate=0.35,
                    confirmation_check_enabled=True, confirmation_games=None,
                    study_name=None):
    """champion_holder: {"weights": <完全なHEURISTIC_WEIGHTS形状の辞書>, "source": str,
    "generation": int} を保持する辞書（呼び出し元と共有し、その場で書き換える）。
    Noneまたはchampion_games<=0なら、従来通りチャンピオン評価を一切行わない

    champion_winrate_cap: 2026-09-05追加。champion_termに使うwin_rateを
    [1-cap, cap] の範囲へクリップしてからスコアへ反映する（昇格判定自体は
    クリップ前の生のwin_rateで行う。後述）。従来は(win_rate-0.5)*champion_bonus_scale
    が線形のまま青天井で、たとえばwin_rate=1.0なら+100点というボーナス単体が
    evaluate_matchupの基礎スコア（実測平均-134.57、標準偏差101.66）を丸ごと
    覆すほどの支配力を持ってしまい、「基礎的なバランス指標は平凡だが、たまたま
    現championにだけ圧勝した」trialが全trial中トップスコアになる事故があった
    （search_balance_weights_260905_v1のtrial 10、詳細は引き継ぎ資料参照）。
    cap=0.85なら寄与は最大±0.35*champion_bonus_scaleに制限される。

    diversity_check_*: 2026-09-05追加。「champion 1体にだけ勝ち越した」を
    昇格の唯一の条件にすると、非推移的（じゃんけん的）なハードカウンター
    ――汎用的な強さを伴わない、直前championの弱点だけを突いた構成――を
    弾けない（trial 10の事後検証で、そのchampionはわずか1trial後に平凡な
    勝率57%で陥落しており、この疑いを裏付けている）。champion単体には
    勝ち越した（＝昇格しそうな）candidateに対してのみ、追加コストとして
    DIVERSITY_PANEL_ARCHETYPES全員との対戦を行い、平均勝率・最低勝率の
    両方が閾値を満たさない限り最終的な昇格を見送る。
    diversity_check_enabled=Falseで従来通り（championにさえ勝てば昇格）に戻せる。
    （後方互換。--tune pieces単体でルールだけ探索する場合など）。

    confirmation_check_*: 2026-09-06(2)追加。search_balance_weights_260905_v3の
    事後分析で、「championが長く生き残った直後（前世代からの間隔が平均の3倍以上）に
    陥落する昇格trialのwin_rateは、ほぼ毎回、二項検定で有意となる最小値ぎりぎり
    （--champion-games 100 / --promotion-alpha 0.1のとき57%）に張り付く」という
    パターンが確認された（例: search_balance_weights_260905_v3のtrial94→世代8
    win_rate=0.57、trial139→世代11 win_rate=0.58。どちらも100局中の最小有意勝数
    ちょうど）。これは偶然ではなく、「有意水準を超える挑戦者が現れるまで何十trialも
    試行し続け、最初に基準を超えた時点で採用する」という運用（停止則
    /optional stopping）に起因する統計的な癖で、多数回試行した末の"初めての
    有意"は基準ぎりぎりに集中しやすい。さらにこの運用は挑戦者を何人も試す
    多重検定でもあるため、真に互角な相手同士でも約20trialに1回程度は
    見かけ上「有意な勝ち越し」が出てしまう（promotion_alpha=0.1・多重検定
    未補正の場合）。この「ぎりぎり昇格」がいったん採用されると、直後の
    数trialでそれをさらに上回る勝率（66%, 73%等）で連続して陥落させる
    "連鎖"が起きやすいことも確認された（前championの実力が過大評価だった
    ため、相対的に打ち破りやすい）。base_scoreはこの勝率上昇と連動しておらず
    （むしろ悪化することもある）、win_rate_vs_championの連続上昇だけを見て
    「探索が着実に強くなっている」と判断するのは危険という結論に至った。

    対策として、championに勝ち越し（beats_champion）かつ多様性パネルを通過した
    candidateに対してのみ、追加コストとして「独立した2回目のchampion戦
    （confirmation_games局、1回目とは異なるseed）」を行い、そちらでも同じ基準
    （promotion_margin・promotion_alphaによる二項検定）で有意な勝ち越しが
    再現されない限り昇格させない。真に互角な相手が2回連続で有意判定を
    偶然クリアする確率は概算でalpha^2（promotion_alpha=0.1なら約1%）まで
    下がるため、上記の「ぎりぎり昇格の連鎖」を強く抑制できる見込み。
    confirmation_games省略時（None）はchampion_gamesと同数を使う。
    confirmation_check_enabled=Falseで無効化できる（後方互換・高速反復用）。
    多様性チェックより先に（diversity_check_gamesより）実行する順序にしてあるのは、
    ここで弾かれるcandidateについては多様性パネルのコストを払わずに済むため
    （diversity_check_gamesは既定40*4アーキタイプ=160局とconfirmation_games
    既定100局より重いことが多く、安い方を先に倒すコスト最適化）。

    study_name: 2026-09-05(3)追加。promotion時にsave_champion()/
    save_champion_history_entry()へそのまま渡し、champion_weights.json・
    champion_history/gen_XXXX.jsonの両方にどのstudyの昇格かを記録する
    （analyze_tuning_run.py側が別studyの記録を誤って混ぜないための対応。
    詳細はsave_champion_history_entry()のdocstring参照）。

    2026-08-16追加: 「常に現在最もスコアが高い設定と対戦する」というチャンピオン制
    （勝ち抜き戦）評価。1トライアルごとに:
      1. このtrialの候補(candidate)を現在のchampionと先後交互でchampion_games局対戦させる。
      2. win_rateを主要なスコア項として反映する（champion_bonus_scaleで重み付け）。
      3. 有意に（_promotion_is_significant）勝ち越していれば、championをこのtrialの
         重みに更新する（以降のtrialは強くなった新championと戦うことになる）。
    study.optimize()はデフォルトで逐次実行（1trialずつ）なので、championの
    その場更新に競合状態は発生しない。
    """
    tune_weights = tune in ("weights", "both")
    tune_pieces = tune in ("pieces", "both")
    seed_params = seed_params or {}
    use_champion = champion_holder is not None and champion_games > 0

    def objective(trial):
        raw_weights_overrides = {}
        for name, (kind, lo, hi) in WEIGHT_SEARCH_SPACE.items():
            if tune_weights:
                if kind == "float":
                    # 2026-09-11追加: step=0.01を指定し、Optunaが提案する時点で
                    # 小数第2位までに量子化する。AIビルドに反映する際どのみ
                    # round(value, 2)される値（_format_weight_assignments_for_source
                    # 等）であり、小数第3位以下の精度はビルド上意味を持たない。
                    # 従来はstep未指定でdouble精度のまま提案されており、
                    # trial_paramsテーブルに無意味に長い浮動小数点の文字列表現
                    # （例: 0.234567891234567）がtrialごとに記録され続けることが
                    # balance_tuning_search.dbの肥大化（5MB超）の主因になっていた。
                    raw_weights_overrides[name] = trial.suggest_float(name, lo, hi, step=0.01)
                else:
                    raw_weights_overrides[name] = trial.suggest_int(name, lo, hi)
            elif name in seed_params:
                raw_weights_overrides[name] = seed_params[name]
        weights_overrides = _resolve_weight_overrides(raw_weights_overrides)

        config_overrides = []
        for (piece_kind, stat), (kind, lo, hi) in CONFIG_PIECE_SEARCH_SPACE.items():
            param_name = f"{piece_kind}_{stat}"
            if tune_pieces:
                if kind == "float":
                    # 2026-09-11追加: 上のWEIGHT_SEARCH_SPACE側と同じ理由で
                    # step=0.01を指定（DB肥大化対策。詳細はそちらのコメント参照）。
                    value = trial.suggest_float(param_name, lo, hi, step=0.01)
                else:
                    value = trial.suggest_int(param_name, lo, hi)
                config_overrides.append((piece_kind, stat, value))
            elif param_name in seed_params:
                config_overrides.append((piece_kind, stat, seed_params[param_name]))

        seed_base = trial.number * (n_games + champion_games)
        score, metrics = evaluate_matchup(
            executor_holder, weights_overrides, config_overrides, n_games, seed_base, depth, candidate_k,
            game_timeout=game_timeout,
        )

        if use_champion:
            candidate_full_weights = _weights_module.resolve_weights(weights_overrides)
            champ_result = evaluate_vs_champion(
                executor_holder, candidate_full_weights, champion_holder["weights"], config_overrides,
                champion_games, seed_base + n_games, depth, candidate_k, game_timeout=game_timeout,
            )
            # 2026-09-05追加: スコアに反映するwin_rateはchampion_winrate_capで
            # [1-cap, cap]にクリップする。昇格の可否判定（beats_champion）は
            # クリップ前の生のwin_rateで行う（スコアの見え方と昇格ロジックを
            # 独立させる。詳細はmake_objectiveのdocstring参照）。
            capped_win_rate = min(max(champ_result["win_rate"], 1.0 - champion_winrate_cap),
                                   champion_winrate_cap)
            champion_term = (capped_win_rate - 0.5) * champion_bonus_scale
            score += champion_term
            metrics["win_rate_vs_champion"] = champ_result["win_rate"]
            metrics["win_rate_vs_champion_capped"] = capped_win_rate
            metrics["champion_wins"] = champ_result["wins"]
            metrics["champion_draws"] = champ_result["draws"]
            metrics["champion_losses"] = champ_result["losses"]
            metrics["champion_generation_faced"] = champion_holder["generation"]
            # 2026-09-10(3)追加（3.24節）: 先後差の診断情報。score・昇格判定には
            # 使わない（evaluate_vs_championのdocstring参照）。analyze_tuning_run.py
            # 側で「先後差が有意なtrialの割合」等を集計する際の材料として残す。
            metrics["champion_win_rate_as_p0"] = champ_result["win_rate_as_p0"]
            metrics["champion_win_rate_as_p1"] = champ_result["win_rate_as_p1"]
            metrics["champion_seat_gap_p_value"] = champ_result["seat_gap_p_value"]

            beats_champion = (
                champ_result["win_rate"] > promotion_margin
                and _promotion_is_significant(champ_result["wins"], champ_result["n_games"],
                                               threshold=promotion_margin, alpha=promotion_alpha)
            )

            # 2026-09-06(2)追加: 「有意水準ぎりぎりの初回勝ち越し」をそのまま
            # 昇格させると、停止則（何十trialも試した末に初めて基準を超えた値は
            # 基準ぎりぎりに集中しやすい）と多重検定の影響で、実力の裏付けが
            # 薄いchampionが居座ってしまう（make_objectiveのdocstring
            # confirmation_check_*節参照）。championに勝ち越したcandidateだけ、
            # 独立した2回目のchampion戦（異なるseed）を追加で行い、そちらでも
            # 同じ基準で有意な勝ち越しが再現されない限り昇格させない。
            confirm_result = None
            confirmation_ok = True
            games_for_confirm = 0  # confirmation_check_enabled=Falseでも後段のseedオフセット計算で参照するため必ず定義しておく
            _confirm_games_requested = (
                confirmation_games if confirmation_games is not None else champion_games
            )
            # confirmation_games=0（または--disable-confirmation-check）は「確認戦自体を
            # 実行しない」という意味であり、evaluate_vs_championにn_games=0を渡すと
            # win_rate=0.0を返してしまい「確認戦で落ちた」と誤判定される（0敗0勝はwin_rate
            # 分母0のため0.0を返す仕様）ため、ここで明示的に区別する。
            if beats_champion and confirmation_check_enabled and _confirm_games_requested > 0:
                games_for_confirm = _confirm_games_requested
                confirm_result = evaluate_vs_champion(
                    executor_holder, candidate_full_weights, champion_holder["weights"], config_overrides,
                    games_for_confirm, seed_base + n_games + champion_games, depth, candidate_k,
                    game_timeout=game_timeout,
                )
                confirmation_ok = (
                    confirm_result["win_rate"] > promotion_margin
                    and _promotion_is_significant(confirm_result["wins"], confirm_result["n_games"],
                                                   threshold=promotion_margin, alpha=promotion_alpha)
                )
                metrics["win_rate_vs_champion_confirm"] = confirm_result["win_rate"]
                metrics["champion_wins_confirm"] = confirm_result["wins"]
                metrics["champion_losses_confirm"] = confirm_result["losses"]
                metrics["champion_draws_confirm"] = confirm_result["draws"]
                metrics["confirmation_ok"] = confirmation_ok
                # 2026-09-10(3)追加（3.24節）: 確認戦側も同様に先後差の診断情報を残す。
                metrics["champion_win_rate_as_p0_confirm"] = confirm_result["win_rate_as_p0"]
                metrics["champion_win_rate_as_p1_confirm"] = confirm_result["win_rate_as_p1"]
                metrics["champion_seat_gap_p_value_confirm"] = confirm_result["seat_gap_p_value"]
                if confirmation_ok:
                    print(f"[champion] trial {trial.number} は確認戦でも有意な勝ち越しを再現しました "
                          f"(1回目: {champ_result['wins']}勝{champ_result['losses']}敗"
                          f"{champ_result['draws']}分win_rate={champ_result['win_rate']:.2f}, "
                          f"確認: {confirm_result['wins']}勝{confirm_result['losses']}敗"
                          f"{confirm_result['draws']}分win_rate={confirm_result['win_rate']:.2f})。")
                else:
                    print(f"[champion] trial {trial.number} は世代{champion_holder['generation']}の"
                          f"championに1回目は有意に勝ち越しました"
                          f"（{champ_result['wins']}勝{champ_result['losses']}敗{champ_result['draws']}分"
                          f"win_rate={champ_result['win_rate']:.2f}）が、独立した確認戦では有意な"
                          f"勝ち越しを再現できなかったため"
                          f"（{confirm_result['wins']}勝{confirm_result['losses']}敗"
                          f"{confirm_result['draws']}分win_rate={confirm_result['win_rate']:.2f}）、"
                          f"昇格を見送ります。1回目の勝ち越しはノイズだった疑いがあります。")

            # 2026-09-05追加: championには勝ち越したcandidateだけ、追加コストとして
            # 多様性パネル（DIVERSITY_PANEL_ARCHETYPES）との対戦を行う。
            # 2026-09-06(2)変更: 確認戦（confirmation_check）にも通ったcandidateに
            # だけ実行する（confirmation_gamesの方がdiversity_check_gamesより
            # 軽いことが多いため、安い方のゲートを先に通す方がコスト効率が良い）。
            diversity_ok = True
            if beats_champion and confirmation_ok and diversity_check_enabled and diversity_check_games > 0:
                diversity_result = evaluate_vs_diversity_panel(
                    executor_holder, candidate_full_weights, config_overrides,
                    diversity_check_games, seed_base + n_games + champion_games + games_for_confirm,
                    depth, candidate_k, game_timeout=game_timeout,
                )
                metrics["diversity_avg_win_rate"] = diversity_result["avg_win_rate"]
                metrics["diversity_min_win_rate"] = diversity_result["min_win_rate"]
                metrics["diversity_min_win_rate_archetype"] = diversity_result["min_win_rate_archetype"]
                metrics["diversity_win_rates"] = {
                    name: r["win_rate"] for name, r in diversity_result["per_archetype"].items()
                }
                diversity_ok = (
                    diversity_result["avg_win_rate"] >= diversity_min_avg_winrate
                    and diversity_result["min_win_rate"] >= diversity_min_winrate
                )
                # 2026-09-06修正: 従来は多様性パネルの基準を満たさず見送りになった
                # 場合にしかこの結果をコンソールに出力していなかった。基準を満たして
                # 昇格した場合（=diversity_ok=True）は何も表示されないため、
                # search_balance_weights_260905_v3のtrial 1（初回championとして
                # いきなり昇格）のように、diversity_avg_win_rateが実際には計算されて
                # いるのにコンソールからは一切確認できないケースがあった。
                # 勝敗（基準を満たした/満たさなかった）に関わらず必ず表示するよう変更。
                breakdown = ", ".join(
                    f"{name}:{r['win_rate']:.2f}"
                    for name, r in sorted(diversity_result["per_archetype"].items())
                )
                print(f"[champion] trial {trial.number} の多様性パネル成績: "
                      f"平均={diversity_result['avg_win_rate']:.2f}（基準>={diversity_min_avg_winrate}）, "
                      f"最低={diversity_result['min_win_rate']:.2f}"
                      f"@{diversity_result['min_win_rate_archetype']}（基準>={diversity_min_winrate}）, "
                      f"内訳: {breakdown} -- "
                      f"{'基準を満たしました（昇格可）' if diversity_ok else '基準を満たしませんでした（昇格見送り）'}")
                if not diversity_ok:
                    print(f"[champion] trial {trial.number} は世代{champion_holder['generation']}の"
                          f"championには勝ち越しましたが（win_rate={champ_result['win_rate']:.2f}）、"
                          f"上記の多様性パネル基準を満たさなかったため昇格を見送ります。"
                          f"特定の相手だけに刺さるハードカウンターの疑いがあります。")

            # 2026-09-06(2)変更: confirmation_ok（独立2回目のchampion戦の有意判定）も
            # 昇格の必須条件に追加。
            promoted = beats_champion and confirmation_ok and diversity_ok
            metrics["beats_champion_only"] = beats_champion
            metrics["promoted_champion"] = promoted
            if promoted:
                champion_holder["weights"] = candidate_full_weights
                champion_holder["generation"] += 1
                champion_holder["source"] = f"trial_{trial.number}"
                # study.best_trialと最終championが別物になりうるため、昇格の瞬間の
                # metricsをchampion_holderにスナップショットし最終レポートで表示する。
                champion_holder["trial_number"] = trial.number
                champion_holder["metrics"] = dict(metrics)
                print(f"[champion] trial {trial.number} が世代{champion_holder['generation']}の"
                      f"新championに昇格しました "
                      f"(vs前champion: {champ_result['wins']}勝{champ_result['losses']}敗"
                      f"{champ_result['draws']}分, win_rate={champ_result['win_rate']:.2f})")
                if champion_path:
                    save_champion(champion_path, champion_holder["weights"],
                                   champion_holder["source"], champion_holder["generation"],
                                   extra={"trial_number": champion_holder["trial_number"],
                                          "metrics": champion_holder["metrics"],
                                          "study_name": study_name})
                if champion_history_dir:
                    # (案A) champion_weights.jsonは上書きなので、この世代のスナップショットを
                    # 別途generationごとのファイルとして残す（champion_battle.py等が読む）。
                    save_champion_history_entry(
                        champion_history_dir, champion_holder["generation"],
                        champion_holder["weights"], config_overrides, champion_holder["source"],
                        trial_number=champion_holder["trial_number"],
                        metrics=champion_holder["metrics"],
                        champion_generation_faced=metrics["champion_generation_faced"],
                        study_name=study_name,
                    )
            elif not beats_champion:
                # 2026-09-03追加: 世代交代しなかった（＝挑戦者が負け越した、または
                # 勝ち越しはしたが二項検定で有意と認められなかった）trialは、従来
                # 標準出力に一切戦績が出ないため、「champion戦をやっているはずなのに
                # 経過が全く見えない」という問い合わせを受けた。世代交代の有無に
                # 関わらず、毎trial同じ書式で戦績を出す。
                if champ_result["win_rate"] > promotion_margin:
                    reason = "勝ち越したが二項検定で有意と認められず昇格見送り"
                else:
                    reason = "負け越し、または勝敗互角"
                print(f"[champion] trial {trial.number} は世代{champion_holder['generation']}の"
                      f"championを破れませんでした "
                      f"(vs champion: {champ_result['wins']}勝{champ_result['losses']}敗"
                      f"{champ_result['draws']}分, win_rate={champ_result['win_rate']:.2f}, {reason})")
            # beats_champion=True かつ confirmation_ok=False、または
            # confirmation_ok=True かつ diversity_ok=False の場合は、それぞれ
            # 上の確認戦ブロック・diversity_ok算出ブロックで既に詳細な見送り
            # メッセージを出力済みなので、ここでは重複出力しない。

        for k, v in metrics.items():
            trial.set_user_attr(k, v)
        if "incoming_damage" in weights_overrides:
            trial.set_user_attr("incoming_damage_resolved", weights_overrides["incoming_damage"])
        if "base_defense" in weights_overrides:
            # 2026-08-14追加: base_defense_ratioから実際に算出されたbase_defenseの値を
            # 記録しておく（incoming_damage_resolvedと同じ理由。トライアル結果を見て
            # base_pressureとの比率が極端に乖離していないか事後確認できるようにする）。
            trial.set_user_attr("base_defense_resolved", weights_overrides["base_defense"])

        return score

    return objective


def _compute_resume_fingerprint(args, study_name):
    """--resume用のフィンガープリント（2026-09-10(3)追加、3.24節）。

    trialの生成条件に影響しうる設定一式をまとめてハッシュ化し、前回中断した
    セッションと今回の実行が「同じ設定の続き」とみなせるかを判定するために使う。
    一致しなければ安全側に倒して新しいセッション（--trials全件を実行）として
    扱う。逆方向の誤り（本当は同じ意図の実行なのに設定の微差で新規扱いに
    なってしまう）は、単に少し余計にtrialを実行するだけで実害が小さいため、
    多少神経質なフィンガープリントにしてある。

    WEIGHT_SEARCH_SPACE/CONFIG_PIECE_SEARCH_SPACE自体を書き換えて同じ
    study-name/storageを使い回す運用（3.22.1節・3.23.1節）も、フィンガー
    プリントに含めているためこの変更を自動検知し、新しいセッションとして扱う
    （動的サーチスペース変更を挟んだ場合はresumeの前提=「同じ設定の続き」が
    崩れるため、これは意図した挙動）。
    """
    payload = {
        "study_name": study_name,
        "storage": args.storage,
        "tune": args.tune,
        "games": args.games,
        "champion_games": args.champion_games,
        "champion_bonus_scale": args.champion_bonus_scale,
        "champion_winrate_cap": args.champion_winrate_cap,
        "promotion_margin": args.promotion_margin,
        "promotion_alpha": args.promotion_alpha,
        "disable_diversity_check": args.disable_diversity_check,
        "diversity_check_games": args.diversity_check_games,
        "diversity_min_avg_winrate": args.diversity_min_avg_winrate,
        "diversity_min_winrate": args.diversity_min_winrate,
        "disable_confirmation_check": args.disable_confirmation_check,
        "confirmation_games": args.confirmation_games,
        "depth": args.depth,
        "candidate_k": args.candidate_k,
        "game_timeout": args.game_timeout,
        "champion_file": args.champion_file,
        "champion_history_dir": args.champion_history_dir,
        "seed_from": args.seed_from,
        "enqueue_advance_ramp_combos": args.enqueue_advance_ramp_combos,
        "weight_search_space": {k: list(v) for k, v in sorted(WEIGHT_SEARCH_SPACE.items())},
        "config_piece_search_space": {
            f"{k[0]}_{k[1]}": list(v) for k, v in sorted(CONFIG_PIECE_SEARCH_SPACE.items())
        },
    }
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _run_measure_first_move_advantage(args):
    """--measure-first-move-advantage のエントリポイント（2026-09-10(3)追加、3.24節）。

    Optuna studyは一切作らず、1つの重み設定を自分自身とjitterありで
    --first-move-games局対戦させ、先手(p0)/後手(p1)としての勝率の差だけを
    計測して終了する。両者が全く同じ重みのため実力差は原理的に存在せず、
    観測される勝率差はそのまま先後の手番差の推定値になる（evaluate_vs_champion
    のdocstring・3.24節参照）。champion_weights.json等は一切書き換えない
    （load_championは読み込み専用に使うだけで、save_champion等は呼ばない）。

    従来の唯一の手番差シグナルだった、ミラー自己対戦(evaluate_matchup)の
    win_balance_infoは、(a) jitterなし（_run_one_gameはSearchBotへrngを渡さない
    ため決定論的）で、(b) スコアからは除外された参考値に過ぎず、(c) 一度も
    まとまった形でレポートされていなかった（このハンドオフにも先後差の
    測定結果は一度も記載がない）。このコマンドは、tune_balance_search.pyが
    実際の探索で使っているのと同じ評価経路（jitterあり、champion戦と同じ
    depth/candidate_k）で先後差を単独計測できるようにするために追加した。
    """
    weights_path = args.first_move_weights or args.champion_file or os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "champion_weights.json"
    )
    if not os.path.exists(weights_path):
        print(f"[first-move] {weights_path} が見つかりません。--first-move-weights か "
              f"--champion-file で対象の重みファイル（champion_weights.json形式、"
              f"または champion_history/gen_XXXX.json）を指定してください。", file=sys.stderr)
        sys.exit(1)

    with open(weights_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    raw_weights = data.get("weights", data)  # champion_weights.json/gen_XXXX.jsonは"weights"配下、生の辞書もそのまま受け付ける
    current_full_weights = _weights_module.resolve_weights(None)
    known_overrides = {k: v for k, v in raw_weights.items() if k in current_full_weights}
    missing = set(raw_weights) - set(current_full_weights)
    if missing:
        print(f"[first-move] {weights_path} に現在のHEURISTIC_WEIGHTSに無いキーが"
              f"{len(missing)}件ありました（無視します）: {sorted(missing)}", file=sys.stderr)
    resolved_weights = _weights_module.resolve_weights(known_overrides)

    n_games = args.first_move_games
    print(f"[first-move] {weights_path} を自分自身と{n_games}局対戦させ、先後差を計測します "
          f"(depth={args.depth}, candidate_k={args.candidate_k}, jitterあり)。")

    executor_holder = ExecutorHolder(max_workers=args.workers)
    try:
        result = evaluate_vs_champion(
            executor_holder, resolved_weights, resolved_weights, [],
            n_games, 0, args.depth, args.candidate_k, game_timeout=args.game_timeout,
        )
    finally:
        executor_holder.shutdown(wait=False)

    n_p0, n_p1 = result["n_games_as_p0"], result["n_games_as_p1"]
    wr_p0, wr_p1 = result["win_rate_as_p0"], result["win_rate_as_p1"]
    z, p_value = result["seat_gap_z"], result["seat_gap_p_value"]

    print("\n" + "=" * 60)
    print("先後差の計測結果（同一重み同士・jitterありの対戦）")
    print("=" * 60)
    print(f"先手(p0)としての勝率: "
          f"{wr_p0:.3f}（{n_p0}局中）" if n_p0 else "先手(p0)としての対局がありませんでした")
    print(f"後手(p1)としての勝率: "
          f"{wr_p1:.3f}（{n_p1}局中）" if n_p1 else "後手(p1)としての対局がありませんでした")
    if wr_p0 is not None and wr_p1 is not None:
        gap = wr_p0 - wr_p1
        print(f"差（先手 - 後手） = {gap:+.3f}")
        if p_value is not None:
            sig = "有意" if p_value < 0.05 else "非有意"
            print(f"両側z検定（正規近似）: z={z:.3f}, p値={p_value:.4g}（alpha=0.05で{sig}）")
            if p_value < 0.05:
                advantaged = "先手" if gap > 0 else "後手"
                print(f"[結論] {advantaged}有利の手番差が統計的に有意に検出されました。"
                      f"ゲームバランス上の対応（先後補正ルールの検討、または重み側で"
                      f"意図的に吸収させるか等）を検討することを推奨します。")
            else:
                print(f"[結論] 今回のサンプル（{n_games}局）では統計的に有意な手番差は"
                      f"検出されませんでした（{n_games}局程度では検出力が不足している"
                      f"可能性もあるため、疑わしい場合は--first-move-gamesを増やして"
                      f"再計測することを推奨する）。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tune", choices=["pieces", "weights", "both"], default="both")
    parser.add_argument("--seed-from", type=str, default=None,
                         help="前段階(HeuristicBot版でも可)のbest_params.jsonのパス")
    parser.add_argument("--enqueue-advance-ramp-combos", action="store_true",
                         help="2026-09-10追加（analyze_tuning_run_handoff.md 3.22.4節・3.23節）。"
                              "advance_ramp_roundsをWEIGHT_SEARCH_SPACEに追加した後もなお"
                              "advance/advance_ramp_roundsが両方ともレンジ下限付近に留まり続け"
                              "『rampを導入すればadvanceが解放される』という3.19節の仮説通りに"
                              "動いていない問題を切り分けるため、enqueue_trial()で"
                              "advance∈{3.0,4.0,5.0}×advance_ramp_rounds∈{10,15}の組み合わせ"
                              "（計6trial）を意図的にキューへ投入する。他の重みはTPEサンプラーに"
                              "任せる（advance/advance_ramp_roundsの2次元だけを固定）。"
                              "--seed-from（study.trials==0の時のみ発火）と異なり、稼働中の既存"
                              "studyに追加投入する運用を想定しているため、study.user_attrsに"
                              "記録する完了フラグでのみ重複投入をガードする（スクリプトを"
                              "何度再実行しても、この6trialが二重にキューへ積まれることはない。"
                              "同じ6trialをもう一度試したい場合はstudy側のuser_attrを手動で"
                              "削除するか新しいstudy-nameで実行すること）。"
                              "--tune weights/bothでadvance/advance_ramp_roundsがactive_names"
                              "に含まれていない場合は何もせずスキップする。")
    parser.add_argument("--trials", type=int, default=100, help="Optunaの試行回数")
    parser.add_argument("--games", type=int, default=N_GAMES_PER_TRIAL_DEFAULT,
                         help="1試行あたりの自己対戦数（SearchBotは重いため既定30）")
    parser.add_argument("--workers", type=int, default=os.cpu_count())
    parser.add_argument("--depth", type=int, default=2, help="SearchBotの探索深さ（0.95節によりdepth2が既定。旧0.77節のdepth3既定は運用コスト実測を踏まえて撤回。depth3で検証し直したい場合は明示的に--depth 3を指定すること）")
    parser.add_argument("--candidate-k", type=int, default=10, help="SearchBotの候補手プルーニング数")
    parser.add_argument("--game-timeout", type=float, default=GAME_TIMEOUT_SECONDS_DEFAULT,
                         help="1局あたりのタイムアウト秒数。超過した対局は退化的な結果として扱う"
                              "（既定240秒。2026-09-03改定: base_hp_panic_thresholdの発火ライン引き上げ後、"
                              "鏡合わせの対局で最悪68秒程度まで伸びることを実測したための安全マージン）")
    parser.add_argument("--study-name", type=str, default=None)
    parser.add_argument("--storage", type=str, default="sqlite:///balance_tuning_search.db")
    parser.add_argument("--champion-file", type=str, default=None,
                         help="チャンピオン制検証で使う重みファイルのパス。省略時は"
                              "スクリプトと同じフォルダの champion_weights.json。"
                              "--champion-games 0 を指定するとチャンピオン評価自体を"
                              "無効化できる（従来のミラー自己対戦のみに戻る）。")
    parser.add_argument("--champion-games", type=int, default=100,
                         help="1トライアルあたり、現チャンピオンと戦わせる対局数"
                              "（先後を交互にするため偶数推奨。既定100。2026-09-06(2)に30→100へ"
                              "引き上げ: 旧既定30・promotion-alpha=0.1では有意判定に必要な勝率が"
                              "20/30=約67%%というかなり粗い基準になってしまい、実運用ではほぼ常に"
                              "--champion-games 100を明示指定していた実態に合わせた。"
                              "0でチャンピオン評価を無効化）")
    parser.add_argument("--champion-bonus-scale", type=float, default=200.0,
                         help="(win_rate_vs_champion - 0.5) * この値 をスコアに加算する。"
                              "従来のwin_balance_penaltyと同スケール（既定200）")
    parser.add_argument("--champion-winrate-cap", type=float, default=0.85,
                         help="2026-09-05追加。スコアに反映するwin_rate_vs_championを"
                              "[1-cap, cap]にクリップしてから--champion-bonus-scaleを掛ける"
                              "（既定0.85）。昇格の可否判定自体はクリップ前の生のwin_rateで"
                              "行うため、この値を変えても昇格ロジックには影響しない。"
                              "1.0でクリップ無効（従来の青天井の挙動に戻る）。"
                              "trial 10（100戦100勝が基礎スコアを丸ごと覆った事故）を受けた対策。")
    parser.add_argument("--promotion-margin", type=float, default=0.5,
                         help="この勝率を統計的に有意に超えたらchampionを更新する（既定0.5=単純多数決）")
    parser.add_argument("--promotion-alpha", type=float, default=0.05,
                         help="昇格判定の二項検定における片側有意水準。2026-09-06(2)に0.1→0.05へ"
                              "引き下げた（search_balance_weights_260905_v3の事後分析で、championが"
                              "長期政権の末に陥落する際の勝率が毎回0.1水準ぎりぎり=champion-games100局中"
                              "57勝に張り付いており、停止則・多重検定の影響で誤昇格が疑われたため。"
                              "--champion-games 100なら必要勝率は57%%→59%%になる。従来通りの緩い基準に"
                              "戻したい場合は明示的に0.1を指定すること）")
    parser.add_argument("--champion-history-dir", type=str, default=None,
                         help="(案A) 世代ごとのchampionスナップショットを保存するディレクトリ。"
                              "省略時はスクリプトと同じフォルダの champion_history/。"
                              "champion_weights.json と違い、世代ごとに"
                              "gen_XXXX.json として個別保存されるため過去の世代が"
                              "消えない。champion_battle.py がこのディレクトリを読む。"
                              "--champion-games 0 の場合は保存されない。")
    parser.add_argument("--disable-diversity-check", action="store_true",
                         help="2026-09-05追加の多様性パネルチェック（後述）を無効化し、"
                              "従来通り「直前championにさえ勝ち越せば昇格」に戻す"
                              "（デバッグ・高速反復用のエスケープハッチ）。")
    parser.add_argument("--diversity-check-games", type=int, default=40,
                         help="多様性パネル（DIVERSITY_PANEL_ARCHETYPES＝"
                              "turtle_vp/rush_kill/economy_boom/vp_spot_hunter）の"
                              "アーキタイプ1体あたりの対戦数（既定40。2026-09-05(3)に"
                              "20→40へ引き上げ。search_balance_weights_260905_v2の"
                              "trial12見送り事例でmin_win_rate=0.20（20戦中4勝）という"
                              "サンプル数の少なさに起因する信頼区間の広さが気になったため、"
                              "判定の安定性を優先して倍増した）。"
                              "championには勝ち越したcandidateに対してのみ実行するため、"
                              "全trialには掛からない（コストは増えるが限定的）。0でこの"
                              "パネル戦自体をスキップ（--disable-diversity-checkと同義）。")
    parser.add_argument("--diversity-min-avg-winrate", type=float, default=0.55,
                         help="多様性パネル全体（4アーキタイプ）の平均勝率がこれ未満なら"
                              "championには勝っていても昇格を見送る（既定0.55）。")
    parser.add_argument("--diversity-min-winrate", type=float, default=0.35,
                         help="多様性パネルの中で最も苦手なアーキタイプに対する勝率が"
                              "これ未満なら昇格を見送る（既定0.35）。平均が高くても"
                              "特定の1アーキタイプに一方的に負ける『穴』を防ぐための下限。")
    parser.add_argument("--disable-confirmation-check", action="store_true",
                         help="2026-09-06(2)追加の確認戦（後述）を無効化し、1回目のchampion戦だけで"
                              "昇格を判定する従来の挙動に戻す（デバッグ・高速反復用のエスケープハッチ）。")
    parser.add_argument("--confirmation-games", type=int, default=None,
                         help="確認戦（championに勝ち越したcandidateに対し、独立したseedでもう一度"
                              "championと対戦させ、そちらでも有意な勝ち越しを要求するゲート。"
                              "make_objectiveのdocstring confirmation_check_*節参照）の対局数。"
                              "省略時（None）は--champion-gamesと同数。1回目の勝ち越しが停止則・"
                              "多重検定によるノイズだった場合、独立した2回目でも有意判定を通過する"
                              "確率はおよそalpha^2まで下がる。0を指定すると--disable-confirmation-check"
                              "と同義（確認戦を実行しない）。")
    parser.add_argument("--resume", action="store_true",
                         help="2026-09-10(3)追加（3.24節）。設定ミス等に気付いてCtrl+Cで"
                              "実行を中断した場合の再開用。study.user_attrsに『このセッション"
                              "（--tune・--games・championオプション・探索レンジ等一式の"
                              "フィンガープリント）で目標--trials件に到達するまで』の進捗を"
                              "記録しておき、同じ設定で再実行すると目標trial数に届くまでの"
                              "残りだけを実行する（既に完了した分は数え直さない）。前回から"
                              "設定が変わっていた場合は安全側に倒し、新しいセッションとして"
                              "--trials全件を実行する（_compute_resume_fingerprint参照）。"
                              "指定しない場合は従来通り、毎回--trials件を無条件に追加実行する"
                              "（既存studyへの積み増し運用。3.23.1節等）。")
    parser.add_argument("--measure-first-move-advantage", action="store_true",
                         help="2026-09-10(3)追加（3.24節）。Optuna探索を一切行わず、"
                              "--first-move-weights（省略時は--champion-file）の重み設定を"
                              "自分自身と--first-move-games局、jitterありで対戦させ、"
                              "先手(p0)/後手(p1)としての勝率の差だけを計測して終了する"
                              "診断専用モード。他のOptuna関連オプション（--trials/--resume等）"
                              "は無視される。champion_weights.json等は変更しない。")
    parser.add_argument("--first-move-games", type=int, default=300,
                         help="--measure-first-move-advantage 使用時の対局数（既定300）。")
    parser.add_argument("--first-move-weights", type=str, default=None,
                         help="--measure-first-move-advantage で使う重みファイルのパス"
                              "（champion_weights.json形式、または champion_history/gen_XXXX.json）。"
                              "省略時は --champion-file（さらに省略時はスクリプトと同じフォルダの"
                              "champion_weights.json）を使う。")
    args = parser.parse_args()

    if args.measure_first_move_advantage:
        _run_measure_first_move_advantage(args)
        return

    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    study_name = args.study_name or f"search_balance_{args.tune}_d{args.depth}k{args.candidate_k}"

    seed_params = {}
    if args.seed_from:
        with open(args.seed_from, "r", encoding="utf-8") as f:
            seed_data = json.load(f)
        seed_params = seed_data.get("params", seed_data)
        print(f"--seed-from {args.seed_from} を読み込みました: {len(seed_params)}個のパラメータ")

    active_names = set()
    if args.tune in ("pieces", "both"):
        active_names |= piece_param_names()
    if args.tune in ("weights", "both"):
        active_names |= weight_param_names()

    study = optuna.create_study(
        study_name=study_name, storage=args.storage, direction="maximize", load_if_exists=True,
    )

    if args.enqueue_advance_ramp_combos:
        # 2026-09-10追加（3.22.4節・3.23節）。詳細は--enqueue-advance-ramp-combosの
        # help参照。study.trials==0でしか発火しない--seed-fromと異なり、稼働中の
        # 既存studyへ追加投入する運用を想定しているため、study.user_attrsの完了
        # フラグでのみ重複投入をガードする。
        _ADVANCE_RAMP_ENQUEUE_TAG = "advance_ramp_combos_20260910_v1_enqueued"
        if study.user_attrs.get(_ADVANCE_RAMP_ENQUEUE_TAG):
            print(f"[enqueue] {_ADVANCE_RAMP_ENQUEUE_TAG} は既に投入済みのため、"
                  f"advance×advance_ramp_roundsの再投入をスキップします。")
        elif "advance" not in active_names or "advance_ramp_rounds" not in active_names:
            print("[enqueue] --tune weights（またはboth）でないため advance/advance_ramp_rounds "
                  "がactive_namesに含まれておらず、組み合わせを投入できません。スキップします。")
        else:
            advance_grid = [3.0, 4.0, 5.0]
            ramp_grid = [10, 15]
            advance_ramp_combos = [
                {"advance": a, "advance_ramp_rounds": r}
                for a in advance_grid for r in ramp_grid
            ]
            for combo in advance_ramp_combos:
                study.enqueue_trial(combo)
            study.set_user_attr(_ADVANCE_RAMP_ENQUEUE_TAG, True)
            print(f"[enqueue] 3.22.4節の仮説検証用にadvance×advance_ramp_roundsの組み合わせを"
                  f"{len(advance_ramp_combos)}件キューに追加しました: {advance_ramp_combos}")
            print("[enqueue] advance/advance_ramp_rounds以外の重みは通常通りTPEサンプラーに"
                  "任せます。--trials 30で実行する場合、この6trial＋通常探索24trial程度の"
                  "内訳になります（enqueueされたtrialはstudy.optimize()の先頭から順に"
                  "消化されるため、実際には最初の6trialがこの組み合わせになります）。")

    if seed_params and len(study.trials) == 0:
        warm_start = {k: v for k, v in seed_params.items() if k in active_names}
        if warm_start:
            study.enqueue_trial(warm_start)
            print(f"ウォームスタート用の初期値をキューに追加しました: {warm_start}")

    champion_holder = None
    champion_path = None
    champion_history_dir = None
    if args.champion_games > 0:
        champion_path = args.champion_file or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "champion_weights.json"
        )
        champion_history_dir = args.champion_history_dir or os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "champion_history"
        )
        champion_data = load_champion(champion_path)
        if champion_data is None:
            # 初回はチャンピオン不在のため、現在のweights.pyのベースライン値を
            # 初代チャンピオンとしてブートストラップする（暫定の出発点）。
            champion_holder = {
                "weights": _weights_module.resolve_weights(None),
                "source": "bootstrap(weights.py baseline)",
                "generation": 0,
            }
            save_champion(champion_path, champion_holder["weights"],
                          champion_holder["source"], champion_holder["generation"],
                          extra={"study_name": study_name})
            save_champion_history_entry(
                champion_history_dir, champion_holder["generation"], champion_holder["weights"],
                config_overrides=[], source=champion_holder["source"],
                study_name=study_name,
            )
            print(f"[champion] {champion_path} が存在しなかったため、weights.pyの現在の"
                  f"ベースライン重みを初代チャンピオン(generation=0)としてブートストラップしました。"
                  f"人間による強さの確認が取れているセットがあれば、このファイルを"
                  f"先に差し替えてから実行することを推奨します。")
        else:
            raw_champion_weights = champion_data["weights"]
            current_full_weights = _weights_module.resolve_weights(None)
            missing_keys = set(current_full_weights) - set(raw_champion_weights)
            extra_keys = set(raw_champion_weights) - set(current_full_weights)
            if missing_keys or extra_keys:
                # 2026-09-09追加: weights.pyのHEURISTIC_WEIGHTSにキーが追加/削除された
                # 直後にこのスクリプトを実行すると、古いスキーマのchampion_weights.jsonを
                # そのままSearchBot(weights=champion_weights)へ渡すことになる。
                # search_bot_skeleton.py/scoring_common.py側の新キー参照の大半は
                # weights["advance_ramp_rounds"]のような直接indexing（.get()を
                # 使っていない）ため、キーが1つでも欠けているとチャンピオン戦の
                # ワーカープロセスがKeyErrorで即クラッシュする。
                # weights.py._sync_champion_weights_from_file()が起動時に出す
                # 「キー集合が一致しないため同期をスキップしました」という警告は
                # あくまでHEURISTIC_WEIGHTSへの反映を諦めるだけで実害が無いのに対し、
                # こちらはチャンピオン戦に実際に使う辞書そのものなので、スキップでは
                # 済まず自動移行する必要がある。不足キーは現在のベースライン既定値で
                # 補い、廃止済みの余分なキーは切り捨てて、champion_weights.json自体も
                # 新スキーマへ書き戻す（次回実行以降はこの移行が発生しなくなる）。
                known_overrides = {
                    k: v for k, v in raw_champion_weights.items() if k in current_full_weights
                }
                raw_champion_weights = _weights_module.resolve_weights(known_overrides)
                print(f"[champion] {champion_path} のキー集合が現在のHEURISTIC_WEIGHTSと"
                      f"一致しなかったため自動移行しました"
                      f"（不足{len(missing_keys)}件は現在のベースライン既定値で補完: "
                      f"{sorted(missing_keys)} / 余分{len(extra_keys)}件は破棄: "
                      f"{sorted(extra_keys)}）。")
                save_champion(champion_path, raw_champion_weights,
                              champion_data.get("source", "unknown"),
                              champion_data.get("generation", 0),
                              extra={k: v for k, v in champion_data.items()
                                     if k not in ("weights", "source", "generation")})
                print(f"[champion] {champion_path} を新しいキー構成で書き戻しました。")

            champion_holder = {
                "weights": raw_champion_weights,
                "source": champion_data.get("source", "unknown"),
                "generation": champion_data.get("generation", 0),
                "trial_number": champion_data.get("trial_number"),
                "metrics": champion_data.get("metrics"),
                # 2026-09-05(3)追加: このchampionを最後に更新したstudy名
                # （champion_weights.json側にこの変更より前に保存された旧形式の
                # ファイルにはキー自体が無いためNoneになる）。今回新規に起きる
                # 昇格（このプロセスのobjective()内）は必ず現在のstudy_nameで
                # 上書きされるので、ここは「バックフィル保存時にどのstudy名を
                # 付けるか」の判断にのみ使う（下のバックフィル参照）。
                "study_name": champion_data.get("study_name"),
            }
            print(f"[champion] {champion_path} を読み込みました "
                  f"(generation={champion_holder['generation']}, source={champion_holder['source']})")
            # champion_history_dirが空の場合（旧バージョンのstudyを継続実行時等）、
            # 少なくとも「今読み込んだ最新世代」だけは上書きされる前にバックフィルする。
            gen_history_path = os.path.join(
                champion_history_dir, f"gen_{champion_holder['generation']:04d}.json"
            )
            if not os.path.exists(gen_history_path):
                # 2026-09-05(3)注意: ここでバックフィルする内容は「今回のstudyで
                # 新たに起きた昇格」ではなく「以前のどこかのstudy/実行で既に
                # 確定していたchampion」なので、study_nameは現在のstudy_name
                # ではなくchampion_weights.json側に記録されていた値
                # （無ければNone=不明）をそのまま使う。ここで誤って現在の
                # study_nameを付けると、まさに今回analyze_tuning_run.py側で
                # 対策した「別studyの昇格記録が紛れ込む」事故を作り込むことに
                # なるため、安易にstudy_name=study_nameとしないこと。
                save_champion_history_entry(
                    champion_history_dir, champion_holder["generation"], champion_holder["weights"],
                    config_overrides=[], source=champion_holder["source"],
                    trial_number=champion_holder.get("trial_number"),
                    metrics=champion_holder.get("metrics"),
                    study_name=champion_holder.get("study_name"),
                )
                print(f"[champion] {champion_history_dir} に世代{champion_holder['generation']}の"
                      f"スナップショットが無かったため、現在読み込んだ内容から補完保存しました"
                      f"（それ以前の世代は旧バージョンでは保存されていなかったため復元できません。"
                      f"study_name={champion_holder.get('study_name')!r}）。")

    # 2026-09-10(3)追加（3.24節）: --resume処理。study.user_attrsに「このフィンガー
    # プリントのセッションが何trialまで完了したか」を記録し、設定ミスに気付いて
    # Ctrl+Cで中断した場合に同じコマンド+--resumeで残りだけを再開できるようにする。
    _RESUME_STATE_KEY = "resume_state"
    trials_to_run = args.trials
    resume_callbacks = None
    if args.resume:
        fingerprint = _compute_resume_fingerprint(args, study_name)
        existing_state = study.user_attrs.get(_RESUME_STATE_KEY)
        if (existing_state and existing_state.get("fingerprint") == fingerprint
                and existing_state.get("completed_trials", 0) < existing_state.get("target_trials", 0)):
            remaining = existing_state["target_trials"] - existing_state["completed_trials"]
            trials_to_run = remaining
            print(f"[resume] 前回同じ設定での実行が{existing_state['completed_trials']}/"
                  f"{existing_state['target_trials']}trialで中断されていたため、"
                  f"残り{remaining}trialだけを実行します（目標: {existing_state['target_trials']}trial）。")
        else:
            if existing_state and existing_state.get("fingerprint") != fingerprint:
                print("[resume] 前回の中断セッションが見つかりましたが、設定"
                      "（--tune・探索レンジ・championオプション等）が変わっているため、"
                      f"安全のため新しいセッションとして--trials {args.trials}件を実行します。")
            elif existing_state:
                print(f"[resume] 前回のセッションは既に目標trial数"
                      f"（{existing_state.get('target_trials')}）に到達済みです。"
                      f"新しいセッションとして--trials {args.trials}件を実行します。")
            existing_state = {
                "fingerprint": fingerprint, "target_trials": args.trials, "completed_trials": 0,
                "started_at": datetime.datetime.now().isoformat(timespec="seconds"),
            }
            study.set_user_attr(_RESUME_STATE_KEY, existing_state)
            trials_to_run = args.trials

        def _resume_callback(study, trial):
            # 2026-09-10(3)追加: study.optimize()は逐次実行なので競合状態は発生しない
            # （--enqueue-advance-ramp-combosの完了フラグと同じ前提。docstring参照）。
            state = dict(study.user_attrs.get(_RESUME_STATE_KEY, existing_state))
            state["completed_trials"] = state.get("completed_trials", 0) + 1
            study.set_user_attr(_RESUME_STATE_KEY, state)

        resume_callbacks = [_resume_callback]

    if trials_to_run <= 0:
        print("[resume] 目標trial数に既に到達しているため、新たなtrialは実行しません。")

    executor_holder = ExecutorHolder(max_workers=args.workers)
    try:
        objective = make_objective(
            executor_holder, args.games, args.tune, seed_params, args.depth, args.candidate_k,
            game_timeout=args.game_timeout,
            champion_holder=champion_holder, champion_games=args.champion_games,
            champion_bonus_scale=args.champion_bonus_scale,
            champion_winrate_cap=args.champion_winrate_cap,
            champion_history_dir=champion_history_dir,
            promotion_margin=args.promotion_margin, promotion_alpha=args.promotion_alpha,
            champion_path=champion_path,
            diversity_check_enabled=(not args.disable_diversity_check),
            diversity_check_games=args.diversity_check_games,
            diversity_min_avg_winrate=args.diversity_min_avg_winrate,
            diversity_min_winrate=args.diversity_min_winrate,
            confirmation_check_enabled=(not args.disable_confirmation_check),
            confirmation_games=args.confirmation_games,
            study_name=study_name,
        )
        if trials_to_run > 0:
            try:
                study.optimize(objective, n_trials=trials_to_run, callbacks=resume_callbacks)
            except KeyboardInterrupt:
                if args.resume:
                    state = study.user_attrs.get(_RESUME_STATE_KEY, {})
                    print(f"\n[resume] 実行を中断しました（"
                          f"{state.get('completed_trials', '?')}/"
                          f"{state.get('target_trials', args.trials)}trial完了）。"
                          f"同じコマンドに--resumeを付けて再実行すると残りから再開できます。")
                raise
    finally:
        executor_holder.shutdown(wait=False)

    print("\n" + "=" * 60)
    print(f"最良トライアル（study: {study_name} / tune: {args.tune} / depth={args.depth}, k={args.candidate_k}）")
    print("=" * 60)
    best = study.best_trial
    print(f"score = {best.value:.2f}")
    print("params:")
    print(json.dumps(best.params, indent=2, ensure_ascii=False))
    print("metrics:")
    print(json.dumps(best.user_attrs, indent=2, ensure_ascii=False))

    if champion_holder is None:
        # チャンピオン評価が無効（--champion-games 0）な場合のみ、study.best_trialが
        # そのままこのstudyの結論なので、これを貼り付け用として案内する。
        print("\ncopy-paste用（weights.pyにそのまま貼り付け可能。比率キー解決済み・小数第2位丸め）:")
        print(format_best_params_for_paste(best.params, args.tune))
    else:
        # study.best_trialは弱いchampionに圧勝しただけの初期trialである可能性があり
        # champion_holderとは別物になりうるため、参考情報として明示するに留める
        # （貼り付け用ダンプはチャンピオン側のみに一本化）。
        print(
            "\n[参考・貼り付け不可] 上記はあくまで生スコアが最も高かったtrialの情報です。"
            "チャンピオン制では後半のtrialほど強い相手と対戦するためスコアを単純比較できず、"
            "このtrialが最終的にチャンピオンへ昇格したtrialと一致するとは限りません"
            "（今回のように食い違うことがあります）。貼り付けに使うべき唯一の出力は、"
            "この下の「チャンピオン制検証の結果」内のダンプです。"
        )

    full_params = dict(seed_params)
    full_params.update(best.params)

    out_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), f"best_params_search_{args.tune}.json"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"params": full_params, "metrics": best.user_attrs, "score": best.value,
                   "depth": args.depth, "candidate_k": args.candidate_k},
                   f, indent=2, ensure_ascii=False)
    print(f"\n{out_path} に保存しました（次の段階の --seed-from にはこのファイルを指定）")

    if champion_holder is not None:
        print("\n" + "=" * 60)
        print("チャンピオン制検証の結果（★ champion_weights.jsonに保存され、weights.pyが次回import時に自動反映します）")
        print("=" * 60)
        print(f"最終世代: generation={champion_holder['generation']} "
              f"(source={champion_holder['source']})")
        print(f"保存先: {champion_path}")
        print(
            "注意: study.best_trial のscoreは、実行中にchampionが強くなるたびに"
            "「勝つべき相手」自体が変わるため、study内の全trialを横断して単純比較は"
            "できない（後半のtrialほど強い相手と戦っている）。上のstudy.best_trialと"
            "パラメータが食い違って見えることがあるが、これはバグではなく、"
            "「一度は王者になった設定が、その後さらに強い設定に敗れて世代交代した」"
            "という勝ち抜き戦として正しい結果である。次にこのスクリプトを実行するときも、"
            "このchampion_weights.jsonが自動的に読み込まれ、続きから検証が再開される。"
        )
        # study.best_trialと最終championが別物になりうるため、昇格時点で
        # スナップショットしたchampion_holderのmetricsをここで必ず表示する。
        champ_trial_number = champion_holder.get("trial_number")
        champ_metrics = champion_holder.get("metrics")
        if champ_metrics:
            trial_label = f"trial_{champ_trial_number}" if champ_trial_number is not None else "不明"
            print(f"\nこのchampion自身の診断指標（昇格時点のもの。由来: {trial_label}）:")
            print(json.dumps(champ_metrics, indent=2, ensure_ascii=False))
        else:
            print(
                "\n[注意] このchampionの診断指標が記録されていません"
                "（このスクリプトの旧バージョンで昇格した、または一度も昇格していない"
                "初代ブートストラップのままの場合に起こる）。品質の裏付けが欲しい場合は、"
                "--champion-games を増やして再実行し、新たに昇格させることを推奨する。"
            )
        print("\n参考: 最終チャンピオンの重み（weights.pyのHEURISTIC_WEIGHTS[key]=value形式。"
              "champion_weights.jsonへ保存済みのため通常は貼り付け不要。手動で反映したい場合のみ"
              "weights.pyのBASE_WEIGHTS/WEIGHT_TIERSのdefault値を直接書き換えること。"
              "_BASELINE_WEIGHTSキャプチャより後ろに代入文として追記してもresolve_weights()には"
              "反映されない点に注意）:")
        print(format_full_weights_for_paste(champion_holder["weights"]))


if __name__ == "__main__":
    main()








