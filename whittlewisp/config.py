# config.py

# --- Experiment Flags ---
# ENABLE_PART_FATIGUE / fatigue_factorは削除(V35)。疲労の影響は
# 「攻撃疲労→calculate_miss_probability」「使用部位疲労→effective_reach」
# 「個別部位疲労→update_part_fatigue」の3箇所に分散済み。
ENABLE_RECOIL_DAMAGE = False        # V22: 部位疲労(ENABLE_PART_USAGE_FATIGUE)に置き換えるため既定OFF。比較検証用にコードは残す。

# --- 巻き添えダメージ(V35: インターフェースのみ準備。戦闘処理への接続は禁止) ---
# 攻撃に範囲を持たせ、範囲内の部位が同時にダメージを受ける(範囲外ほど減衰)構想の設定値。
# SplashCandidateやBattleSimulator._compute_splash_damage()に接続予定だが、
# 現時点ではどこからも呼び出されない(クラス・変数の骨組みのみ)。
ENABLE_SPLASH_DAMAGE = True
SPLASH_COEF = 0.2
SPLASH_MAX = 0.5
ENABLE_SPLASH_REACH_GATE = False              # True: 射程不足の薙ぎ払いは命中率0
ENABLE_SPLASH_BLOCKED_BY_INTIMIDATION = False # True: 威嚇状態の相手には巻き添え無効

# --- 威嚇(V35: インターフェースのみ準備。戦闘処理への接続は禁止) ---
# 威嚇が高いほど相手の攻撃力を下げる効果の構想値。Creature.is_intimidating()と
# intimidation_power_mult()の骨組みのみ用意し、まだダメージ計算には接続しない。
INTIMIDATION_POWER_MULT = 0.8  # 威嚇成立時、相手の攻撃力に将来乗じる予定の係数(仮値・未接続)

# --- Part Usage Fatigue (V22 提案) ---
# 部位反動(V9)を廃止し、これに置き換える提案。「同じ部位を振り続けると疲れて狙いが甘くなり、
# 伸びも悪くなる」という自然な現象として、命中率とReach(使用部位側のみ)を低下させる。
# 使わなかった部位は毎ターン回復する。ENABLE_RECOIL_DAMAGEとは独立フラグ。
# 検証時はRule1(一度に一要素)に従い、どちらか一方のみONにすること。
ENABLE_PART_USAGE_FATIGUE = True
PART_FATIGUE_GAIN_PER_USE = 1.2      # 1回攻撃に使うごとに蓄積する疲労量(仮値)
PART_FATIGUE_DECAY_PER_TURN = 0.25    # その部位を使わなかったターンの回復量(仮値)。V29: 呼吸(respiratory_factor)により
                                      # 実効値が下方修正される(下記RESP_*参照)。この値はrespiratory_factorが
                                      # 標準投資量の場合の「素の」回復量として扱う。
PART_FATIGUE_MAX = 5.0               # 疲労の上限(仮値、クランプ)
# DEPRECATED_UNUSED_since_V26: PART_FATIGUE_HIT_COEF = 3.0          # 疲労1につき命中率%から差し引く量(仮値)
PART_FATIGUE_REACH_COEF = 0.15       # 疲労1につきReach実効値から差し引く量(仮値、攻撃側部位のみに適用)
PART_REACH_FLOOR = 0.3               # Reach実効値の下限(疲労で際限なく下がらないためのクランプ、仮値)
# DEPRECATED_UNUSED_since_V35: PART_FATIGUE_GLOBAL_COEF (fatigue_factor削除に伴い廃止)

# --- Hit Rate Formula Model ---
# "ADDITIVE" (V19で確定) or "MULTIPLICATIVE" (V1-V18の旧式、比較用に残す)
HIT_RATE_FORMULA = "ADDITIVE"

# --- Intimidation ---
INTIMIDATION_ANGLE_THRESHOLD = 3.0  # 角度0~4のうちこの値以上で威嚇状態(V8で角度を絶対値化した際の基準)

# --- Hyperparameters (V19時点の確定値) ---
SIGMA = 0.8         # V33: target_part方式対応。狙った部位中心への収束を強化
REACH_K = 0.8
LEG_ELEV_COEF = 0.5
SIZE_K = 0.4        # V25新規。heavyアーキタイプのみ、atk_part.sizeがElevation差の許容を広げる
                     # (REACH_Kと対称構造。仮値・小さめから開始。効果不足/過剰は次サイクルで調整)
LEG_DUR_LEN_PENALTY = 2.0
RECOIL_COEF = 0.15
RECOIL_SIZE_FLOOR = 0.3
# DEPRECATED_UNUSED_since_V35: BULK_DAMPEN (問題①解決=weightを戦闘中固定化したため削除)

# --- Battle Format ---
CORE_HP = 100.0
TURN_LIMIT = 90

# --- Weight Class (V26で確定。ユーザー実測に基づく階級区切り) ---
# Weightの算出式自体はmodels.py参照(V26でlength*size*count*coefの乗算式に変更済み。
# V35でさらに、戦闘開始時点(全部位生存状態)のビルド重量として固定するよう変更)。
WEIGHT_CLASS_LIMITS = {
    "light": 140,
    "middle": 310,
    "heavy": 600,
}
# 過剰投資(一極集中)の上限＝所属階級の重量上限の40%。今回はまず中量級(310)で検証するため
# 125を使う(310*0.4=124を切り上げ)。他階級で検証する際はその階級の上限×0.4を使うこと。
OVERINVEST_RATIO = 0.4
PART_WEIGHT_CAP_FOR_TESTING = 125  # 中量級(310)での検証用。階級を変える場合は要再計算。

# --- V35新規: 階級上限(重量超過)ペナルティ ---
# 現在の検証がどの階級を基準にしているかを明示する変数。PART_WEIGHT_CAP_FOR_TESTINGの
# コメント通り、現状は中量級(310)を基準に検証しているため既定値は"middle"。
# 他階級で検証する場合はここを変更すること。
WEIGHT_TEST_CLASS = "middle"
# ビルドの総重量(Creature.weight)がWEIGHT_CLASS_LIMITS[WEIGHT_TEST_CLASS]を超過した場合、
# speed_factorに乗算するoverweight_factorの値(仮値)。
# 【注意】このコードベースのspeed_factorは「値が低いほど速い(=避けやすい)、
# 1.0に近いほど遅い(=避けにくい・当てやすい)」という向き(raw_speedが高いほど
# speed_factorが下がる式)。よって「勝ち目がないレベルまで素早さを下げる」には、
# speed_factorを0へ近づける小さい係数ではなく、1.0(最遅・最も当てやすいクランプ上限)へ
# 押し上げる大きい係数を乗じる必要がある。base(0.4?1.0)に対しどんな値でも
# 確実に1.0クランプへ到達するよう、十分大きい値(10.0)を設定する。
OVERWEIGHT_SPEED_MULT = 10.0

# --- Angle -> Radian変換(V26新規) ---
# 内部角度値(-2?+2)を、Elevation計算式(FK)で使うラジアンに変換する係数。仮値。
# 例: 角度+2で+60度相当まで傾く、という初期見積もり。要検証。
ANGLE_UNIT_RAD_DEG = 30.0  # 内部角度1につき何度傾くか(仮値)

# --- 着弾ズレ方式(V26新規、「命中率」概念の廃止に伴う空振り判定) ---
# 全体の完全空振り確率(miss_prob)の初期仮値。要検証。
# 防御側が速い(effective_speed_factorが低い)ほど、
# 攻撃側の攻撃部位が疲労しているほど狙いが甘くなり空振りしやすくなる。
MISS_PROB_BASE = 15.0
MISS_PROB_SPEED_COEF = 30.0      # (1 - defender.effective_speed_factor)に乗じる
MISS_PROB_FATIGUE_ATK_COEF = 3.0   # atk_part.fatigueに乗じる
MISS_PROB_FLOOR = 5.0
MISS_PROB_CEIL = 60.0

# --- V29新規: 目・耳による命中・回避補正 ---
# 目(eye)は攻撃側の照準精度(自分のmiss_probを下げる)、耳(ear)は防御側の察知精度
# (相手のmiss_probを上げる=避ける)を担当する。count=0にする代償を明確化するための実装。
EYE_SIZE_COEF = 0.3       # eyeのsize1につき視覚acuityへ加算する量(仮値)
EAR_SIZE_COEF = 0.3       # earのsize1につき聴覚acuityへ加算する量(仮値)
MISS_PROB_EYE_ATK_COEF = 2.0   # attacker.visual_acuity 1につきmiss_probから差し引く量(仮値)
MISS_PROB_EAR_DEF_COEF = 2.0   # defender.auditory_acuity 1につきmiss_probへ加算する量(仮値)

# 旧SWEEP_SIGMA_MULT: DEPRECATED_UNUSED_since_V29。
# 薙ぎ払いの拡散は「狙う高さ(Elevation軸)」ではなく「横方向(X軸)」で表現するのが物理的に
# 自然という結論のため、Elevation側のsigmaを広げる旧方式は廃止。X軸側のSIGMA_X_*へ役割移管。
SWEEP_SIGMA_MULT = 1.6  # 現在は参照されない(互換性のため定義のみ残す)。

# --- V35変更: 「範囲外への空振り」の基礎重み ---
# 旧AIM_MISS_BASE_WEIGHTは固定値だったため、部位破壊が進み候補の当たり判定重みが
# 縮小するほど、相対的に空振りが選ばれやすくなる(瀕死ほど狙いが逸れる)副作用があった。
# V35: 空振り重みを固定値ではなく、その時点の候補重み合計(sum(weights)=実際の命中可能
# 範囲の大きさ)に比例させる。破壊が進んでも「空振りとの相対比率」が変わらないようにする。
AIM_MISS_RATIO = 0.15  # sum(weights)に乗じる比率(仮値)
AIM_MISS_MIN = 0.5     # sum(weights)が極small・0の場合の下限(仮値)

# --- Archetype Reach Modifier (V28新規) ---
# 企画書§13.1「一撃集中(heavy)はReachが短い」をコード上でも成立させるための係数。
# 着弾ズレ計算(_impact_weights)でのみ使用し、ダメージ・耐久には影響しない。仮値。
ARCH_REACH_MULT_HEAVY = 0.5

# --- V29新規: リーチの威力伝達(ペナルティ型) ---
# 「しっかり届けば満額ダメージ、届いていなければダメージが伝わらない」を表現する。
# adj_dev(_impact_weights内で計算される、Elevationのズレの残差)を
# ダメージ倍率の減衰に使う。命中判定と威力判定を同じ物差しで貫通させる設計。
# 【重要】全アーキタイプに同一適用する(heavyも例外にしない。短リーチはheavyの明記された
# デメリットであり、優遇する理由がないため)。heavyの「当たれば重い」はARCH_MULT_HEAVYの
# 倍率側で表現する。
REACH_DAMAGE_PENALTY_COEF = 0.2  # adj_dev 1につきダメージ倍率をどれだけ削るか(仮値)

# --- V35新規: Reachによる高低差ダメージ減衰の緩和(問題②対応) ---
# 「基準距離(BASE_ATTACK_REACH)を超えた分だけ、Elevationのズレをダメージ計算上で吸収する」
# という命中率に影響しない威力専用のReach活用。V19?V34まで未接続だったeffective_reach()を
# ここで初めて着弾判定(ダメージ計算)に接続する。
# 命中判定(_impact_weights内のgauss_y)には一切影響させない(問題②の要求通り、
# Reachは「Elevation差による威力減衰の緩和」専用)。
# 初期値はBASE_REACH(models.py)の最頻値(neck/torso/arm/leg/tail=2.0)に合わせ、
# 「標準的なReachの部位ならボーナス・ペナルティどちらも受けない」基準点とする。
BASE_ATTACK_REACH = 2.0
print(
    "[config] BASE_ATTACK_REACH は既存configに定義が無かったため新規作成しました "
    f"(初期値={BASE_ATTACK_REACH})。models.BASE_REACHの最頻値(neck/torso/arm/leg/tail=2.0)に "
    "合わせた基準点で、この値を超えるReachの部位のみダメージ計算上のElevation差吸収(reach_surplus)を "
    "得ます。命中率(_impact_weights)には影響しません。"
)

# --- V29新規: 技アーキタイプ倍率(config化。旧models.py内ハードコード辞書から移設) ---
# 一撃集中は企画書§13.1の確定値(1.8倍)へ復帰。従前1.4はいつ・なぜ下げられたか記録がなく
# 経緯不明のため、reach_damage_multをheavyにも適用する代わりに正式な値へ戻す。
ARCH_MULT_THRUST = 1.0
ARCH_MULT_SWEEP = 0.7
ARCH_MULT_HEAVY = 1.8
# V36(技獲得システム移行): 打撃(Strike)は基準となる通常攻撃のため、thrustと同じ1.0とする。
ARCH_MULT_STRIKE = 0.9
# 【今回の検証で発見した既存バグの修正】models.Move.__init__が参照する"low"アーキタイプの
# 倍率がconfig.pyに未定義だったため、Creature生成時(get_available_moves→Move())に
# AttributeErrorで必ず落ちる状態だった。MOVE_ACQUISITION_TABLEには"low"archetypeを
# 生成する項目が無く実質未使用のため、他のアーキタイプに合わせた仮値(1.0)を追加しておく。
# 今回の未実装項目1〜5とは無関係の別件。
ARCH_MULT_LOW = 1.0

# --- V29新規: 呼吸(mouth/nose)による全身疲労回復への影響 ---
# 口・鼻のsize/countが「呼吸のしやすさ」を表し、疲労回復速度(decay)に直接影響する。
# mouth/noseを0にする行為に明確なデメリットを持たせるための実装(従来はほぼノーコストだった)。
RESP_SIZE_COEF = 0.3     # mouth/noseの各インスタンスのsize1につき呼吸能力へ加算する量(仮値)
RESP_BASELINE = 2.0      # 標準投資量(mouth1体+nose1体、size0)での呼吸能力を1.0とみなす基準値
RESP_FLOOR_RATIO = 0.3   # mouth/noseを完全に失った場合でも回復速度が0にはならない下限比率(仮値)

# --- V29新規: 部位の配置(スロット)・横方向(X軸)命中モデル ---
# 企画書§6.5「二層構造」の第一段階として、複数インスタンスを中心線(x=0)基準の
# 左右対称スロットへ配置する(例: count=2→肩、count=4→肩+胸)。
# 現時点では命中判定のX軸ズレにのみ使用し、重心・支持多角形・転倒(§14)には未接続。
SLOT_SPACING_X = 1.0     # スロット間の基準間隔(仮値)
SIGMA_X_THRUST = 0.6     # thrust/low/heavy: 狙いを絞った横方向の散らばり(仮値、小さいほど狙い澄ます)
SIGMA_X_SWEEP = 2.5      # sweep: 横方向に大きく広げる散らばり(仮値、旧SWEEP_SIGMA_MULTの役割を継承)

# --- V30新規: 転倒・重心・支持多角形 ---
TIPPING_TOLERANCE = 0.3  # 重心が支持多角形の境界からこの距離以内なら「ギリギリ持ちこたえた」扱い(仮値)

# --- V35変更: 転倒による回避性能低下(mobility_factor) ---
# 旧TIPPING_SPEED_PENALTYはspeed_factorへ直接加算していたため、Reachや着弾重み(width)にまで
# 転倒の影響が波及していた。V35では新変数mobility_factorに分離し、
# calculate_miss_probability内でのみ使用する(speed_factor自体・width・着弾重量には無関係)。
# 「転倒中は素早さ半減」という自然表現のため乗算係数として0.5を用いる。
TIPPING_MOBILITY_MULT = 0.5  # 転倒中にmobility_factorへ乗じる係数(仮値。1.0未満=回避性能低下)

# --- V35新規(追加項目①): 位置関係(高低差)によるダメージ補正 ---
# 「高い側は上から余裕を持って打ち下ろす=威力が伝わりやすい」「低い側は下から必死に
# 攻撃を伸ばす=威力が伝わりにくい」という自然の原理を表現する。
# (attacker_elevation - defender_elevation) * HEIGHT_DAMAGE_COEF をダメージ計算後の
# 最終値に加算する(最低ダメージ0は下回らない)。
HEIGHT_DAMAGE_COEF = 0.01

# --- V35新規: AI長期コスト評価(問題6-1対応) ---
# greedy/extreme AIが、この技を使うことで自分の攻撃部位に蓄積する疲労
# (=将来の自分のmiss_prob悪化)を評価値(EV)から差し引くための重み(仮値)。
EV_FUTURE_FATIGUE_WEIGHT = 0.3

# --- V36新規: 技獲得システム移行(部位破壊済みでも攻撃可能) ---
# 部位破壊＝完全消滅ではなく「折れた腕」「欠けた牙」として扱う設計方針のため、
# 破壊済み(all instances broken)の部位でも技自体は保持し続け、攻撃を行える。
# ただし威力のみ大幅に低下させる。命中判定(miss_prob/着弾サンプリング)には
# 一切影響させない(仕様上「命中判定は通常実行」のため)。
BROKEN_PART_DAMAGE_MULT = 0.1

# --- V38新規: tactical AI(戦術AI)評価補正 ---
# greedyのestimate_ev(damage_ev)を基礎値とし、以下3種の「戦術的圧力」を加算して
# 評価する。全てai.py側(AIPlayer._tactical_*_bonus)からのみ参照され、
# simulator.py/models.pyの既存戦闘ロジックには一切影響しない(仮値・要調整)。

# 1) vital_break_pressure: 相手のvital部位(head/neck/torso)を今回の攻撃で
#    破壊できる可能性が高いほど加算するボーナスの重み。
TACTICAL_VITAL_WEIGHT = 5.0

# 2) disable_pressure: 相手の非vital部位破壊による「戦闘能力低下」の価値を
#    部位タイプごとに設定する(仮値)。
#    arm: 攻撃手段減少 / leg: 回避・速度低下 / eye: 命中低下 / ear: 回避性能低下。
#    ここに存在しない部位タイプ(tail/mouth/noseなど)はdisable_pressureの対象外(0.0)。
TACTICAL_DISABLE_WEIGHTS = {
    "arm": 3.0,
    "leg": 3.0,
    "eye": 4.0,
    "ear": 2.0,
}

# 3) kill_pressure: 相手を戦闘不能に追い込む可能性(core_hpへのオーバーフロー、
#    または非vital部位全滅条件への接近)を評価するボーナスの重み。
TACTICAL_KILL_WEIGHT = 8.0

# --- V39新規: Z軸(前後方向)着弾判定・突き刺し(thrust)貫通 ---
# 既存のElevation軸×X軸のガウス分布(_impact_weights)にZ軸(前後方向。models.ATTACH_Z由来)
# を掛け合わせ、3軸ガウス分布として着弾候補の重みを計算する。
# SIGMA_Zはアーキタイプに依らず一定値とする(X軸のようにsweep/thrustで拡げ縮めしない。
# 前後方向の拡散はSweep/Thrustの役割ではなく、Thrustの「貫通」という別特性で表現するため)。
SIGMA_Z = 1.0  # 仮値。SIGMA(Elevation=0.8)とSIGMA_X_THRUST(0.6)の中間程度から開始。

# 突き刺し(thrust)専用: 最初に着弾した部位よりZ値が小さい(=奥にある)生存部位への
# 追加貫通判定。全範囲攻撃ではなく、範囲・貫通数を明示的に制限し、距離・貫通数に応じて
# 威力を減衰させる。
THRUST_Z_RANGE = 1.6          # 最初の着弾点からこのZ距離以内の部位のみ貫通対象にする(仮値)
THRUST_MAX_PIERCE = 2         # 最初の着弾に加えて追加貫通できる部位数の上限(仮値)
THRUST_PENETRATION_DECAY = 0.5  # 貫通1段につき威力に乗じる減衰係数(貫通するほど威力は分散する)
THRUST_Z_DIST_COEF = 0.25     # Z距離1につき追加で威力を減衰させる係数(距離による減衰)
THRUST_MIN_POWER_RATIO = 0.15   # 減衰後の威力比率がこれを下回ったら以降の貫通を打ち切る(仮値)


# ============================================================
# V40新規: Whittlewisp プロトタイプA 未実装項目仕様書(2026/08/07改訂版)対応
# ============================================================

# --- 未実装項目1: count(本数)とパラメータの二層構造化 ---
# ON: count個生成される各PartInstanceが、それぞれ独立してlength/sizeをロールする
#     (angleはtorso・tailのみが対象。tailがcount>1の場合は全インスタンスで同一角度を共有する
#     ―― 実装前確認事項②の確定方針)。
# OFF: count内の全PartInstanceがPartProfileで指定された値のミラーコピーになる(従来挙動)。
# 【今回追加確定事項】体重(build_weight)・素早さ(raw_speed)・技獲得判定(get_available_moves)は、
# ON/OFFに関わらず常に「実際に生成されたPartInstanceの実測値の集計(合計/平均/最大)」を正とする。
# PartProfile側のlength/size/angleは「ロールの中心値(ビルド設計上の狙い値)」としてのみ残り、
# 生成後の各種計算には使用しない。
ENABLE_ASYMMETRIC_INSTANCES = True
INSTANCE_PARAM_VARIANCE = 1  # ONの時、Profileのlength/size/(tailのみ)angleを中心に
                              # ±この範囲の整数オフセットでInstanceごとに独立ロールする(仮値)。
                              # vital部位(head/neck/torso)は常にcount=1で左右非対称という概念が
                              # 発生しないため、ONでもロール対象外(ビルド設計値をそのまま採用する)。

# --- 未実装項目2: count時の配置パターン(arrangement_pattern) ---
# ON: countごとに定義された配置パターン(models.ARRANGEMENT_PATTERNS)からランダムに1つ選択する
#     (プロトタイプAの段階ではランダム選択で構わない、との指示に対応)。
# OFF: 単純に等間隔・一列に並べるだけの固定配置になる(パターン選択なし)。
ENABLE_ARRANGEMENT_PATTERNS = True

# --- 未実装項目3: ソケット位置の相対計算ルール ---
# ON: 親パーツの末端位置を基準とした相対計算式でソケット位置を決定する。
#     torso→neck→head→(mouth/nose/eye/ear)は真の骨格チェーン(親の実測lengthに追従)。
#     arm/leg/tail/wingはtorso直下の子として、torsoの実測length(前後位置)・
#     size(横方向の広がり)の両方に比例して接続点が追従する(「torsoが変化すれば
#     全ての接続点が自動追従する」という項目3本来の目的に対応)。
# OFF: 固定の絶対座標オフセット(models.ATTACH_Z)でソケット位置を配置する(既存の暫定挙動)。
ENABLE_RELATIVE_SOCKET_CALC = True
SOCKET_LATERAL_SIZE_COEF = 0.2  # ON時、親(torso/head)のsizeが大きいほど、その子部位の
                                 # X軸配置(横方向の広がり)を外側へ押し広げる係数(仮値)。

# --- 2026/08/07改訂: 角度(angle)パラメータのtorso・tail限定化と4段階化 ---
# 角度は0/30/60/90度相当の4段階(内部値0-3)に変更された(旧: 5段階0-4)。
# quad_factor(models.Creature.elevation_of内)の除数を、旧5段階基準の4.0から
# 新4段階基準の3.0へ変更する(「最大角度で完全な四足姿勢になる」という意味づけを維持するため)。
ANGLE_QUAD_DIVISOR = 3.0




