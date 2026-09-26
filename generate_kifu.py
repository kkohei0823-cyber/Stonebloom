"""
棋譜生成スクリプト（2026-08-22統合: 旧main.pyを吸収 / 2026-08-25非ミラー戦対応）
==================
game.py のゲームエンジンと、HeuristicBot / SearchBot のいずれかを使って対局を
実行し、結果を kifu_data_<タイムスタンプ>.json に保存する。

使い方:
  python3 generate_kifu.py                         # 既定: HeuristicBot、seed 1,2,3 で3試合
  python3 generate_kifu.py 10 11 12 13              # 好きなseedを指定して試合数を変更（HeuristicBot）
  python3 generate_kifu.py --bot search             # SearchBot(depth=2, candidate_k=10)同士を1局対戦（0.95節によりdepth2が既定。旧0.77節のdepth3既定は運用コスト実測を踏まえて撤回）
                                                      # （seed省略時はOSエントロピーで初期化、旧main.py相当）
  python3 generate_kifu.py --bot search 5 6         # SearchBotをseed指定で再現可能に対戦させる（新機能）
  python3 generate_kifu.py --bot search --depth 1 --candidate-k 6
  python3 generate_kifu.py --bot search --debug     # 候補手ログ(choose_with_debug)を
                                                      # コンソールにも出力する（2026-09-06変更:
                                                      # 既定は無効。同内容は棋譜ファイルに
                                                      # 記録されるようになったため、通常は
                                                      # コンソールで確認する必要がない）

非ミラー戦（異なる重み設定同士の対局。2026-08-25追加）:
  meta_diversity_check.pyは総当たり勝率の集計しかできず、「実際にどう指されて
  本拠が落ちたか」を棋譜で追えなかった。以下でHeuristicBot同士を異なる重み
  設定で対局させ、通常どおりkifu_data_<タイムスタンプ>.jsonとして保存できる
  （build_tools.pyのビューアーでそのまま再生可能）。

  python3 generate_kifu.py --archetype-a rush_kill --archetype-b turtle_vp 1 2 3 4
      # meta_diversity_check.py のARCHETYPES定義をそのまま流用（本拠特攻の
      # 再現に最適）。player0=A側(rush_kill)、player1=B側(turtle_vp)。
  python3 generate_kifu.py --archetype-a rush_kill
      # B側を省略するとbalanced（重み無補正=weights.pyのBASE_WEIGHTSそのまま）
      # 扱いになる。
  python3 generate_kifu.py --weights-a my_rush.json --weights-b my_turtle.json
      # 自作の重み上書きJSON（{"attack": 1.8, "kill_bonus": 40.0, ...}の
      # ような差分ファイル）を直接指定することもできる。--dump-weightsで
      # 書き出したファイルもそのまま読み込める。
  python3 generate_kifu.py --archetype-a rush_kill --archetype-b turtle_vp --swap-sides
      # A/Bを入れ替えてplayer0=B側、player1=A側にする（先手/後手両方から
      # 防御側の動きを検証したい場合用）。
  （--weights-a/-b と --archetype-a/-b は片側ごとに排他。--archetype-a/-b は
   --bot heuristic 専用で、--bot searchと同時指定はエラーにする）

チャンピオン同士の対局（2026-09-01追加。champion_tools.pyのbattle等は勝率集計
のみで棋譜を残さないため、SearchBot同士を任意の重み設定で対局させて棋譜を
残せるようにした）:
  python3 generate_kifu.py --bot search \\
      --weights-a champion_weights.json --weights-b champion_history/gen_0000.json \\
      --depth 2 1 2 3 4
      # --weights-a/-b にはchampion_weights.json/champion_history/gen_XXXX.json
      # （{"weights": {...}}形式）をそのまま指定できる。素の重み辞書
      # （{"attack": ..., ...}のような上書きJSON）も従来どおり指定可能。
      # B側を省略するとbalanced（重み無補正）扱いになる点はheuristicと同じ。

Optuna DBから直接trialの重み設定を読み込む（2026-09-06追加）:
  戦績が極端に悪いtrialなど、championに昇格しなかった（=champion_history/に
  残らない）trialの重み設定を直接調べたい場合に使う。tune_balance_search.py
  実行時に使ったのと同じ --storage / --study-name を指定し、--trial-a/-b に
  trial番号を渡すと、そのtrialのparamsをOptunaのstudy DBから読み込んで
  resolve_weights()済みの完全な重み設定として使う（--tune pieces/both で
  駒コスト等もチューニングしていた場合はCONFIG["pieces"]にも自動反映する）。
  --archetype-a/-b・--weights-a/-b と同じ枠（片側ごとに排他）で使う。

  ★重要（2026-09-06追加の注意）: tune_balance_search.pyの評価（trialのscore算出も
  championとの対戦も）は常にSearchBotで行われ、HeuristicBotは一切使われない。
  そのため--trial-a/-bを使う場合、--botを指定しなければ自動的に--bot search
  （tune_balance_search.py既定と同じdepth=2, candidate_k=10）になる
  （--bot heuristicを明示した場合は使えるが、元trialの戦績とは全く別の
  アルゴリズムでの対局になり、勝敗が再現されない旨の警告が出る。実際に
  「0勝100敗だったtrialを--bot指定なしで棋譜化したら勝っていた」という事故が
  発生した原因はこれだった）。

  python3 generate_kifu.py --storage sqlite:///balance_tuning_search.db \\
      --study-name search_balance_weights_260905_v3 --trial-a 6 1 2 3 4
      # trial 6の重み設定をSearchBot（既定で自動選択）でplayer0側に読み込み、
      # player1側はbalanced(無補正)として対局（戦績が悪かったtrial単体の挙動を見る）。
  python3 generate_kifu.py --storage sqlite:///balance_tuning_search.db \\
      --study-name search_balance_weights_260905_v3 \\
      --trial-a 6 --weights-b champion_weights.json 1 2 3 4
      # trial 6 vs 実際に対戦していたchampionの重み、という形で当時の
      # championマッチを再現する（--trial-b <championのtrial番号> でも可）。
      # tune_balance_search.py実行時に--depth/--candidate-kを既定値から
      # 変えていた場合は、ここでも同じ値を--depth/--candidate-kで指定すること
      # （既定同士なら省略可）。

重み設定ファイルの書き出し（2026-08-25追加）:
  weights.py の現在のHEURISTIC_WEIGHTS（プレイヤーがweights.py本体を直接
  書き換えて試している最中の値も含む）を、上書き事故を避けるため必ず
  タイムスタンプ付きファイル名で書き出す。
  なお読み込み側（--weights-a/-b、および本機能で読み込む既存ファイル）は
  標準のjson.loadを使っており、Pythonのjson実装は元々「同じキーが複数回
  出てきたら最後の出現を採用する」動作のため、末尾に上書き用の行を追記
  しただけの（キーが重複した）JSONもそのまま「後勝ち」で正しく読み込める。
  書き出し側は通常のPython辞書からdumpするため重複キーは構造的に発生しない。

  python3 generate_kifu.py --dump-weights
      # weights_dump_<タイムスタンプ>.json に書き出して終了（対局は行わない）
  python3 generate_kifu.py --dump-weights my_snapshot
      # my_snapshot_<タイムスタンプ>.json という名前で書き出す

2026-08-22統合の経緯:
  旧main.pyは「SearchBot(depth=2, candidate_k=10, debug=True)同士を1局対戦させ
  kifu_data_debug.jsonへ固定名で上書き保存する」専用スクリプトだった。ボット種別が
  違うだけでgenerate_kifu.pyとほぼ同じ処理（対局→保存→ビューアー自動生成）を
  重複実装していたため、--bot オプションでこちらに統合した。
  なお旧main.pyの「kifu_data_debug.jsonへの固定名上書き」は行わない（下記の
  2026-08-03節でgenerate_kifu.py自身がこの上書き事故パターンをやめた経緯があり、
  main.py側だけ古い方式のまま残すのは一貫性がなく、再度同種の事故が起こり得たため）。
  旧main.pyを直接実行していたコマンドは `python3 generate_kifu.py --bot search` に
  読み替えること。
  あわせて、旧main.pyの出力データには`midgame_spots`キーが欠けていた（generate_kifu.py側
  は2026-08-07に追加済み）という地味なスキーマ不一致も、統合により解消された。

2026-08-03 修正: 大量の棋譜を扱う際に上書き事故が起きないよう、
出力ファイル名に実行時刻（マイクロ秒まで）を含めるように変更した。
以前の固定名 kifu_data.json は使用しない。

生成した kifu_data_<タイムスタンプ>.json は
  python3 build_tools.py viewer kifu_data_<タイムスタンプ>.json
に渡すと、オフラインで開けるビューアー(local_viewer.html)を再生成できる
（引数を省略した場合はカレントフォルダの最新ファイルを自動選択する）。
"""

import argparse
import json
import os
import random
import sys
from datetime import datetime, timezone

import game as G
import build_tools
import config as C
import weights as W
from heuristic_bot import HeuristicBot
from search_bot_skeleton import SearchBot

# ARCHETYPESの実体はmeta_diversity_check.pyに1箇所だけ持たせ、ここではimportのみ
from meta_diversity_check import ARCHETYPES

DEFAULT_STORAGE = "sqlite:///balance_tuning_search.db"  # tune_balance_search.py/analyze_tuning_run.pyと同じ既定値


def _resolve_weight_file_path(path):
    """--weights-a/-b に指定されたパスをそのまま試し、存在しなければ
    champion_history/（tune_balance_search.py/champion_tools.py既定の
    保存先。champion_tools.pyのdefault_history_dir()と同じ基準＝
    generate_kifu.pyと同じフォルダ直下）内の同名ファイルをフォールバックで探す。
    2026-09-01追加: champion_history/gen_XXXX.jsonをディレクトリ prefix なし
    のファイル名だけで指定してしまい FileNotFoundError になる事故が実際に
    発生したため（保存場所がchampion_history/である一方、CLIの
    --weights-a/-bはカレントディレクトリ相対のパスをそのまま開く実装だった）。"""
    if os.path.exists(path):
        return path
    fallback = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "champion_history", os.path.basename(path)
    )
    if os.path.exists(fallback):
        return fallback
    return path  # 見つからない場合はそのまま返す。後続のopen()で通常通りエラーになる。


def _load_weight_overrides(path):
    """--weights-a/-b で指定されたJSONファイルを読み込み、重み上書き辞書を返す。
    標準のjson.loadを使うため、キーが重複したJSON（例: 末尾に上書き行を追記した
    だけのファイル）でも「後の出現が勝つ」形で自然に解決される。
    2026-09-01追加: champion_weights.json / champion_history/gen_XXXX.json
    形式（{"weights": {...}, "generation": ..., "source": ...}）もそのまま
    渡せるよう、"weights"キーを持つオブジェクトはその中身を取り出す
    （チャンピオン同士の対局をそのまま指定できるようにするための対応）。"""
    path = _resolve_weight_file_path(path)
    with open(path, encoding="utf-8") as f:
        overrides = json.load(f)
    if not isinstance(overrides, dict):
        raise ValueError(f"{path}: 重み設定ファイルはオブジェクト(dict)である必要があります")
    if "weights" in overrides and isinstance(overrides["weights"], dict):
        overrides = overrides["weights"]
    return overrides


def _load_trial_from_db(storage, study_name, trial_number):
    """Optunaのstudy DBから指定trial番号のparamsを読み込み、resolve_weights()済み
    の完全な重み辞書と、駒設定(CONFIG["pieces"])の上書きリスト(config_overrides、
    tune_balance_search.pyのevaluate_matchup()が受け取るのと同じ
    [(駒種, stat, value), ...]形式)を返す。

    2026-09-06追加: 戦績が極端に悪いtrialなど、championに昇格しなかった
    （＝champion_history/gen_XXXX.jsonに残らない）trialの重み設定は、従来
    generate_kifu.py側から直接参照する手段が無く、棋譜を作って局面から原因を
    追うことができなかった。tune_balance_search.py実行時に使ったのと同じ
    --storage/--study-nameを指定すれば、trial番号だけでその重み設定を
    そのまま棋譜生成に使えるようにする。
    """
    try:
        import optuna
    except ImportError as e:
        raise SystemExit(
            "optunaがインストールされていません（--trial-a/--trial-bの利用には"
            f"tune_balance_search.pyと同じ環境で実行してください）: {e}"
        )
    if not study_name:
        raise SystemExit(
            "--trial-a/--trial-bを使う場合は--study-nameの指定が必須です"
            "（1つのdbファイルに複数studyが同居しうるため、trial番号だけでは"
            "どのstudyのものか一意に決まりません。analyze_tuning_run.pyと同じ制約）。"
        )
    try:
        from tune_balance_search import weight_param_names, piece_param_names, _resolve_weight_overrides
    except ImportError as e:
        raise SystemExit(f"tune_balance_search.pyのimportに失敗しました（同じフォルダに置いてください）: {e}")

    study = optuna.load_study(study_name=study_name, storage=storage)
    trial = next((t for t in study.trials if t.number == trial_number), None)
    if trial is None:
        raise SystemExit(
            f"study '{study_name}'（{storage}）にtrial番号{trial_number}が見つかりませんでした。"
        )
    if trial.value is None:
        print(f"[trial] 警告: trial {trial_number} はvalue未確定（実行中に中断/失敗した可能性がある）"
              f"trialです。paramsのみ読み込んで続行します。", file=sys.stderr)

    weight_names = weight_param_names()
    piece_names = piece_param_names()
    raw_weights = {k: v for k, v in trial.params.items() if k in weight_names}
    weights_overrides = _resolve_weight_overrides(raw_weights)
    full_weights = W.resolve_weights(weights_overrides)

    config_overrides = []
    for param_name, value in trial.params.items():
        if param_name in piece_names:
            # param_nameは"{kind}_{stat}"形式（例: "歩兵_cost", "歩兵_produce_cost"）。
            # statに"produce_cost"のようにアンダースコアを含むものがあるため、
            # rsplitではなくsplit(maxsplit=1)で最初の"_"で分割する（駒種名(kind)
            # 自体にアンダースコアが含まれないことを利用。tune_balance_search.py
            # のformat_best_params_for_pasteに同種のバグがあったのと同じ理由で
            # 2026-09-06に注意して実装）。
            kind, stat = param_name.split("_", 1)
            config_overrides.append((kind, stat, value))

    # 戦績の悪いtrialを調べる、という本機能の主目的に沿って、読み込んだtrialの
    # 診断指標（championとの戦績・多様性パネル成績等、あれば）を読み込み時点で
    # 表示しておく（棋譜を開く前にコンソールだけで当たりをつけられるように）。
    metrics = dict(trial.user_attrs)
    summary_bits = [f"score={trial.value:.2f}" if trial.value is not None else "score=None"]
    if "win_rate_vs_champion" in metrics:
        summary_bits.append(
            f"vs champion: {metrics.get('champion_wins')}勝{metrics.get('champion_losses')}敗"
            f"{metrics.get('champion_draws')}分(win_rate={metrics.get('win_rate_vs_champion'):.2f})"
        )
    if "diversity_avg_win_rate" in metrics:
        summary_bits.append(
            f"多様性平均={metrics.get('diversity_avg_win_rate'):.2f} "
            f"最低={metrics.get('diversity_min_win_rate'):.2f}"
            f"@{metrics.get('diversity_min_win_rate_archetype')}"
        )
    print(f"[trial] study '{study_name}' trial {trial_number} を読み込みました "
          f"({', '.join(summary_bits)})")
    if config_overrides:
        print(f"[trial] trial {trial_number} はCONFIG['pieces']も{len(config_overrides)}件"
              f"チューニングしています: {config_overrides}")

    # 2026-09-06追加: trial番号とchampionのgeneration番号は無関係の別カウンタ
    # （generationはchampion_weights.json側で全study通算・trial番号はstudy内で
    # 0始まりの連番）であり、数字が同じ・近いだけで「このtrialが現在の
    # championだろう」と誤解して--trial-a/-bに指定してしまう事故が実際に
    # 発生した（trial番号7を「generation 7のchampion」だと勘違いした例）。
    # promoted_championはこのtrial自身が実際に昇格したかどうかを直接示すため、
    # ここで必ず明示する。
    if "promoted_champion" in metrics:
        if metrics["promoted_champion"]:
            print(f"[trial] trial {trial_number} はこのstudyでchampionに昇格したtrialです"
                  f"（champion_weights.json、または対応するchampion_history/gen_XXXX.jsonと"
                  f"同じ重みのはずです）。")
        else:
            print(f"[trial] 注意: trial {trial_number} はchampionに昇格していません"
                  f"（promoted_champion=False。このtrialのscoreに含まれるchampion_termは、"
                  f"世代{metrics.get('champion_generation_faced')}のchampionと対戦した結果です）。"
                  f"「今のchampion」の重みを使いたい場合は、trial番号ではなく"
                  f"champion_weights.json（または該当するchampion_history/gen_XXXX.json）を"
                  f"--weights-a/-bで指定してください。trial番号とchampionのgeneration番号は"
                  f"別のカウンタで、数字が一致していても無関係です。")

    label = f"trial{trial_number}({study_name})"
    return full_weights, label, config_overrides


def resolve_side_weights(archetype_name, weights_path, trial_number=None,
                          storage=None, study_name=None):
    """--archetype-* / --weights-* / --trial-* のいずれか（すべて省略なら
    balanced=無補正）から、そのプレイヤーの重み辞書(80キー全て埋まったもの)と
    表示ラベル、駒設定の上書き(config_overrides、trial由来の場合のみ非空)を作る。

    2026-09-06追加: trial_numberを指定した場合はOptunaのstudy DBから該当trialの
    重み設定を読み込む（詳細は_load_trial_from_dbのdocstring参照）。
    """
    specified = [bool(archetype_name), bool(weights_path), trial_number is not None]
    if sum(specified) > 1:
        raise ValueError("--archetype-* / --weights-* / --trial-* は同じ側で同時指定できません")
    if archetype_name:
        if archetype_name not in ARCHETYPES:
            raise ValueError(
                f"未知のアーキタイプ: {archetype_name!r}（選択肢: {sorted(ARCHETYPES)}）"
            )
        overrides = ARCHETYPES[archetype_name]
        label = f"archetype:{archetype_name}"
        return W.resolve_weights(overrides), label, []
    elif weights_path:
        overrides = _load_weight_overrides(weights_path)
        # 2026-09-01変更: labelはplayer0/player1の表示名としてそのまま使われるため、
        # ディレクトリ・拡張子を含むフルパス（例: "file:champion_history/gen_0002.json"）
        # ではなく、ファイル名から拡張子を除いた名称（例: "gen_0002"）にする。
        label = os.path.splitext(os.path.basename(weights_path))[0]
        return W.resolve_weights(overrides), label, []
    elif trial_number is not None:
        full_weights, label, config_overrides = _load_trial_from_db(storage, study_name, trial_number)
        return full_weights, label, config_overrides
    else:
        return W.resolve_weights({}), "default(balanced)", []


def _unique_weights_filename(prefix="weights_dump"):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"{prefix}_{ts}.json"


def dump_current_weights(prefix=None):
    """weights.py の現在のHEURISTIC_WEIGHTSを、上書き事故を避けるため必ず
    タイムスタンプ付きの新規ファイルへ書き出す。書き出したファイルパスを返す。"""
    filename = _unique_weights_filename(prefix) if prefix else _unique_weights_filename()
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(dict(W.HEURISTIC_WEIGHTS), f, indent=2, ensure_ascii=False, sort_keys=True)
    return filename


def run_and_collect(seeds, bot_kind="heuristic", depth=2, candidate_k=10, debug=True,
                     weights_a=None, weights_b=None, label_a="mirror", label_b="mirror",
                     swap_sides=False):
    games_data = []
    for seed in seeds:
        g = G.Game()

        if bot_kind == "heuristic":
            # HeuristicBotは自前のrandom.Random()を持つため、グローバルseedではなく
            # 専用rngを両ボットへ明示的に渡さないと再現性が保てない。
            if weights_a is None and weights_b is None:
                # 既定のミラー戦: 従来どおり両ボットで同じrngオブジェクトを共有する
                # （挙動を一切変えない）。
                rng = random.Random(seed)
                bots = {0: HeuristicBot(rng=rng), 1: HeuristicBot(rng=rng)}
                bot0_label, bot1_label = "mirror(default)", "mirror(default)"
            else:
                # 非ミラー戦: meta_diversity_check.pyのrun_match同様、それぞれの
                # ボットに専用のrngを与える（片方のrngの消費量が変わっても
                # もう片方の手には影響しない、ミラー戦と別の再現性モデル）。
                base_seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
                rng_a = random.Random(base_seed)
                rng_b = random.Random(base_seed + 1)
                w_a = weights_a if weights_a is not None else W.resolve_weights({})
                w_b = weights_b if weights_b is not None else W.resolve_weights({})
                if swap_sides:
                    bots = {0: HeuristicBot(weights=w_b, rng=rng_b),
                            1: HeuristicBot(weights=w_a, rng=rng_a)}
                    bot0_label, bot1_label = label_b, label_a
                else:
                    bots = {0: HeuristicBot(weights=w_a, rng=rng_a),
                            1: HeuristicBot(weights=w_b, rng=rng_b)}
                    bot0_label, bot1_label = label_a, label_b
        elif bot_kind == "search":
            # rng付きで渡すとchoose()/choose_production()にジッターが載り毎回展開が変わる。
            # seed指定で再現可能、Noneならエントロピー初期化で毎回変わる。
            #
            # 2026-09-06追加: record_debug_kifu=Trueを明示指定する。SearchBotは
            # 既定では（main.py・tune_balance_search.pyの大量自己対戦を誤って
            # 低速化しないよう）choose_with_debugを持たず、game.py Game.run()の
            # hasattr(bot, "choose_with_debug")判定に引っかからないため、
            # このフラグなしではgenerate_kifu.pyが吐く棋譜のcandidatesが常に
            # Noneになり、local_viewerで「候補ログなし」表示になってしまっていた
            # （search_bot_skeleton.py debug_choose_candidates()のdocstring参照）。
            # generate_kifu.pyは1回の実行で少数の対局しか行わない（tune_balance_search.py
            # のような大量自己対戦ではない）ため、常に有効化して問題ない。
            if weights_a is None and weights_b is None:
                # 既定のミラー戦（従来どおり。挙動を一切変えない）。
                bots = {
                    0: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                  rng=random.Random(seed) if seed is not None else random.Random(),
                                  record_debug_kifu=True),
                    1: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                  rng=random.Random(seed) if seed is not None else random.Random(),
                                  record_debug_kifu=True),
                }
                bot0_label, bot1_label = "mirror(search)", "mirror(search)"
            else:
                # 2026-09-01追加: 非ミラー戦（チャンピオン同士の対局など）。
                # heuristic側と同じ「片方ずつ専用rng」モデルを踏襲する。
                base_seed = seed if seed is not None else random.SystemRandom().randrange(2**32)
                rng_a = random.Random(base_seed)
                rng_b = random.Random(base_seed + 1)
                w_a = weights_a if weights_a is not None else W.resolve_weights({})
                w_b = weights_b if weights_b is not None else W.resolve_weights({})
                if swap_sides:
                    bots = {0: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                          weights=w_b, rng=rng_b, record_debug_kifu=True),
                            1: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                          weights=w_a, rng=rng_a, record_debug_kifu=True)}
                    bot0_label, bot1_label = label_b, label_a
                else:
                    bots = {0: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                          weights=w_a, rng=rng_a, record_debug_kifu=True),
                            1: SearchBot(depth=depth, candidate_k=candidate_k, debug=debug,
                                          weights=w_b, rng=rng_b, record_debug_kifu=True)}
                    bot0_label, bot1_label = label_a, label_b
        else:
            raise ValueError(f"unknown bot_kind: {bot_kind!r}")

        res = g.run(bots)
        games_data.append({
            "seed": seed,
            "result": res,
            "kifu": g.kifu,
            "base_positions": G.CONFIG["base_positions"],
            "vp_spots": G.CONFIG["vp_spots"],
            "rp_spots": G.CONFIG["rp_spots"],
            "midgame_spots": G.CONFIG["midgame_spots"],  # 2026-08-07追加
            "board_size": G.CONFIG["board_size"],
            # 2026-08-25追加: 非ミラー戦（--archetype-a/-b, --weights-a/-b）の
            # 場合、どちらのプレイヤーがどの重み設定だったかを棋譜ビューアー側で
            # 判別できるようにラベルを残す。ミラー戦では従来どおり区別不要。
            "bot0_label": bot0_label,
            "bot1_label": bot1_label,
            # 2026-09-06追加: local_viewer/play_vs_aiの表示統一のため、駒種ごとの
            # コスト・HP・ATK等（CONFIG["pieces"]）も棋譜に含める（web_api.py
            # kifu_json()と同じ理由。--trial-a/-b等でCONFIG["pieces"]自体を
            # チューニング値に上書きしている場合も、実際に使われた値がここに残る）。
            "pieces": G.CONFIG["pieces"],
        })
        print(f"seed={seed} bot={bot_kind} result={res} "
              f"(player0={bot0_label}, player1={bot1_label})")
    return games_data


def _unique_kifu_filename():
    # 例: kifu_data_20260803_142310_483921.json
    #   YYYYMMDD_HHMMSS_マイクロ秒 で、同一プロセス内の連続実行でも重複しない。
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
    return f"kifu_data_{ts}.json"


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "seeds", nargs="*", type=int,
        help="対局ごとのseed。省略時: --bot heuristicなら[1,2,3]、--bot searchなら"
             "[None]（OSエントロピー、旧main.py互換で毎回結果が変わる）"
    )
    # 2026-09-06修正: 既定は従来通りNone（未指定）にし、実際の既定値解決は
    # _parse_args末尾で行う。--trial-a/-b使用時は"heuristic"にせず"search"を
    # 既定にするための変更（下記の理由コメント・_resolve_bot_default参照）。
    parser.add_argument("--bot", choices=["heuristic", "search"], default=None)
    parser.add_argument("--depth", type=int, default=2, help="SearchBotのdepth（--bot search時のみ。0.95節によりdepth2が既定。旧0.77節のdepth3既定は撤回）")
    parser.add_argument("--candidate-k", type=int, default=10, help="SearchBotのcandidate_k（--bot search時のみ）")
    # 2026-09-06変更: 候補手ログ(choose_with_debug)は棋譜ファイル自体に同内容が
    # 記録される仕様になったため、コンソールへの二重出力は不要になった
    # （ログを遡って確認する必要があるだけの手間だった）。既定を「出力しない」に
    # 反転し、必要な時だけ--debugで明示的に有効化する形にする。--no-debugは
    # 後方互換のため残すが、既定が既にオフのため何もしない（no-op）。
    parser.add_argument("--debug", action="store_true",
                         help="SearchBotの候補手ログ(choose_with_debug)をコンソールにも出力する"
                              "（既定は無効。同内容は棋譜ファイルに記録されるため通常は不要）")
    parser.add_argument("--no-debug", action="store_true",
                         help="（後方互換のため残置。既定が既に無効のため指定してもno-op）")

    # 2026-08-25追加: 非ミラー戦（--archetype-a/-bのみ--bot heuristic専用。
    # --weights-a/-bは2026-09-01よりSearchBotでも使用可）
    group = parser.add_argument_group("非ミラー戦（異なる重み設定同士の対局。"
                                       "--archetype-a/-bは--bot heuristic専用）")
    group.add_argument("--archetype-a", metavar="NAME",
                        help=f"player0側のアーキタイプ名（meta_diversity_check.pyのARCHETYPES流用）。"
                             f"選択肢: {sorted(ARCHETYPES)}")
    group.add_argument("--archetype-b", metavar="NAME", help="player1側のアーキタイプ名（同上）")
    group.add_argument("--weights-a", metavar="PATH", help="player0側の重み上書きJSONファイル")
    group.add_argument("--weights-b", metavar="PATH", help="player1側の重み上書きJSONファイル")
    group.add_argument("--swap-sides", action="store_true",
                        help="A側/B側を入れ替えてplayer0=B、player1=Aにする")

    # 2026-09-06追加: Optuna DBから直接trialの重み設定を読み込む（--bot heuristic/search両対応）
    db_group = parser.add_argument_group(
        "Optuna DBからtrialの重み設定を読み込む（champion_historyに残らないtrialの調査用）"
    )
    db_group.add_argument("--trial-a", type=int, metavar="N",
                           help="player0側の重み設定として、--study-nameのtrial番号Nをdbから読み込む。"
                                "--bot未指定時は自動的に--bot searchになる"
                                "（tune_balance_search.pyの評価は常にSearchBotのため）")
    db_group.add_argument("--trial-b", type=int, metavar="N",
                           help="player1側の重み設定として、--study-nameのtrial番号Nをdbから読み込む"
                                "（--bot自動選択の挙動は--trial-aと同じ）")
    db_group.add_argument("--storage", type=str, default=DEFAULT_STORAGE,
                           help=f"--trial-a/-b読み込み用のOptuna storage URL（既定: {DEFAULT_STORAGE}。"
                                f"tune_balance_search.py実行時と同じ値を指定すること）")
    db_group.add_argument("--study-name", type=str, default=None,
                           help="--trial-a/-b読み込み用のstudy名（--trial-a/-bを使う場合は必須）")

    # 2026-08-25追加: 現在の重み設定をファイルへ書き出す（対局は行わない専用モード）
    parser.add_argument("--dump-weights", nargs="?", const="weights_dump", default=None,
                         metavar="PREFIX",
                         help="対局を行わず、weights.pyの現在のHEURISTIC_WEIGHTSを"
                              "<PREFIX>_<タイムスタンプ>.json（既定PREFIX=weights_dump）"
                              "へ書き出して終了する。上書き事故防止のため既存ファイルには"
                              "常にタイムスタンプ付きの新規名で保存する。")
    return parser.parse_args(argv)


if __name__ == "__main__":
    args = _parse_args(sys.argv[1:])

    if args.dump_weights is not None:
        # 対局は行わず、重み設定の書き出しだけ行って終了する。
        out_path = dump_current_weights(prefix=args.dump_weights)
        print(f"saved {out_path} ({len(W.HEURISTIC_WEIGHTS)} keys)")
        sys.exit(0)

    trial_requested = args.trial_a is not None or args.trial_b is not None

    # 2026-09-06追加: --bot未指定時の既定値解決。
    # tune_balance_search.pyの評価（評価対局・champion戦とも）は常にSearchBotを
    # 使い、HeuristicBotは一切使われない（generate_kifu.py側の既定値
    # "heuristic"は、あくまでこのスクリプト単体でお試し対局する場合の既定に
    # 過ぎない）。このため、Optuna DBから読み込んだtrialの重み（--trial-a/-b）を
    # 使うときに従来の既定"heuristic"のままだと、tune_balance_search.py側の
    # 実際の評価（例: SearchBot(depth=2, candidate_k=10)同士でのchampion戦）を
    # 全く別のアルゴリズム（1手先読みのHeuristicBot）で"再現"したことになり、
    # 元trialの戦績（例: 0勝100敗）と似ても似つかない結果になる事故が実際に
    # 発生した。--trial-a/-bを使い、かつ--botが明示されなかった場合は"search"を
    # 既定にする。--bot heuristicを明示した場合は尊重するが、上記の理由により
    # 強く警告する。
    if args.bot is None:
        if trial_requested:
            args.bot = "search"
            print("[trial] --botが指定されなかったため、--trial-a/-b使用時の既定として"
                  "--bot search を使用します（tune_balance_search.pyの評価は常にSearchBotを"
                  "使うため、--bot heuristicでは元trialの戦績を再現できません）。")
        else:
            args.bot = "heuristic"
    elif args.bot == "heuristic" and trial_requested:
        print("[警告] --trial-a/-bで読み込んだ重みを--bot heuristicで対局させようとしています。"
              "tune_balance_search.pyの評価（元trialの戦績）は常にSearchBotで行われているため、"
              "HeuristicBotでの対局は元trialの戦績（勝敗）を再現しません。"
              "元の評価を再現したい場合は--bot searchを指定してください。", file=sys.stderr)

    non_mirror_requested = any([
        args.archetype_a, args.archetype_b, args.weights_a, args.weights_b, args.swap_sides,
        trial_requested,
    ])
    # 2026-09-01変更: --archetype-a/-b（meta_diversity_check.py由来のヒューリスティック
    # 専用プリセット）は --bot heuristic 専用のまま。一方 --weights-a/-b（外部JSON）・
    # --trial-a/-b（2026-09-06追加、Optuna DB由来）は完全な重み辞書をSearchBotにも
    # そのまま渡せるため、--bot search でも許可する（チャンピオン同士の対局用）。
    if (args.archetype_a or args.archetype_b) and args.bot != "heuristic":
        raise SystemExit("--archetype-a/-b は --bot heuristic 専用です")

    weights_a = weights_b = None
    label_a = label_b = "mirror"
    if non_mirror_requested:
        try:
            weights_a, label_a, config_overrides_a = resolve_side_weights(
                args.archetype_a, args.weights_a, args.trial_a, args.storage, args.study_name)
            weights_b, label_b, config_overrides_b = resolve_side_weights(
                args.archetype_b, args.weights_b, args.trial_b, args.storage, args.study_name)
        except (ValueError, OSError, json.JSONDecodeError) as e:
            raise SystemExit(f"重み設定の解決に失敗しました: {e}")

        # 2026-09-06追加: --trial-a/-bはCONFIG["pieces"]のチューニング値も伴うことが
        # ある（--tune pieces/both運用）。駒設定はゲーム側に1つしか持てないため、
        # 両側が異なる駒設定を要求した場合はplayer0側（trial-a）を優先し、
        # player1側の駒設定は無視する旨を警告する。
        if config_overrides_a and config_overrides_b and config_overrides_a != config_overrides_b:
            print(f"[警告] player0側（{label_a}）とplayer1側（{label_b}）でCONFIG['pieces']の"
                  f"チューニング値が異なりますが、駒設定はゲーム側で1つしか持てないため、"
                  f"player0側の設定を採用し、player1側の駒設定は無視します。", file=sys.stderr)
            config_overrides = config_overrides_a
        else:
            config_overrides = config_overrides_a or config_overrides_b
        if config_overrides:
            C.apply_config_overrides(config_overrides)
            print(f"[trial] CONFIG['pieces']に{len(config_overrides)}件の上書きを適用しました。")

    if args.seeds:
        seeds = args.seeds
    elif args.bot == "heuristic":
        seeds = [1, 2, 3]
    else:
        seeds = [None]  # 旧main.py互換: seed指定なし = OSエントロピーで毎回結果が変わる

    data = run_and_collect(
        seeds, bot_kind=args.bot, depth=args.depth,
        candidate_k=args.candidate_k, debug=args.debug,
        weights_a=weights_a, weights_b=weights_b,
        label_a=label_a, label_b=label_b, swap_sides=args.swap_sides,
    )
    filename = _unique_kifu_filename()
    with open(filename, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(f"saved {filename} ({len(data)} games)")

    # 2026-08-11修正: 棋譜を保存したら、その場でビューアーも自動生成する。
    # 「棋譜生成は実行したがビューアーの再生成を忘れて古いビューアーを見ていた」
    # という事故が構造的に起こらなくなる。
    build_tools.build_local_viewer(kifu_path=filename)
