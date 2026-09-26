import math
import random
import config

# 部位種別ごとの基礎値マスター(v4企画書§2・§3準拠。baseElevationは検証用の仮置き値で
# 企画書上の根拠を持たない。四足/二足の基準ポーズからの実測が今後の課題)
# V26: wing/eye/earを追加(まだ実際のビルドには未使用だが、重量係数の検証のため定義しておく)。
BASE_ELEVATION = {
    "head": 5.0, "neck": 4.0, "torso": 3.0, "arm": 3.0,
    "leg": 1.0, "tail": 1.0, "mouth": 4.5, "nose": 5.0,
    "wing": 4.0, "eye": 5.0, "ear": 5.0,
}
BASE_REACH = {
    "head": 1.0, "neck": 2.0, "torso": 2.0, "arm": 2.0,
    "leg": 2.0, "tail": 2.0, "mouth": 1.0, "nose": 1.0,
    "wing": 1.5, "eye": 0.5, "ear": 0.5,
}
BASE_WIDTH = {
    "head": 3.0, "neck": 2.0, "torso": 2.5, "arm": 1.5,
    "leg": 1.8, "tail": 1.5, "mouth": 1.5, "nose": 0.8,
    "wing": 2.5, "eye": 0.6, "ear": 0.6,
}
ATTACH_Z = {
    "head": 2.0, "neck": 1.5, "mouth": 2.2, "nose": 2.2, "eye": 2.0, "ear": 2.0,
    "torso": 0.0, "wing": 0.0,
    "arm": 1.0,   # 四足時の前脚として機能する既存設計(quad_factor)と対応
    "leg": -1.0,  # 後脚
    "tail": -1.5,
}
# V39: 上記ATTACH_Zは従来、center_of_gravity/support_points(重心・支持多角形・転倒判定)
# 専用の値として定義されていたが、今回のZ軸(前後方向)着弾判定実装により、
# BattleSimulator._impact_weights側でも「攻撃がどの前後位置へ届くか」の判定軸として
# 共用する(新しい座標系・新しいステータス軸を追加するのではなく、既存のFK/取付座標を
# 読み取り専用で流用する)。破壊された部位・count=0の部位は、従来通りliving_instances/
# living_instances()に含まれないため、Z軸判定候補からも自然に除外される。
# vital部位の表面耐久基礎値。coefは大きさ1につき増減する量(vitalは5、非vitalは3)
DURABILITY_BASE = {
    "head": 25, "neck": 20, "torso": 20,   # vital
    "leg": 12,                             # vitalではないが特別式(下記)
    "arm": 10, "tail": 10, "mouth": 8, "nose": 8,  # 非vitalの既定値
    "wing": 9, "eye": 6, "ear": 6,
}
# V26: Weight計算の係数を変更。tailは重心バランスを取る役割のため4に引き上げ、
# 顔の器官4種(mouth/nose/eye/ear)は0.5、wingは2で計算する(ユーザー指示)。
# torso/leg/arm/neck/headは従来値を踏襲。
WEIGHT_COEF = {
    "torso": 5, "leg": 4, "arm": 3, "neck": 2, "head": 2,
    "tail": 4,
    "mouth": 0.5, "nose": 0.5, "eye": 0.5, "ear": 0.5,
    "wing": 2,
}
VITAL_TYPES = {"head", "neck", "torso"}
# 対になる器官(count基準2)と単独の非vital器官(count基準1)。v5 §3.4。
PAIRED_TYPES = {"arm", "leg", "eye", "ear", "wing"}
SINGLE_NONVITAL_TYPES = {"tail", "mouth", "nose"}

# ============================================================
# V42新規(Stonebloom統合企画書①⑦タブ対応): 材質ベースの属性システム
# 「胴体のステータスで駒全体の防御属性を一括決定する」旧方式を撤回し、
# 各部位(PartProfile)が保持する材質そのものが攻撃属性・防御属性を決める。
# 判定は「命中/攻撃に使った部位の材質」単位で行う(Creature全体で一括ではない)。
# 材質は落ちているパーツ自体が固定で保持し、ドロップ後の変更手段はない
# (=ビルド側は「どの材質のパーツをどの部位に載せるか」というロート基盤の
# 取捨選択を行うだけで、材質そのものの自由入力ではない)。
# 三すくみ(Bind/Pierce/Crush)の意匠:
#   bind   = 柔らかい・丸みのある材質。バランス型(重量・耐久ともに無補正=基準点)。
#   pierce = 鋭く尖った軽量な材質。軽いが壊れやすい(低耐久)。
#   crush  = 重く金属質な材質。重いが頑丈(高耐久)。
# 数値は全て仮値・実測待ち(⑥未決定事項タブ「材質係数の具体値」参照)。
# ============================================================
MATERIAL_TYPES = {"bind", "pierce", "crush"}
DEFAULT_MATERIAL = "bind"  # 素材省略時の後方互換フォールバック(既存ビルドは無補正のまま動く)

# 重量式(Creature._compute_build_weight)のインスタンスごとの項に、
# もう1つの乗算係数として接続する(仮値)。
MATERIAL_WEIGHT_COEF = {
    "bind": 1.0,
    "pierce": 0.7,
    "crush": 1.4,
}
# 耐久式(compute_max_dur)の結果全体に乗算する係数(仮値)。
MATERIAL_DUR_COEF = {
    "bind": 1.0,
    "pierce": 0.6,
    "crush": 1.5,
}

# ============================================================
# V40新規: Whittlewisp プロトタイプA 未実装項目仕様書(2026/08/07改訂版)対応
# ============================================================

# --- 2026/08/07改訂: angleを持つ部位はtorso・tailの2部位のみ ---
ANGLE_CAPABLE_TYPES = {"torso", "tail"}
ANGLE_STAGE_MIN = 0
ANGLE_STAGE_MAX = 3  # 4段階(0/30/60/90度相当。旧5段階0-4から変更)

# --- 未実装項目3: ソケット位置の相対計算ルール ---
# 親子関係(全11部位の統一ルール)。torsoが唯一の根で、他の全部位はここから辿れる。
PARENT_TYPE = {
    "torso": None,
    "neck": "torso", "arm": "torso", "leg": "torso", "tail": "torso", "wing": "torso",
    "head": "neck",
    "mouth": "head", "nose": "head", "eye": "head", "ear": "head",
}
# torso.length(実測値)に乗じる比率。torsoの前端を+1.0・後端を-1.0とした相対位置。
# neckはtorso前端に接続、tailはtorso後端に接続。armはやや前寄り、legはやや後ろ寄り、
# wingは中央。torsoのlengthが変化すれば、これらの接続点は全て自動的に追従する
# (未実装項目3「親パーツの変化に接続位置が追従する」という目的そのものに対応)。
TORSO_ATTACH_RATIO = {
    "neck": 1.0,
    "arm": 0.5,
    "wing": 0.0,
    "leg": -0.5,
    "tail": -1.0,
}
# head.length(実測値)に乗じる比率+区別用の微小オフセット(仮値)。
# 未実装項目5確認事項①(a)採用により顔器官はangleを持たず、22.5-2節の固定アンカー
# (口=下段中央/鼻=中段中央/目=上段中央/耳=頭頂後端付近)の「前後方向の区別」のみを
# 踏襲する(向きが変化する可変ロジックは実装しない)。
HEAD_ATTACH_RATIO = {"mouth": 0.9, "nose": 1.0, "eye": 0.3, "ear": -0.3}
HEAD_LOCAL_OFFSET = {"mouth": -0.2, "nose": 0.2, "eye": 0.0, "ear": -0.1}

# --- 未実装項目2: count時の配置パターン(arrangement_pattern) ---
# count個のインスタンスの相対座標オフセット(dx, dz)をあらかじめ定義しておく。
# このコードベースは3D非表示のためX軸(左右)・Z軸(前後)の2値のみで表現する
# (企画書側の「球面座標」は、頭部表面の緯度・経度に相当する2値という意味であり、
# 概念としてはここでのdx/dzと同型のため、この2軸モデルで代替する)。
ARRANGEMENT_PATTERNS = {
    1: {"center": [(0.0, 0.0)]},
    2: {"pair": [(-1.0, 0.0), (1.0, 0.0)]},
    3: {
        "horizontal_line": [(-1.0, 0.0), (0.0, 0.0), (1.0, 0.0)],
        "vertical_line": [(0.0, -1.0), (0.0, 0.0), (0.0, 1.0)],
        "triangle": [(-1.0, -0.5), (1.0, -0.5), (0.0, 0.8)],
    },
    4: {
        "square": [(-1.0, -1.0), (1.0, -1.0), (-1.0, 1.0), (1.0, 1.0)],
        "diamond": [(0.0, -1.2), (-1.2, 0.0), (1.2, 0.0), (0.0, 1.2)],
        "double_row": [(-1.0, -0.5), (1.0, -0.5), (-1.0, 0.5), (1.0, 0.5)],
    },
    5: {
        "cross": [(0.0, 0.0), (-1.2, 0.0), (1.2, 0.0), (0.0, -1.2), (0.0, 1.2)],
        "pentagon": [(0.0, -1.3), (-1.2, -0.3), (1.2, -0.3), (-0.8, 1.1), (0.8, 1.1)],
    },
}

def _simple_line_offsets(instance_count: int, spacing: float) -> list:
    """ENABLE_ARRANGEMENT_PATTERNS=OFF時、および未定義instance_countのフォールバック用。
    単純に等間隔・一列(X軸方向)に並べるだけの固定配置。
    V41: 引数は「生成済み(これから生成する)インスタンス数」を表す(旧count)。"""
    if instance_count is None or instance_count <= 0:
        return []
    if instance_count == 1:
        return [(0.0, 0.0)]
    half = (instance_count - 1) / 2.0
    return [((i - half) * spacing, 0.0) for i in range(instance_count)]

def resolve_arrangement(instance_count: int, spacing: float = None):
    """未実装項目2: インスタンス数ごとに定義された配置パターンの中からいずれかを選択する。
    プロトタイプAの段階では選択自体はランダムでよい、との指示に対応(ランダム選択)。
    V41: 引数instance_countは「PartProfileが持つインスタンス数」を表す(旧count引数を
    そのままリネームしたもので、意味論上はPartProfile.build_instances()に渡された
    instance_countと同じ値)。
    戻り値: (pattern_name, [(dx, dz), ...]) ※dx/dzはspacing倍済みの相対オフセット。"""
    if spacing is None:
        spacing = config.SLOT_SPACING_X
    table = ARRANGEMENT_PATTERNS.get(instance_count)
    if not table:
        return "line", _simple_line_offsets(instance_count, spacing)
    pattern_name = random.choice(list(table.keys()))
    offsets = [(dx * spacing, dz * spacing) for dx, dz in table[pattern_name]]
    return pattern_name, offsets

def _roll_instance_value(base: float, variance: int):
    """未実装項目1: ENABLE_ASYMMETRIC_INSTANCES=True時の個体差ロール。
    baseを中心に±variance(整数)のオフセットをランダムに加える。"""
    if not variance:
        return base
    return base + random.randint(-variance, variance)

def effective_reach(instance: "PartInstance", apply_fatigue: bool = False) -> float:
    """技のReachは使用/被弾部位のbaseReach+その"インスタンス自身"の長さから動的に算出する。
    部位固定値の直接指定(Move側でreachを持つ)は禁止
    (v4企画書 1・ 11の"見た目=情報公開"原則のため)。
    V40: 未実装項目1(二層構造化)により、Profile共通値ではなくInstance固有の実測値
    (instance.length)を使用するよう変更(左右非対称な個体差がReachにも反映されるようにする)。
    V22: apply_fatigue=Trueの場合、部位疲労によるReach低下を適用する。疲労は「攻撃に使う側の
    腕が疲れて伸びきらなくなる」現象を表すため、防御側の被弾部位(target_part)には適用しない
    ―― 呼び出し側は攻撃部位(atk_part)にのみ apply_fatigue=True を渡すこと。"""
    base = BASE_REACH.get(instance.profile.part_type, 2.0) + instance.length
    if apply_fatigue and config.ENABLE_PART_USAGE_FATIGUE:
        base -= instance.fatigue * config.PART_FATIGUE_REACH_COEF
    return max(config.PART_REACH_FLOOR, base)

def effective_width(instance: "PartInstance") -> float:
    """Widthは部位のbaseWidthと"インスタンス自身"の大きさから動的に算出する。
    上と同じ理由でMove側の固定値は禁止。V40: Profile共通値ではなくInstance固有の
    実測値(instance.size)を使用するよう変更。"""
    return BASE_WIDTH.get(instance.profile.part_type, 2.0) * (1 + 0.15 * instance.size)

def compute_max_dur(part_type: str, size: float, length: float,
                     material: str = DEFAULT_MATERIAL) -> float:
    """V40: 二層構造化に伴い、耐久はPartProfile単位ではなく各PartInstanceが個別に
    自分自身のsize/lengthから算出する(non-vitalのcount<=0による「非存在」は
    PartProfile側でそもそもinstanceを生成しない形で表現するため、この関数自体は
    常に「実在するインスタンス1体分」の耐久を計算すればよく、旧count引数は不要になった)。
    size/lengthが最小でも痕跡器官として残るよう、下限floorを設ける
    (max(2.0,...)・max(4.0,...)がその役割)。
    V42(材質統合): 既存式(下限floorクランプ込み)の結果全体に材質係数を乗算する。
    floorより先に材質補正を掛けると「Pierceは下限floorすら削られる」事態になり
    「痕跡器官として必ず残る」という下限floorの意図が崩れるため、floor確定後に乗算する。"""
    if part_type == "leg":
        # 脚は長さを伸ばしてElevationを稼ぐほど脆くなるトレードオフ(V8)
        raw = max(4.0, 12 + size * 4 - length * config.LEG_DUR_LEN_PENALTY)
    else:
        base = DURABILITY_BASE.get(part_type, 10)
        coef = 5 if part_type in VITAL_TYPES else 3
        raw = max(2.0, base + size * coef)
    return raw * MATERIAL_DUR_COEF.get(material, 1.0)

class PartProfile:
    """触媒で編集される部位共通の定義データクラス。

    V40(未実装項目1: 二層構造化): このクラスが持つsize/length/angleは、もはや
    「実際に使われる値」そのものではなく、"生成されるPartInstanceのロールの
    中心値(ビルド設計上の狙い値)"という位置づけに変わった。
    実際の戦闘計算(Reach/Width/耐久/Elevation等)は必ずPartInstance側の実測値
    (instance.size/length/angle)を参照すること。
    体重(Creature._compute_build_weight)・素早さ(Creature.raw_speed)・
    技獲得判定(get_available_moves)も同様に、Instance側の実測値の集計
    (合計/平均/最大)を正とする。

    V41(count廃止リファクタ): PartProfileはもはや「何本生成するか(count)」を
    引数として受け取らない・保持しない。PartProfileが持つのはあくまで
    「部位タイプの設計値(狙い値)」だけであり、実際に何個のPartInstanceを
    生成するかはビルド定義側(battle.py等)がbuild_instances(instance_count)を
    明示的に呼び出すことで決める。PartProfileの生成(__init__)時点では
    self.instancesは空リストのままで、PartInstanceは1つも作られない。
    """
    def __init__(self, name: str, size: float, length: float, angle: float,
                 is_vital: bool = False, part_type: str = "other",
                 material: str = DEFAULT_MATERIAL):
        self.name = name
        self.size = size

        assert not math.isnan(size), f"PartProfile '{name}' created with NaN size."
        assert not math.isnan(length), f"PartProfile '{name}' created with NaN length."
        assert not math.isnan(angle), f"PartProfile '{name}' created with NaN angle."
        assert material in MATERIAL_TYPES, (
            f"PartProfile '{name}': unknown material '{material}' "
            f"(must be one of {sorted(MATERIAL_TYPES)})."
        )

        self.length = length
        # 2026/08/07改訂: angleを持つのはtorso・tailのみ。それ以外の9部位は
        # コンストラクタ引数として値を受け取っても内部的には保持しない(None固定)。
        # 既存呼び出し側(battle.py等)がangle=0を渡し続けても後方互換で動作する。
        self.angle = angle if part_type in ANGLE_CAPABLE_TYPES else None
        self.is_vital = is_vital
        self.part_type = part_type
        # V42(材質統合): 材質は「落ちているパーツ自体が固定で保持する」仕様のため、
        # PartProfile(=装備された1パーツ)単位で持ち、生成される全PartInstance
        # (count個の実体)に共通適用される。ドロップ後に変更する手段はない。
        self.material = material

        # V41: countを持たない。実体化(PartInstance生成)は必ず外部からの
        # build_instances(instance_count)呼び出しによって行われる。
        self.instances: list["PartInstance"] = []
        # position解決(未実装項目3)はCreature側で全部位が揃った後にまとめて行う
        # (Creature._resolve_positions参照。親パーツの実測length/sizeを参照する必要があるため)。

    def build_instances(self, instance_count: int) -> None:
        """V41(count廃止リファクタ): 呼び出し側(Creature/ビルド定義側)が
        「何本生成するか」を引数(instance_count)として明示的に渡し、その数だけ
        PartInstanceを生成する。PartProfile自身はもうcountを保持しないため、
        このメソッドを呼ばない限りself.instancesは空のままである
        (=「部位が存在しない」状態と等価。従来のcount<=0に相当)。

        それぞれ独立してlength/size(angleはtorso・tailのみ)をロールする
        (未実装項目1)。配置パターン(未実装項目2)もここで決定する。
        """
        assert instance_count >= 0, (
            f"PartProfile '{self.name}'.build_instances(): instance_count must be >= 0 (got {instance_count})."
        )
        if self.is_vital:
            # vital部位(head/neck/torso)は常にちょうど1体のみ存在する、という
            # 従来からの不変条件を維持する(vital部位が0体・複数体になることは想定外)。
            assert instance_count == 1, (
                f"PartProfile '{self.name}' is vital and must be built with instance_count=1 "
                f"(got {instance_count})."
            )

        self.instances = []
        if instance_count <= 0:
            return  # 「本数0=その部位は存在しない」旧count<=0と同じ扱い

        # vital部位(head/neck/torso)は常に1体のみであり、「同一部位種類内での左右非対称」
        # という概念自体が発生しないため、ENABLE_ASYMMETRIC_INSTANCES=ONでもロール対象外
        # とし、ビルド設計値をそのまま体幹の基準形状として採用する。
        allow_roll = config.ENABLE_ASYMMETRIC_INSTANCES and not self.is_vital
        variance = config.INSTANCE_PARAM_VARIANCE

        # 尻尾(tail)が複数体の場合、角度は全インスタンスで共有する
        # (実装前確認事項②の確定方針: 全ての尻尾に同じ角度を適用する)。
        shared_tail_angle = None
        if self.part_type == "tail" and self.angle is not None:
            shared_tail_angle = (
                self._clamp_angle(_roll_instance_value(self.angle, variance))
                if allow_roll else self.angle
            )

        if config.ENABLE_ARRANGEMENT_PATTERNS:
            pattern_name, offsets = resolve_arrangement(instance_count, config.SLOT_SPACING_X)
        else:
            pattern_name, offsets = "line", _simple_line_offsets(instance_count, config.SLOT_SPACING_X)

        for i in range(instance_count):
            if allow_roll:
                length = _roll_instance_value(self.length, variance)
                size = _roll_instance_value(self.size, variance)
            else:
                length = self.length
                size = self.size

            if self.part_type == "torso":
                angle = self.angle  # torsoは常に1体固定でロールしない
            elif self.part_type == "tail":
                angle = shared_tail_angle
            else:
                angle = None

            local_offset = offsets[i] if i < len(offsets) else (0.0, 0.0)
            self.instances.append(PartInstance(
                profile=self,
                instance_index=i,
                length=length,
                size=size,
                angle=angle,
                arrangement_pattern=pattern_name,
                local_offset=local_offset,
            ))

    @staticmethod
    def _clamp_angle(value: float) -> float:
        return max(ANGLE_STAGE_MIN, min(ANGLE_STAGE_MAX, value))

    @property
    def living_instances(self) -> list["PartInstance"]:
        """生存しているPartInstanceのリストを返す"""
        return [inst for inst in self.instances if not inst.is_broken]

    @property
    def all_broken(self) -> bool:
        """全instanceが破壊済み(またはcount<=0で初期から存在しない)か判別"""
        if not self.instances:
            return True
        return all(inst.is_broken for inst in self.instances)

    @property
    def max_surface_dur(self) -> float:
        """後方互換用の代表値(先頭インスタンスの耐久)。V40以降、耐久は
        インスタンスごとに個別なので、部位全体を代表する単一値が必要な場面
        (ログ表示等)でのみ参照すること。"""
        return self.instances[0].max_surface_dur if self.instances else 0.0

class PartInstance:
    """個別の部位インスタンス（実体）の状態を管理するクラス。

    V40(未実装項目1): length/sizeをPartProfileから独立して持つようになった
    (二層構造化の第二層)。angleはtorso・tailのみが値を持ち、それ以外はNone固定。
    """
    def __init__(self, profile: "PartProfile", instance_index: int,
                 length: float, size: float, angle,
                 arrangement_pattern: str = "line", local_offset: tuple = (0.0, 0.0)):
        self.profile = profile
        self.instance_index = instance_index
        self.length = length
        self.size = size
        self.angle = angle  # torso・tailのみ値を持つ。それ以外はNone固定
        self.arrangement_pattern = arrangement_pattern  # 未実装項目2
        # local_offsetは配置パターンによる(dx, dz)。未実装項目3のソケット解決前の
        # ローカル値で、Creature._resolve_positions()が実座標(position)へ変換する。
        self.local_offset = local_offset
        self.position = local_offset  # 解決前の仮値。Creature構築時に必ず上書きされる。

        self.max_surface_dur = compute_max_dur(profile.part_type, size, length, profile.material)
        self.current_surface_dur = self.max_surface_dur
        self.fatigue = 0.0  # V22: 部位使用疲労

    @property
    def is_broken(self) -> bool:
        """耐久が尽きているか、または基礎耐久が0かを判定"""
        return self.current_surface_dur <= 0

class Move:
    """
    攻撃行動定義

    V37: target_partをMoveから分離。
    Move生成時点では相手Creatureが存在せず、target_partを固定値として
    持たせる設計はZ軸・奥行き判定・貫通攻撃・範囲攻撃と相性が悪いため撤廃した。

    Moveが保持するのは技そのものの定義のみ:

    part_name:
        攻撃に使用する自分側の部位

    技タイプ(archetype)・技倍率(arch_mult)・技取得条件は本クラスと
    MOVE_ACQUISITION_TABLE側に閉じる。

    target_part(攻撃対象として狙う相手側の部位)は、戦闘時に
    AIPlayer.select_action()が別途決定し、resolve_impact/estimate_evへ
    引数として渡す(Moveのフィールドとしては保持しない)。

    例:
        鼻で頭を狙う

        Move("鼻突き", "nose", "thrust")
        + 戦闘時に target_part="head" をAIが決定

    AIは
    「どの部位で攻撃するか(Move)」
    「どの部位を狙うか(target_part)」
    を別々に判断できる。
    """

    def __init__(
        self,
        name: str,
        part_name: str,
        archetype: str
    ):
        self.name = name

        # 攻撃側部位
        self.part_name = part_name

        # 技タイプ
        self.archetype = archetype

        mults = {
            "thrust": config.ARCH_MULT_THRUST,
            "low": config.ARCH_MULT_LOW,
            "sweep": config.ARCH_MULT_SWEEP,
            "heavy": config.ARCH_MULT_HEAVY,
            "strike": config.ARCH_MULT_STRIKE,  # V36: 技獲得システムで追加された基本技
        }

        self.arch_mult = mults.get(archetype, 1.0)

# ============================================================
# V36新規: 技獲得システム(部位のsize/lengthから使用可能な技を自動決定する)
#
# 設計思想(依頼元 v6企画書系の「見た目=能力公開」原則):
#   「腕だから攻撃可能」「口だから攻撃可能」のような部位種別による固定判定は禁止。
#   代わりに、各PartProfile(=技を1つ以上取得できるPartInstanceの集合)が
#   size/lengthの条件を満たすかどうかだけで、使用可能な技が決定される。
#
# 技獲得条件(確定仕様):
#   打撃(Strike)     : size   >= 1
#   薙ぎ払い(Swing)   : length >= 1   (実装上のarchetypeは既存の"sweep"を流用)
#   一撃集中(HeavyBlow): size   >= 2   (実装上のarchetypeは既存の"heavy"を流用)
#   突き刺し(Thrust)  : length >= 2   (V39: Z軸貫通効果を実装。BattleSimulator側の
#                        _resolve_thrust_penetrationが担当。archetype判定はこれまで通り
#                        ここでのみ行い、貫通処理自体はsimulator.py側に閉じる)
#
# target_partについて(V37):
#   Move自体はもうtarget_partを一切持たない(上のMoveクラス参照)。
#   ここに残すDEFAULT_MOVE_TARGET_PARTは、Move生成用の既定値ではなく、
#   AI側(ai.AIPlayer)が「防御側に生存部位が1つも無い」という異常系の
#   フォールバック値としてのみ参照する定数として存置する
#   (全Creatureが必ず持つvital部位である"torso"を使えば、
#   defender.get_part()が常に解決できるため)。
# ============================================================
DEFAULT_MOVE_TARGET_PART = "torso"

# (表示名, archetype, 判定関数) のリスト。
# V40(未実装項目1対応): 判定はもはやPartProfileの「狙い値」ではなく、実際に生成された
# PartInstance群の実測値(size/length)を基準に行う。判定関数は(max_size, max_length)を
# 受け取る形に変更した―― count内で最も条件を満たしやすい(＝最も仕上がりの良い)
# インスタンスが1体でもいれば、その部位種類全体としてその技を獲得できるものとする
# 解釈とする(「一番良く育った個体が技を代表する」という考え方)。
# 破壊済み(all_broken)かどうかは技の獲得可否に一切影響しない
# (「部位破壊時の扱い」仕様: 破壊済み部位でも技は保持する)。
MOVE_ACQUISITION_TABLE = [
    ("打撃", "strike", lambda size, length: size >= 1),
    ("薙ぎ払い", "sweep", lambda size, length: length >= 1),
    ("一撃集中", "heavy", lambda size, length: size >= 2),
    ("突き刺し", "thrust", lambda size, length: length >= 2),
]

def get_available_moves(creature: "Creature") -> list["Move"]:
    """creatureの各部位(PartProfile)配下のPartInstance群の実測値がMOVE_ACQUISITION_TABLE
    の条件を満たすかどうかを確認し、取得可能なMoveを生成して返す。

    フロー: PartInstance群の実測値集計(max) → 技取得条件確認 → 取得可能Move生成 → (AIが選択)

    ・count<=0で実体(PartInstance)が存在しない部位は対象外(技を持つ主体が無いため)。
    ・破壊済み(all_broken)でも対象に含める(技は保持されたままという仕様のため)。
    ・1つの部位が複数の技を同時に獲得することがある(例: size=2,length=1のインスタンスを
      含む腕は打撃/薙ぎ払い/一撃集中の3つを獲得する)。
    ・V37: target_partはここでは一切決定しない(Move自体が保持しない情報のため)。
      戦闘時にAIPlayerが別途決定する。
    """
    moves: list[Move] = []
    for part_name, prof in creature.parts.items():
        if not prof.instances:
            continue  # count<=0: そもそも部位が存在しない
        max_size = max(inst.size for inst in prof.instances)
        max_length = max(inst.length for inst in prof.instances)
        for label, archetype, condition in MOVE_ACQUISITION_TABLE:
            if condition(max_size, max_length):
                moves.append(
                    Move(f"{part_name}の{label}", part_name, archetype)
                )
    return moves

class Creature:
    def __init__(self, name: str, parts: list[PartProfile], core_hp: float = None):
        self.name = name
        self.parts = {p.name: p for p in parts}
        # VITAL_TYPESは必須部位とする
        for vital_type in VITAL_TYPES:
            assert vital_type in self.parts, f"Creature '{name}' is missing vital part: '{vital_type}'"

        # 未実装項目3: 全部位が出揃った時点でソケット位置(前後・左右)を解決する。
        # (親パーツの実測length/sizeを参照する必要があるため、PartProfile単体では
        # 決定できず、ここCreature側でまとめて行う)
        self._resolve_positions()

        # V36: 技はもうCreature構築時に外部から渡されない。
        # 各部位のsize/lengthから自動的に決定する(get_available_moves参照)。
        # battle.py等に固定技リストを持たせない、という今回の移行の中核。
        self.moves = get_available_moves(self)
        self.core_hp = core_hp if core_hp is not None else config.CORE_HP
        self.max_core_hp = self.core_hp
        self.accumulated_damage = 0.0
        self.turn_count = 0  # battle.py側で毎ターン更新する
        self.is_tipped = False
        self.skip_action_this_turn = False

        # V35(問題①対応): Weightは戦闘開始時点(全部位生存状態)のビルド構築値として
        # 固定する。戦闘中にliving_cntが変化しても再計算されない
        # (「部位が壊れて軽くなり、raw_speedが上がって避けやすくなる」逆転構造を解消)。
        self.build_weight = self._compute_build_weight()

        # V35(未実装機能: 階級上限): ビルド重量が検証対象階級の上限を超過していないか
        # 構築時に一度だけ判定し、警告を表示する。
        class_limit = config.WEIGHT_CLASS_LIMITS.get(config.WEIGHT_TEST_CLASS)
        self.is_overweight = class_limit is not None and self.build_weight > class_limit
        if self.is_overweight:
            print(
                f"[WARN] Creature '{self.name}' is overweight: build_weight="
                f"{self.build_weight:.1f} > WEIGHT_CLASS_LIMITS['{config.WEIGHT_TEST_CLASS}']="
                f"{class_limit}. overweight_factor={config.OVERWEIGHT_SPEED_MULT} が"
                f"speed_factorに乗算されます。"
            )

    def _resolve_positions(self) -> None:
        """未実装項目3: ソケット位置の相対計算ルール。

        torso→neck→head→(mouth/nose/eye/ear) は真の骨格チェーンとし、各段の
        接続点(前後方向z)は「親パーツの末端位置(=親の実測length分だけ延びた先)」
        として再帰的に計算する(neckのlengthが変われば、headの接続位置は自動追従する)。

        arm/leg/tail/wingはtorso直下の子として、torsoの実測length(z方向の割合)に
        比例した接続点を持つ。加えて、torso.size(・head.size)が大きいほど、その
        直下の子部位のX軸(左右)配置が外側へ広がるようスケーリングする
        (「torsoの現在の大きさ・長さに応じて接続点が追従する」という要件への対応)。

        ENABLE_RELATIVE_SOCKET_CALC=OFFの場合は、従来通りATTACH_Zの固定絶対値・
        スケール無し(1.0倍)にフォールバックする。
        """
        torso = self.parts.get("torso")
        torso_inst = torso.instances[0] if (torso and torso.instances) else None
        torso_length = torso_inst.length if torso_inst else 0.0
        torso_size = torso_inst.size if torso_inst else 0.0

        neck = self.parts.get("neck")
        neck_inst = neck.instances[0] if (neck and neck.instances) else None

        head = self.parts.get("head")
        head_inst = head.instances[0] if (head and head.instances) else None

        use_relative = config.ENABLE_RELATIVE_SOCKET_CALC

        if use_relative:
            neck_z = torso_length * TORSO_ATTACH_RATIO["neck"]
            head_z = neck_z + (neck_inst.length if neck_inst else 0.0)
        else:
            neck_z = ATTACH_Z.get("neck", 0.0)
            head_z = ATTACH_Z.get("head", 0.0)

        for part_type, prof in self.parts.items():
            if not prof.instances:
                continue

            if use_relative:
                if part_type == "torso":
                    anchor_z, scale_x = 0.0, 1.0
                elif part_type == "neck":
                    anchor_z = neck_z
                    scale_x = 1.0 + config.SOCKET_LATERAL_SIZE_COEF * torso_size
                elif part_type == "head":
                    anchor_z = head_z
                    scale_x = 1.0 + config.SOCKET_LATERAL_SIZE_COEF * torso_size
                elif part_type in TORSO_ATTACH_RATIO:  # arm/leg/tail/wing
                    anchor_z = torso_length * TORSO_ATTACH_RATIO[part_type]
                    scale_x = 1.0 + config.SOCKET_LATERAL_SIZE_COEF * torso_size
                elif part_type in HEAD_ATTACH_RATIO:  # mouth/nose/eye/ear
                    head_len = head_inst.length if head_inst else 0.0
                    anchor_z = head_z + head_len * HEAD_ATTACH_RATIO[part_type] + HEAD_LOCAL_OFFSET[part_type]
                    head_size = head_inst.size if head_inst else 0.0
                    scale_x = 1.0 + config.SOCKET_LATERAL_SIZE_COEF * head_size
                else:
                    anchor_z, scale_x = ATTACH_Z.get(part_type, 0.0), 1.0
            else:
                anchor_z, scale_x = ATTACH_Z.get(part_type, 0.0), 1.0

            for inst in prof.instances:
                dx, dz_local = inst.local_offset
                inst.position = (dx * scale_x, anchor_z + dz_local)

    def _convex_hull(self, points: list) -> list:
        pts = sorted(set(points))
        if len(pts) <= 2:
            return pts

        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])

        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)

        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)

        return lower[:-1] + upper[:-1]

    def _point_to_segment_dist(self, pt, a, b) -> float:
        ax, az = a
        bx, bz = b
        px, pz = pt

        dx, dz = bx - ax, bz - az

        if dx == 0 and dz == 0:
            return math.hypot(px - ax, pz - az)

        t = max(
            0.0,
            min(
                1.0,
                ((px - ax) * dx + (pz - az) * dz) / (dx * dx + dz * dz),
            ),
        )

        proj = (ax + t * dx, az + t * dz)

        return math.hypot(px - proj[0], pz - proj[1])

    def _point_in_hull(self, pt: tuple, hull: list, tolerance: float) -> bool:
        """支持多角形(凸包)の内側に重心があるかを判定する。
        点が0個(脚も腕もない)→常に転倒。1個→その点に重心が一致していないと転倒。
        2個(退化して線分)→線分上からtolerance以内か。3個以上→通常の凸多角形内外判定。"""
        if not hull:
            return False

        if len(hull) == 1:
            return math.hypot(pt[0] - hull[0][0], pt[1] - hull[0][1]) <= tolerance

        if len(hull) == 2:
            return self._point_to_segment_dist(pt, hull[0], hull[1]) <= tolerance

        n = len(hull)
        sign = None
        for i in range(n):
            a, b = hull[i], hull[(i + 1) % n]
            cross_v = (b[0] - a[0]) * (pt[1] - a[1]) - (b[1] - a[1]) * (pt[0] - a[0])

            if abs(cross_v) < 1e-9:
                continue

            s = cross_v > 0

            if sign is None:
                sign = s
            elif s != sign:
                return False

        return True

    @property
    def _theta_torso(self) -> float:
        """torsoの傾き(ラジアン)。ビルドで固定された角度値(torso.angle)のみに基づく。
        V31: ダメージによる動的な姿勢崩れ(torso_ratio連動)を廃止。バランス変化は
        「部位が壊れる」「(将来実装の)変化権を使う」の2つの手段のみで起きる、という方針に統一。
        elevation_of(攻撃照準)とcenter_of_gravity/update_tipping_state(重心・転倒判定)の
        両方がこのプロパティを共用する。"""
        torso = self.get_part("torso")
        angle = torso.instances[0].angle if torso.instances else 0.0
        return math.radians((angle or 0.0) * config.ANGLE_UNIT_RAD_DEG)

    def get_part(self, name: str) -> PartProfile:
        return self.parts[name]

    def living_instances(self) -> list[PartInstance]:
        """生きている全instanceを部位タイプ横断でフラットに返すヘルパー"""
        living = []
        for prof in self.parts.values():
            living.extend(prof.living_instances)
        return living

    def vital_parts(self) -> list[PartProfile]:
        """全インスタンスが破壊されていないvital部位プロファイルを返す"""
        return [p for p in self.parts.values() if p.is_vital and not p.all_broken]

    @staticmethod
    def _calc_scale(internal_value: float) -> float:
        """V26: Weight計算専用のスケール変換。内部値(-2?+2)を1?5に変換する"""
        return internal_value + 3.0

    def _compute_build_weight(self) -> float:
        """V26: 縦(length)×横幅(size)×本数(count)の乗算式。
        V35(問題①対応): 戦闘開始時点(=まだ何も壊れていない状態)のcountを使って
        一度だけ計算する値。__init__からのみ呼ばれ、self.build_weightにキャッシュされる。
        戦闘中の破壊によるliving_cntの変動では再計算されない
        (ビルド構築時の重量計算にのみ影響させる、という要求のため)。
        V40(未実装項目1対応): PartProfileの「狙い値」ではなく、実際に生成された
        各PartInstanceの実測length/sizeを1体ずつ集計(合計)する形に変更した。
        左右非対称に生成された場合、その非対称さが必ず体重に反映される。
        V42(材質統合): インスタンスごとの項(length_scale×size_scale×部位係数)に、
        さらに材質係数(そのパーツが属するPartProfileの材質)を乗算する。"""
        total = 0.0
        for p in self.parts.values():
            if not p.instances:
                # V41: p.countが廃止されたため、「部位が存在するか」はinstances
                # (build_instances()で実際に生成されたPartInstance群)の有無だけで判定する。
                continue
            coef = WEIGHT_COEF.get(p.part_type, 1)
            material_coef = MATERIAL_WEIGHT_COEF.get(p.material, 1.0)
            for inst in p.instances:
                length_scale = Creature._calc_scale(inst.length)
                size_scale = Creature._calc_scale(inst.size)
                total += length_scale * size_scale * coef * material_coef
        return total

    @property
    def weight(self) -> float:
        """V35: 戦闘開始時に固定されたビルド重量を返す(問題①対応)。
        部位破壊によるliving_cntの変化では変動しない。"""
        return self.build_weight

    @staticmethod
    def _avg_living_length(prof: PartProfile) -> float:
        """V40新規: 部位の生存instance群のlengthの平均値。二層構造化に伴い、
        arm/leg等の「代表length」が必要な箇所(ground_limb_len・raw_speed等)は
        Profileの狙い値ではなく、生存中の各Instanceの実測lengthの平均を使う。"""
        if not prof:
            return 0.0
        living = prof.living_instances
        if not living:
            return 0.0
        return sum(inst.length for inst in living) / len(living)

    def elevation_of(self, instance: "PartInstance") -> float:
        """V26: 順運動学(FK)ベースの式。
        脚・腕のGroundLimbLen計算において生きているインスタンスのみを考慮。
        V40(未実装項目1対応): 引数をPartProfileからPartInstanceに変更し、
        instance自身の実測length/angleを使うようにした(左右非対称な個体差が
        Elevationにも反映されるようにするため)。
        2026/08/07改訂対応: angleを持つのはtorso・tailのみになったため、
        theta_partはtailの場合のみ非ゼロとなる(それ以外の9部位はangle=Noneなので常に0)。"""
        torso = self.get_part("torso")
        arm = self.parts.get("arm")
        leg = self.parts.get("leg")

        torso_angle = torso.instances[0].angle if torso.instances else 0.0
        quad_factor = 1.0 - max(0.0, min(1.0, (torso_angle or 0.0) / config.ANGLE_QUAD_DIVISOR))
        arm_len = self._avg_living_length(arm) if (arm and not arm.all_broken) else 0.0
        leg_len = self._avg_living_length(leg) if (leg and not leg.all_broken) else 0.0
        ground_limb_len = leg_len + arm_len * quad_factor
        torso_height = ground_limb_len * config.LEG_ELEV_COEF

        theta_torso = self._theta_torso  # ← 修正後は torso.angle のみに基づく固定値
        part_type = instance.profile.part_type
        theta_part = 0.0
        if part_type == "tail" and instance.angle is not None:
            theta_part = math.radians(instance.angle * config.ANGLE_UNIT_RAD_DEG)

        attach_height = BASE_ELEVATION.get(part_type, 3.0)

        elevation = (torso_height
                + attach_height * math.cos(theta_torso)
                + instance.length * math.sin(theta_torso + theta_part))

        assert not math.isnan(elevation), (
            f"elevation_of() returned NaN for {self.name}'s {instance.profile.name}. "
            f"torso_height={torso_height}, theta_torso={theta_torso}, instance.length={instance.length}, theta_part={theta_part}"
        )
        return elevation

    @property
    def tail_balance(self) -> float:
        """V40(未実装項目1対応): sizeは生存tail instanceの実測値の平均を使う。
        angleはtail(count>1でも)全インスタンス共有の値(確認事項②の確定方針)なので、
        代表として先頭の生存instanceの値を参照すればよい。"""
        tail = self.parts.get("tail")
        if not tail or tail.all_broken:
            return 0.0
        living = tail.living_instances
        avg_size = sum(inst.size for inst in living) / len(living)
        angle = living[0].angle if living[0].angle is not None else 0.0
        return 0.1 * min(angle, avg_size) + 0.03 * (angle + avg_size)

    @property
    def raw_speed(self) -> float:
        """V40(未実装項目1対応): legのlengthはProfileの狙い値ではなく、
        生存中の各leg instanceの実測lengthの平均を使う。"""
        leg = self.parts.get("leg")
        leg_len = self._avg_living_length(leg) if (leg and not leg.all_broken) else 0.0
        return (leg_len * 2) - (0.3 * self.weight) + self.tail_balance

    #@property
    #def speed_factor(self) -> float:
    #    return 1.0 - max(0.0, min(0.6, self.raw_speed * 0.03))

    @property
    def overweight_factor(self) -> float:
        """V35新規(機能未実装だった階級上限の実装): 戦闘開始時のビルド重量が
        検証対象階級(config.WEIGHT_TEST_CLASS)の上限を超えている場合、
        「勝ち目がないレベル」までspeed_factorを落とすための専用係数。
        speed_factorにのみ乗算し、Reach・攻撃力には影響させない。"""
        return config.OVERWEIGHT_SPEED_MULT if self.is_overweight else 1.0

    @property
    def speed_factor(self) -> float:
        """V35: 転倒による低下(旧TIPPING_SPEED_PENALTY)はmobility_factorへ分離した
        (calculate_miss_probability内でのみ使用)。speed_factor自体は転倒の影響を
        受けない。重量超過ペナルティ(overweight_factor)のみここに乗算する。
        speed_factorは値が低いほど速い(避けやすい)向きのため、overweight時は
        1.0(最遅・最も当てやすいクランプ上限)へ押し上げる形でoverweight_factorを
        乗算し、min(1.0, ...)でクランプする。"""
        base = 1.0 - max(0.0, min(0.6, self.raw_speed * 0.03))
        return min(1.0, base * self.overweight_factor)

    @property
    def mobility_factor(self) -> float:
        """V35新規(問題①関連の追加要求): 転倒状態(腕・脚以外の部位が接地している状態)
        による回避性能低下のみを表現する変数。speed_factor自体・Reach・攻撃力・
        width/着弾重量計算には一切使わず、calculate_miss_probability内のみで使用する。
        転倒中は素早さ半減(=当てられやすくなる)という想定でTIPPING_MOBILITY_MULTを乗じる。"""
        return config.TIPPING_MOBILITY_MULT if self.is_tipped else 1.0

    @property
    def effective_speed_factor(self) -> float:
        """V35新規: calculate_miss_probability専用の一時的な計算値。
        speed_factor * mobility_factor。width計算や着弾重量計算での使用は禁止。"""
        return self.speed_factor * self.mobility_factor

    @property
    def respiratory_factor(self) -> float:
        """V29新規: mouth/noseのsize・生存数から算出する「呼吸のしやすさ」。
        全身疲労の回復速度(update_part_fatigue)に直接影響する。mouth/noseをcount=0にする
        行為に明確なデメリットを持たせるための実装(従来はほぼノーコストだった)。
        標準投資(mouth1体+nose1体、size0)でRESP_BASELINE相当になるよう調整済み。"""
        total = 0.0
        for part_type in ("mouth", "nose"):
            prof = self.parts.get(part_type)
            if not prof:
                continue
            for inst in prof.living_instances:
                total += 1.0 + max(0.0, inst.size) * config.RESP_SIZE_COEF
        return total

    @property
    def visual_acuity(self) -> float:
        """V29新規: eyeのsize・生存数から算出する視覚acuity。攻撃側として使う場合、
        自分のmiss_probを下げる(狙いが正確になる)。
        V40: Profile共通値ではなくInstance固有の実測sizeを使う。"""
        prof = self.parts.get("eye")
        if not prof:
            return 0.0
        return sum(1.0 + max(0.0, inst.size) * config.EYE_SIZE_COEF for inst in prof.living_instances)

    @property
    def auditory_acuity(self) -> float:
        """V29新規: earのsize・生存数から算出する聴覚acuity。防御側として使う場合、
        相手のmiss_probを上げる(察知して避ける)。
        V40: Profile共通値ではなくInstance固有の実測sizeを使う。"""
        prof = self.parts.get("ear")
        if not prof:
            return 0.0
        return sum(1.0 + max(0.0, inst.size) * config.EAR_SIZE_COEF for inst in prof.living_instances)

    def is_intimidating(self) -> bool:
        """2026/08/07改訂対応: neckはangleを失った(torso・tailのみがangleを持つ)ため、
        判定対象をneck→torsoへ差し替えた(実装前確認事項で確定)。
        torsoは常にcount=1・angleロール無しなので代表値は単純にinstances[0]。
        tailはcount>1でも角度は全インスタンス共有(確認事項②)なので同様に代表可能。"""
        torso = self.parts.get("torso")
        tail = self.parts.get("tail")
        th = config.INTIMIDATION_ANGLE_THRESHOLD

        torso_angle = torso.instances[0].angle if (torso and torso.instances) else None
        torso_ok = torso_angle is not None and torso_angle >= th

        tail_living = tail.living_instances if tail else []
        tail_angle = tail_living[0].angle if tail_living else None
        tail_ok = bool(tail) and not tail.all_broken and tail_angle is not None and tail_angle >= th

        return torso_ok or tail_ok

    def intimidation_power_mult(self, opponent: "Creature") -> float:
        """V35新規: 威嚇のインターフェースのみ準備(戦闘処理への接続は禁止)。
        opponent(=このCreatureに威嚇されている側)の攻撃力に将来乗じる想定の係数。
        現時点ではどこからも呼び出されない(骨組みのみ)。"""
        if self.is_intimidating():
            return config.INTIMIDATION_POWER_MULT
        return 1.0

    def is_alive(self) -> bool:
        """1) core_hp<=0
        2) 元々count>=1で保有していた非vital部位の全PartInstanceが破壊された状態
        のいずれかで敗北判定。
        注意: 非vital部位を全てcount=0にすると、この2番目の条件はowned_nonvital_profsが
        空リストになるため実質的に機能しなくなる(coreHP0でしか負けなくなる)。これは特別な
        マイナス補正で塞ぐのではなく、mouth/nose(呼吸)・eye/ear(命中/回避)を実戦価値のある
        部位にすることで「非vital全部0」という選択自体を割に合わなくする方針とする(V29)。"""
        if self.core_hp <= 0:
            return False
        # V41: p.countが廃止されたため、「元々保有していたか」はp.instancesの有無で判定する
        # (build_instances()でinstance_count<=0だった部位はinstancesが空のまま=旧count<=0相当)。
        owned_nonvital_profs = [p for p in self.parts.values() if not p.is_vital and p.instances]
        if owned_nonvital_profs and all(p.all_broken for p in owned_nonvital_profs):
            return False
        return True

    def update_part_fatigue(self, used_part_name: str):
        """指定された部位名のインスタンスに疲労を蓄積し、それ以外は回復させる。
        V29: 回復量(decay)はrespiratory_factor(呼吸のしやすさ)に応じて増減する。
        呼吸能力が標準(RESP_BASELINE)なら従来通りの回復速度、mouth/noseを失うほど
        RESP_FLOOR_RATIOまで回復が遅くなる。"""
        if not config.ENABLE_PART_USAGE_FATIGUE:
            return
        resp_ratio = min(1.0, self.respiratory_factor / config.RESP_BASELINE) if config.RESP_BASELINE else 1.0
        effective_decay = config.PART_FATIGUE_DECAY_PER_TURN * (
            config.RESP_FLOOR_RATIO + (1.0 - config.RESP_FLOOR_RATIO) * resp_ratio
        )
        for name, profile in self.parts.items():
            for inst in profile.instances:
                if name == used_part_name:
                    inst.fatigue = min(config.PART_FATIGUE_MAX, inst.fatigue + config.PART_FATIGUE_GAIN_PER_USE)
                else:
                    inst.fatigue = max(0.0, inst.fatigue - effective_decay)

    @staticmethod
    def _instance_mass(inst: "PartInstance") -> float:
        """1インスタンスあたりの質量。Weight計算と同じスケール変換を使い、
        内部値(-2~+2)由来の負の質量が出ないようにする。
        V40: Profile共通値ではなくInstance固有の実測length/sizeを使う
        (左右非対称な個体差が重心位置にも反映されるようにするため)。"""
        length_scale = Creature._calc_scale(inst.length)
        size_scale = Creature._calc_scale(inst.size)
        return length_scale * size_scale

    @property
    def center_of_gravity(self) -> tuple:
        """支持部位(arm/leg)は接地点として姿勢に関わらずZ位置固定(足は地面についたまま)。
        それ以外の部位(head/neck/torso/tail/wing/mouth/nose/eye/ear)は、torsoの傾き
        (theta_torso)に応じてZ位置がcos(theta_torso)倍にシフトする。angle=3(90°)付近で
        ほぼ中心へ collapse し、angle=4(120°)では符号が反転して逆側へ寄る
        ―― 上体を反らせすぎると頭・尻尾の重心が支持多角形の外(あるいは逆側)へ
        抜けてしまい、転倒に繋がりうる、という意図的な結合。"""
        theta_torso = self._theta_torso
        support_types = ("arm", "leg")
        total_mass = 0.0
        sx = sz = 0.0
        for inst in self.living_instances():
            m = self._instance_mass(inst)
            x, base_z = inst.position
            z = base_z if inst.profile.part_type in support_types else base_z * math.cos(theta_torso)
            sx += m * x
            sz += m * z
            total_mass += m
        if total_mass <= 0:
            return (0.0, 0.0)
        return (sx / total_mass, sz / total_mass)

    @property
    def support_points(self) -> list:
        """接地点=生きているarm・legインスタンスの(x,z)座標。両方とも全滅していれば空リスト
        (支持多角形が存在しない=常に転倒扱いにする)。"""
        pts = []
        for part_type in ("arm", "leg"):
            prof = self.parts.get(part_type)
            if not prof:
                continue
            for inst in prof.living_instances:
                pts.append(inst.position)
        return pts

    def update_tipping_state(self) -> bool:
        """支持多角形(凸包)に対して重心が内側にあるかを判定し、is_tippedを更新する。
        「転倒状態へ新たに遷移した」場合のみTrueを返す(継続的な転倒中はFalse)。
        battle.py側はこのTrueを見て、次の1行動をスキップさせる。"""

        hull = self._convex_hull(self.support_points)
        cog = self.center_of_gravity

        now_tipped = not self._point_in_hull(
            cog,
            hull,
            config.TIPPING_TOLERANCE
        )

        became_tipped = now_tipped and not self.is_tipped
        self.is_tipped = now_tipped
        return became_tipped





