# -*- coding: utf-8 -*-
"""
build_tools.py
==========================================================
2026-08-22統合: build_local_viewer.py / build_play_vs_ai.py の2つのビルドスクリプトを
1本化した。いずれも「.pyファイル一式・kifuデータをテキストのまま単体HTMLへ埋め込んで
生成する」という同型のパターンの繰り返しだったため、共通処理をまとめ、サブコマンドで
呼び分ける形にした。

2026-08-23追記: 以前ここに同居していた`puzzle_solver`サブコマンド（詰めゲーパズル機能の
ビルド）は、本体プロジェクト（このハンドブック・このフォルダ）とは別件として進めている
企画のため、切り分けて別フォルダへ完全に移設した（`パズル関連プロジェクト（別件）`
フォルダの`build_puzzle_solver.py`を参照。本体プロジェクトの`config.py`/`game.py`は
そちらから直接importする形にしてあり、コピーはしていない）。今後このハンドブック・
この`build_tools.py`側でパズル機能を追跡する必要はない。

使い方:
  python3 build_tools.py viewer                      # local_viewer.html を最新のkifu_data_*.jsonから生成
  python3 build_tools.py viewer kifu_data_xxx.json    # 特定の棋譜から生成
  python3 build_tools.py viewer --watch               # テンプレート編集中に自動再生成し続ける
  python3 build_tools.py viewer --en                  # 英語版local_viewer.htmlを生成（2026-09-08追加）
  python3 build_tools.py viewer --empty               # 棋譜が1つも無くてもエラーにせず空の状態で生成
  python3 build_tools.py play_vs_ai                   # play_vs_ai.html を生成（駒名は日本語の世界観呼称。3.2/4.2節）
  python3 build_tools.py play_vs_ai --en               # 駒名・UI文言を英語表記で生成
  python3 build_tools.py play_vs_ai --show-hp-numbers  # 駒の上にHP数値を表示
  python3 build_tools.py play_vs_ai --director-mode    # 演出モード（自由配置・動画撮影用）を有効化
  python3 build_tools.py play_vs_ai --output foo.html  # 出力ファイル名を変更
  python3 build_tools.py play_vs_ai_video              # 英語圏向け紹介・トレーラー撮影用プリセット
                                                        # （--en --show-hp-numbers --director-mode相当。
                                                        #   play_vs_ai_video.html として出力）
  python3 build_tools.py itch                         # itch.io配布用パッケージ一式（2026-09-08新設）を
                                                        # itch_export/ に生成する。index.html（英語版
                                                        # play_vs_ai）・local_viewer.html（相互にリンク
                                                        # 済み）・両方をまとめたitch_upload.zipの3点が
                                                        # 出力される。itch_upload.zipをitch.ioの
                                                        # アップロード欄にそのままドラッグ&ドロップし、
                                                        # 埋め込みファイルとしてindex.htmlを指定すればよい。
  python3 build_tools.py itch --ja                    # 日本語版でパッケージする（既定は英語版=en）
  python3 build_tools.py itch --output foo_dir         # 出力先フォルダ名を変更（既定: itch_export）
  python3 build_tools.py all                          # 生成可能なものをすべて生成（欠損は警告してスキップ）

2026-08-27追加: play_vs_aiのビルドオプション（BUILD_OPTIONS）について
  駒の内部キー名（歩兵・騎兵・重装兵・弓兵・工兵・本拠）はconfig.py/game.py側の
  ものを一切変更していない。表示名（3.2/4.2節の世界観呼称・英語呼称）と
  HP数値表示・演出モードのON/OFFは、すべてplay_vs_ai_template.html内の
  PIECE_DISPLAY/UI_STRINGS対応表とBUILD_OPTIONSマーカーの差し込みだけで
  実現しており、Python側のコードには一切手を入れていない。

2026-09-08追加: local_viewer側のBUILD_OPTIONS/GAME_HTML_HREFについて
  play_vs_aiと同じ仕組みで、local_viewer_template.htmlにも表示言語切り替え
  （BUILD_OPTIONS.locale）を追加した。あわせて、「対局画面へ戻る」リンクの
  遷移先ファイル名（通常配布時"play_vs_ai.html"／itch.io配布時"index.html"）を
  GAME_HTML_HREFマーカーで差し替えられるようにした（itchサブコマンド・
  build_itch_package()参照）。

他のスクリプトから関数として呼ぶ場合（例: generate_kifu.py が棋譜保存直後に
ビューアーを再生成する用途）:
  import build_tools
  build_tools.build_local_viewer(kifu_path="kifu_data_xxx.json")

旧スクリプトからの移行:
  旧 `python3 build_local_viewer.py [kifu_path] [--watch]` → `python3 build_tools.py viewer [kifu_path] [--watch]`
  旧 `python3 build_play_vs_ai.py`                          → `python3 build_tools.py play_vs_ai`
  旧 `import build_local_viewer; build_local_viewer.build(...)`
                                                              → `import build_tools; build_tools.build_local_viewer(...)`

2026-08-11大改修の経緯（play_vs_ai）: 旧バージョンはHEURISTIC_WEIGHTS等の
「数値だけ」をconfig.pyから抜き出してJS版CONFIGへ書き込む_WEIGHT_KEY_MAP方式だった。これだと
「ロジック」自体（探索アルゴリズムや評価関数の構造）は手作業でJSへ再実装する必要があり、反映漏れが
起き得た。新方式ではCONFIGの数値もロジックも一切JSへ移植せず、対象の.pyファイルをテキストとして
そのまま<script type="text/python-source">ブロックへ埋め込み、ブラウザ上のPyodideでそのまま
インポートして実行する。そのため「ビルドスクリプトの実行を忘れて反映が漏れる」という事故は、
対象の.pyファイルのいずれかを変更したら本スクリプトを再実行するだけで解消される。
"""

import glob
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = Path(_SCRIPT_DIR)


# ------------------------------------------------------------
# 0. 共通ヘルパー: テンプレート内の `var NAME = /*@@MARKER@@*/...;` という
#    1行マーカーを、JSON.dumpsした値へ置換する。
#    2026-09-08追加: play_vs_ai_template.htmlのBUILD_OPTIONSマーカー置換用に
#    書かれていた正規表現処理を汎用化し、local_viewer_template.htmlの
#    BUILD_OPTIONS/GAME_HTML_HREFマーカーとも共通化した（3箇所で同じ正規表現を
#    バラバラに持つと、テンプレート側の書式を変えたときに直し漏れが起きるため）。
# ------------------------------------------------------------

def _apply_var_marker(text, var_name, marker_name, value, template_label):
    """text中の `var {var_name} = /*@@{marker_name}@@*/...;` を
    `var {var_name} = <json.dumps(value)>;` へ置換して返す。
    マーカーが見つからない場合はRuntimeErrorを送出する（テンプレート側の
    書式が変わった/壊れたことにすぐ気付けるようにするため、無言でスキップしない）。"""
    pattern = re.compile(
        r"var " + re.escape(var_name) + r" = /\*@@" + re.escape(marker_name) + r"@@\*/.*?;", re.S
    )
    if not pattern.search(text):
        raise RuntimeError(
            f"{template_label} に /*@@{marker_name}@@*/ の差し込み位置（var {var_name}）が見つかりません"
        )
    replacement = "var " + var_name + " = " + json.dumps(value, ensure_ascii=False) + ";"
    return pattern.sub(replacement, text)


# ------------------------------------------------------------
# 1. local_viewer.html （kifuデータの埋め込み。旧 build_local_viewer.py）
# ------------------------------------------------------------

_LOCAL_VIEWER_TEMPLATE = os.path.join(_SCRIPT_DIR, "local_viewer_template.html")


def _latest_kifu_path():
    candidates = glob.glob("kifu_data_*.json") or glob.glob("kifu_data.json")
    if not candidates:
        raise SystemExit("kifu_data*.json が見つかりません。先に generate_kifu.py を実行してください。")
    # 2026-08-29修正: 従来は sorted(candidates)[-1] で「ファイル名の辞書順ソート＝
    # 時系列順」という前提に頼っていたが、kifu_data_sample.json のようにタイムスタンプ
    # 形式でない名前（数字で始まらない名前）が1つでも同じフォルダに存在すると、
    # ASCIIでは英字が数字より大きいため辞書順で必ず最後に来てしまい、実際には
    # どれだけ新しいタイムスタンプ付きファイルがあってもそちらが「最新」として
    # 選ばれ続けるという事故があった（検証ハンドブック0.76節のkifu_data_sample.json
    # 同梱時に実際に発生）。ファイル名の文字列ではなく、実際の更新時刻(mtime)で
    # 最新のものを選ぶよう変更する。
    return max(candidates, key=os.path.getmtime)


def build_local_viewer(kifu_path=None, output_path="local_viewer.html", locale="ja",
                        game_html_href="play_vs_ai.html", allow_empty=False):
    """kifu_path を local_viewer_template.html に埋め込んで output_path を生成する。
    kifu_path省略時はカレントフォルダの最新の kifu_data_*.json を使う。
    生成したファイルパスを返す。

    2026-09-08追加のオプション（play_vs_aiと同じくJS側のBUILD_OPTIONSへ渡る）:
      locale: "ja" | "en"。駒の表示名・UI文言を切り替える（play_vs_ai.htmlの
        localeと同じ仕組み。英語版local_viewer.htmlをビルドできるようにする
        ための追加）。
      game_html_href: ビューアー画面の「対局画面へ戻る」リンクの遷移先
        ファイル名。通常配布時は既定の"play_vs_ai.html"のままでよいが、
        itch.io向けパッケージ（build_itch_package()参照）ではゲーム側の
        出力ファイル名が"index.html"になるため、そちらに合わせて差し替える。
      allow_empty: Trueの場合、kifu_path省略時にカレントフォルダに
        kifu_data_*.jsonが1つも無くてもエラーにせず、空の試合一覧（[]）を
        埋め込んで生成する（ローカルビューアー側は「まだ試合データがありません」
        という案内表示に切り替わる。itch.io向けパッケージのように、対局前で
        まだ棋譜が存在しない状態で同梱する場合に使う）。
    """
    if kifu_path is None:
        try:
            kifu_path = _latest_kifu_path()
        except SystemExit:
            if not allow_empty:
                raise
            kifu_path = None

    if kifu_path is None:
        data_json = "[]"
    else:
        with open(kifu_path, encoding="utf-8") as f:
            data_json = f.read()

    if not os.path.exists(_LOCAL_VIEWER_TEMPLATE):
        raise SystemExit(
            f"テンプレートが見つかりません: {_LOCAL_VIEWER_TEMPLATE}\n"
            "local_viewer_template.html を build_tools.py と同じフォルダに置いてください。"
        )
    with open(_LOCAL_VIEWER_TEMPLATE, encoding="utf-8") as f:
        template = f.read()

    if "__DATA__" not in template:
        raise SystemExit(
            "local_viewer_template.html に __DATA__ マーカーが見つかりません。"
            "テンプレートが壊れている可能性があります。"
        )

    output = template.replace("__DATA__", data_json)
    output = _apply_var_marker(output, "BUILD_OPTIONS", "BUILD_OPTIONS",
                                {"locale": locale}, "local_viewer_template.html")
    output = _apply_var_marker(output, "GAME_HTML_HREF", "GAME_HTML_HREF",
                                game_html_href, "local_viewer_template.html")
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(output)

    print(f"saved {output_path} (source: {kifu_path or '(no kifu data — empty viewer)'}, locale={locale})")
    return output_path


def _watch_local_viewer():
    """local_viewer_template.html を監視し、変更されるたびに最新の
    kifu_data_*.json で再生成する（テンプレートのUI調整中に使う）。
    kifu_data自体を作り直したい場合はgenerate_kifu.pyを実行すること
    （そちらが自動でbuild_local_viewer()を呼ぶので、この監視モードは不要）。"""
    print(f"watching {_LOCAL_VIEWER_TEMPLATE} for changes... (Ctrl+C to stop)")
    last_mtime = None
    while True:
        try:
            mtime = os.path.getmtime(_LOCAL_VIEWER_TEMPLATE)
        except FileNotFoundError:
            time.sleep(1)
            continue
        if mtime != last_mtime:
            last_mtime = mtime
            try:
                build_local_viewer()
            except SystemExit as e:
                print(f"  skip: {e}")
        time.sleep(1)


# ------------------------------------------------------------
# 2. 共通: 「.pyファイル一式をテキストで<script>埋め込みする」ビルダー
#    （旧 build_play_vs_ai.py / build_puzzle_solver.py の重複部分をまとめた）
# ------------------------------------------------------------

def _build_pyodide_html(template_path, output_path, python_sources, label, build_options=None):
    """template_path内の<!--@@PYTHON_SOURCES@@-->を、python_sourcesで指定した
    (script_id, filename)一覧をテキストのまま埋め込んだ<script>群に置き換えて
    output_pathへ書き出す。

    build_optionsを渡すと、テンプレート内の /*@@BUILD_OPTIONS@@*/ マーカーを
    `const BUILD_OPTIONS = {...};` へ置換する（2026-08-27追加。表示言語・
    HP数値表示・演出モードのON/OFFをJS側へ渡すための仕組み）。"""
    template_path = Path(template_path)
    output_path = Path(output_path)

    missing = [fn for _, fn in python_sources if not (SRC_DIR / fn).exists()]
    if missing:
        raise SystemExit(
            f"[{label}] 以下のファイルが見つからないためビルドできません: {', '.join(missing)}"
        )
    if not template_path.exists():
        raise SystemExit(f"[{label}] テンプレートが見つかりません: {template_path}")

    template = template_path.read_text(encoding="utf-8")

    blocks = []
    for script_id, filename in python_sources:
        src = (SRC_DIR / filename).read_text(encoding="utf-8")
        # </script> を万一含む文字列があっても閉じタグとして解釈されないよう、
        # 一致する部分がないことをここで検査する。
        assert "</script" not in src, f"{filename} に '</script' を含む文字列があり埋め込めません"
        blocks.append(
            f'<script type="text/python-source" id="{script_id}">\n{src}</script>'
        )
    embedded = "\n".join(blocks)

    if "<!--@@PYTHON_SOURCES@@-->" not in template:
        raise RuntimeError(
            f"{template_path.name} に <!--@@PYTHON_SOURCES@@--> の差し込み位置が見つかりません"
        )
    output = template.replace("<!--@@PYTHON_SOURCES@@-->", embedded)

    if build_options is not None:
        # テンプレート側は `var BUILD_OPTIONS = /*@@BUILD_OPTIONS@@*/{...デフォルト値...};`
        # という1行になっている（テンプレート単体で開いても壊れないよう既定値を残すため）。
        # マーカーとその直後のデフォルトのオブジェクトリテラルをまとめて置換する
        # （2026-09-08: local_viewer_template.html側のBUILD_OPTIONS/GAME_HTML_HREF
        # マーカー置換と共通の_apply_var_marker()に統一した）。
        output = _apply_var_marker(output, "BUILD_OPTIONS", "BUILD_OPTIONS",
                                    build_options, template_path.name)

    output_path.write_text(output, encoding="utf-8")
    print(f"生成しました: {output_path} ({len(output):,} bytes)")
    for script_id, filename in python_sources:
        size = (SRC_DIR / filename).stat().st_size
        print(f"  埋め込み: {filename} ({size:,} bytes) -> #{script_id}")
    if build_options is not None:
        print(f"  BUILD_OPTIONS: {build_options}")
    return output_path


# --- play_vs_ai.html （旧 build_play_vs_ai.py） ---

_PLAY_VS_AI_SOURCES = [
    ("py-config", "config.py"),
    ("py-weights", "weights.py"),  # 2026-08-25分離。config/game/heuristic/search全てがimportするため必須
    ("py-game", "game.py"),
    ("py-scoring-common", "scoring_common.py"),  # heuristic/searchが低レベル評価関数をimport
    ("py-heuristic-bot", "heuristic_bot.py"),
    ("py-search-bot-skeleton", "search_bot_skeleton.py"),
    ("py-web-api", "web_api.py"),
]


def _collect_champion_history(history_dir="champion_history"):
    """champion_history/gen_XXXX.json一式を読み込み、play_vs_aiの対戦相手選択UI用に
    [{"name": "gen_0002", "weights": {...}, "generation": 2}, ...] （世代の昇順）を返す。

    2026-09-01追加。ディレクトリが存在しない/空/壊れたファイルのみの場合は空リストを
    返す（後方互換。champion_historyを持たない配布環境でも従来通りビルドできる）。
    name はディレクトリ・拡張子なしのファイル名（例: "gen_0002"）にしてあり、
    generate_kifu.pyの--weights-a/-b指定時のlabel命名規則と揃えてある。"""
    dir_path = SRC_DIR / history_dir
    if not dir_path.is_dir():
        return []
    entries = []
    for path in sorted(dir_path.glob("gen_*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            weights = data.get("weights")
            if not isinstance(weights, dict):
                continue
            entries.append({
                "name": path.stem,
                "weights": weights,
                "generation": data.get("generation"),
            })
        except (json.JSONDecodeError, OSError) as e:
            print(f"[build_play_vs_ai] {path} の読み込みに失敗したためスキップします: {e}")
    entries.sort(key=lambda e: e["generation"] if e["generation"] is not None else 0)
    return entries


def build_play_vs_ai(output_name="play_vs_ai.html", locale="ja", show_hp_numbers=False,
                      director_mode=False, champion_history_dir="champion_history"):
    """通常配布用のplay_vs_ai.htmlを生成する。

    2026-08-27追加のオプション（すべてJS側のBUILD_OPTIONSへそのまま渡る）:
      locale: "ja" | "en"。駒の表示名（3.2/4.2節の呼称表）とUI文言を切り替える。
      show_hp_numbers: Trueだと駒の上にHP数値（現在/最大）を表示する。
      director_mode: Trueだと「演出モード（自由配置・動画撮影用）」トグルを
        画面に出す。実際の対局進行(Pythonエンジン側の状態)には一切書き込まない
        見た目だけの編集モードなので、配布用ビルドでは既定でOFFにしている。
      champion_history_dir: 2026-09-01追加。対戦相手選択UI用にchampion_history/
        gen_XXXX.jsonを埋め込む対象ディレクトリ（既定"champion_history"）。
        中身はビルド時点のものがそのままHTMLへ埋め込まれる（pyodideはブラウザの
        サンドボックス内で動くため、実行時にディスクを読みには行けない。ビルドの
        たびに再生成することで最新のchampion_historyを反映する）。
    """
    champion_history = _collect_champion_history(champion_history_dir)
    return _build_pyodide_html(
        SRC_DIR / "play_vs_ai_template.html",
        SRC_DIR / output_name,
        _PLAY_VS_AI_SOURCES,
        "build_play_vs_ai",
        build_options={
            "locale": locale,
            "show_hp_numbers": show_hp_numbers,
            "director_mode_enabled": director_mode,
            "champion_history": champion_history,
        },
    )


def build_play_vs_ai_video():
    """英語圏向けの紹介・トレーラー撮影用ビルド（play_vs_ai_video.html）。
    駒名は4.2節の英語呼称（Mosskin等）、HP数値表示ON、演出モード（自由配置）ON。
    通常配布用のplay_vs_ai.htmlとは出力ファイルを分けているので、
    間違って両方を同じ場所にアップロードする事故を防げる。"""
    return build_play_vs_ai(
        output_name="play_vs_ai_video.html",
        locale="en",
        show_hp_numbers=True,
        director_mode=True,
    )


# --- itch.io配布用パッケージ（2026-09-08新設） ---
#
# 背景: play_vs_ai.htmlをitch.ioへ「index.htmlへリネーム＋local_viewer.htmlと
# 一緒にzip化」という手順でアップロードしようとしたところ、「これだと
# local_viewer.htmlへは辿り着けない（play_vs_ai側からのリンクが無い）」と
# 指摘された。play_vs_ai_template.html側は常に相対パス"local_viewer.html"で
# リンクするようにした（ファイル名が変わらないため固定でよい）が、
# local_viewer_template.html側の「対局画面へ戻る」リンクは逆に、通常配布時の
# "play_vs_ai.html"とitch.io配布時の"index.html"とでリンク先が変わる
# （build_local_viewer()のgame_html_href引数で吸収する）。
# 本関数はこの2ファイルの組み合わせを毎回手作業で意識しなくて済むよう、
# 「itch.ioへドラッグ&ドロップでアップロードするzipファイル」を1コマンドで
# 生成する。zipの中身はindex.html/local_viewer.htmlがフォルダなしで直接
# ルートに並ぶ構成にする（itch.ioのHTML5公開はzipのルートにあるindex.htmlを
# 見つけて実行するため、サブフォルダに入れると認識されない）。

def build_itch_package(output_dir="itch_export", locale="en", kifu_path=None,
                        show_hp_numbers=False, champion_history_dir="champion_history"):
    """itch.io配布用の index.html + local_viewer.html を output_dir に生成し、
    同じ内容を output_dir/itch_upload.zip としてまとめる（このzipをitch.ioの
    アップロード欄へそのままドラッグ&ドロップすればよい）。

    locale: 既定"en"（英語版itch.ioページ向け。日本語版を作る場合は"ja"を渡す）。
    kifu_path: 同梱するサンプル棋譜。省略時はカレントフォルダの最新の
      kifu_data_*.jsonを使うが、無くてもエラーにはならず、空の試合一覧を
      埋め込んだlocal_viewer.htmlを生成する（プレイヤーは対局後に
      index.html側の「Download Game Record」でダウンロードした棋譜を、
      local_viewer.html側の「Load a game record file」から読み込んで見る、
      という導線が主なので、ビルド時点でサンプル棋譜が無くても問題ない）。
    """
    # 2026-09-08注記: build_play_vs_ai()/_build_pyodide_html()はoutput_nameを
    # SRC_DIR基準で解決するのに対し、build_local_viewer()はoutput_pathを
    # カレントディレクトリ基準でそのまま開く（既存のkifu関連コマンド一式との
    # 慣習を踏襲したもの。0.xx節の旧スクリプトから引き継いだ差異）。
    # ここでは呼び出し時のカレントディレクトリに関わらず同じ場所に2ファイルが
    # 並ぶよう、両方ともSRC_DIR基準の絶対パスを明示的に組み立てて渡す。
    out_dir = SRC_DIR / output_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    index_path = build_play_vs_ai(
        output_name=str(Path(output_dir) / "index.html"),
        locale=locale,
        show_hp_numbers=show_hp_numbers,
        director_mode=False,
        champion_history_dir=champion_history_dir,
    )
    viewer_path = build_local_viewer(
        kifu_path=kifu_path,
        output_path=str(out_dir / "local_viewer.html"),
        locale=locale,
        game_html_href="index.html",
        allow_empty=True,
    )

    zip_path = out_dir / "itch_upload.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(index_path, arcname="index.html")
        zf.write(viewer_path, arcname="local_viewer.html")

    print(f"itch.io向けパッケージを作成しました: {zip_path}")
    print("  → itch.ioのプロジェクト編集画面の「Upload files」にこのzipをそのまま")
    print("    ドラッグ&ドロップし、埋め込みファイルとして index.html を指定してください"
          "（\"This file will be played in the browser\" にチェック）。")
    return zip_path


# --- puzzle_solver.html は2026-08-23に別プロジェクトへ完全移設した ---
# 移設先: `パズル関連プロジェクト（別件）`フォルダの`build_puzzle_solver.py`。
# 本体プロジェクト（このフォルダ）はもうpuzzle_solver関連を一切ビルドしない。

_BUILDERS = [
    ("viewer", build_local_viewer),
    ("play_vs_ai", build_play_vs_ai),
    ("play_vs_ai_video", build_play_vs_ai_video),
    ("itch", build_itch_package),
]


def _main(argv):
    if not argv:
        print(__doc__)
        raise SystemExit(1)

    cmd, rest = argv[0], argv[1:]

    if cmd == "viewer":
        # 2026-09-08追加のオプションフラグ（play_vs_aiと同じ命名に揃えた）:
        #   --en      駒名・UI文言を英語表記にする（既定はja）
        #   --empty   カレントフォルダにkifu_data_*.jsonが無くてもエラーにせず、
        #             空の試合一覧を埋め込んで生成する（itch.io配布パッケージの
        #             動作確認用など。通常のローカル作業では基本的に不要）
        if "--watch" in rest:
            _watch_local_viewer()
        else:
            arg_path = rest[0] if rest and not rest[0].startswith("--") else None
            locale = "en" if "--en" in rest else "ja"
            allow_empty = "--empty" in rest
            build_local_viewer(arg_path, locale=locale, allow_empty=allow_empty)
    elif cmd == "itch":
        # itch.io配布用パッケージ（index.html + local_viewer.html + それらを
        # まとめたitch_upload.zip）を itch_export/ フォルダに生成する。
        #   --ja               既定の英語版(en)ではなく日本語版でパッケージする
        #   --show-hp-numbers  play_vs_ai同様、駒の上にHP数値を表示する
        #   --output <dir>     出力先フォルダ名を変える（既定: itch_export）
        #   --kifu <path>      同梱するサンプル棋譜を指定する（既定: 自動検出。
        #                      無くてもエラーにはならない）
        locale = "ja" if "--ja" in rest else "en"
        show_hp_numbers = "--show-hp-numbers" in rest
        output_dir = "itch_export"
        if "--output" in rest:
            idx = rest.index("--output")
            if idx + 1 < len(rest):
                output_dir = rest[idx + 1]
        kifu_path = None
        if "--kifu" in rest:
            idx = rest.index("--kifu")
            if idx + 1 < len(rest):
                kifu_path = rest[idx + 1]
        build_itch_package(output_dir=output_dir, locale=locale,
                            kifu_path=kifu_path, show_hp_numbers=show_hp_numbers)
    elif cmd == "play_vs_ai":
        # 2026-08-27追加のオプションフラグ:
        #   --en                 駒名・UI文言を英語表記にする（既定はja）
        #   --show-hp-numbers    駒の上にHP数値（現在/最大）を表示する
        #   --director-mode      演出モード（自由配置・動画撮影用）トグルを出す
        #   --output <filename>  出力ファイル名を変える（既定: play_vs_ai.html）
        locale = "en" if "--en" in rest else "ja"
        show_hp_numbers = "--show-hp-numbers" in rest
        director_mode = "--director-mode" in rest
        output_name = "play_vs_ai.html"
        if "--output" in rest:
            idx = rest.index("--output")
            if idx + 1 < len(rest):
                output_name = rest[idx + 1]
        build_play_vs_ai(output_name=output_name, locale=locale,
                          show_hp_numbers=show_hp_numbers, director_mode=director_mode)
    elif cmd == "play_vs_ai_video":
        # 英語表記・HP数値表示・演出モード（自由配置）をすべてONにした
        # プリセット。紹介動画・トレーラー撮影用に play_vs_ai_video.html を生成する。
        build_play_vs_ai_video()
    elif cmd == "all":
        for name, fn in _BUILDERS:
            try:
                fn()
            except SystemExit as e:
                print(f"[skip] {name}: {e}")
    else:
        print(f"unknown subcommand: {cmd}\n")
        print(__doc__)
        raise SystemExit(1)


if __name__ == "__main__":
    _main(sys.argv[1:])
