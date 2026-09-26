# Sprigling検証環境（verify_lab.py）

Spriglingはビルドごとにステータスが変わるため、「固定の5駒種で勝率を見る」だけでは
バランスもAIの強さも測れない。この環境は次の3つを満たすことを目標にしている。

| 性質 | 仕組み |
|---|---|
| **再現できる** | 対局は全てシード文字列から決まる。`PYTHONHASHSEED=0` で自分自身を起動し直す（駒種名のsetの順序まで固定）。実行ごとに `runs/*.json` へ git commit・未コミット変更の有無・CONFIGのハッシュ・引数・全ペアの結果を保存する。`repro` で同じ対局が同じ結果になることを確認できる |
| **統計的に正しい** | 先後を入れ替えた2局（ペア）を1標本とし、95%CIとSPRT（逐次検定）で判定する。探索で選ばれた候補は、選抜に使っていない新しいシードで再検定する（勝者の呪い対策）。`aa-test` で「同条件なら0.5になる」ことを確認し、環境自体のバグを検出する |
| **壊れたビルドを探しに行く** | `exploit` がWhittlewispのビルド空間（部位ごとのsize/length/angle/本数/材質）を進化的に探索し、「同じ属性・近い動員コストの標準ビルド」を持つ相手に勝ち越すビルドを探す |

## コマンド

```bash
python3 verify_lab.py calibrate                  # ステータス式の静的チェック（1秒）
python3 verify_lab.py aa-test --pairs 400        # 環境の健全性（同条件で0.5になるか）
python3 verify_lab.py repro                      # 同じ対局が同じ結果になるか
python3 verify_lab.py compare --a heuristic --b heuristic --random-rosters --pairs 400 --sprt 0.5,0.55
python3 verify_lab.py classes --mode vs-base --pairs 200   # 階級ごと: Sprigling込み vs 既存5種のみ
python3 verify_lab.py classes --mode bring --pairs 200     # 両者が1体持ち込み、階級総当たり
python3 verify_lab.py classes --mode bring --overrides '{"sprigling_stats":{"model":"physical"},"combat":{"initiative":true}}'
python3 verify_lab.py exploit --weight-class middle --pop 12 --gens 6
python3 tune_sprigling_ai.py --trials 40 --pairs 60        # OptunaでAIの重みを最適化（要 pip install optuna）
python3 verify_lab.py placement-sweep --ratios 0.5,0.75,1.0 --pairs 200
```

- `compare --random-rosters` はペアごとに違うランダムなSprigling構成を両者に配る。
  AIを改良したら、固定5駒種ではなくこちらで比べる（特定の構成への過剰適応を防ぐ）。
- `classes` は同じシードなら同じビルドを引くので、`--overrides` だけ変えた2回の実行は
  「同じビルドで式・ルールだけ違う」対応比較になる。
- `tune_sprigling_ai.py` は全trialで同じシード列を使い（比較のノイズ削減）、最後に
  上位候補を新しいシードでSPRT再検定する。`best_sprigling_ai.json` の
  `"confirmed": true` のときだけ採用してよい。
- `exploit` の判定: `BROKEN` = SPRTでH1（期待スコア≥0.6）採択、`OK` = H0（0.5）採択、
  `INCONCLUSIVE` = 上限ペア数まで決着せず。`採用率` が低い候補は、AIがその駒をほとんど
  動員していないので、結果の解釈に注意。
- 対局ボットは `--bot heuristic`（1局約0.3秒）が基本。`search` は1局数十秒かかるので、
  最終確認用。

## これまでの主な発見（2026-09-26、heuristicボット）

| 実験 | 結果 |
|---|---|
| 階級別: Sprigling込み vs 既存5種のみ（200ペア, 指数1） | 軽量 **0.80** / 中量 0.60 / 重量 0.55 |
| 同上（指数2、現行） | 軽量 0.48 / 中量 0.56 / 重量 0.55 |
| 同コスト・同HP×ATKで攻撃寄り vs HP寄り（150ペア） | 攻撃寄り 0.61。ただしAIがHP寄りの駒をほぼ動員しない（採用率3.5%）ので、AIの評価の偏りも混ざっている |
| 配置コスト比 0.5 / 0.75 / 1.0（指数1、全Sprigling込み vs 既存のみ） | 0.82 / 0.81 / 0.75 |
| 配置コスト比 0.5 / 0.75 / 1.0（指数2、現行） | 0.70 / **0.485** / 0.41 → 0.75が釣り合う点 |
| exploit（指数2、全階級, pop10×4世代） | 攻撃寄りのcrush中量級（HP148 ATK39 移動2, 382RP）が対照（L3-crush, 352RP, 移動3）に0.58〜0.66でBROKEN判定。上の「攻撃寄りが有利」と同じ傾向 |
| A/Aテスト（同条件400ペア×3） | 0.478 / 0.496 / 0.489（全てCIが0.5を含む） |

## 読み方の注意

- 150ペア程度だと、同条件でも20回に1回はCIが0.5を外れる。結論はSPRTで出す。
- `exploit` の選抜段階のスコアは高めに出る（たくさんの候補から最大値を選ぶため）。
  必ず再検定側の数字を見る。
- AIの駒の評価が偏っていると、「強い駒」ではなく「AIが好む駒」が見つかる。
  採用率と合わせて見る。

## ステータス算出式（sprigling.py）

1. HP×攻撃力（総合力）= 18 × 450 × (動員コスト/450)^2（移動力3なら×0.8）。
   指数は `power_cost_exponent`。1（比例）だと軽量級の数押しが0.80で圧勝したため2にした
2. HPと攻撃力の配分 = 部位ごとのWhittlewisp式（耐久: `compute_max_dur`の合計、
   攻撃: 技の `base_damage` の最大値＋他部位ぶん×0.25）の比で決める
3. 移動力 = 2 基準、脚の平均length≥1で+1、重量級で-1、脚なしは1
4. 属性 = 重量への寄与が最大の材質
5. 配置コスト = 動員コスト × `placement_cost_ratio`(0.75, 暫定)

係数は全て `config.py` の `CONFIG["sprigling_stats"]`。
