# -*- coding: utf-8 -*-
"""
重み一覧表（weight_reference.md）を生成するツール。

背景: weights.py（97キー。2026-09-09時点）は「解放条件（Tier/champion_index）」の一覧性は
あるが、「各重みが実際にどの条件で・どんな計算式でスコアに効くか」は
heuristic_bot.py / search_bot_skeleton.py / scoring_common.py 側のコードを
読まないと分からず、一目で把握する手段が無かった（2026-09-02、引き継ぎ資料の
やり取りで指摘）。本スクリプトはweights.pyのメタ情報（label・tier・
champion_index・implemented）と、このファイル内に手で書き起こした
「条件・計算式」の対訳表を突き合わせてweight_reference.mdを生成する。

新しい重みを追加・変更した場合は、下のDESCRIPTIONS辞書に説明を追記した上で
本スクリプトを再実行すること（説明の不足はASSERTで検出される）。
"""
import weights as W

# key -> (condition, formula) の対訳表。
# condition: どんな時にスコアへ影響するか。formula: 具体的な計算式（変数名は
# 該当コード中の変数名にできるだけ合わせてある）。
# 実装済み(implemented=True or BASE_WEIGHTS所属)の重みは全てここに実データを
# 書く。未実装(implemented=False)のものは一律 (None, None) とし、生成時に
# 「未実装（プレースホルダ、スコアに一切影響しない）」と表示する。
DESCRIPTIONS = {
    # ---------------- BASE_WEIGHTS（26種。初期解放・常時編集可） ----------------
    "advance": (
        "駒をmoveまたはplaceした結果、敵本拠までの距離が縮む場合",
        "score += advance * adv_mult　(placeの場合は advance*0.2*(board_size-dist)*adv_mult。0.85節で式が変更されており、"
        "moveとplaceで係数が異なる点に注意。adv_multはadvance_ramp_rounds由来の立ち上がり倍率、2026-09-09追加)",
    ),
    "advance_ramp_rounds": (
        "2026-09-09新設。advanceが実際にスコアへ乗る全箇所（両botの_score_actions）で、"
        "game.round_numberを見る度に毎回",
        "adv_mult = _base_pressure_ramp_multiplier(round_number, advance_ramp_rounds)　"
        "→ advanceの直前でこの倍率を掛ける。既定0(=無効・従来通り常にadvanceがフルで乗る)、"
        "現行チャンピオン値は5（base_pressure_ramp_roundsと同じ関数・同じ考え方を流用。"
        "advanceがbase_pressureと違ってrampを持たず常にフル値が乗っていたため、tune_balance_"
        "search.pyのレポートでadvance自体がレンジ下限(0.2、レンジ内位置2.12%)に張り付き"
        "続ける問題が観測された。詳細はanalyze_tuning_run_handoff.md 3.19節参照）",
    ),
    "attack": (
        "move/placeでその位置に置いた場合の潜在攻撃力(_potential_attack)を評価する時。本拠へのダメージ分(base_dmg)は除く",
        "score += (dmg - base_dmg) * attack",
    ),
    "base_defense": (
        "自陣本拠が今まさに受けている/受ける見込みの脅威(_own_base_threat / _hypothetical_own_base_threat)を評価する時。"
        "常時有効（base_hp_panic_thresholdにより本拠HPが危険水域に入るとさらに倍率がかかる）",
        "score += base_threat * base_defense * panic　(panicは1.0以上。base_hp_panic_threshold参照)",
    ),
    "base_hp_panic_threshold": (
        "自陣本拠HPが割合BASE_HP_PANIC_ZONE_RATIO(=0.8。2026-09-02(2)に0.5→0.8へ引き上げ)を下回った時のみ。"
        "weight<=0なら常時無効",
        "severity=(0.8-hp_ratio)/0.8 (0〜1)　panic = 1 + severity*weight　"
        "→ base_defense項に乗算される倍率になる（weight=10・hp比40%ならpanic=6.0）",
    ),
    "base_hp_weight": (
        "SearchBotのevaluate_state（探索の葉評価）でのみ使用。HeuristicBotの_score_actions（1手先読みの簡易評価）では使われない",
        "score += sign * base.hp * base_hp_weight　(自陣本拠ならsign=+1、敵陣本拠ならsign=-1)",
    ),
    "base_pressure": (
        "move/placeでその位置の潜在攻撃力に敵本拠への直接ダメージ分(base_dmg)が含まれる時。"
        "実効値にはbase_pressure_ramp_rounds由来の立ち上がり倍率(bp_mult)がさらに掛かる（2026-09-04）",
        "score += base_dmg * base_pressure * bp_mult　(本拠特攻への過度な誘引を抑えるため、attackより低めに据え置く設計。"
        "bp_multの算出はbase_pressure_ramp_rounds/rush_opening_pressureの項参照)",
    ),
    "base_pressure_ramp_rounds": (
        "2026-09-04新設。base_pressureが実際にスコアへ乗る全箇所（_position_hold_value経由・"
        "各botの1手先読みインライン式）で、game.round_numberを見る度に毎回",
        "bp_mult = 0 (ramp_rounds<=0なら常に1.0) / "
        "round_number>=ramp_roundsなら1.0 / それ未満はmax(0, round_number/ramp_rounds)　"
        "→ base_pressureの直前でこの倍率を掛ける。既定0(=無効)、現行チャンピオン値は5"
        "（『ラウンド1の丸裸の本拠を攻める』ことと『ラウンド20で消耗した本拠にとどめを刺す』ことを"
        "同じ強さで評価してしまう問題への構造的対策。rp_income_marginのような加点による対症療法は"
        "実測で機能しなかったため撤回し、本重みに置き換えた。詳細はscoring_common.py "
        "_base_pressure_ramp_multiplierのコメント参照）",
    ),
    "base_threat_counter_priority": (
        "生産(choose_production)時、自陣本拠が現在脅かされている(_own_base_threat>0)場合のみ",
        "score += base_threat_counter_priority * urgency * (0.3 if 既にその駒種を保有 else 1.0)　"
        "（相性で優位に立てる駒種の生産を優先する）",
    ),
    "danger": (
        "move/placeでその位置に隣接する敵駒の数(enemy)を評価する時。常時有効",
        "score += enemy * danger",
    ),
    "efficiency": (
        "生産(choose_production)時、駒種ごとのコスト効率を評価する時。常時有効",
        "score += (hp/100 + atk/10) / max(cost, 1) * efficiency",
    ),
    "engineer_econ_bonus": (
        "工兵をRPスポット/中盤スポットへplace・moveした時（スポット占有による経済効果を重視）",
        "score += engineer_econ_bonus　(スポット到達時に固定加算)",
    ),
    "exposure": (
        "move/placeでその位置に置いた場合の被露出リスク(_new_placement_threat)を評価する時。常時有効",
        "score += threat * exposure",
    ),
    "incoming_damage": (
        "move/placeでその位置における被ダメージ見込み(_potential_incoming_damage)を評価する時。常時有効",
        "score += incoming * incoming_damage",
    ),
    "kill_bonus": (
        "move/placeの結果、敵駒を撃破できる見込みがある時",
        "score += kills * kill_bonus",
    ),
    "midgame_spot": (
        "駒を中盤スポット(midgame_spots)へplace/moveした時",
        "score += midgame_spot　(固定加算。さらに敵の隣接数×midgame_spot_guard_bonusが加わる)",
    ),
    "placement_priority_bonus": (
        "そもそも駒をplaceする(盤上に新規投入する)行動を評価する時。常時有効（move/passには乗らない）",
        "score += placement_priority_bonus　(placeという行動種別そのものへの固定加点。配置を後回しにしすぎる問題への対策として導入)",
    ),
    "production_diversity_pref": (
        "生産(choose_production)時、保有駒の構成が三すくみ的に偏っている場合の補正",
        "score += composition_edge * production_diversity_pref",
    ),
    "rp_cost": (
        "駒をplaceする時のコスト(cfg['cost'])、またはmoveの移動コスト(move_action_cost)を評価する時",
        "score += cost * rp_cost　(placeはcfg['cost']、moveはCONFIG['economy']['move_action_cost']を使う)",
    ),
    "rp_differential": (
        "SearchBotのevaluate_state（探索の葉評価）でのみ使用。自陣と敵陣のRP差を評価する",
        "score += (my_rp - opp_rp) * rp_differential",
    ),
    "rp_income_margin": (
        "2026-09-04新設。move/placeの結果を仮に盤面へ反映した上で"
        "_estimate_next_round_income()（RPスポット・中盤スポットの保有状況からの来期RP収入見積もり）"
        "を自分・相手それぞれについて計算する時。常時有効（passでも現状の値をそのまま評価）",
        "score += (my_next_income - opp_next_income) * rp_income_margin　"
        "(HeuristicBot: _hypothetical_next_round_income_marginで移動元の削除/移動先or配置先の仮挿入込みで計算。"
        "SearchBot: evaluate_stateのみで使用。単体では『序盤の本拠特攻』を淘汰するには実測上桁が"
        "足りず、base_pressure_ramp_roundsと併用する設計に変更した)",
    ),
    "rp_spot": (
        "駒をRPスポット(rp_spots)へplace/moveした時",
        "score += rp_spot　(固定加算。さらに敵の隣接数×rp_spot_guard_bonusが加わる)",
    ),
    "rp_spot_kill_bonus": (
        "move/placeの結果、RPスポット上の敵駒を撃破できる見込みがある時",
        "score += rp_spot_kills * rp_spot_kill_bonus",
    ),
    "support": (
        "move/placeでその位置に隣接する味方駒の数(friendly)を評価する時。常時有効",
        "score += friendly * support",
    ),
    "vp_spot_kill_bonus": (
        "move/placeの結果、VPスポット(星・天元)上の敵駒を撃破できる見込みがある時",
        "score += vp_spot_kills * vp_spot_kill_bonus",
    ),
    "vp_star": (
        "駒をVP星スポットへplace/moveした時",
        "score += vp_star　(固定加算。さらに敵の隣接数×vp_spot_guard_bonusが加わる)",
    ),
    "vp_tengen": (
        "駒をVP天元スポットへplace/moveした時",
        "score += vp_tengen　(固定加算。さらに敵の隣接数×vp_spot_guard_bonusが加わる)",
    ),

    # ---------------- Tier1（15種。撃破1〜15体目で解放。全て実装済み） ----------------
    "infantry_place_pref": ("歩兵をplaceする時", "score += infantry_place_pref"),
    "cavalry_place_pref": ("騎兵をplaceする時", "score += cavalry_place_pref"),
    "heavy_place_pref": ("重装兵をplaceする時", "score += heavy_place_pref"),
    "archer_place_pref": ("弓兵をplaceする時", "score += archer_place_pref"),
    "engineer_place_pref": ("工兵をplaceする時", "score += engineer_place_pref"),
    "infantry_produce_pref": ("生産候補に歩兵を含める時", "score += infantry_produce_pref（駒種ごとのplace_pref値の0.5倍がmoveにも乗る点と対で運用）"),
    "cavalry_produce_pref": ("生産候補に騎兵を含める時", "score += cavalry_produce_pref"),
    "heavy_produce_pref": ("生産候補に重装兵を含める時", "score += heavy_produce_pref"),
    "archer_produce_pref": ("生産候補に弓兵を含める時", "score += archer_produce_pref"),
    "engineer_produce_pref": ("生産候補に工兵を含める時", "score += engineer_produce_pref"),
    "cavalry_overextend_tolerance": (
        "騎兵をmoveし、その結果敵本拠までの距離が縮み、かつ移動先が盤面の敵陣側半分(board_size//2未満)まで前進した時",
        "score += cavalry_overextend_tolerance　(advanceに加えて追加加点。前のめりな騎兵単騎侵攻をどれだけ許容するか)",
    ),
    "heavy_frontline_bonus": (
        "重装兵をplace/moveし、その位置に味方が2体以上隣接している(friendly>=2)時",
        "score += heavy_frontline_bonus",
    ),
    "archer_kiting_pref": (
        "弓兵をmoveし、①移動後の方が最も近い敵駒との距離が離れ(new_nearest>old_nearest)、②それでも潜在攻撃力(dmg)が0より大きい（＝射程内を維持できている）時",
        "score += archer_kiting_pref　(距離を取りながら攻撃するキッティング機動を評価)",
    ),
    "engineer_econ_solo_bonus": (
        "工兵をmoveし、目的地がVP/RP/中盤スポットのいずれでもない（純粋な経済目的の単独移動）時",
        "score += friendly * engineer_econ_solo_bonus",
    ),
    "engineer_shield_bonus": (
        "工兵をplace/moveし、その位置に敵の弓兵が隣接している（工兵は弓兵の攻撃を無効化できるため、弓兵の的として機能する）時",
        "score += shield_targets * engineer_shield_bonus",
    ),

    # ---------------- Tier2（20種。撃破16〜35体目で解放。うち10種実装済み） ----------------
    "favorable_matchup_bonus": (
        "move/placeの結果、三すくみで有利な相手(fav)が隣接する時",
        "score += fav * favorable_matchup_bonus",
    ),
    "unfavorable_matchup_penalty": (
        "move/placeの結果、三すくみで不利な相手(unfav)が隣接する時",
        "score -= unfav * unfavorable_matchup_penalty",
    ),
    "archer_immunity_awareness": (
        "弓兵をmove/placeし、その位置に敵の工兵(弓兵の攻撃を無効化できる駒種)が隣接している時",
        "score -= nullifiers * archer_immunity_awareness",
    ),
    "trade_ratio_threshold": (None, None),
    "retreat_hp_threshold": (None, None),
    "cluster_bonus": (None, None),
    "overextension_penalty": (None, None),
    "center_control": (
        "move/placeで盤面中央への距離を評価する時。常時有効",
        "score += (board_size - dist_to_center) * center_control * 0.1",
    ),
    "corner_edge_avoidance": (
        "move/placeの結果、隅から2マス以内(corner_dist<=2)に位置する時",
        "score -= (3 - corner_dist) * corner_edge_avoidance",
    ),
    "liberty_defense_awareness": (None, None),
    "recapture_ban_exploit": (None, None),
    "finishing_blow_bonus": (
        "move/placeの結果、敵駒を撃破できる見込みがある時（kill_bonusと重複加算される、いわば『撃破』への二重の重み）",
        "score += kills * finishing_blow_bonus",
    ),
    "vp_spot_guard_bonus": (
        "駒をVP星/天元スポットへplace/moveした時、そのスポットに隣接する敵駒の数(enemy)に応じて",
        "score += enemy * vp_spot_guard_bonus",
    ),
    "rp_spot_guard_bonus": (
        "駒をRPスポットへplace/moveした時、そのスポットに隣接する敵駒の数(enemy)に応じて",
        "score += enemy * rp_spot_guard_bonus",
    ),
    "midgame_spot_guard_bonus": (
        "駒を中盤スポットへplace/moveした時、そのスポットに隣接する敵駒の数(enemy)に応じて",
        "score += enemy * midgame_spot_guard_bonus",
    ),
    "base_proximity_alert": (
        "move/placeの結果、自陣本拠から2マス以内(base_dist<=2)に位置する時。常時有効（敵の接近有無に関わらず一定の在宅志向）",
        "score += (3 - base_dist) * base_proximity_alert",
    ),
    "position_hold_coefficient": (None, None),
    "new_placement_exposure_sensitivity": (None, None),
    "opponent_next_best_awareness": (None, None),
    "mutual_destruction_tolerance": (None, None),

    # ---------------- Tier3（12種。撃破36〜47体目、全て未実装） ----------------
    "early_rp_spot_priority": (None, None),
    "late_rp_spot_priority": (None, None),
    "early_vp_indifference": (None, None),
    "late_vp_urgency": (None, None),
    "midgame_spot_timing_aggression": (None, None),
    "endgame_clock_pressure": (None, None),
    "first_move_aggression": (None, None),
    "second_move_aggression": (None, None),
    "rp_differential_asymmetry": (None, None),
    "vp_lead_conservatism": (None, None),
    "vp_deficit_desperation": (None, None),
    "move_cost_dynamic_sensitivity": (None, None),

    # ---------------- Tier4（10種。撃破48〜57体目、うち1種実装済み） ----------------
    "enemy_base_finish_urgency": (None, None),
    "randomness_dial": (None, None),
    "bluff_tendency": (None, None),
    "opponent_adaptation_speed": (None, None),
    "second_move_compensation": (None, None),
    "pass_tolerance": (None, None),
    "efficiency_hp_atk_balance": (None, None),
    "danger_range_decay": (None, None),
    "capture_delay_lookahead": (None, None),

    # 2026-09-04新設。rush_killアーキタイプ自体を選択肢から消すのではなく、
    # 意図的に序盤の本拠特攻を選ぶ尖ったビルド向けの上級者専用ノブ（Tier4）。
    "rush_opening_pressure": (
        "base_pressure_ramp_roundsによる立ち上がり抑制の期間中（round_number < ramp_rounds）のみ。"
        "抑制期間が終わった後（round_number >= ramp_rounds）は一切影響しない",
        "bp_mult = max(0, round_number/ramp_rounds) + rush_opening_pressure　"
        "(1.0でramp分をちょうど打ち消して従来のbase_pressureへ復元、1.0超でramp前より強い特攻を選べる。"
        "SEARCH_SPACE_KEYSには含めず、Optunaの既定バランス探索対象外＝プレイヤーが任意に選ぶスタイル重み)",
    ),

    # ---- 2026-09-09新設: AIビルドモード向け新規重み8案（analyze_tuning_run_handoff.md
    # 3.20節）。champion_index 58〜69。rush_opening_pressureと同じくSEARCH_SPACE_KEYS
    # には含めない（プレイヤーが任意に選ぶスタイル重みのため）。----
    "opening_tempo_pref": (
        "序盤（round_number < OPENING_TEMPO_ROUNDS=5、scoring_common.py参照）のaction評価時。"
        "両botの_score_actions（浅い候補評価）に加え、search_bot_skeleton.pyのevaluate_state"
        "（探索の葉評価）・_ProductionEvalContext.best_value_for_placements（生産→即配置の"
        "差分評価）にも配線済み（3.19節で確認した『ラウンド1はpassが全placeより高評価になる』"
        "縮退への直接対策のため、深い探索の最終値そのものに効かせる必要があった）",
        "tempo_mult = max(0, 1-(round_number-1)/5)　"
        "_score_actions: pass側 score -= tempo_mult*opening_tempo_pref*3、"
        "RPスポットへのplace/move側 score += tempo_mult*opening_tempo_pref*2　"
        "evaluate_state: score += (own_rp_spots_owned - opp_rp_spots_owned)*tempo_mult*opening_tempo_pref",
    ),
    "comeback_desperation_pref": (
        "VPで劣勢（相手cumulative_vpが自分より大きい）×ラウンドが進んでいるほど。"
        "両botの_score_actionsのみ（advance/rush_opening_pressureと同じ『行動選好』レイヤー限定、"
        "evaluate_state等の状態評価レイヤーへの配線は見送った）",
        "frac = min(1, vp_deficit/100) * min(1, round_number/turn_limit)　(0〜1)　"
        "boldness = frac * comeback_desperation_pref　"
        "place/move: score += boldness*enemy + boldness*kills*2　pass: score -= frac*comeback_desperation_pref*3",
    ),
    "tempo_loss_aversion": (
        "move actionが「敵本拠に近づかず・与ダメージも無く・スポット(vp/rp/midgame)へも向かわない」場合のみ"
        "（opening_tempo_prefの中盤以降版）。両botの_score_actions",
        "score -= tempo_loss_aversion",
    ),
    "mirror_match_awareness": (None, None),
    "first_kill_momentum": (
        "自分がgame.total_kills[player]>0（対局中に1体でも撃破済み）の場合、place/moveの"
        "action評価時。両botの_score_actions。game.py Game.total_kills（2026-09-09新設、"
        "run()のremoved処理で更新）を参照。search_bot_skeleton.pyのclone_game/"
        "resolve_round_end（探索内の仮想対局用の独立実装）も同じ値を引き継ぐよう修正済み",
        "score += first_kill_momentum*(dmg-base_dmg) + first_kill_momentum*kills*2",
    ),
    "infantry_signature_affinity": (
        "evaluate_state（自軍/敵軍の歩兵の数、盤上+reserve合算）・choose_production系"
        "（生産候補の歩兵に対して、production_diversity_prefと同じ場所に配線）",
        "evaluate_state: score += own_infantry_count*infantry_signature_affinity "
        "- opp_infantry_count*infantry_signature_affinity　"
        "choose_production(HeuristicBot): score += infantry_signature_affinity　"
        "（他4駒種も同型。scoring_common.SIGNATURE_AFFINITY_KEY参照）",
    ),
    "cavalry_signature_affinity": (
        "infantry_signature_affinityと同型（対象駒種が騎兵）", "infantry_signature_affinity参照（対象駒種のみ異なる）",
    ),
    "heavy_signature_affinity": (
        "infantry_signature_affinityと同型（対象駒種が重装兵）", "infantry_signature_affinity参照（対象駒種のみ異なる）",
    ),
    "archer_signature_affinity": (
        "infantry_signature_affinityと同型（対象駒種が弓兵）", "infantry_signature_affinity参照（対象駒種のみ異なる）",
    ),
    "engineer_signature_affinity": (
        "infantry_signature_affinityと同型（対象駒種が工兵）", "infantry_signature_affinity参照（対象駒種のみ異なる）",
    ),
    "flourish_tiebreak_pref": (
        "最終的な候補選択の時点で、最良スコア（SearchBotは1e-6以内の実質同点）の候補が複数ある場合のみ。"
        "HeuristicBot.choose_with_debug／SearchBot._select_best。scoreの計算そのものには一切加算しない"
        "ため、通常のプレイ強度・Optuna探索結果には実質影響しない設計",
        "同点候補のうち (kills+vp_spot_kills+rp_spot_kills) が最大のものだけに絞り込んでから、"
        "従来通りrandom/jitter選択を行う（絞り込み後の候補が全てflourish値0なら絞り込み自体を行わない）",
    ),
    "endgame_clock_bluff": (None, None),

    # ---------------- ENGINE_ONLY（1種。プレイヤー編集対象外） ----------------
    "jitter": (
        "全てのaction候補のスコアを計算した最後に、常時",
        "score += uniform(-jitter, +jitter)　(同点候補が並んだ時に手をばらけさせるための微小ノイズ。エンジン内部固定値でプレイヤーは編集不可)",
    ),
}


def build_rows():
    rows = []
    for key in W.BASE_WEIGHT_KEYS:
        rows.append({
            "key": key, "category": "BASE", "champion_index": "-", "label": "-",
            "implemented": True, "default": W.HEURISTIC_WEIGHTS[key],
        })
    for t in W.WEIGHT_TIERS:
        rows.append({
            "key": t["key"], "category": f"Tier{t['tier']}",
            "champion_index": t["champion_index"], "label": t["label"],
            "implemented": t["implemented"], "default": t["default"],
        })
    for key, val in W.ENGINE_ONLY_WEIGHTS.items():
        rows.append({
            "key": key, "category": "ENGINE_ONLY", "champion_index": "-", "label": "-",
            "implemented": True, "default": val,
        })
    return rows


def main():
    rows = build_rows()
    missing = [r["key"] for r in rows if r["key"] not in DESCRIPTIONS]
    assert not missing, f"DESCRIPTIONSに説明が無いキーがあります: {missing}"
    extra = [k for k in DESCRIPTIONS if k not in {r["key"] for r in rows}]
    assert not extra, f"weights.pyに存在しないキーがDESCRIPTIONSにあります: {extra}"

    lines = []
    lines.append("# 重み一覧表（weight_reference.md）")
    lines.append("")
    lines.append(f"weights.py 全{len(rows)}キーを自動抽出し、各重みの「発火条件」と「計算式」を"
                  "手動で対訳付けした一覧。生成元: gen_weight_reference.py。")
    lines.append("重みの追加・変更時はgen_weight_reference.py内のDESCRIPTIONSを更新して再生成すること"
                  "（説明の過不足はASSERTで検出される）。")
    lines.append("")
    lines.append(f"- 実装済み: {sum(1 for r in rows if r['implemented'])}種 / 未実装（プレースホルダ）: "
                  f"{sum(1 for r in rows if not r['implemented'])}種")
    lines.append("- 「未実装」は default=0.0 固定でスコア計算に一切関与しない（値を変更しても無意味）")
    lines.append("")

    order = ["BASE", "Tier1", "Tier2", "Tier3", "Tier4", "ENGINE_ONLY"]
    cat_titles = {
        "BASE": "## BASE_WEIGHTS（初期解放・常時編集可、27種）",
        "Tier1": "## Tier1（撃破1〜15体目で解放、15種・全実装済み）",
        "Tier2": "## Tier2（撃破16〜35体目で解放、20種・うち10種実装済み）",
        "Tier3": "## Tier3（撃破36〜47体目で解放、12種・全て未実装）",
        "Tier4": "## Tier4（撃破48〜69体目で解放、22種・うち11種実装済み）",
        "ENGINE_ONLY": "## ENGINE_ONLY（プレイヤー編集対象外、1種）",
    }
    for cat in order:
        cat_rows = [r for r in rows if r["category"] == cat]
        if not cat_rows:
            continue
        lines.append(cat_titles[cat])
        lines.append("")
        lines.append("| キー | champion_index | ラベル | 既定値 | 状態 | 発火条件 | 計算式 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in cat_rows:
            cond, formula = DESCRIPTIONS[r["key"]]
            if not r["implemented"]:
                cond_disp = "―（未実装）"
                formula_disp = "スコアに一切影響しない"
            else:
                cond_disp = cond.replace("|", "\\|")
                formula_disp = formula.replace("|", "\\|")
            default_disp = r["default"] if isinstance(r["default"], str) else f"{r['default']:.4g}" if isinstance(r["default"], float) else r["default"]
            lines.append(
                f"| `{r['key']}` | {r['champion_index']} | {r['label']} | {default_disp} | "
                f"{'実装済み' if r['implemented'] else '未実装'} | {cond_disp} | {formula_disp} |"
            )
        lines.append("")

    lines.append("## 補足")
    lines.append("")
    lines.append("- 「BASE」の重みはHEURISTIC_WEIGHTS内での既定値（世代53・trial_1130時点の検証済み値、"
                  "base_hp_panic_thresholdのみ2026-09-02にBASE_WEIGHTSへ昇格した際の再設定値）。")
    lines.append("- `_score_actions`（HeuristicBot / SearchBotの候補絞り込み）と`evaluate_state`（SearchBotの探索の葉評価）は"
                  "別の関数であり、重みによってはどちらか片方でしか使われないもの（`base_hp_weight`・`rp_differential`は"
                  "evaluate_stateのみ）がある点に注意。")
    lines.append("- 大半の重みはheuristic_bot.pyとsearch_bot_skeleton.pyの双方に同じ計算式が重複実装されているため、"
                  "計算式を変更する場合は両ファイルを直すこと。")

    with open("weight_reference.md", "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"weight_reference.md を生成しました（{len(rows)}行）")


if __name__ == "__main__":
    main()
