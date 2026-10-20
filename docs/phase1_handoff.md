# Phase 1 引き継ぎ資料

更新日: 2026-10-20<br>
対象: Phase 1A（選択器分離）、Phase 1B（位置評価AI）<br>
状態: 実装・検証・descriptive比較を完了。次の開発候補として`positional_v1`を暫定採用することを推奨。

この文書をPhase 1の現行参照先とする。詳細な当時の設計・実装報告は[`docs/archive/phase1/`](archive/phase1/)に保管し、評価JSON・対局原証拠・品質実行証拠は`evaluation_results/`に保持する。

## 1. 引き継ぐ決定

- Phase 1Aの選択器分離とPhase 1Bの位置評価実装は完了。`random_v0`は独立基準として維持する。
- 位置評価は固定の人手設定であり、学習済みでも探索型でもない。100局の結果はdevelopment集合上の参考観測で、一般的な優越性や正式採用を証明しない。
- 事前登録したdescriptive採用目安を満たしたため、`positional_v1`をPhase 2以降の比較候補として暫定維持する。
- Phase 2の設計に進める。Phase 2実装の開始条件は、そのPhaseのモデル・契約・テスト条件を別途確定すること。

## 2. 比較版とAI契約

| 版 | 固定資産 | SHA-256 |
|---|---|---|
| `positional_v1`本体 | [`baselines/positional_v1/reversi.py`](../baselines/positional_v1/reversi.py) | `a012c2b30c304b4716e5f3df2d0ffac4f689a5b35cb38fefe6c16f7b27428efe` |
| `positional_v1` engine | [`baselines/positional_v1/ai_engine.py`](../baselines/positional_v1/ai_engine.py) | `3a6d69c35bf79d2a184cabde023b5274a4a1e34eb318aa4c4be42569e6401c5f` |
| 比較相手 | [`baselines/random_v0/`](../baselines/random_v0/) | 詳細は同ディレクトリのmanifest |

`positional_v1`のmanifestは[`baselines/positional_v1/manifest.json`](../baselines/positional_v1/manifest.json)。固定時点の作業ツリーは未コミットだったため、独立バックアップは未作成。manifest記載のhashで資産の同一性を照合する。

engineは合法手の移譲選択だけを担当する。Boardが盤面、合法手生成、選択結果の検証、着手適用、標準入出力を所有する。位置評価は`POSITION_WEIGHTS[y][x]`を最大化し、同点は`(y, x)`昇順。固定表はengineに保存されている。Minimax、Mobility探索、Alpha-Beta、学習は含まない。

## 3. 参考比較の固定条件と結果

事前登録条件の原本はarchive内の`20261020_phase1b_design.md`。評価対象は`positional_v1`対固定`random_v0`、development序盤集合Population v1（1000局面、各8着手済み）。held-outは使用していない。

| 条件 | 値 |
|---|---|
| 対局 | 100局、50序盤ケースを非復元抽出し、各ケースを先後交換 |
| selection seed / master seed | `20261020` / `20261021` |
| profile / budget policy | `descriptive` / `descriptive` |
| startup / 通常command / genmove timeout | 5秒 / 1秒 / 10秒 |
| 実行環境 | Windows AMD64、直接CPython 3.13.5 |
| 2局smoke | 2完了、失敗0 |
| 100局 | 86勝、14敗、0分。勝ち点率0.86 |
| 黒番 / 白番 | 42勝8敗0分（0.84） / 44勝6敗0分（0.88） |

100局の評価JSONは[`position-vs-random-v0-100-20261020.json`](../evaluation_results/phase1b/position-vs-random-v0-100-20261020.json)、run IDは`a8f6ce3b11794102a0310784143e90e0`。2局smokeは[`position-vs-random-v0-smoke-20261020.json`](../evaluation_results/phase1b/position-vs-random-v0-smoke-20261020.json)、run IDは`a75265450ccd465fa5858c219f0cddbc`。原証拠は`evaluation_results/phase1b/runs/<run_id>/`。

## 4. 品質・回帰の証拠

最新の直接CPython品質ゲートは**全10段階PASS**。全pytest 787成功・0失敗、独立smoke 1成功、strict mypy 14 source files、公開証拠検証エラー0件。証拠一式は[`quality-c1e5e48e0616406fb55c8274a9654107`](../evaluation_results/quality-c1e5e48e0616406fb55c8274a9654107/)。

分離回帰は固定seed `0, 7, 31, 73, 20261016`で旧`random_v0`との棋譜・盤面・応答・RNG状態を比較した。Phase 1Bの位置selectorは合法手選択、最高重み、同点順、入力非変更、1回の着手適用を検証した。実行記録は[`20261019_phase1a_compatibility_baseline.md`](archive/phase1/20261019_phase1a_compatibility_baseline.md)、[`20261019_phase1a_implementation.md`](archive/phase1/20261019_phase1a_implementation.md)、[`20261020_phase1b_implementation.md`](archive/phase1/20261020_phase1b_implementation.md)に残す。

## 5. 正式評価上の未達と制約

- 実AIは内部探索予算を消費・強制せず、trace4を生成しない。対局原証拠にA/B.jsonlはなく、内部思考時間・子プロセス全時系列は未知。
- gameplayと先後交換の構造は検証済みだが、全100局pairは`budget_not_verified`で統計資格未達。正式CI・統計的優越性・同一work比較は主張しない。
- ブラウザを使ったGUIの目視操作は未確認。GUI接続回帰テストの成功とは区別する。
- Phase 1対象の未コミット状態から作ったbaselineである。別媒体バックアップ・commitは未実施。

## 6. Phase 2へ渡す作業

1. `positional_v1`資産のhashを変更前に照合し、比較対象として変更せず保持する。
2. Phase 2の局面評価契約（評価視点、終局優先、盤面非変更、特徴量）とモデル図・テストを実装前に確定する。
3. 比較条件と判断基準を結果を見る前に登録し、`random_v0`とこの固定`positional_v1`の両方を対照にする。
4. 正式資格が必要な評価へ移る場合、実AIの予算計測、trace4、時系列検証を整備してから評価する。

## 7. 過去の詳細記録

以下は作業時点の記録として保存し、現状判断には本書を使う。

- [`20261019_phase1a_compatibility_baseline.md`](archive/phase1/20261019_phase1a_compatibility_baseline.md)
- [`20261019_phase1a_implementation.md`](archive/phase1/20261019_phase1a_implementation.md)
- [`20261020_phase1b_design.md`](archive/phase1/20261020_phase1b_design.md)
- [`20261020_phase1b_implementation.md`](archive/phase1/20261020_phase1b_implementation.md)
- [`20261020_phase1_summary.md`](archive/phase1/20261020_phase1_summary.md)
