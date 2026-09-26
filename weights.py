# -*- coding: utf-8 -*-
"""
weights.py
==========================================================
プレイヤーが編集する「自分のAIを育てる」コンテンツ用の重み（HEURISTIC_WEIGHTS、
全97種。2026-09-09: advance_ramp_rounds新設・AIビルドモード向け新規重み12種新設に
伴い84→97。内訳はBASE_WEIGHT_KEYS/TIER_WEIGHT_KEYS/ENGINE_ONLY_WEIGHTSの各assert
コメント参照）を管理するモジュール。config.py（ゲームバランス設定）とは別の関心事
（チャンピオン撃破による段階解放）を持つため分離している。

鉄則はconfig.pyと同じ: 実体はこの1箇所に集約し、上書き/リセットは
apply_weight_overrides() / reset_weights() / resolve_weights() 経由で行う。
再代入(HEURISTIC_WEIGHTS = {...})は禁止、他ファイルへの再エクスポートもしない。

重み一覧・各設定の内容は企画書「AIビルドモード：重み一覧」の節を参照。
"""

import copy

# ============================================================
# Tier0: 初期解放24種（一覧は企画書「AIビルドモード：重み一覧」参照）
# 値は世代53・trial_1130時点の検証済み値。"jitter"のみエンジン内部固定値
# として ENGINE_ONLY_WEIGHTS 側に分離している（プレイヤー編集対象外）。
# base_hp_panic_thresholdのみ2026-09-02にTier4から昇格したため世代53時点の
# 検証値ではなく別途設定した値（下記代入行のコメント参照）。
# ============================================================
BASE_WEIGHTS = {
    "exposure": 0.8626628516443489,
    "vp_star": 11.36442261525369,
    "vp_tengen": 17.146937975988592,
    "rp_spot": 36.28703628993618,
    "engineer_econ_bonus": 5.987072134653674,
    "midgame_spot": 10.841242134397897,
    "support": 2.3408344612960286,
    "danger": 6.636093254117585,
    "attack": 0.6054833882046308,
    "incoming_damage": 0.6140939603985467,
    "base_pressure": 0.3677782633680533,
    "base_defense": 0.7210457101868182,
    "base_hp_weight": 1.8699270451527295,
    "kill_bonus": 18.63287612093654,
    "vp_spot_kill_bonus": 25.82671669868017,
    "rp_spot_kill_bonus": 15.986999400407262,
    "advance": 1.4943091509548914,
    "efficiency": 2.007307235436276,
    "rp_cost": 1.2678451003430202,
    "rp_differential": 0.1130189998120052,
    "placement_priority_bonus": 35.0,  # 2026-08-30追加: 配置(place)を移動(move)より原則優先するための定数ボーナス。「持ち駒(reserve)」の9体上限に達して生産不能になるのを防ぐ（heuristic_bot._score_actions参照）。実測で3000プレイヤー分のセルフプレイにおいて上限到達率0%を確認した値

    # 生産タイミング判断用の2種。最序盤から必須のためTier3からBASE_WEIGHTSへ
    # 昇格した。
    # 2026-08-30改修: 旧rp_hoarding_pref/rp_overflow_avoidanceは「RP残高
    # そのもの」を見て温存の是非を判断していたが、この2つは検証の結果
    # (a) 無駄RPの大半(実測約86%)はこの2つと無関係に発生しており(当初は
    # owned_piece_countがreserve+盤上を合算していた実装ミスにより上限へ
    # 誤って張り付いていたことが原因、後述)、(b) 残り約14%についても数値を
    # どう振っても一貫した改善が確認できず、事実上機能していなかった。本来
    # 「RPを温存すべき理由」は「配置に回すRPを確保するため」であり、RP残高
    # の多寡自体には意味がない。そこで「配置待ちの手駒(reserve)があるなら、
    # 生産後もその配置コスト分のRPが残るか」という、本来見るべき直接の状態
    # を見るガードに置き換えた（choose_production冒頭の分岐0-b参照）。
    # 2026-08-30再改修: 一度は「保有数上限との間に必ず空けておく枠数」
    # (piece_cap_safety_margin)と「許容する配置待ち駒の最大数」
    # (max_pending_reserve)の2種を導入したが、前者は対症療法、後者は硬直的
    # だったため廃止し、「来期収入をあてにする」income_confidence_ratioへ
    # 統合した。
    # 2026-08-30再々改修: income_confidence_ratioもラウンド進行順序を誤認した
    # バグだった（次ラウンドの収入は次ラウンドの配置行動より後に加算される
    # ため、あてにできない。heuristic_bot.choose_production参照）ため廃止。
    # 分岐0-bは単純な「生産後も配置コスト分のRPが残るか」だけの判断に戻し、
    # 重みを持たない絶対ルールとした。
    #
    # 真因はgame.Game.owned_piece_count()が「持ち駒(reserve)」ではなく
    # 「reserve＋盤上」を合算していた実装ミスで、盤面に駒を展開している
    # だけで上限に達し生産が止まっていたこと（2026-08-30、game.py参照）と、
    # 配置(place)を移動(move)より優先する仕組みが存在しなかったこと
    # （2026-08-30、下記placement_priority_bonus新設で対応）の2つだった。
    "base_threat_counter_priority": 4.0,  # 分岐②: 自陣本拠が脅かされている時、有利な相性の駒を最優先で生産する志向

    # 2026-08-31新設: 生産の歩兵一辺倒を是正するための恒常的な相性評価。
    # 経緯: _eff(kind)（HP/ATKに対するproduce_costの効率）が現行ステータス
    # では常に歩兵が最高値になり、production_scoreにjitterが乗らないため
    # argmaxが毎回決定論的に歩兵を選び続けていた（実測: 同梱棋譜3局で
    # 歩兵79回に対し騎兵0回）。base_threat_counter_priorityは「自陣本拠が
    # 今まさに脅かされている」場合のみ発火する狭い受動的な重みであり、
    # 平時の生産構成には一切影響しない。本重みはそれとは別に、盤上の敵
    # 駒構成全体を見て「今作るならどの駒種が相性面で有利か」を常時
    # （本拠が脅かされているか否かに関わらず）評価する。企画書5.0.1節の
    # ブレインストーミング候補#12 production_diversity_pref に対応する。
    # base_threat_counter_priorityとは意味・発火条件が異なるため、統合
    # せず併存させる（本拠緊急時は両方が加算されうる）。
    #
    # ⚠️ 2026-09-07追記（重要・引き継ぎ必読）: 上記の実装（heuristic_bot.py
    # choose_production()内、盤上の敵駒構成とのtype_multiplier相性エッジ）は
    # 実際にはSearchBot（search_bot_skeleton.py、tune_balance_search.pyが
    # チューニング・評価に使う本体）には一切配線されていなかった
    # （analyze_tuning_run_handoff.md 3.16節参照）。このため2026-09-06時点まで、
    # Optunaがこの重みをどれだけ探索してもSearchBot同士の対局には無効
    # だった（死んだ探索軸）。2026-09-07、scoring_common.py側に
    # _production_diversity_penalty_from_counts/_production_diversity_marginal_delta
    # を新設し、search_bot_skeleton.pyのevaluate_state/choose_productionから
    # 参照するよう配線した。ただしSearchBot側の実装は上記のheuristic_bot.py
    # 側（敵構成との三すくみ相性エッジ）とは計算式が別物（自軍の駒種構成
    # そのものの集中度を見る）である点に注意。同じ重み名を「生産の偏りを
    # 是正する」という共通の目的のもとで、2つのボットが別々の具体的な仕組み
    # として参照している。詳細はscoring_common.pyのコメント参照。
    "production_diversity_pref": 3.0,
    # 2026-08-30削除: early_engineer_bootstrap_pref（序盤に工兵を安く生産して
    # 経済を立ち上げる志向、旧分岐①）は、実測で既定値(1.5)と0.0の効果差が
    # round5時点の平均工兵数で+3%程度しかなく、1.5超20.0まで上げても変化が
    # ないことが判明し、事実上機能していなかったため撤去した。工兵は
    # produce_costが低廉なため_eff(kind)の時点で既に選ばれやすく、専用の
    # 追加ボーナスがなくても序盤の工兵生産は十分に発生する。恒常的な「工兵を
    # どれだけ好むか」という志向は、KIND_TO_PRODUCE_PREFの`engineer_produce_pref`
    # （Tier1 #10。下記TIER_WEIGHTS参照）が既に担っており役割が重複していた。

    # 2026-09-02昇格: Tier4 champion_index=48からBASE_WEIGHTSへ昇格。
    # 本拠HP危険水域でのパニック的防衛優先という性質上、チャンピオン撃破報酬
    # として後回しにできる「上級・演出」枠ではなく、防御系ビルドに必須の
    # 主要重みとして最初から編集可能にすべきという判断（詳細は下記
    # BASE_WEIGHTS["base_hp_panic_threshold"]の代入行コメント参照）。
    #
    # 2026-09-02追記: 「敵が本拠に近づいたら無条件で帰還する」という別の
    # 新重み base_approach_defense_priority も試験導入したが、実測ベンチマークで
    # base_hp_panic_threshold単体（本拠速攻相手に勝率15.0%）より悪化する
    # （併用で26.7〜35.0%、重みを上げるほど56〜98%まで悪化）結果となったため
    # 撤回した（迎撃したほうが得な場面まで撤退させてしまい逆効果だった）。
    # 撤回の経緯はscoring_common.py側のコメントに残してある。
    "base_hp_panic_threshold": 0.0,

    # 2026-09-04新設: rp_income_margin（RPスポット確保を伴わない攻勢の「息切れ」を
    # 評価する新重み）。evaluate_state側で(自分の来期RP収入見積もり - 相手の
    # 来期RP収入見積もり)に本重みを掛けて加算する（_estimate_next_round_income、
    # scoring_common.py参照）。「本拠特攻をルールで禁止するのではなく、RPスポットを
    # 空けて攻める手が持つ経済的な代償を、探索の地平線より先まで読まなくても今の
    # 評価値に反映させたい」という要望（企画書9.1節フォローアップ）に対応する。
    # base_approach_defense_priority（0.89節、行動を強制する形の防御重み）は実測で
    # 悪化したため撤回済みだが、本重みは「行動を縛る」のではなく「状態の価値を
    # 正しく見積もる」ための静的評価項である点が異なる。まずは初期値0.0（無効）で
    # 導入し、下記のBASE_WEIGHTS再代入ブロックで実際の検証用初期値を与える。
    "rp_income_margin": 0.0,

    # 2026-09-04新設: base_pressure_ramp_rounds（本拠特攻そのものへの対症療法を
    # やめ、base_pressureの実効値自体をラウンド数で立ち上げる構造的な修正）。
    # 背景: rp_income_marginのような加点で対抗する対症療法は、実測（kifu調査）で
    # 全アーキタイプが例外なく序盤に本拠特攻を行う結果となり、advance・
    # base_pressure・efficiency・kill_bonus等の合算に対して桁が小さすぎて
    # 機能しないことが判明した（企画書9.1節フォローアップ）。base_dmg（本拠への
    # ダメージ）は既にattackとは別のbase_pressureという専用重みに分離されている
    # （_potential_attackの戻り値参照）が、この重み自体が「ラウンド1の丸裸の
    # 本拠を攻める（danger=0・incoming=0でノーリスク）」ことにも「ラウンド20の
    # 消耗した本拠にとどめを刺す」ことにも同じ強さで効いてしまうのが真因だった。
    # 本重みが指すラウンド数に達するまで、base_pressureの実効値を0から線形に
    # 立ち上げる（_base_pressure_ramp_multiplier参照）。0（既定＝無効）なら
    # 従来通り常にbase_pressureがそのまま効く。値を入れて初めて「立ち上がり」が
    # 有効になる後方互換設計。
    "base_pressure_ramp_rounds": 0,

    # 2026-09-09新設: advance_ramp_rounds（analyze_tuning_run_handoff.md 3.19節）。
    # base_pressure_ramp_roundsと全く同じ理由・全く同じ仕組み（_base_pressure_
    # ramp_multiplierをそのまま流用）で、advance（敵本拠へ近づく移動/配置への
    # 評価）にもラウンド立ち上がりを導入する。advanceはbase_pressureと違って
    # rampを持たず常にフル値が乗っていたため、tune_balance_search.pyのレポートで
    # advance自体がレンジ下限（0.2、レンジ内位置2.12%）に張り付き続ける問題が
    # 観測された。0（既定＝無効）なら従来通り常にadvanceがそのまま効く。
    "advance_ramp_rounds": 0,
}
BASE_WEIGHT_KEYS = tuple(BASE_WEIGHTS.keys())
assert len(BASE_WEIGHT_KEYS) == 27, "初期解放セットは27種であること（2026-08-30: RP関連2種廃止24→22、placement_priority_bonus新設22→23、early_engineer_bootstrap_pref廃止23→22。income_confidence_ratioは導入直後にラウンド順序の誤認バグと判明し撤回。2026-08-31: production_diversity_pref新設22→23。2026-09-02: base_hp_panic_thresholdをTier4からBASE_WEIGHTSへ昇格23→24。base_approach_defense_priorityも同日新設・撤回している（撤回の経緯はscoring_common.py参照）ため24のまま。2026-09-04: rp_income_margin新設24→25、base_pressure_ramp_rounds新設25→26。あわせてbase_pressureがevaluate_state内で敵駒側にも二重適用されていた問題を修正し、自分の駒が敵本拠を脅かす価値のみを表すよう純化した（scoring_common._position_hold_valueのapply_base_pressure引数参照）。2026-09-09: advance_ramp_rounds新設26→27（heuristic_bot.py/scoring_common.py/gen_weight_reference.pyの3ファイルだけが前セッションで反映されたと引き継ぎ資料に記載されていたが、本セッションに実際に添付された5ファイルのいずれにもその反映が見られなかったため、本セッションで5ファイル全てへ改めて実装した。weights.py/search_bot_skeleton.pyは前セッションで未添付だったため元々未実装だった）"

BASE_WEIGHTS["advance"] = 4.85
BASE_WEIGHTS["attack"] = 0.83
BASE_WEIGHTS["base_defense"] = 6.47
# 2026-09-02: base_hp_panic_threshold昇格に伴いここへ実値を設定。0.0のままだと
# 「守備寄りビルドが最初から使うべき主要重み」であるにもかかわらず新規プレイヤーの
# AIでは常時無効（本拠HPが危険水域に入っても倍率1.0のまま）になってしまうため、
# meta_diversity_check.pyのturtle_vp検証（HP比40%で倍率6.0相当。危険水域自体は
# 2026-09-02(2)に0.5→0.8へ引き上げ済み）と同じ基準の
# 10.0を初期値とする。
BASE_WEIGHTS["base_hp_panic_threshold"] = 10.0
BASE_WEIGHTS["base_hp_weight"] = 0.99
BASE_WEIGHTS["base_pressure"] = 3.35
BASE_WEIGHTS["base_threat_counter_priority"] = 1.5
BASE_WEIGHTS["danger"] = 0.29
BASE_WEIGHTS["efficiency"] = 2.27
BASE_WEIGHTS["engineer_econ_bonus"] = 2.93
BASE_WEIGHTS["exposure"] = 4.88
BASE_WEIGHTS["incoming_damage"] = 0.78
BASE_WEIGHTS["kill_bonus"] = 24.0
BASE_WEIGHTS["midgame_spot"] = 5.69
BASE_WEIGHTS["placement_priority_bonus"] = 42.15
BASE_WEIGHTS["production_diversity_pref"] = 9.28
BASE_WEIGHTS["rp_cost"] = 2.84
BASE_WEIGHTS["rp_differential"] = 1.5
# 2026-09-04追加: rp_income_marginの検証用初期値。rp_differential（現在のRP
# 保有量の差、1.5）と同程度のオーダーから始め、tune_balance_search.py側の
# 実測（本拠特攻構成 vs RPスポット確保構成）を見ながら較正する想定。
BASE_WEIGHTS["rp_income_margin"] = 2.0
# 2026-09-04追加: base_pressure_ramp_roundsの検証用初期値。5ラウンドかけて
# base_pressureを0→通常値まで立ち上げる（ラウンド1〜4は本拠へのダメージ評価が
# 弱まり、ラウンド5以降は従来通り）。meta_diversity_check.py実測（本拠特攻
# アーキタイプの序盤挙動）を見ながら較正する想定。
BASE_WEIGHTS["base_pressure_ramp_rounds"] = 5
# 2026-09-09追加: advance_ramp_roundsは0（無効）のまま据え置く。
# base_pressure_ramp_rounds=5は導入時にmeta_diversity_check.pyの実測で既存の
# 強さを損なっていないことを確認した上で「現行チャンピオンの検証済み値」として
# 5を採用しているが、advance_ramp_roundsについては本セッションにその種の検証
# ツール（meta_diversity_check.py・rematch_eval.py・tune_balance_search.py）が
# 一切添付されておらず、5にした場合の影響を確認できない（実際に本セッション内で
# depth2の自己対戦1局を比較したところ、advance_ramp_rounds=5だと同一シードでも
# 決着の勝者・累計VPがadvance_ramp_rounds=0（未導入時と同一）の場合と変わる
# ことを確認済み＝無視できない影響がある）。ハンドオフ資料3.19節が「デフォルト値は
# 要判断」と明記していた通り、検証手段が無い状態で既定値として実質的なバランス
# 変更を静かに混入させるのは避け、0（＝完全に無効・従来通り）のまま次回の検証
# ツール添付を待つのが安全と判断した。次回、上記3ツールが揃った時点で
# meta_diversity_check.py等を使い、5前後の値で既存の強さを損なわないか確認した
# 上で正式な初期値を決定すること。
BASE_WEIGHTS["rp_spot"] = 22.33
BASE_WEIGHTS["rp_spot_kill_bonus"] = 21.09
BASE_WEIGHTS["support"] = 6.01
BASE_WEIGHTS["vp_spot_kill_bonus"] = 27.08
BASE_WEIGHTS["vp_star"] = 6.54
BASE_WEIGHTS["vp_tengen"] = 17.92

# 2026-09-02削除: 以下10種（archer_immunity_awareness・base_proximity_alert・
# center_control・corner_edge_avoidance・favorable_matchup_bonus・
# finishing_blow_bonus・midgame_spot_guard_bonus・rp_spot_guard_bonus・
# unfavorable_matchup_penalty・vp_spot_guard_bonus）へのBASE_WEIGHTS[...]=値の
# 代入が本来あるべきでない場所に残っていたのを発見・削除した。いずれも実体は
# WEIGHT_TIERS（Tier1/2）側のキーであり、BASE_WEIGHT_KEYSには含まれないにも
# かかわらずBASE_WEIGHTSという辞書オブジェクトには紛れ込んでいた（おそらく
# 0.86節で修正済みの「貼り付け用重み出力が探索対象外のTier重みまで丸ごと
# ダンプしていたバグ」の影響を受けた、修正前の出力をそのまま貼り付けてしまった
# 名残）。HEURISTIC_WEIGHTSの構築順序（BASE_WEIGHTSをupdateした直後に
# WEIGHT_TIERSのdefaultで同キーを再度上書きする）により、この10行は現状の
# HEURISTIC_WEIGHTSの値に一切影響していない完全なデッドコードだったため
# （resolve_weights()等を経由しない限りHEURISTIC_WEIGHTSの実値は変化しない
# ことをpythonで確認済み）、削除しても振る舞いは変わらない。


# ============================================================
# 符号統一方針（2026-08-27）: プレイヤーが編集する重みの数値は常に0以上の
# 「大きさ」として持つ。「マイナスを入力させると人間は必ず混乱する」ため、
# 見せる数値・保存する数値は正の数だけに統一し、「その重みが評価式の中で
# 加点として働くか減点として働くか」は下記PENALTY_WEIGHT_KEYSという別の
# 分類（＝ゲーム側の実装でcandidateを増やすパラメーターと減らすパラメーターを
# 分けたもの）で管理する。符号の反転（-1をかける処理）はエンジンが重みを
# 実際にスコア計算へ渡す直前（to_engine_signed()。HeuristicBot/SearchBotの
# __init__からのみ呼ぶ）でのみ行い、それ以外の場所（保存値・UI表示・
# tune_balance_search.py等の探索空間・puzzle_generator.py等の出題定義）は
# 常にこの正の大きさの表記で統一する。
#
# UI側の実装メモ（企画書と合わせて実装する側で参照）: パラメーター名の表記を
# 「加点系」「減点系」で色分けし、数値入力欄の左側に固定のマイナス表記を
# 添えることで、プレイヤーはマイナス記号を一切入力せず大きさだけを入力すれば
# よいようにする。
# ============================================================
PENALTY_WEIGHT_KEYS = frozenset({
    "exposure", "danger", "incoming_damage", "base_defense", "rp_cost",
})


def to_engine_signed(weights_dict):
    """プレイヤー向け（常に0以上の大きさ）の重み辞書を受け取り、評価式が直接
    使える符号付きの辞書を返す（PENALTY_WEIGHT_KEYSのみ符号を反転する）。
    新しいdictを返すだけで引数は変更しない。HeuristicBot/SearchBotの
    __init__以外からは呼ばないこと（評価式自体は今まで通り
    `score += x * self.w[key]` の形のままでよい設計にしている）。"""
    out = dict(weights_dict)
    for key in PENALTY_WEIGHT_KEYS:
        if key in out:
            out[key] = -out[key]
    return out


def _validate_no_negative_penalty_values(weights_dict):
    negatives = {k: v for k, v in weights_dict.items()
                 if k in PENALTY_WEIGHT_KEYS and v < 0}
    if negatives:
        raise ValueError(
            f"PENALTY_WEIGHT_KEYSは常に0以上の大きさで指定すること（符号は"
            f"to_engine_signed()側で自動的に付与される）。負値が渡された: {negatives}"
        )

# プレイヤーには見せない・解放対象外のエンジン固定値
ENGINE_ONLY_WEIGHTS = {
    "jitter": 1.5,
}

# ============================================================
# Tier1〜4: チャンピオン撃破報酬56種。一覧・各設定の内容は企画書
# 「AIビルドモード：重み一覧」の節を参照（このコメントには複製しない）。
#
# フィールド定義:
#   champion_index: 撃破すると解放される番号（1〜56）
#   implemented   : スコアリング側に実配線済みか。Falseはdefault(0.0)で
#                   何のスコアにも影響しない「解放だけのダミー枠」
#   default       : 未撃破/未実装時のプレースホルダ値
#
# Tier1(15種)とTier2の一部(10種)
# =計25種のみ実装済み。残り31種
# （交換比率・撤退閾値・陣形認識等、新しい状態管理が要るもの）は
# チャンピオン36体目以降でないと解放されないため未実装のまま据え置き。
# 2026-09-02: base_hp_panic_threshold（旧Tier4 champion_index=48）は
# BASE_WEIGHTSへ昇格したためここから除外。Tier4の残り9種はchampion_index
# を48〜56へ詰め直した（1つ空き番号ができる歯抜け運用は行わない方針）。
WEIGHT_TIERS = [
    # ---- Tier1: 駒種別の細分化（撃破1〜15体目） ----
    {"champion_index": 1, "tier": 1, "key": "infantry_place_pref", "label": "歩兵配置優先度", "default": 0.0, "implemented": True},
    {"champion_index": 2, "tier": 1, "key": "cavalry_place_pref", "label": "騎兵配置優先度", "default": 0.0, "implemented": True},
    {"champion_index": 3, "tier": 1, "key": "heavy_place_pref", "label": "重装兵配置優先度", "default": 0.0, "implemented": True},
    {"champion_index": 4, "tier": 1, "key": "archer_place_pref", "label": "弓兵配置優先度", "default": 0.0, "implemented": True},
    {"champion_index": 5, "tier": 1, "key": "engineer_place_pref", "label": "工兵配置優先度", "default": 0.0, "implemented": True},
    {"champion_index": 6, "tier": 1, "key": "infantry_produce_pref", "label": "歩兵生産優先度", "default": 0.0, "implemented": True},
    {"champion_index": 7, "tier": 1, "key": "cavalry_produce_pref", "label": "騎兵生産優先度", "default": 0.0, "implemented": True},
    {"champion_index": 8, "tier": 1, "key": "heavy_produce_pref", "label": "重装兵生産優先度", "default": 0.0, "implemented": True},
    {"champion_index": 9, "tier": 1, "key": "archer_produce_pref", "label": "弓兵生産優先度", "default": 0.0, "implemented": True},
    {"champion_index": 10, "tier": 1, "key": "engineer_produce_pref", "label": "工兵生産優先度", "default": 0.0, "implemented": True},
    {"champion_index": 11, "tier": 1, "key": "cavalry_overextend_tolerance", "label": "騎兵前のめり許容度", "default": 0.0, "implemented": True},
    {"champion_index": 12, "tier": 1, "key": "heavy_frontline_bonus", "label": "重装兵籠城ボーナス", "default": 0.0, "implemented": True},
    {"champion_index": 13, "tier": 1, "key": "archer_kiting_pref", "label": "弓兵キッティング志向", "default": 0.0, "implemented": True},
    {"champion_index": 14, "tier": 1, "key": "engineer_econ_solo_bonus", "label": "工兵経済特化度（非スポット単独行動）", "default": 0.0, "implemented": True},
    {"champion_index": 15, "tier": 1, "key": "engineer_shield_bonus", "label": "工兵盾特化度（弓兵無効化利用）", "default": 0.0, "implemented": True},

    # ---- Tier2: 三すくみ・戦闘計算・陣形（撃破16〜35体目） ----
    {"champion_index": 16, "tier": 2, "key": "favorable_matchup_bonus", "label": "有利対面積極度", "default": 0.0, "implemented": True},
    {"champion_index": 17, "tier": 2, "key": "unfavorable_matchup_penalty", "label": "不利対面回避度", "default": 0.0, "implemented": True},
    {"champion_index": 18, "tier": 2, "key": "archer_immunity_awareness", "label": "弓兵無効化認識（対工兵）", "default": 0.0, "implemented": True},
    {"champion_index": 19, "tier": 2, "key": "trade_ratio_threshold", "label": "交換比率許容閾値", "default": 0.0, "implemented": False},
    {"champion_index": 20, "tier": 2, "key": "retreat_hp_threshold", "label": "撤退HP閾値", "default": 0.0, "implemented": False},
    {"champion_index": 21, "tier": 2, "key": "cluster_bonus", "label": "密集陣形ボーナス", "default": 0.0, "implemented": False},
    {"champion_index": 22, "tier": 2, "key": "overextension_penalty", "label": "過剰展開ペナルティ", "default": 0.0, "implemented": False},
    {"champion_index": 23, "tier": 2, "key": "center_control", "label": "中央支配志向", "default": 0.0, "implemented": True},
    {"champion_index": 24, "tier": 2, "key": "corner_edge_avoidance", "label": "隅・端回避度", "default": 0.0, "implemented": True},
    {"champion_index": 25, "tier": 2, "key": "liberty_defense_awareness", "label": "呼吸点（連結分断）防御意識", "default": 0.0, "implemented": False},
    {"champion_index": 26, "tier": 2, "key": "recapture_ban_exploit", "label": "再配置禁止マス活用度", "default": 0.0, "implemented": False},
    {"champion_index": 27, "tier": 2, "key": "finishing_blow_bonus", "label": "一撃必殺優先度", "default": 0.0, "implemented": True},
    {"champion_index": 28, "tier": 2, "key": "vp_spot_guard_bonus", "label": "VPスポット護衛優先度", "default": 0.0, "implemented": True},
    {"champion_index": 29, "tier": 2, "key": "rp_spot_guard_bonus", "label": "RPスポット護衛優先度", "default": 0.0, "implemented": True},
    {"champion_index": 30, "tier": 2, "key": "midgame_spot_guard_bonus", "label": "中盤スポット防衛優先度", "default": 0.0, "implemented": True},
    {"champion_index": 31, "tier": 2, "key": "base_proximity_alert", "label": "本拠警戒半径", "default": 0.0, "implemented": True},
    {"champion_index": 32, "tier": 2, "key": "position_hold_coefficient", "label": "移動元喪失価値の係数", "default": 0.0, "implemented": False},
    {"champion_index": 33, "tier": 2, "key": "new_placement_exposure_sensitivity", "label": "新規配置露出感度", "default": 0.0, "implemented": False},
    {"champion_index": 34, "tier": 2, "key": "opponent_next_best_awareness", "label": "相手次善手警戒度", "default": 0.0, "implemented": False},
    {"champion_index": 35, "tier": 2, "key": "mutual_destruction_tolerance", "label": "相打ち許容度", "default": 0.0, "implemented": False},

    # ---- Tier3: フェーズ・経済（撃破36〜47体目、未実装） ----
    {"champion_index": 36, "tier": 3, "key": "early_rp_spot_priority", "label": "序盤RPスポット優先度", "default": 0.0, "implemented": False},
    {"champion_index": 37, "tier": 3, "key": "late_rp_spot_priority", "label": "終盤RPスポット優先度", "default": 0.0, "implemented": False},
    {"champion_index": 38, "tier": 3, "key": "early_vp_indifference", "label": "序盤VP軽視度", "default": 0.0, "implemented": False},
    {"champion_index": 39, "tier": 3, "key": "late_vp_urgency", "label": "終盤VP追い上げ度", "default": 0.0, "implemented": False},
    {"champion_index": 40, "tier": 3, "key": "midgame_spot_timing_aggression", "label": "中盤スポット着手タイミング積極度", "default": 0.0, "implemented": False},
    {"champion_index": 41, "tier": 3, "key": "endgame_clock_pressure", "label": "終盤クロック意識", "default": 0.0, "implemented": False},
    {"champion_index": 42, "tier": 3, "key": "first_move_aggression", "label": "先手時積極度", "default": 0.0, "implemented": False},
    {"champion_index": 43, "tier": 3, "key": "second_move_aggression", "label": "後手時積極度", "default": 0.0, "implemented": False},
    {"champion_index": 44, "tier": 3, "key": "rp_differential_asymmetry", "label": "RP差非対称評価", "default": 0.0, "implemented": False},
    {"champion_index": 45, "tier": 3, "key": "vp_lead_conservatism", "label": "VPリード時守備固め度", "default": 0.0, "implemented": False},
    {"champion_index": 46, "tier": 3, "key": "vp_deficit_desperation", "label": "VP劣勢時逆転志向", "default": 0.0, "implemented": False},
    {"champion_index": 47, "tier": 3, "key": "move_cost_dynamic_sensitivity", "label": "移動コスト動的敏感度", "default": 0.0, "implemented": False},

    # ---- Tier4: 上級・演出（撃破48〜56体目、未実装） ----
    {"champion_index": 48, "tier": 4, "key": "enemy_base_finish_urgency", "label": "敵本拠HP危険水域攻め急ぎ度", "default": 0.0, "implemented": False},
    {"champion_index": 49, "tier": 4, "key": "randomness_dial", "label": "ランダム性強度（jitter可変化）", "default": 0.0, "implemented": False},
    {"champion_index": 50, "tier": 4, "key": "bluff_tendency", "label": "ブラフ度（探索ボット向け）", "default": 0.0, "implemented": False},
    {"champion_index": 51, "tier": 4, "key": "opponent_adaptation_speed", "label": "対戦相手適応速度（将来の学習機能予約枠）", "default": 0.0, "implemented": False},
    {"champion_index": 52, "tier": 4, "key": "second_move_compensation", "label": "先出し不利補正", "default": 0.0, "implemented": False},
    {"champion_index": 53, "tier": 4, "key": "pass_tolerance", "label": "パス選択許容度", "default": 0.0, "implemented": False},
    {"champion_index": 54, "tier": 4, "key": "efficiency_hp_atk_balance", "label": "効率レンジ配分（HP/ATK比重）", "default": 0.0, "implemented": False},
    {"champion_index": 55, "tier": 4, "key": "danger_range_decay", "label": "危険度距離減衰", "default": 0.0, "implemented": False},
    {"champion_index": 56, "tier": 4, "key": "capture_delay_lookahead", "label": "RPスポット捕獲後遅延評価", "default": 0.0, "implemented": False},

    # 2026-09-04新設: rush_opening_pressure。base_pressure_ramp_rounds
    # （序盤の本拠特攻を抑える立ち上がり倍率。BASE_WEIGHTS参照）はビルド初期状態の
    # 「初心者を事故らせない」既定挙動として導入したが、rush_killというアーキタイプ
    # 自体を選択肢から完全に消すべきではない、という方針に基づき、Tier4（上級・
    # 撃破57体目）で「意図的に序盤の本拠特攻を解禁する」尖ったスタイル用の重みを
    # 新設した。base_pressure_ramp_roundsによる立ち上がり抑制の"最中"（ラウンド数が
    # ramp_roundsに達するまでの間）にのみ上乗せされ、抑制期間が終わった後の中盤・
    # 終盤の挙動には一切影響しない（＝「速攻ビルド」という趣味的・尖った選択を
    # 復活させるための専用ノブであり、通常ビルドの終盤バランスを歪めない）。
    # 1.0でramp分をちょうど打ち消して従来のbase_pressureへ戻り、1.0超で
    # ramp前より強い"特攻"を意図的に選べる（メタの穴を突く尖ったビルド向け）。
    # SEARCH_SPACE_KEYSには含めない（Optunaの既定バランス探索対象ではなく、
    # プレイヤーが任意に選ぶスタイル選択の重みのため）。
    {"champion_index": 57, "tier": 4, "key": "rush_opening_pressure", "label": "序盤本拠特攻の意図的解禁（尖ったビルド用）", "default": 0.0, "implemented": True},

    # ---- 2026-09-09新設: AIビルドモード向け新規重み8案（analyze_tuning_run_handoff.md
    # 3.20節。champion_index 58〜69）。全て「AIビルドモードで player が自由に
    # 尖らせる」ためのTier4枠であり、SEARCH_SPACE_KEYSには含めない（rush_opening_
    # pressureと同じ位置づけ）。既定0.0なら全てのキーが従来の挙動を一切変えない
    # （後方互換）。3.16節の教訓（production_diversity_prefがHeuristicBotにしか
    # 配線されずSearchBotに一切効いていなかった事故）を繰り返さないため、
    # 「implemented: True」とした重みは全てheuristic_bot.py・search_bot_skeleton.py
    # の両方に配線済み（詳細は各重みの直下コメント・scoring_common.pyの該当セクション
    # 参照）。
    #
    # #1 opening_tempo_pref: 序盤（OPENING_TEMPO_ROUNDS=5ラウンド、scoring_common.py
    # 参照）ほどpassを忌避しRPスポット確保系の行動を優先する。3.19節で確認した
    # 「ラウンド1にどこへ置いてもpassより評価が悪くなる」縮退への直接対策。両bot
    # の_score_actions（浅い候補評価）に加え、search_bot_skeleton.pyのevaluate_state
    # （探索の葉評価。深い探索の最終値そのものに効かせるため）・
    # _ProductionEvalContext.best_value_for_placements（生産→即配置の差分評価）にも
    # 配線済み。「優先度高・技術難度低」と評価されていたため今回実装した。
    {"champion_index": 58, "tier": 4, "key": "opening_tempo_pref", "label": "序盤テンポ最優先度（pass忌避・RPスポット優先）", "default": 0.0, "implemented": True},
    # #2 comeback_desperation_pref: VPで劣勢×終盤（scoring_common._comeback_
    # desperation_fraction、[0,1]）ほど、危険への接近・撃破そのものを積極的に評価する
    # 「捨て身の逆転」志向。両botの_score_actionsに配線済み（advance/rush_opening_
    # pressureと同じ「行動選好」レイヤーに限定。状態評価レイヤーへの配線は見送った）。
    {"champion_index": 59, "tier": 4, "key": "comeback_desperation_pref", "label": "劣勢終盤の捨て身逆転志向", "default": 0.0, "implemented": True},
    # #3 tempo_loss_aversion: 前進せず・攻撃機会も生まず・スポットへも向かわない
    # 「意味の薄い移動」への忌避（opening_tempo_prefの中盤以降版）。両botの
    # _score_actions（move分岐）に配線済み。
    {"champion_index": 60, "tier": 4, "key": "tempo_loss_aversion", "label": "手待ち移動忌避度", "default": 0.0, "implemented": True},
    # #4 mirror_match_awareness: 対戦相手の直近の行動パターンから速攻寄り/経済寄りを
    # 推定し、有利対面を取りやすい構成へ重みを動的に調整する志向。3.20節の元案でも
    # 「実装難度高（対戦相手の行動推定ロジックの新規実装が必要）」と明記されている。
    # 生半可な推定ロジックを拙速に両botへ配線し「登録されているのに実は意図通り
    # 動いていない」事故を生むリスクの方が、解放だけのダミー枠として正直に留保する
    # よりも高いと判断し、他のTier3/4の多数の未実装枠と同じ形で解放だけの
    # プレースホルダとして登録する（次回、対戦相手の行動履歴を追跡する専用の
    # 状態管理を新設した上で実装すること）。
    {"champion_index": 61, "tier": 4, "key": "mirror_match_awareness", "label": "対戦相手アーキタイプ推定・対応（実装予約枠）", "default": 0.0, "implemented": False},
    # #5 first_kill_momentum: 対局中に自分が1体でも撃破した後、勢いに乗って攻勢を
    # さらに強める。game.py Game.total_kills（本セッションで新設。run()内のremoved
    # 処理で更新）を両botの_score_actionsから参照する。search_bot_skeleton.pyの
    # clone_game()・resolve_round_end()（探索内の仮想対局用の独立実装）も同じ形で
    # total_killsを引き継ぐよう修正済み。
    {"champion_index": 62, "tier": 4, "key": "first_kill_momentum", "label": "初撃破後の勢い倍加度", "default": 0.0, "implemented": True},
    # #6 signature_unit_affinity（5駒種分）: 特定の駒種を偏重して「〜特化型」を
    # 演出する自由度の高いノブ。production_diversity_prefと対になる重みとして
    # evaluate_state・choose_production系（両bot）に配線済み（KIND_TO_PLACE_PREF/
    # KIND_TO_PRODUCE_PREFという既存の狭い局面判断とは異なり、自軍の駒種構成
    # そのものへの状態評価レイヤーに置く。scoring_common.SIGNATURE_AFFINITY_KEY
    # 参照）。
    {"champion_index": 63, "tier": 4, "key": "infantry_signature_affinity", "label": "歩兵特化度", "default": 0.0, "implemented": True},
    {"champion_index": 64, "tier": 4, "key": "cavalry_signature_affinity", "label": "騎兵特化度", "default": 0.0, "implemented": True},
    {"champion_index": 65, "tier": 4, "key": "heavy_signature_affinity", "label": "重装兵特化度", "default": 0.0, "implemented": True},
    {"champion_index": 66, "tier": 4, "key": "archer_signature_affinity", "label": "弓兵特化度", "default": 0.0, "implemented": True},
    {"champion_index": 67, "tier": 4, "key": "engineer_signature_affinity", "label": "工兵特化度", "default": 0.0, "implemented": True},
    # #7 flourish_tiebreak_pref: 評価値が実質同点の候補が複数ある場合に限り、
    # 撃破・スポットキルを伴う「演出映えする」手をタイブレークで優先する。score
    # そのものには一切加算しない設計（HeuristicBot.choose_with_debugの候補選択、
    # SearchBot._select_best双方で、通常の比較後・同点候補の絞り込みとしてのみ
    # 作用する）ため、通常のプレイ強度・Optuna探索結果には実質影響しない
    # （3.20節が謳う「バランス影響ゼロ」設計をそのまま反映）。
    {"champion_index": 68, "tier": 4, "key": "flourish_tiebreak_pref", "label": "同点候補の演出タイブレーク優先度", "default": 0.0, "implemented": True},
    # #8 endgame_clock_bluff: 終盤、実際の形勢に関わらず時間切れ際の圧力を強める
    # （対人戦の持ち時間ルールと組み合わせて初めて意味を持つ）。AI同士のセルフプレイ
    # には壁時計の概念が無く、3.20節の元案自体も「AI同士の対戦では効果が薄く実装
    # 優先度は低い」と明記しているため、#4と同じ理由でダミー枠として登録するに
    # 留める（対人戦のクロック機構と合わせて実装すること）。
    {"champion_index": 69, "tier": 4, "key": "endgame_clock_bluff", "label": "終盤クロックブラフ（対人戦向け・実装予約枠）", "default": 0.0, "implemented": False},

    # ---- Tier5: Sprigling時代の重み（2026-09-26新設。撃破70〜75体目） ----
    # ステータスが固定でない駒（Sprigling）を前提に、「どんな駒をどう評価し、どう使うか」
    # という個性を作るためのノブ。配線は scoring_common.py の piece_value /
    # sprigling_place_bonus / sprigling_production_bonus（両ボット共通）。
    # 全て default=0.0 で従来の挙動と完全に同じ。
    # 耐久重視度: 駒の価値評価（efficiency）でHPをどれだけ重く見るか。0で従来式
    #   hp/100+atk/10（攻撃力1=HP10）。0.8で攻撃力1=HP約5.6（苔兵のHP/攻撃力比と同じ）。
    {"champion_index": 70, "tier": 5, "key": "hp_valuation_bonus", "label": "耐久重視度", "default": 0.0, "implemented": True},
    {"champion_index": 71, "tier": 5, "key": "sprigling_affinity", "label": "Sprigling重用度", "default": 0.0, "implemented": True},
    # 速度差活用度: 自分より遅い敵の隣を好み、速い敵の隣を避ける（先制・連撃ルール用）。
    {"champion_index": 72, "tier": 5, "key": "speed_edge_pref", "label": "速度差活用度", "default": 0.0, "implemented": True},
    # 重量級拠点守備志向: HPの高い駒ほどVP地点（星・天元）に置きたがる。
    {"champion_index": 73, "tier": 5, "key": "heavy_anchor_pref", "label": "重量級拠点守備志向", "default": 0.0, "implemented": True},
    # 数押し志向 / 精鋭志向: 動員コストの安い駒 / 高い駒を好む（苔兵の400RPが基準点）。
    {"champion_index": 74, "tier": 5, "key": "swarm_pref", "label": "数押し志向", "default": 0.0, "implemented": True},
    {"champion_index": 75, "tier": 5, "key": "elite_pref", "label": "精鋭志向", "default": 0.0, "implemented": True},
    # 耐久前線志向: HPの高い駒ほど敵の隣（前線）に立ちたがり、低い駒ほど避ける。
    {"champion_index": 76, "tier": 5, "key": "tank_frontline_pref", "label": "耐久前線志向", "default": 0.0, "implemented": True},
]

# 2026-09-26: Tier5新設前に作られたchampion_weights.json等のスナップショットは、
# これらのキーだけが欠けていても既定値(0.0=従来挙動)で補って読み込んでよい。
SPRIGLING_TIER_KEYS = frozenset(t["key"] for t in WEIGHT_TIERS if t["tier"] == 5)

TIER_WEIGHT_KEYS = tuple(t["key"] for t in WEIGHT_TIERS)
assert len(TIER_WEIGHT_KEYS) == 76, "Tier1-5は合計76種であること（2026-09-26: Tier5（Sprigling時代の重み）7種新設69→76。2026-08-25: RP関連3種をBASE_WEIGHTSへ昇格したため60→57。2026-09-02: base_hp_panic_thresholdをBASE_WEIGHTSへ昇格したため57→56。2026-09-04: rush_opening_pressure新設（Tier4）56→57。2026-09-09: AIビルドモード向け新規重み12種新設（opening_tempo_pref/comeback_desperation_pref/tempo_loss_aversion/mirror_match_awareness/first_kill_momentum/signature_unit_affinity×5/flourish_tiebreak_pref/endgame_clock_bluff）57→69。詳細はanalyze_tuning_run_handoff.md 3.20節参照）"
assert len(set(TIER_WEIGHT_KEYS) & set(BASE_WEIGHT_KEYS)) == 0, "初期解放セットとキー名が重複していないこと"
assert sorted(t["champion_index"] for t in WEIGHT_TIERS) == list(range(1, 77)), \
    "champion_indexは1〜76が過不足なく1つずつ割り当てられていること（2026-09-26: Tier5の7種新設により1〜69→1〜76。2026-09-04: rush_opening_pressure新設により1〜56→1〜57。2026-09-09: 12種新設により1〜57→1〜69）"

WEIGHT_TIER_BY_KEY = {t["key"]: t for t in WEIGHT_TIERS}


def get_unlocked_weight_keys(defeated_champion_count):
    """撃破済みチャンピオン数から、現在解放されている重みキーの集合を返す
    （初期20種は常に含む）。"""
    unlocked = set(BASE_WEIGHT_KEYS)
    for t in WEIGHT_TIERS:
        if t["champion_index"] <= defeated_champion_count:
            unlocked.add(t["key"])
    return unlocked


# ============================================================
# HEURISTIC_WEIGHTS: 実体（81キー全て）。エンジン側は常に全キーが埋まった
# 1つの辞書だけを参照する（未解放/解放済みという概念はUI側だけが持ち、
# エンジンには持ち込まない。未解放キーはdefault=0.0でスコアに無影響）。
# ============================================================
HEURISTIC_WEIGHTS = {}
HEURISTIC_WEIGHTS.update(copy.deepcopy(BASE_WEIGHTS))
HEURISTIC_WEIGHTS.update(copy.deepcopy(ENGINE_ONLY_WEIGHTS))
for _t in WEIGHT_TIERS:
    HEURISTIC_WEIGHTS[_t["key"]] = _t["default"]

assert len(HEURISTIC_WEIGHTS) == 27 + 76 + len(ENGINE_ONLY_WEIGHTS), \
    "HEURISTIC_WEIGHTSは 27(初期) + 76(Tier1-5) + engine_only の合計であること（2026-09-26: Tier5新設69→76。2026-09-02: base_hp_panic_thresholdをTier4からBASE_WEIGHTSへ昇格したため23+57→24+56に更新。同日試験導入したbase_approach_defense_priorityは実測で悪化が確認されたため撤回済み。2026-09-04: rp_income_margin新設24→25、base_pressure_ramp_rounds新設25→26。あわせてTier4にrush_opening_pressure新設56→57。2026-09-09: advance_ramp_rounds新設26→27、Tier4にAIビルドモード向け新規重み12種新設57→69）"


# ============================================================
# champion_weights.json 自動同期（2026-08-28復旧）
# ------------------------------------------------------------
# 旧config.py時代（0.60節・9章-5）に存在した_sync_champion_weights_from_file()の
# 再実装。0.69節でHEURISTIC_WEIGHTSの実体をconfig.py→weights.pyへ移設した際に
# この関数だけ引き継がれておらず、tune_balance_search.pyが書き出す
# champion_weights.jsonが以後ずっとweights.pyへ反映されない状態になっていた
# （4.31/4.34節と同型の「手動貼り付け忘れ」事故の再発。本来は0.60節で
# 恒久対応したはずのクラスの問題）。
#
# 挙動: このファイルと同じディレクトリのchampion_weights.jsonを読み、
# "weights"のキー集合がHEURISTIC_WEIGHTSと完全一致する場合のみ同期する
# （キー構成が古い/新しいスキーマの場合は黙って無視し、辞書リテラルの値の
# ままにする。中途半端な部分適用はしない）。ファイル不在・壊れている・
# 読み込み中の例外は全て握りつぶしてリテラル値へフォールバックするため、
# champion_weights.jsonを持たない配布環境・テスト環境でも壊れない。
# 同期した/できなかった場合はいずれもprintで明示する。
# _BASELINE_WEIGHTSのキャプチャより前に実行するため、reset_weights()/
# apply_weight_overrides()/resolve_weights()の基準点も自動的にチャンピオン値になる。
# ============================================================
def _sync_champion_weights_from_file(path=None):
    import json
    import os

    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "champion_weights.json")

    try:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        champion_weights = data["weights"]
    except Exception as exc:  # noqa: BLE001 - 意図的に広く握りつぶす（配布環境で壊れないため）
        print(f"[weights] champion_weights.json の読み込みに失敗したため、"
              f"辞書リテラルの初期値のままにします（同期スキップ）: {exc}")
        return

    current_keys = set(HEURISTIC_WEIGHTS)
    champion_keys = set(champion_weights)
    # Tier5新設前のファイルは、Tier5のキーだけが欠けている場合に限り既定値で補って同期する
    if champion_keys < current_keys and (current_keys - champion_keys) <= SPRIGLING_TIER_KEYS:
        champion_weights = dict(champion_weights)
        for k in current_keys - champion_keys:
            champion_weights[k] = HEURISTIC_WEIGHTS[k]
        champion_keys = set(champion_weights)
    if champion_keys != current_keys:
        missing = current_keys - champion_keys
        extra = champion_keys - current_keys
        print(f"[weights] champion_weights.json のキー集合がHEURISTIC_WEIGHTSと"
              f"一致しないため同期をスキップしました（不足: {sorted(missing)}, "
              f"余分: {sorted(extra)}）。champion_weights.jsonが古い/新しい"
              f"スキーマの可能性があります。tune_balance_search.pyを再実行して"
              f"作り直すことを推奨します。")
        return

    HEURISTIC_WEIGHTS.update(champion_weights)
    print(f"[weights] {path} の重み（source={data.get('source', '不明')}, "
          f"generation={data.get('generation', '不明')}）をHEURISTIC_WEIGHTSへ"
          f"同期しました。")


_sync_champion_weights_from_file()

_BASELINE_WEIGHTS = copy.deepcopy(HEURISTIC_WEIGHTS)


def reset_weights():
    """HEURISTIC_WEIGHTSを初期値に戻す（その場書き換え）。"""
    HEURISTIC_WEIGHTS.clear()
    HEURISTIC_WEIGHTS.update(copy.deepcopy(_BASELINE_WEIGHTS))


def apply_weight_overrides(overrides):
    """HEURISTIC_WEIGHTSへの上書きをベースラインから再適用する。
    overrides: {"exposure": 2.0, "infantry_place_pref": 0.3, ...}
    （符号統一方針により、PENALTY_WEIGHT_KEYSも含めて常に0以上の値で渡すこと）"""
    reset_weights()
    if overrides:
        unknown = set(overrides) - set(HEURISTIC_WEIGHTS)
        if unknown:
            raise KeyError(f"未知の重みキー: {sorted(unknown)}")
        _validate_no_negative_penalty_values(overrides)
        HEURISTIC_WEIGHTS.update(overrides)


def resolve_weights(overrides=None):
    """グローバルを書き換えずに「ベースライン+overrides」の新しい辞書を返す
    （candidate/championを同時保持するチャンピオン制検証で使う）。
    戻り値はプレイヤー向け表記（PENALTY_WEIGHT_KEYSも含め常に0以上）のまま。
    エンジンの評価式にそのまま渡す直前にto_engine_signed()を通すこと。
    overridesに未知のキー（現在のHEURISTIC_WEIGHTSに存在しないキー）が含まれる
    場合はKeyErrorで弾く厳格版。意図的な少数キーの上書き指定（tune_balance_search.py
    等）でのタイプミスにすぐ気付けるようにするための挙動なので、この厳格さは
    変えないこと。過去の世代の「フルスナップショット」を読み込む用途には
    下のresolve_opponent_weights()を使うこと（用途が違う）。"""
    weights = copy.deepcopy(_BASELINE_WEIGHTS)
    if overrides:
        unknown = set(overrides) - set(weights)
        if unknown:
            raise KeyError(f"未知の重みキー: {sorted(unknown)}")
        _validate_no_negative_penalty_values(overrides)
        weights.update(overrides)
    return weights


def resolve_opponent_weights(saved_weights):
    """2026-09-12追加。champion_history/gen_XXXX.jsonのような「過去のある
    時点のHEURISTIC_WEIGHTS全体のスナップショット」を、現在のHEURISTIC_WEIGHTS
    （その後の開発でキーが増えたり減ったりしている可能性がある）に安全に
    読み込むための緩い合成。

    背景（2026-09-12報告の不具合）: search_bot_skeleton.SearchBot/
    heuristic_bot.HeuristicBotはweights引数を「完全な辞書」として無条件に
    そのまま使っていた（to_engine_signed(weights)を通すだけ）。そのため
    play_vs_aiの対戦相手選択で古い世代（例: signature_affinity系やTier4の
    新規重みが存在しなかった時点のgen_XXXX.json）を選ぶと、その後の開発で
    新設された重みキーへのアクセス（例: self.w["infantry_signature_affinity"]）
    が単純にKeyErrorになり、その世代を選んだ対局だけが必ず失敗していた
    （「新しい重みを追加した最近の世代なら発生しない・古い世代ほど発生する」
    という報告と一致する）。

    resolve_weights()（上）は「未知キーがあればKeyErrorで弾く」厳格版だが、
    これは意図的な少数上書き指定のタイプミス検出用であり、正反対の要件を持つ
    「過去のフルスナップショットの読み込み」には使えない（廃止済みキーが
    1つでも残っていると弾かれてしまう）。この関数はその逆で、常に成功する
    ことを優先する:
      - saved_weightsに無いキー（保存後に新設された重み）は、現在のデフォルト
        （HEURISTIC_WEIGHTS）の値で補う。
      - saved_weightsにあるが現在は存在しないキー（廃止された重み）は無視する。
    """
    merged = copy.deepcopy(HEURISTIC_WEIGHTS)
    if saved_weights:
        for key, value in saved_weights.items():
            if key in merged:
                merged[key] = value
    return merged


# ============================================================
# tune_balance_search.py の自動検証対象34種（BASE_WEIGHTS全24種＋支配戦略化
# リスクが高い領域＝三すくみ・キル計算・新メカニクス・VP経済・本拠パニック
# から選んだTier1/2の10種）。残りは局所的な影響のため対象外。
# ============================================================
SEARCH_SPACE_KEYS = tuple(BASE_WEIGHT_KEYS) + (
    "favorable_matchup_bonus",
    "unfavorable_matchup_penalty",
    "finishing_blow_bonus",
    "vp_spot_guard_bonus",
    "rp_spot_guard_bonus",
    "midgame_spot_guard_bonus",
    "base_proximity_alert",
    "center_control",
    "corner_edge_avoidance",
    "archer_immunity_awareness",
)
assert len(SEARCH_SPACE_KEYS) == 37, "検証対象は37種であること（2026-08-30: rp_hoarding_pref/rp_overflow_avoidance廃止34→32、placement_priority_bonus新設32→33、early_engineer_bootstrap_pref廃止33→32。2026-08-31: production_diversity_pref新設（BASE_WEIGHTS昇格に伴い自動的に含まれる）32→33。2026-09-02: base_hp_panic_threshold新設（BASE_WEIGHTS昇格に伴い自動的に含まれる）33→34。base_approach_defense_priorityは同日試験導入後、実測で悪化が確認されたため撤回済み。2026-09-04: rp_income_margin新設34→35、base_pressure_ramp_rounds新設35→36（いずれもBASE_WEIGHTS昇格に伴い自動的に含まれる）。rush_opening_pressureはTier4止まりで意図的にSEARCH_SPACE_KEYSへ含めていない（Optunaの既定バランス探索対象ではなく、プレイヤーが任意に選ぶ尖ったスタイル用の重みのため）。2026-09-09: advance_ramp_rounds新設36→37（BASE_WEIGHTS昇格に伴い自動的に含まれる）。3.20節の新規重み12種は全てTier4止まりで、rush_opening_pressureと同じ理由でSEARCH_SPACE_KEYSへは含めていない（tune_balance_search.pyは本セッションに添付されておらずWEIGHT_SEARCH_SPACE側は未反映。次回反映すること）"
assert len(set(SEARCH_SPACE_KEYS)) == 37, "検証対象に重複がないこと"
assert all(k in HEURISTIC_WEIGHTS for k in SEARCH_SPACE_KEYS), \
    "検証対象キーは全てHEURISTIC_WEIGHTSに存在すること"
