# whittlewisp/（読み取り専用の同梱コピー）

Whittlewisp本体（Colab側の検証フォルダ）から `models.py` / `config.py` だけを
そのままコピーしたもの。Stonebloom側では **Spriglingの重量・部位耐久・技の算出にだけ**
使う（戦闘シミュレータ本体は同梱していない）。

- このフォルダのファイルは直接編集しない。Whittlewisp側で式や係数を変えたら、
  2ファイルを丸ごと上書きコピーし直すこと。
- 読み込みは `sprigling.py` の `_load_whittlewisp()` 経由のみ。Whittlewispの
  `models.py` は `import config` で自分のconfigを読むが、Stonebloomにも同名の
  `config.py` があるため、一時的に `sys.modules["config"]` を差し替えて
  別名（`ww_config` / `ww_models`）で読み込んでいる。
