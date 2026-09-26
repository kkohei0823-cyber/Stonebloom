
"""
4X x 囲碁 x 将棋 ハイブリッドゲーム: ゲームエンジン本体
==========================================================
移動・戦闘・経済・勝利判定を実装する中核モジュール。ルールの正確な数値仕様は
検証ハンドブックを参照。CONFIGはconfig.pyから import するだけ（実体はそちら）。

未確定事項: 生産は1ラウンド1体までの制限は仮／本拠駐留駒の撃破時VP付与は
「本拠HP0=即時敗北」ルールと矛盾するため未実装（企画書9章）。
"""

import random
import itertools
from dataclasses import dataclass, field
from collections import Counter
from functools import lru_cache

from config import CONFIG

DIRECTIONS_4 = [(1, 0), (-1, 0), (0, 1), (0, -1)]


@lru_cache(maxsize=None)
def _adjacent_positions_cached(size, pos):
    """Board.adjacent_positions()の実体。(size, pos)だけで一意に決まるため、
    Boardインスタンス（盤面クローン）をまたいでプロセス全体で使い回せる。
    盤サイズは対局中・チューニング実行中を通じて種類が少ない（通常1種類）ため、
    実質的なユニークキー数は「マス数」程度に収まりキャッシュは即座に飽和する。"""
    x, y = pos
    result = []
    for dx, dy in DIRECTIONS_4:
        nx, ny = x + dx, y + dy
        if 0 <= nx < size and 0 <= ny < size:
            result.append((nx, ny))
    return result


# ============================================================
# 基本データ構造
# ============================================================
_uid_counter = itertools.count(1)

@dataclass
class Piece:
    kind: str
    owner: int
    hp: float
    max_hp: float
    atk: float
    movable: bool
    uid: int = field(default_factory=lambda: next(_uid_counter))

    def is_alive(self):
        return self.hp > 0


def make_piece(kind, owner, movable=True):
    cfg = CONFIG["pieces"][kind]
    return Piece(kind=kind, owner=owner, hp=cfg["hp"], max_hp=cfg["hp"],
                 atk=cfg["atk"], movable=movable)


class Board:
    def __init__(self, size):
        self.size = size
        self.grid = {}

    def in_bounds(self, pos):
        x, y = pos
        return 0 <= x < self.size and 0 <= y < self.size

    def adjacent_positions(self, pos):
        # (size, pos)だけで決まる純粋計算なのでプロセス全体でグローバルキャッシュ
        # （clone_gameのたびに再計算していたのが性能ボトルネックだったため）
        return _adjacent_positions_cached(self.size, pos)

    def distance(self, a, b):
        return abs(a[0] - b[0]) + abs(a[1] - b[1])

    def place(self, pos, piece):
        assert pos not in self.grid
        self.grid[pos] = piece

    def move(self, src, dst):
        self.grid[dst] = self.grid.pop(src)

    def remove_dead(self):
        dead = []
        for p, piece in list(self.grid.items()):
            if not piece.is_alive():
                dead.append((p, piece.owner, piece.kind))
                del self.grid[p]
        return dead

    def valid_move_targets(self, pos, move_range):
        """
        直線4方向、障害物（敵味方問わず）に当たったらそこで止まる。
        到達可能な空きマス全てを返す（プレイヤー/ボットはこの中から選べる）。
        """
        targets = []
        for dx, dy in DIRECTIONS_4:
            for step in range(1, move_range + 1):
                npos = (pos[0] + dx * step, pos[1] + dy * step)
                if not self.in_bounds(npos):
                    break
                if npos in self.grid:
                    break  # 障害物。これ以上進めない・このマスにも入れない
                targets.append(npos)
        return targets


def attribute_of(kind):
    """駒種の属性（"bind"/"pierce"/"crush"、属性なしはNone）を返す。"""
    return CONFIG["pieces"][kind].get("attribute")


def role_of(kind):
    """AIが駒種ごとの重み（配置/動員の好み、壁役・突撃役ボーナス等）を引くときの役割名。
    既存駒は自分自身、Sprigling（sprigling.register_sprigling()で登録）は性能値が
    最も近い既存の三すくみ駒種（歩兵/騎兵/重装兵）を返す。"""
    return CONFIG["pieces"][kind].get("role", kind)


def piece_speed(kind):
    """素早さ。未指定の駒種（既存5種・本拠）はCONFIG["combat"]["base_speed"]。"""
    return CONFIG["pieces"][kind].get("speed", CONFIG["combat"]["base_speed"])


def hit_count(attacker_kind, defender_kind):
    """multi_attack有効時の攻撃回数。素早さの比がthresholds[i]以上なら i+2 回。"""
    cb = CONFIG["combat"]
    if not cb["multi_attack"]:
        return 1
    ratio = piece_speed(attacker_kind) / max(piece_speed(defender_kind), 1e-9)
    n = 1
    for i, th in enumerate(cb["multi_attack_thresholds"]):
        if ratio >= th:
            n = i + 2
    return n


def damage_multiplier(attacker_kind, defender_kind):
    """分割後のダメージに掛ける倍率＝属性相性×攻撃回数。"""
    return type_multiplier(attacker_kind, defender_kind) * hit_count(attacker_kind, defender_kind)


def type_multiplier(attacker_kind, defender_kind):
    """属性相性（三すくみ）によるダメージ倍率（検証ハンドブック6章）。
    攻撃側・防御側の駒種が持つ属性（CONFIG["pieces"][kind]["attribute"]）で判定し、
    bind→pierce、pierce→crush、crush→bindの組み合わせのみ
    CONFIG["type_advantage"]["multiplier"]（既定2.0）を返す。既存駒では
    苔兵(歩兵,bind)→棘走(騎兵,pierce)→岩守(重装兵,crush)→苔兵。
    どちらかが属性なし（弓兵・工兵・本拠）や不利/同属性の組み合わせは1.0。"""
    atk_attr = attribute_of(attacker_kind)
    def_attr = attribute_of(defender_kind)
    if atk_attr is None or def_attr is None:
        return 1.0
    ta = CONFIG["type_advantage"]
    if ta["pairs"].get(atk_attr) == def_attr:
        return ta["multiplier"]
    return 1.0


def is_damage_nullified(attacker_kind, defender_kind):
    """駒種間の完全ダメージ無効化判定（検証ハンドブック0.32節・3.5節）。
    工兵は弓兵タイプの攻撃を距離を問わず常に無効化する
    （CONFIG["damage_immunity"]["pairs"]に(attacker_kind -> defender_kind)として登録）。

    type_multiplier（三すくみ、倍率補正）とは別枠の判定であり、こちらは倍率ではなく
    「そもそも対象にならない」。resolve_combat の target 抽出段階でこの判定を使い、
    無効化される相手は呼吸点方式の分割対象（母数）にも含めない。"""
    di = CONFIG.get("damage_immunity")
    if not di:
        return False
    return di["pairs"].get(attacker_kind) == defender_kind


def resolve_combat(board, verbose=False):
    """呼吸点方式ダメージ計算。弓兵のみ遠隔（隣接以外）攻撃可能。

    2026-08-08修正: 駒種相性（三すくみ）を実装。計算順序は検証ハンドブック6.2節の通り、
    ①呼吸点方式の分割（ATKを対象数で割る）を先に行い、②分割後の各対象への最終ダメージに
    対して、相性のある対象への分だけ倍率を乗算する（分割前のATK総量に先に倍率をかけると、
    相性のない対象への被害まで底上げしてしまう誤りになるため、この順序を厳守）。

    2026-08-12修正: 工兵の弓兵ダメージ無効化（0.32節）を実装。is_damage_nullified()で
    無効化対象と判定される相手は、そもそも targets（=呼吸点方式の分割母数）に含めない。
    倍率(type_multiplier)ではなく対象からの除外にしているのは、「無効化された相手がいる
    ことで他の対象への分割ダメージが薄まる」という不自然な副作用を避けるため
    （工兵は弓兵の攻撃対象として最初から存在しないものとして扱う）。    2026-09-26追加: 素早さ（CONFIG["combat"]）。既存5種は全て基準速度なので、
    既存駒どうしの戦闘結果はこの追加で一切変わらない。
      initiative   : 速い駒のグループから順に攻撃し、そのグループのダメージを適用して
                     撃破された駒は、それより遅いグループの攻撃に参加できない
                     （同じ速さどうしは従来通り同時）。
      multi_attack : 攻撃側の素早さが相手の2倍以上なら2回、3倍以上なら3回…と、
                     その相手へのダメージを回数倍する（damage_multiplier()参照）。"""
    cb = CONFIG.get("combat", {})
    attackers = [(pos, piece) for pos, piece in board.grid.items()
                 if piece.is_alive() and piece.atk > 0]
    if cb.get("initiative"):
        speeds = sorted({piece_speed(p.kind) for _, p in attackers}, reverse=True)
        tiers = [[(pos, p) for pos, p in attackers if piece_speed(p.kind) == sp] for sp in speeds]
    else:
        tiers = [attackers]

    damage = {}
    removed = []
    for tier in tiers:
        tier_damage = {}
        for pos, piece in tier:
            if not piece.is_alive():
                continue  # より速いグループの攻撃で既に撃破されている
            cfg = CONFIG["pieces"][piece.kind]
            if cfg["ranged"]:
                reach = 1 + cfg["range"]
                targets = [p for p in board.grid
                           if board.grid[p].owner != piece.owner
                           and board.grid[p].is_alive()
                           and board.distance(pos, p) <= reach
                           and not is_damage_nullified(piece.kind, board.grid[p].kind)]
            else:
                targets = [p for p in board.adjacent_positions(pos)
                           if p in board.grid and board.grid[p].owner != piece.owner
                           and board.grid[p].is_alive()
                           and not is_damage_nullified(piece.kind, board.grid[p].kind)]
            if not targets:
                continue
            dmg_each = piece.atk / len(targets)  # ①分割（相性適用前）
            for t in targets:
                mult = damage_multiplier(piece.kind, board.grid[t].kind)  # ②分割後に相性・連撃倍率
                tier_damage[t] = tier_damage.get(t, 0) + dmg_each * mult
        for pos, dmg in tier_damage.items():
            board.grid[pos].hp -= dmg
            damage[pos] = damage.get(pos, 0) + dmg
        if len(tiers) > 1:
            removed += board.remove_dead()
    removed += board.remove_dead()
    if verbose:
        for pos, dmg in damage.items():
            print(f"  {pos} に {dmg:.1f} ダメージ")
        if removed:
            print(f"  撃破: {removed}")
    return removed, damage


# ============================================================
# 経済
# ============================================================
class Economy:
    def __init__(self):
        cfg = CONFIG["economy"]
        self.rp = [cfg["initial_rp"], cfg["initial_rp"]]
        # 初期値1000は表示・内部計算上のバッファ（両者同額なので勝敗判定には無関係）
        self.cumulative_vp = [cfg["initial_vp"], cfg["initial_vp"]]  # 勝利条件はこちら（RP収入とは無関係）
        self.spot_owner = {}          # rp_spots・midgame_spots共通の占有遅延判定用
        self.spot_owned_since = {}
        # midgame_spotsのVP税の端数繰り越し（RP単位、rp_per_vp_upkeep未満）
        self.vp_tax_carry = [0, 0]

    def compute_income(self, board, round_number, engineer_positions):
        """RP経済。rp_spots(4-4点)とmidgame_spots(隅、2026-08-07新設)が対象。
        VPスポット(星・天元)はRPを生まない。

        2026-08-08修正（対人戦で発覚したバグ）: 「RPの増加=VPの減少」が機能していない
        不具合を修正。従来はmidgame_spotsのVP税を _accrue_spot_income 内で無条件に
        徴収していたが、RPには上限(rp_cap)があり、既にRPが上限に達している（または
        上限超過分がある）場合、そのRP収入は実際には1RPも増えない。にもかかわらず
        VP税だけは名目上の収入額（税引き前の収入）に基づいて満額徴収されてしまい、
        「RPが増えていないのにVPだけ減る」という非対称な挙動になっていた。

        2026-08-08(2)修正: 上の修正を「名目収入に対する実現割合(realized_ratio)を
        VP税全体に一括で乗算する」方式で入れたところ、RP上限にちょうど半端な位置で
        当たった場合にVPが小数点になってしまう副作用が判明（対人戦で報告）。
        base_income・rp_spots・midgame_spotsをこの順で1スポットずつ、そのつど
        RP上限との差分だけ整数のまま加算し、taxVpスポットについては「実際に加算できた
        RPの量」とちょうど同じ整数量だけVP税を課す方式に変更した。スポット単位の
        収入額(base)は元々すべて整数のため、この方式なら按分計算が発生せず、
        VPが小数点になることはない。

        2026-08-15追加: rp_cap(=10)に張り付いたまま生産しないターンが実戦で観測され、
        「RPが残っているのに生産していない」不可解な挙動として報告された。rp_capの
        存在により、そうしたターンの収入は上限超過分がそのまま切り捨てられ実質的に
        消滅する（貯めておいて後で使う、ができない）。この切り捨て量を可視化・
        スコア化できるよう、戻り値を income_total だけでなく
        (income_total, wasted_total) のタプルに変更した（wasted_total=名目収入のうち
        上限超過で加算されなかった量）。呼び出し側であるproduction_phase()の戻り値も
        合わせて変更している。
        """
        cfg = CONFIG["economy"]
        cap = cfg["rp_cap"]
        running_rp = list(self.rp)   # このラウンドの経済フェーズ処理中の暫定RP
        income_total = [0, 0]
        wasted_total = [0, 0]

        def add_income(owner, amount, rp_per_vp):
            """RP上限の残り枠ぶんだけ整数量を加算し、実際に加算できた量rp_per_vpにつき
            VP1の税を課す（rp_per_vp=Noneなら非課税）。amountがrp_capの残り枠を超える分は
            RPも増えずVP税も発生しない（「RPの増加=VPの減少」を1RP単位で厳密に対応）。
            rp_per_vpに満たない端数はvp_tax_carryに繰り越すため、VPは常に整数のまま。
            超過して切り捨てられた量はwasted_totalに積算する（2026-08-15追加）。"""
            room = max(cap - running_rp[owner], 0)
            gained = min(amount, room)
            running_rp[owner] += gained
            income_total[owner] += gained
            wasted_total[owner] += (amount - gained)
            if rp_per_vp:
                taxable = self.vp_tax_carry[owner] + gained
                self.cumulative_vp[owner] -= taxable // rp_per_vp
                self.vp_tax_carry[owner] = taxable % rp_per_vp

        add_income(0, cfg["base_income"], None)
        add_income(1, cfg["base_income"], None)
        self._accrue_spot_income(board, round_number, engineer_positions,
                                  CONFIG["rp_spots"], add_income, tax_vp=False)
        self._accrue_spot_income(board, round_number, engineer_positions,
                                  CONFIG["midgame_spots"], add_income, tax_vp=True)

        self.rp = running_rp
        return income_total, wasted_total

    def _accrue_spot_income(self, board, round_number, engineer_positions,
                             spot_cfg, add_income, tax_vp):
        """rp_spots・midgame_spots共通の収入計算(2026-08-07新設)。
        スポット1つごとに add_income(owner, amount, rp_per_vp) を呼び出す
        （2026-08-08(2)修正。理由はcompute_incomeのコメント参照）。
        spot_cfgに"start_round"が無ければ常時解禁扱い（既存のrp_spotsはこれに該当し、
        挙動は変更前と同じ）。

        2026-08-16修正（4.30節）: 工兵ボーナスの判定を、別途保持している
        `engineer_positions`（Game.engineer_positions、place/moveでのみ更新される
        座標集合）の座標一致ではなく、`board.grid.get(pos).kind == "工兵"`という
        「そのマスに今いる駒の実体」を直接見る方式に変更した。
        `engineer_positions`は駒が撃破されて盤面から消えても該当座標がクリアされず
        （resolve_combat/remove_deadがこの集合を一切関知しない）、その後別の駒
        （工兵以外・別プレイヤーでもよい）が同じマスに来ると、実体は工兵でないのに
        ボーナスだけ亡霊のように残り続けるバグがあったため
        （検証ハンドブック4.30節参照）。engineer_positions引数は他モジュールからの
        呼び出し互換のためシグネチャ上は残しているが、収入計算では使用しない。"""
        cfg = CONFIG["economy"]
        start_round = spot_cfg.get("start_round", 0)
        active = round_number >= start_round
        rp_per_vp = spot_cfg["rp_per_vp_upkeep"] if tax_vp else None

        for pos in spot_cfg["points"]:
            piece = board.grid.get(pos)
            if piece is None:
                continue
            owner = piece.owner
            if self.spot_owner.get(pos) != owner:
                self.spot_owner[pos] = owner
                self.spot_owned_since[pos] = round_number
            held = round_number - self.spot_owned_since[pos]
            if not active or held < spot_cfg["capture_income_delay"]:
                continue
            base = spot_cfg["income"]
            if piece.kind == "工兵":
                base += cfg["engineer_bonus"]
            add_income(owner, base, rp_per_vp)

    def compute_vp(self, board, round_number):
        """勝利条件(VP)。3-3点(星)と天元が対象。4ラウンド目から加算開始。"""
        vs = CONFIG["vp_spots"]
        vp_gained = [0, 0]
        if round_number >= vs["count_start_round"]:
            for pos in vs["stars"]:
                piece = board.grid.get(pos)
                if piece is not None:
                    vp_gained[piece.owner] += vs["star_vp"]
            for pos in vs["tengen"]:
                piece = board.grid.get(pos)
                if piece is not None:
                    vp_gained[piece.owner] += vs["tengen_vp"]
        for i in range(2):
            self.cumulative_vp[i] += vp_gained[i]
        return vp_gained

    def pay_upkeep(self, board):
        upkeep = [0, 0]
        for piece in board.grid.values():
            u = CONFIG["pieces"][piece.kind]["upkeep"]
            upkeep[piece.owner] += u
        for i in range(2):
            self.rp[i] = max(0, self.rp[i] - upkeep[i])
        return upkeep


# ============================================================
# ゲーム本体
# ============================================================
class Game:
    def __init__(self, roster=None, extra_reserve=None):
        """roster: {player: 動員できる駒種の集合}（省略時・Noneの要素はCONFIG["pieces"]の全駒種）。
        検証環境で片方のプレイヤーにだけSpriglingを使わせる、といった非対称な対局に使う。
        extra_reserve: {player: [駒種...]} 最初の手駒に追加する駒（持ち込んだSprigling等）。"""
        self.board = Board(CONFIG["board_size"])
        self.econ = Economy()
        extra_reserve = extra_reserve or {}
        self.reserve = {p: list(CONFIG["starting_reserve"]) + list(extra_reserve.get(p) or [])
                        for p in (0, 1)}
        self.engineer_positions = set()
        self.round_number = 0
        self.turn_order = [0, 1]  # パイルールでスワップされうる（現在は凍結）
        self.first_move_done = False
        self.pie_rule_resolved = not CONFIG["pie_rule"]
        self.winner = None
        self.win_reason = None
        # 2026-09-12追加: 投了機能用。win_reason == "resigned" のとき、実際に
        # 投了したプレイヤー番号（winnerはその相手）をここに記録する。
        # winner/win_reasonだけでは「誰が投了したか」を区別できない
        # （winnerは常に投了していない側になるため一意に定まるはずだが、
        # 将来の拡張時の取り違えを防ぐため明示的に持たせておく）。
        self.resigned_player = None
        # 再配置禁止ルール: {pos: (禁止対象player, 禁止が適用されるラウンド番号)}
        self.placement_ban = {}
        self.tengen_owner_log = []  # 天元の所有者推移（診断用）: [(round, owner or None), ...]
        self.kifu = []  # 棋譜: ラウンドごとの手・ダメージ・盤面スナップショット
        # 2026-09-09追加: 各プレイヤーが対局中に撃破した敵駒の累計数
        # （first_kill_momentum用）。resolve_combatの戦闘解決結果を反映する
        # run()側で更新する。search_bot_skeleton.pyのclone_game()/
        # resolve_round_end()も同じ形で更新すること（探索中の仮想対局でも
        # 「まだ誰も撃破していない」という初期状態からのモメンタム発生を
        # 正しくシミュレートするため）。
        self.total_kills = [0, 0]
        roster = roster or {}
        self.roster = {p: (frozenset(roster[p]) if roster.get(p) is not None else None)
                       for p in (0, 1)}

        for owner, pos in CONFIG["base_positions"].items():
            self.board.place(pos, make_piece("本拠", owner, movable=False))

    # --------------------------------------------------------
    def valid_actions(self, player):
        """('place', kind, pos) / ('move', src, dst) / ('pass',) のリストを返す"""
        actions = []
        rp = self.econ.rp[player]

        # 2026-08-28修正: 従来はself.reserve[player]（同じ駒種が複数入りうる
        # multiset）をそのままループしていたため、同じ("place", kind, pos)が
        # reserve内の駒種の個数だけ重複してactionsに入っていた。1ターンに
        # 複数個同時設置することはできない（reserveの個数は「配置できる回数の
        # 上限」であって「1手で選べる選択肢の数」ではない）ため、選択肢としては
        # 種類（set）だけ見れば十分で、重複は生成上の無駄かつ副作用がある:
        # HeuristicBot._score_actionsが候補ごとに独立してjitterを加えるため、
        # 同一の(kind, pos)が複数エントリとして残っていると「たまたま在庫を
        # 2個以上持っている駒種」だけ乱数を複数回引けることになり、統計的に
        # 有利になってしまう（本来は在庫数と無関係であるべき）。加えて、
        # choose_with_debug()のcandidatesログにも同じ手が値違いで重複表示され、
        # デバッグ時に紛らわしかった（本来別の8件が見えるはずの枠を消費する）。
        # 2026-08-28修正(2): set()によるdedupはPythonのハッシュ乱数化
        # (PYTHONHASHSEED)の影響で文字列setの反復順序がプロセスごとに変わり、
        # 生成されるactionsのリスト順が変わってしまう。jitter=0時は複数の
        # actionが厳密に同点になり得るため、tie-break（rng.choice(best_actions)）
        # の対象となるリストの並び順が変わると、同じ重み・同じ乱数シードでも
        # 選ばれる手が変わってしまう（puzzle_generator.pyが前提とする
        # 「jitter=0なら完全決定論」を壊す重大な副作用だった）。
        # dict.fromkeys()はsetと同じ重複排除をしつつ、追加順（＝reserveの並び順、
        # これはgame進行にのみ依存し環境非依存）を保つため、これで置き換える。
        for kind in dict.fromkeys(self.reserve[player]):
            cost = CONFIG["pieces"][kind]["cost"]
            if cost > rp:
                continue
            for x in range(self.board.size):
                for y in range(self.board.size):
                    pos = (x, y)
                    if pos in self.board.grid:
                        continue
                    if CONFIG["recapture_ban_enabled"]:
                        ban = self.placement_ban.get(pos)
                        if ban is not None and ban[0] == player and ban[1] == self.round_number:
                            continue
                    actions.append(("place", kind, pos))

        move_cost = CONFIG["economy"]["move_action_cost"]
        if move_cost <= rp:
            for pos, piece in list(self.board.grid.items()):
                if piece.owner != player or not piece.movable:
                    continue
                move_range = CONFIG["pieces"][piece.kind]["move"]
                for dst in self.board.valid_move_targets(pos, move_range):
                    if CONFIG["recapture_ban_enabled"]:
                        ban = self.placement_ban.get(dst)
                        if ban is not None and ban[0] == player and ban[1] == self.round_number:
                            continue  # 移動での再配置禁止マスへの侵入も禁止（配置と同様に扱う）
                    actions.append(("move", pos, dst))

        actions.append(("pass",))
        return actions

    def apply_action(self, player, action):
        if action[0] == "place":
            _, kind, pos = action
            cost = CONFIG["pieces"][kind]["cost"]
            self.econ.rp[player] -= cost
            self.reserve[player].remove(kind)
            self.board.place(pos, make_piece(kind, player))
            if kind == "工兵":
                self.engineer_positions.add(pos)
        elif action[0] == "move":
            _, src, dst = action
            self.econ.rp[player] -= CONFIG["economy"]["move_action_cost"]
            if src in self.engineer_positions:
                self.engineer_positions.discard(src)
                self.engineer_positions.add(dst)
            self.board.move(src, dst)
        # pass: 何もしない

    def maybe_resolve_pie_rule(self, first_action):
        """初手直後、後手がスワップするか判定（ヒューリスティック）"""
        if self.pie_rule_resolved:
            return
        self.pie_rule_resolved = True
        if first_action[0] != "place":
            return
        _, kind, pos = first_action
        vs = CONFIG["vp_spots"]
        es = CONFIG["rp_spots"]
        ms = CONFIG["midgame_spots"]
        special_positions = set(vs["stars"]) | set(vs["tengen"]) | set(es["points"]) | set(ms["points"])
        if pos in special_positions:
            # スワップ: 駒の所有者を反転する
            self.board.grid[pos].owner = 1
            if CONFIG["pie_rule_swap_turn_order"]:
                self.turn_order = [1, 0]

    def check_base_destroyed(self):
        alive_bases = {p.owner for p in self.board.grid.values() if p.kind == "本拠"}
        destroyed = [owner for owner in (0, 1) if owner not in alive_bases]
        if not destroyed:
            return False
        if len(destroyed) == 2:
            # 同時破壊: 「後手勝ち」を機械的に確定させず、score_and_finish と同じ
            # 基準（累計VP→総HP→...）で決着をつける。本拠HPは両者0なのでその段は飛ばす。
            s0, s1 = self.econ.cumulative_vp
            if s0 != s1:
                self.winner = 0 if s0 > s1 else 1
                self.win_reason = "base_destroyed_simultaneous_vp_tiebreak"
                return True
            total_hp = [0, 0]
            for p in self.board.grid.values():
                total_hp[p.owner] += p.hp
            if total_hp[0] != total_hp[1]:
                self.winner = 0 if total_hp[0] > total_hp[1] else 1
                self.win_reason = "base_destroyed_simultaneous_hp_tiebreak"
                return True
            self.winner = None
            self.win_reason = "draw_simultaneous_destruction"
            return True

        owner = destroyed[0]
        self.winner = 1 - owner
        self.win_reason = "base_destroyed"
        return True

    def resign(self, player):
        """2026-09-12追加: `player`が投了する（play_vs_ai.htmlの投了ボタン用）。
        既に対局が終了している場合は何もしない（多重クリック・対局終了後の
        誤送信からの二重処理を防ぐ）。winnerはもう片方のプレイヤーになり、
        win_reasonは"resigned"、resigned_playerに実際に投了した側を記録する
        （kifu_json()・local_viewerでの表示用）。"""
        if self.winner is not None:
            return
        self.winner = 1 - player
        self.win_reason = "resigned"
        self.resigned_player = player

    def score_and_finish(self):
        s0, s1 = self.econ.cumulative_vp
        if s0 != s1:
            self.winner = 0 if s0 > s1 else 1
            self.win_reason = "vp_score"
            return
        base_hp = {}
        for p in self.board.grid.values():
            if p.kind == "本拠":
                base_hp[p.owner] = p.hp
        h0, h1 = base_hp.get(0, 0), base_hp.get(1, 0)
        if h0 != h1:
            self.winner = 0 if h0 > h1 else 1
            self.win_reason = "base_hp_tiebreak"
            return
        total_hp = [0, 0]
        for p in self.board.grid.values():
            total_hp[p.owner] += p.hp
        if total_hp[0] != total_hp[1]:
            self.winner = 0 if total_hp[0] > total_hp[1] else 1
            self.win_reason = "total_hp_tiebreak"
            return
        self.winner = 1
        self.win_reason = "second_player_default"

    def owned_piece_count(self, player):
        """本拠を除く、そのプレイヤーの「持ち駒」＝reserve（まだ盤面に配置していない
        手持ちの駒）の数を返す。企画書2.2節の保有上限9体判定に使う
        （2026-08-25追加、2026-08-30訂正）。

        2026-08-30訂正: 検証ハンドブックでは「持ち駒（reserve）」という語が
        一貫してreserveのみを指す用語として使われており（0.38節・4.28節等）、
        企画書2.2節の「保有上限は本拠を除き9体」もこれに従うのが正しい解釈
        だった。旧実装は「reserve＋盤上」を合算していたため、盤上に駒を
        展開しているだけで上限に達してしまい、盤面での兵力そのものには
        本来上限がないにもかかわらず生産が止まってしまう誤りがあった
        （盤面の駒数自体は9x9=81マスあるboard_sizeの範囲でのみ制約される）。
        """
        return len(self.reserve[player])

    def production_phase(self, bots):
        """
        新ルール: アップキープ時、RPを使って好きな駒種を1体、手番を消費せずに
        手持ち(reserve)へ追加できる（配置自体は次以降のターンで別途行う）。
        1ラウンドにつき生産は0〜1体まで（連続生産で経済が破綻しないための仮の制限。要検証）。
        bot に choose_production(game, player, affordable_kinds) が実装されていれば使う。
        未実装のbotは常に「生産しない」を選ぶ。

        2026-08-25追加: 企画書71行目「保有上限は本拠を除き9体」を実装。上限に
        達している（またはそれ以上の）プレイヤーは、RPが足りていても
        affordableを空にし、生産選択自体をスキップする（絶対ルール。
        choose_production側の判断より優先する）。
        2026-08-30訂正: 上限は「持ち駒(reserve)」のみに対する制限であり、
        盤上の駒数は含まない（owned_piece_count()のdocstring参照）。
        """
        produced = {0: None, 1: None}
        for player in (0, 1):
            bot = bots[player]
            if not hasattr(bot, "choose_production"):
                continue
            if self.owned_piece_count(player) >= CONFIG["max_owned_pieces"]:
                continue
            allowed = self.roster[player]
            affordable = [k for k in CONFIG["pieces"] if k != "本拠"
                          and (allowed is None or k in allowed)
                          and CONFIG["pieces"][k]["produce_cost"] <= self.econ.rp[player]]
            if not affordable:
                continue
            kind = bot.choose_production(self, player, affordable)
            if kind is None:
                continue
            assert kind in affordable, f"choose_productionが購入不可能な駒種を返した: {kind}"
            self.econ.rp[player] -= CONFIG["pieces"][kind]["produce_cost"]
            self.reserve[player].append(kind)
            produced[player] = kind
        return produced

    def run(self, bots, verbose=False):
        """bots: {0: bot_obj, 1: bot_obj}  bot_obj.choose(game, player, actions) -> action"""
        safety = CONFIG["max_rounds_safety"]
        while self.round_number < CONFIG["turn_limit_per_player"] and safety > 0:
            safety -= 1
            self.round_number += 1
            if CONFIG["alternate_initiative_each_round"] and self.round_number % 2 == 0:
                order = list(reversed(self.turn_order))
            else:
                order = self.turn_order
            round_actions = {}
            turn_snapshots = []

            def _serialize_action(a):
                if a[0] == "place":
                    return [a[0], a[1], list(a[2])]
                elif a[0] == "move":
                    return [a[0], list(a[1]), list(a[2])]
                return list(a)

            def _board_snapshot():
                return [
                    {"pos": list(pos), "kind": p.kind, "owner": p.owner,
                     "hp": round(p.hp, 1), "max_hp": p.max_hp}
                    for pos, p in self.board.grid.items()
                ]

            for player in order:
                actions = self.valid_actions(player)
                bot = bots[player]
                # choose_with_debug()を持つbotなら候補手の評価値も棋譜に記録する
                if hasattr(bot, "choose_with_debug"):
                    debug_info = bot.choose_with_debug(self, player, actions)
                    action = debug_info["action"]
                    candidates = debug_info["candidates"]
                    random_pick = debug_info["random_pick"]
                else:
                    action = bot.choose(self, player, actions)
                    candidates = None
                    random_pick = None
                self.apply_action(player, action)
                round_actions[player] = action

                if not self.first_move_done:
                    self.first_move_done = True
                    self.maybe_resolve_pie_rule(action)

                # このターン単体の状態（まだ戦闘解決前 = ダメージ・撃破は未反映）。
                # ビューアーでラウンド単位ではなく1手ごとに進行を追えるようにするための記録。
                turn_snapshots.append({
                    "player": player,
                    "action": _serialize_action(action),
                    "board": _board_snapshot(),
                    "rp": list(self.econ.rp),
                    # 着手適用後の持ち駒（reserve）。ビューアーでのAI判断確認用
                    "reserve": {0: list(self.reserve[0]), 1: list(self.reserve[1])},
                    "candidates": (
                        [{"action": _serialize_action(c["action"]), "value": c["value"]}
                         for c in candidates]
                        if candidates is not None else None
                    ),
                    "random_pick": random_pick,
                })

            # 戦闘解決は両者が1手ずつ打ち終えた後、ラウンドに1回だけ行う
            # （手番内の後出し優位を解消するための変更）
            removed, damage = resolve_combat(self.board, verbose=verbose)
            # 駒を失った側は、次のラウンドに限りその地点への再配置を禁止する
            vs = CONFIG["vp_spots"]
            vp_spot_positions = set(vs["stars"]) | set(vs["tengen"])
            vp_kill_bonus = [0, 0]
            for pos, owner, kind in removed:
                self.placement_ban[pos] = (owner, self.round_number + 1)
                # 2026-09-09追加: 撃破数の累計（first_kill_momentum用）。ownerは
                # 「駒を失った側」なので、撃破した側は1-owner。
                self.total_kills[1 - owner] += 1
                # 撃破された工兵はengineer_positionsからも除去（他モジュール参照用の防御的対応）
                if kind == "工兵":
                    self.engineer_positions.discard(pos)
                if pos in vp_spot_positions:
                    # VPスポットに駐留していた駒が撃破された → 相手にボーナスVP
                    beneficiary = 1 - owner
                    self.econ.cumulative_vp[beneficiary] += vs["kill_bonus_vp"]
                    vp_kill_bonus[beneficiary] += vs["kill_bonus_vp"]

            base_destroyed = self.check_base_destroyed()

            _income_total, rp_wasted = self.econ.compute_income(
                self.board, self.round_number, self.engineer_positions)
            self.econ.compute_vp(self.board, self.round_number)
            self.econ.pay_upkeep(self.board)  # 現行ルールでは維持費0のため実質no-op（将来の維持費復活に備えて残置）

            # 2026-09-06追加: 生産フェーズ直前（＝戦闘解決・収入計算は終わっているが、
            # まだ何も生産していない時点）のRP・持ち駒のスナップショット。
            # local_viewer/play_vs_aiの表示統一のため、「戦闘解決」ステップと
            # 「生産（アップキープ）」ステップを別々のコマとして再生できるように
            # する目的で追加した（以前はラウンド終了時点＝生産後の値しか棋譜に
            # 残っておらず、戦闘解決直後の状態を正しく再現できなかった）。
            rp_after_combat = list(self.econ.rp)
            reserve_after_combat = {0: list(self.reserve[0]), 1: list(self.reserve[1])}

            produced = self.production_phase(bots) if CONFIG["production_enabled"] else {0: None, 1: None}

            tengen_pos = CONFIG["vp_spots"]["tengen"][0]
            tengen_piece = self.board.grid.get(tengen_pos)
            self.tengen_owner_log.append((self.round_number, tengen_piece.owner if tengen_piece else None))

            board_snapshot = _board_snapshot()
            self.kifu.append({
                "round": self.round_number,
                "actions": {p: _serialize_action(a) for p, a in round_actions.items()},
                "turns": turn_snapshots,
                "damage": {f"{pos[0]},{pos[1]}": round(d, 1) for pos, d in damage.items()},
                "deaths": [{"pos": list(pos), "owner": owner, "kind": kind} for pos, owner, kind in removed],
                "board": board_snapshot,
                "rp": list(self.econ.rp),
                "vp": list(self.econ.cumulative_vp),
                # ラウンド終了時点（生産フェーズ後）の持ち駒
                "reserve": {0: list(self.reserve[0]), 1: list(self.reserve[1])},
                # 2026-09-06追加: 生産フェーズ直前（戦闘解決・収入計算後）のRP・持ち駒。
                # 上のrp/reserveは生産後の最終値なので、この2つと併せて持つことで
                # 「戦闘解決」と「生産」を別コマとして再生できる（無ければ古い棋譜との
                # 後方互換のため、閲覧側はrp/reserveへフォールバックする）。
                "rp_after_combat": rp_after_combat,
                "reserve_after_combat": reserve_after_combat,
                # rp_cap超過で切り捨てられたRP収入（compute_income()参照）
                "rp_wasted": list(rp_wasted),
                "produced": {p: k for p, k in produced.items() if k is not None},
                "vp_kill_bonus": {p: v for p, v in enumerate(vp_kill_bonus) if v > 0},
            })

            if base_destroyed:
                return self.result()

        self.score_and_finish()
        return self.result()

    def result(self):
        return {
            "winner": self.winner,
            "reason": self.win_reason,
            "rounds": self.round_number,
            "cumulative_vp": tuple(self.econ.cumulative_vp),
            # 2026-09-12追加: 投了機能用（web_api.kifu_json()と同じフィールド。
            # 通常の対局ではNoneのまま）。
            "resigned_player": self.resigned_player,
        }


# ============================================================
# ランダムボット
# ============================================================
class RandomBot:
    def choose(self, game, player, actions):
        return random.choice(actions)

    def choose_production(self, game, player, affordable_kinds):
        # 50%の確率で生産をパスし、残りはランダムな駒種を1体生産する
        if random.random() < 0.5:
            return None
        return random.choice(affordable_kinds)


# ============================================================
# バッチ実行・集計
# ============================================================
def run_batch(n_games=300, verbose_first=False):
    results = []
    for i in range(n_games):
        random.seed()  # 明示的にシード固定したい場合はここを random.seed(i) に変更
        game = Game()
        bots = {0: RandomBot(), 1: RandomBot()}
        res = game.run(bots, verbose=(verbose_first and i == 0))
        results.append(res)

    total = len(results)
    win_counts = Counter(r["winner"] for r in results)
    reason_counts = Counter(r["reason"] for r in results)
    avg_rounds = sum(r["rounds"] for r in results) / total

    print("=" * 60)
    print(f"ランダムボット同士 {total} 戦の集計結果")
    print("=" * 60)
    print(f"先手(player0)勝率: {win_counts[0] / total * 100:.1f}%  ({win_counts[0]}勝)")
    print(f"後手(player1)勝率: {win_counts[1] / total * 100:.1f}%  ({win_counts[1]}勝)")
    print(f"平均決着ラウンド数: {avg_rounds:.1f}")
    print("勝敗理由の内訳:")
    for reason, count in reason_counts.most_common():
        print(f"  {reason}: {count}件 ({count / total * 100:.1f}%)")
    print()
    print("※ ランダムボットは『意味のある戦略』を取らないため、")
    print("  この結果はあくまで『ルールが正常に動作しているか』の一次確認用です。")
    print("  先手/後手の勝率差やパイルールの効果を測るには、")
    print("  次段階のヒューリスティックボット導入が必要です。")
    return results


if __name__ == "__main__":
    run_batch(n_games=300, verbose_first=False)





