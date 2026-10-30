# Phase 2 盤面評価・1手選択の実装記録

実施日: 2026-10-30

状態: **実装PASS・参考評価完了・棋力版としての採用は保留**。Phase 3の構造的な着手条件は満たすが、今回のGoalでは実装しない。

## 1. 対象と開始条件

開始HEADは `ed7b4af3d4806261b1fa5ec0e7aa8268f4bfcf6c`、ブランチは `feat/ai-phase2`、開始時clean。準備時点のHEAD `82f2c7b` と区別する。開始前確認（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/preflight.json`）に、準備時の8項目のhash一致・変更前2/100局のfresh解析成功・直前Fullの39source一致を保存した。既存beforeは現在のハーネスで再利用可能だったため再取得していない。

本番変更は `reversi_player/reversi.py` と `reversi_player/ai_engine.py` のみ。固定baseline、ハーネス、GUI、プロトコル、品質設定、序盤集合は変更していない。実装開始前に[設計第8節](../phase2_design.md#8-実装着手時の具体化2026-10-30)で責務・入力検証・整数式・診断方法を確定した。実装Goalの範囲ではcommit/push/merge/tagを実施していない。

## 2. 実装した責務

- 本体の `_board_from_rows`、`legal_moves_for`、`apply_move` が不変snapshotから独立した作業用Boardを作り、既存の `_ask/_play` を利用する。コピーには位置selectorを明示し、既定factoryへの再帰を避ける。
- engineの `validate_position` がsnapshotと色の値域を共有検査する。座標のbool・範囲違反・非合法手を含む違反はValueError。到達可能性は静的snapshotの保証外とし、診断側で合法棋譜を再生した。
- 凍結された整数特徴、特徴抽出、評価を分離した。角差・mobility差・有効位置差の40/40/20という事前固定重みを維持し、角の二重加点と取得後の隣接負点を除く。非終局は `(6960*c+435*m+20*p)/696`、終局は確定勝敗と石差を使う。
- factoryはmodule関数2個だけを保持する。各候補をroot色の視点で評価し、正確に同点なら(y,x)順。空候補ではcallbackなしNone。Boardの戻り値検証後、実盤面へ一度だけ確定する。
- Board既定は1手selectorへ接続した。ランダム・位置selectorと偽値のcallableを含む明示注入は維持した。逆import、新規本番module、探索・学習・trace producerは追加していない。

[現行AI内部図](../harness_architecture.md#phase2-design)と[内部契約](../harness_contracts.md#phase2-boundary)を実装へ照合した。Phase 1の図は当時の記録と明記した。ハーネスの責務・依存・状態・正式入口・schemaを変えないため、ハーネスのDAGと状態図への新しい辺は不要。

## 3. 正しさ・品質の証拠

| 検証 | 結果 |
|---|---|
| 実装前の選択回帰 | 未実装のcallback/特徴を検出し11失敗、124除外 |
| 実装後の関連回帰 | 245成功。新規Phase 2専用135ケースを含む |
| 全pytest | 1041成功、失敗・error・skip 0 |
| 独立smoke | 1成功、失敗・error・skip 0 |
| Full品質ゲート | 必須11工程すべて成功 |
| strict | 既存17対象すべて成功。scope・ignore・期待値の緩和なし |
| source照合 | 40項目の開始/終了/現在hash一致 |
| 保存証拠 | verify_publication=[] |

Fullは直接CPython 3.13.5 / Windows AMD64、venvではない環境で実行した。pytest外部計測は449.92秒、独立smokeは7.88秒。原証拠はquality.json（ローカル保存・Git管理外: `evaluation_results/quality-bc0fb825dd474969a5c2804d69f6b54a/quality.json`）、evidence.json（ローカル保存・Git管理外: `evaluation_results/quality-bc0fb825dd474969a5c2804d69f6b54a/evidence.json`）。quality.jsonのSHA-256は `380307ac80db32fb97320b128b130fe26239b52632bf859b785529acbb650270`。

[独立の固定棋譜10局面](../../tests/fixtures/phase2_positions.json)と[専用テスト](../../tests/test_ai_phase2.py)で、角取得・複数方向反転・片側パス・空きあり終局を照合した。人工盤面は局所重み・確定勝敗の算術検証にだけ使い、合法棋譜のある局面と区別した。元の式のFraction計算と独立refereeから期待値を得ており、本番関数の出力を期待値へ流用していない。

黒(3,2)後は角差0・合法手数3対3・位置差9・評価15/58。次の白の3候補は225/232、0、-5/8。全黒盤の黒1064/白-1064を確認した。評価値の8対称性・視点反転、同点・候補順、有限値と値域、入力違反、コピー非共有、構築の非再帰性、実着手一回、例外伝播、RNG/stdout不変を検証した。選択座標には同点時の回転同変性を要求していない。

特徴診断（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/feature-diagnostics.json`）は局面ID・棋譜・盤面・視点・各候補の生特徴・正規化・寄与・終局・評価・選択理由を保持する。空き44以上/13〜43/12以下は観測用の群であり、重み切替や学習には使っていない。固定JSONのhashは品質ゲートのPython source一覧とは別に、診断と最終監査で保全する。

実装前の失敗ログ（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/before-implementation-tests.txt`）、関連JUnit（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/related.xml`）、静的検査（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/static-pre-full.json`）も保存した。

## 4. 事前登録した参考対局

全204局が正常完了し、異常0。各100局はdevelopment.jsonの1000局面から50ケースを非復元抽出して先後交換した。selection seed=20261020、master seed=20261021、profile/policy=descriptive、startup/command/genmove上限=5/1/10秒。smokeは初期盤面2局で各100局の前に実行した。

候補のengine、random_v0のmanifest、positional_v1の独立engineとmanifestを明示資産へ登録した。実argv・終了値・外部時間は評価driverのjournal（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/evaluation-driver.json`）を参照する。JSONと隣接runsを保持し、held-outはbytes hashだけを確認して内容解析・選択・対局には使っていない。

| 対戦 | 正常完了 | A勝敗 | 勝ち点率 | genmove件数 | 平均ms | 最大ms |
|---|---|---|---|---|---|---|
| random_v0 2局smoke | 2/2 | 2勝0敗0分 | 1.00 | 62 | 2.572290 | 7.9746 |
| random_v0 100局 | 100/100 | 97勝3敗0分 | 0.97 | 2781 | 2.628463 | 18.5923 |
| positional_v1 2局smoke | 2/2 | 0勝2敗0分 | 0.00 | 62 | 2.230752 | 4.8349 |
| positional_v1 100局 | 100/100 | 39勝61敗0分 | 0.39 | 2738 | 2.619066 | 51.8717 |

| 100局の相手 | Aの色 | 勝敗 | 勝ち点率 |
|---|---|---|---|
| random_v0 | 黒 | 48勝2敗0分 | 0.96 |
| random_v0 | 白 | 49勝1敗0分 | 0.98 |
| positional_v1 | 黒 | 24勝26敗0分 | 0.48 |
| positional_v1 | 白 | 15勝35敗0分 | 0.30 |

時間は全genmoveの外部応答で、pass/終局問い合わせも含む。内部探索時間や同一workを意味しない。100局での最大応答はrandom戦18.5923ms、positional戦51.8717msで、取得した範囲の期限違反はない。

原評価はrandom smoke（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/after-vs-random-smoke.json`）、random 100局（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/after-vs-random-100.json`）、positional smoke（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/after-vs-positional-smoke.json`）、positional 100局（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/after-vs-positional-100.json`）。最終再解析記録（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/evaluation-verification.json`）と各 `*-reanalysis.json` に全4件のreference_available=true、request_success=true、descriptive_only、正式intervalなしを保存した。ハーネス・本体・明示資産の実bytesも照合した。

## 5. 比較と採否

変更前random戦は86勝14敗、勝ち点率0.86、変更後は97勝3敗、0.97。公開比較結果（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/random-before-after-comparison.json`）はcomparable=true、condition_mismatches=[]。同じ50ケース・100局で先後、role seed、開始棋譜を照合し、対応する参考観測差（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/matched-descriptive-differences.json`）は **+11/100=+0.11**。平均応答は変更前0.176260msから変更後2.628463msへ増えた。

一方、固定positional_v1との直接対戦は **39勝61敗**、特に白番15勝35敗だった。randomへの成績改善を、旧位置評価版への優越や一般的な棋力改善へ読み替えない。正しい1手評価でも、固定特徴と浅い選択で相手への応手を読まないことが弱さの候補となるが、原因を特定した証拠ではない。結果を見て重みや判定基準を変更していない。

**判定**: 評価・コピー・1手選択の実装PASSと参考評価完了。対戦用の強化版としての昇格は保留し、固定positional_v1を維持する。現行Boardの1手selector接続はPhase 3で探索を検証する研究用基盤として残す。正式採用PASS・「旧版より強い」は主張しない。

内部予算/trace4/時系列の正式資格は未達。公開比較のpair_statistics_available=false、budget_verified=false、decision=undeterminedを保持し、正式CIや同一workの保証を追加していない。

## 6. G0〜G5と次工程

| ゲート | 判定と証拠 |
|---|---|
| G0 構成・設計 | PASS。先行契約、現行図、初期重み、専用テスト、比較条件、開始hashを記録 |
| G1 正しさ | PASS。専用135・関連245・全1041成功。独立ルール、手計算、プロトコル、GUI接続を検証 |
| G2 品質 | PASS。Full11工程、strict17、独立smoke、40sourceと保存証拠が一致 |
| G3 対局健全性 | PASS。4run・204局完走、異常0、取得条件の外部期限違反なし |
| G4 比較可能性 | 参考比較としてPASS。公開比較条件一致、50共通ケース・両leg・role seed一致。同一workの正式資格は未達 |
| G5 証拠と判断 | PASS。raw、fresh解析、診断、hash、実argvを保存。棋力版採用保留と限界を明記 |

Phase 2固有の特徴・終局・視点反転・盤面不変とPhase 1回帰を満たす。評価基盤としてはPhase 3の浅いMinimaxへ着手可能。ただし、このGoalでPhase 3を実装したわけではなく、棋力改善と正式資格は次工程でも別に確認する。

## 7. 新規テスト分類レビュー

レビュー原記録（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/test-scope-review.json`）: 新規135ケースのJUnit時間合計0.231秒、最大0.054秒。Fullの全1041 nodeに包含され、現行Quick274/Standard296のどちらにも新規nodeは0件だった。

今回の登録変更は行わず、Phase 2の独立した正しさの一式をFullへ保持する。Quick/Standardの成功だけではPhase 2特徴・1手選択の正しさを保証しない。Phase 3着手時にコピー・評価境界の不変条件をStandard_AIへ登録することを再検討し、ファイル全体をQuickへ一括追加しない。

## 8. 再現用の入口と制約

実際の主要入口はルートから以下。対応Pythonの実体と詳細argvは保存manifest/journalにある。

```powershell
python -B -X utf8 -m pytest -q tests/test_ai_phase2.py tests/test_ai_engine.py tests/test_ai_compatibility.py tests/test_result.py tests/test_stdin_commands.py -p no:cacheprovider
python -B -X utf8 tools/quality.py
python -B -X utf8 evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/run-evaluations.py
python -B -X utf8 evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/verify-evaluations.py
```

評価driverは既存出力への上書きを拒否する。新しい対局実験は新UUIDの領域で、journal記載の引数・明示資産・条件を維持して実行する。今回の評価JSON、runs、before、品質原証拠を再利用コマンドの出力先として上書きしない。

解析補助スクリプトの初回は_Targetの本体metadataをscriptキーの下と誤認してKeyErrorになった。修正記録（ローカル保存・Git管理外: `evaluation_results/phase2/candidate-29fb8abc734d43398785f5fc562d8441/analysis-helper-correction.json`）のとおり現行契約の直接path/sha256参照へ直し、その後全4runと対応比較に成功した。ハーネス・本番・テスト・原証拠はこの修正では変更していない。

実ブラウザでのGUI操作・Mermaid実描画は未確認。接続回帰と静的モデル照合とは区別する。開発用50ケースの成績から未使用局面への一般化を保証せず、held-out、正式統計、探索・学習の完成を主張しない。現時点で未解消の実装blockerはない。

## 9. 公開準備（2026-10-30）

利用者の追加依頼によりコミットとmainへの反映を準備する。新規チェックアウトではGit管理外の評価証拠が存在しないため、準備資料と実装報告の計28リンクをローカル保存先のコード表記へ変更した。過去の実行結果・hash・原証拠・必須テストは変更していない。検査対象40sourceはFull成功時と同一であり、公開ファイル集合のリンク整合性と既存の文書回帰を追加確認する。品質・棋力の判定は前節までの記録を維持する。
