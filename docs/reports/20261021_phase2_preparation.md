# Phase 2 準備記録

実施日: 2026-10-21  
状態: 準備完了。Phase 2の本体・テスト実装は未着手。

## 作業内容

開始HEADは `82f2c7b46d4150144fc58d1525fbfdd69277584f`、開始時main・clean。作業用 `feat/ai-phase2` を作成した。Phase 1引き継ぎ、段階設計、課題仕様、ハーネス契約・モデル、新規テスト方針を照合し、[Phase 2実装準備](../phase2_design.md)に具体的な契約・式・テスト条件・比較条件を記録した。

Python・テスト・設定・固定AIの変更は行っていない。予定するコピーcallbackと1手評価の呼出図・境界を、現行実装とは区別して先行追記した。ハーネスの正式入口・schema・時計・予算・統計契約は変更していないため、ハーネスの現行import図・状態図の辺は追加しない。

## 開始時の確認

原証拠: preflight.json（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/preflight.json`）。

- 直接CPython 3.13.5 / Windows AMD64、venvではないことを確認。
- random_v0の本体、positional_v1の本体・engine、現行本体・engine、Phase 0のbaseline manifest、開発用／held-out序盤bytesの8照合は一致。
- 開発用1000件は既存のload_populationで検証。held-outはbytesのSHA-256だけを確認し、盤面内容の解析や対局には使用していない。
- 通常sandboxではPythonが見つからなかったため、確認済みの直接Pythonを絶対パスで起動した。パッケージの導入や環境変更は行っていない。

## 変更前対局

Phase 2の新しいbeforeとして、現行A対固定random_v0を新規取得する。profile/policyはsmokeまたはdescriptive、開発用100局のselection seed/master seedは20261020/20261021。

今回の実行結果（終了0）:

| 対局 | 正常完了 | A勝・敗・分 | 異常 |
|---|---|---|---|
| 初期盤面2局smoke | 2/2 | 0勝2敗0分 | 0 |
| 開発用100局 | 100/100 | 86勝14敗0分、勝ち点率0.86 | 0 |

100局の黒番は42勝8敗、白番は44勝6敗。Aの全genmove外部応答は2733件、平均0.176260ms・最大1.8381ms。pass/終局問い合わせも含む値で、内部探索時間ではない。2局smokeの勝敗は棋力判定に使わない。

smoke評価JSON（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/before-vs-random-smoke.json`）、100局評価JSON（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/before-vs-random-100.json`）、再解析・hash確認記録（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/before-verification.json`）を保存した。隣接するruns配下も保持する。run IDはsmokeが `5aa059aae1944adc92b8c6fd045fa1ba`、100局が `e0d8e4be616843aca0787e07c81e9647`。

両runを `harness.analysis.reanalysis_report` からfreshに再解析し、`reference_available=true`、`request_success=true`、`statistics=descriptive_only`を確認した。原報告・cache・rawの検証を省略していない。内部予算・実AI trace4の未対応は残り、正式CI・正式な棋力改善は主張しない。

100局JSONのSHA-256: `74bd6a0262a031dc75c38c5f2f02941266ce22fd86ee941e06f8174efe0d9ad5`。既存Phase 1と同じ86勝14敗だったが、今回の現行ハーネスによる独立したbefore取得として識別する。

## 品質検証

設計資料を更新後、直接CPython 3.13.5で `python -B -X utf8 tools/quality.py` を実行し、終了0、Full必須11工程すべてPASSを確認した。

| 確認 | 今回の結果 |
|---|---|
| 全pytest | 906成功、失敗・error・skip 0、外部467.59秒 |
| 独立smoke | 1成功、外部2.99秒 |
| strict登録 | 既存17対象、型検査成功 |
| モデル・対象scope | 成功、モデル不一致0 |
| source照合 | 39項目の開始・終了・現在hash一致 |
| 保存証拠 | `verify_publication=[]` |

quality.json（ローカル保存・Git管理外: `evaluation_results/quality-9a53a591a833489f997b7c8f5c418a7b/quality.json`）、evidence.json（ローカル保存・Git管理外: `evaluation_results/quality-9a53a591a833489f997b7c8f5c418a7b/evidence.json`）、evidence.md（ローカル保存・Git管理外: `evaluation_results/quality-9a53a591a833489f997b7c8f5c418a7b/evidence.md`）に保存した。quality.jsonのSHA-256は `ffd6063f617e232c8d4059a7c6e878c75bf40d6a192dada2cce634d0500dd388`。検査後に更新する本報告・準備資料の進捗表示・開発計画・QUALITY_PROGRESSは品質ゲートのsource hash対象外であり、モデル・契約・コード・設定は再変更していない。

資料8ファイルの相対リンク実在・コードfence対応・UTF-8と、コード/設定/固定資産の差分なしを確認した。黒(3,2)後の両者合法手数3・位置差9・評価値15/58は独立refereeで照合した。資料確認記録（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/document-checks.json`）と最終照合（ローカル保存・Git管理外: `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/quality-verification.json`）に保存する。

## 完了判定と残る作業

Phase 2の実装準備は完了。作業ブランチ、G0の具体的な図・契約・初期式・テスト条件、現行ハーネスによるbefore、開始前のFull品質を揃えた。次は準備資料の順に、コピー接続・特徴評価・1手selectorを実装する。

Phase 2用の新規テスト、コピーcallback、盤面評価、1手selector、実装後対局、GUI実操作は未実施。Mermaidの実描画も未確認。準備完了をPhase 2実装PASSや棋力改善・正式資格へ読み替えない。commit・push・merge・tagは行っていない。
