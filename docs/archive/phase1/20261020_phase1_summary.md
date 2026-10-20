> **Archived record:** This is a historical Phase 1 record. Use [Phase 1 handoff](../../phase1_handoff.md) for the current consolidated status. Machine-readable execution evidence remains under evaluation_results/.

# Phase 1 完了報告・採用判断

判定日: 2026-10-20<br>
対象branch: `feat/ai-phase1`<br>
HEAD: `eb2e860f06ac731c13c7a0169e4de93f3b8e1108`<br>
対象: Phase 1Aの選択器分離、Phase 1Bの位置評価、品質検証、固定random_v0との参考比較

## 1. 結論と独立判定

**Phase 1の実装・品質検証・予定した参考評価は完了。positional_v1を次工程の開発候補として暫定採用することを推奨する。** これは下記の事前登録済みdescriptive基準に基づく技術判断であり、正式統計の成功、同一探索予算での優越性、授業最終AIへの採用ではない。

| 項目 | 判定 | 根拠と適用範囲 |
|---|---|---|
| A. Phase 1Aの構造分離 | PASS | engineは選択だけを担当し、盤面・合法手・確定着手・入出力はBoardが所有。5 seedの固定random_v0完全対局互換、候補順・shuffle・RNG回帰が成功。 |
| B. Phase 1Bの位置評価 | PASS | 固定8×8表の`weights[y][x]`を最大化し、同点は`(y,x)`昇順。候補所属、無手・単独候補、非変更性、一回の着手適用をテスト。最新全品質ゲートも成功。 |
| C. AIの正常対局 | PASS | 位置AI対固定random_v0の2局smoke、100局本評価が全局完了。再検証したgameplay・coverage・母集団・選択・source consistencyはverified、integrity errorsは0件。 |
| D. 固定random_v0との参考性能差 | PASS（参考結果の取得） | 100局で86勝14敗0分、勝ち点率0.86。黒0.84、白0.88。今回の開発sampleで良好だったという観測に限定する。 |
| E. Goal 5の事前採用基準 | PASS（暫定採用基準） | 事前登録4.2の対局健全性・50先後交換pairの構造検証を満たし、4.3の全体0.55以上・先後それぞれ0.45以上を達成。正式統計上の採用資格とは別。 |
| F. Phase 2への技術的前提 | PASS（前段階の条件） | Phase 1回帰・対応環境の品質ゲート・参考比較が揃い、Phase 2の設計へ進行可能。実装前の比較版固定とPhase 2固有設計は第8節の次作業。今回Phase 2は実装していない。 |

採用は「次工程で候補として維持する」という事前登録の意味に限定する。ハーネスの保存済み`status.decision=undetermined`は書き換えず、この報告による人の判断・推奨と区別する。

## 2. Phase 1A・1Bの実装と回帰

[Phase 1A実装報告](20261019_phase1a_implementation.md)と[分離前互換基準](20261019_phase1a_compatibility_baseline.md)を照合した。engineはimmutableな盤面行、色、順序付き合法手tupleを受け取り、座標またはNoneを返す。循環importは作らず、直接script起動・package import・module起動・GUI接続の既存回帰を維持する。

ランダムselectorは候補を複製して非空時に一度だけshuffleする。固定seed `0, 7, 31, 73, 20261016`について固定random_v0と全応答・棋譜・各盤面・終局結果を比較する。候補列挙順、shuffle回数、RNG状態、不正な戻り値を適用前に拒否する回帰もある。Phase 1Bではこの互換検査にランダムselectorを明示注入する。production既定の棋譜が位置評価へ変わるのは意図した仕様変更であり、位置AIの棋譜がrandom_v0と同一だとは主張しない。

位置selectorは人手設定の固定表で合法候補を採点し、最大重み、次に最小y、最小xで選ぶ。盤面・候補・乱数状態を変更せず、Boardが検証後に着手を一度だけ確定する。単体・性質・統合回帰は`tests/test_ai_engine.py`、`tests/test_ai_compatibility.py`と既存のstdin/result/invariants試験で確認する。実装時の専用36成功、関連112成功に加え、下記の現在sourceに対応する全787件も成功している。

実装前の8失敗・19成功は未実装selectorと既定動作差を検出したred段階の記録であり、残存する失敗件数ではない。

## 3. 品質証拠

対応するWindows 11 / AMD64 / **直接CPython 3.13.5**で実行済みの`python -B tools/quality.py`を確認した。

| 検証 | 結果 | 保存先 |
|---|---|---|
| 全pytest | 787成功・0失敗、493.90秒 | ローカル保存・Git管理外：`../../../evaluation_results/quality-31d29272b409461294ffb0c45a9474bb/pytest.txt` |
| 独立smoke marker | 1成功・786 deselected、4.78秒 | ローカル保存・Git管理外：`../../../evaluation_results/quality-31d29272b409461294ffb0c45a9474bb/smoke.txt` |
| strict mypy | 14 source files、診断0 | ローカル保存・Git管理外：`../../../evaluation_results/quality-31d29272b409461294ffb0c45a9474bb/typing.txt` |
| Lint・限定format・model・dependencies | 全必須検査成功 | ローカル保存・Git管理外：`../../../evaluation_results/quality-31d29272b409461294ffb0c45a9474bb/quality.json` |
| 保存証拠 | 公開検証`verify_publication=[]`、開始/終了hash一致 | 同directoryの`evidence.json`、`evidence.md`、pytest/smokeのJUnit・journal |

environment / model / dependencies / lint / format / typing / pytest / smoke / snapshot / evidenceの全10段階がPASS。今回の総括でもpublication検証結果が空であり、現在ファイルと品質JSONの検査対象hashに不一致がないことを再確認した。履歴のPhase 1A 779成功は当時のsnapshotの結果であり、現在の全件数は787。

以前のvenv実行は726成功・61失敗、統合ゲートはenvironment FAILだった。Windows venv redirectorではPopen PIDと子の実PIDが異なる。対応する直接Pythonで同じ失敗箇所および全体を再検証して成功したため、venvの結果を最終PASSへ数えず、環境起因の履歴として保持する。「直接Pythonが存在しない」とした以前の記述は、sandbox内の探索範囲による誤判断であり訂正する。

## 4. 参考対局・事前基準

条件の正本は[Phase 1B事前登録](20261020_phase1b_design.md)。条件・重み・閾値は結果を見て変更していない。

- A: `reversi_player/reversi.py`、明示asset `reversi_player/ai_engine.py`。
- B: 独立した固定`baselines/random_v0/reversi.py`、manifestを明示assetに登録。
- 母集団: development、Population v1、1000件、8 placements。held-outは使用しない。
- selection seed: `20261020`、master seed: `20261021`。50異なるcaseを非復元抽出し、各caseを先後交換して100局。
- profile / budget policy: 本比較は`descriptive / descriptive`。先行2局は`smoke / descriptive`。
- timeout: startup 5秒、通常command 1秒、genmove 10秒。内部探索時間とは区別する。

| Aの成績 | 局数 | 勝 | 負 | 分 | 勝率・勝ち点率 |
|---|---:|---:|---:|---:|---:|
| 先行smoke | 2 | 1 | 1 | 0 | 0.50 |
| 本比較全体 | 100 | 86 | 14 | 0 | 0.86 |
| 本比較・黒番 | 50 | 42 | 8 | 0 | 0.84 |
| 本比較・白番 | 50 | 44 | 6 | 0 | 0.88 |

100局すべて`complete`かつprotocol/referee再検証済み。50 pairは各2 leg、同一case、先後交換、seed対応、coverageを検証し、失敗0・中断0・未着手0。raw勝ち点合計86÷100がsummaryの0.86と一致する。

| 外部genmove応答 | 標本数 | 平均ms | 最大ms |
|---|---:|---:|---:|
| A（位置評価） | 2733 | 0.197634 | 0.776700 |
| B（random_v0） | 2726 | 0.189058 | 0.793400 |

この時間は親が計測したコマンド外部応答時間であり、内部探索だけの時間・予算遵守の証拠ではない。起動時間もこの値へ混ぜない。

事前登録4.2の正常対局・ケース対応に必要なデータは検証済みで、事前登録4.3の参考昇格閾値を達成した。明示的に未対応とした内部予算はunknownのまま保持する。段階設計の共通ゲート進行原則も、内部予算unknownを同一workの証明とせず、正式資格と実装PASSを区別して参考研究の次工程を個別に判断すると定める。本判定はその範囲での暫定採用推奨である。

## 5. 保存証拠・再解析

| run | JSON | run ID |
|---|---|---|
| 2局smoke | ローカル保存・Git管理外：`../../../evaluation_results/phase1b/position-vs-random-v0-smoke-20261020.json` | `a75265450ccd465fa5858c219f0cddbc` |
| 100局descriptive | ローカル保存・Git管理外：`../../../evaluation_results/phase1b/position-vs-random-v0-100-20261020.json` | `a8f6ce3b11794102a0310784143e90e0` |

原証拠は`evaluation_results/phase1b/runs/<run_id>/`に保存され、各runの`run-state.json`は全game・command応答・親側lifecycle・途中経過を含む。母集団原本は`opening-population.json`。実AIがtrace4 producerを実装していないため、legごとのA/B.jsonlは**生成されていない**。原証拠directoryの保存を、内部traceの存在と取り違えない。

本総括で保存JSONを`contracts.inspect_evaluation`と公開入口`analysis.assess_evaluation`へ再投入した。両runともinput valid、gameplay / coverage / population / selection / source consistency verified、integrity errors=[]、reference_available=true、request_success=true。保存checkpointも予定局数の全局完了と一致した。

100局JSON SHA-256: `7b1954b694dc21f402a5bffc0debc97e637dbcbdb1e3b99c5e6bcc40842183f3`。smoke JSON SHA-256: `e755d0fb9cb1ae7610dc12bb0cc1d09f3a366019a0c9ca20428ab5ebbba4f903`。

同じreportを再解析する際は原証拠directoryも保持する。再対局は同じCLI条件で別output名を使い、既存JSONを上書きしない。時間計測の完全一致や、正式なdeterminism probe成功は主張しない。品質・評価artifactは通常Git管理外なので、将来の掃除前にdirectory単位で保全する必要がある。

## 6. Git・source・評価識別

HEADはPhase 0統合時点であり、評価対象は`feat/ai-phase1`の**未コミットworking tree**。HEADだけでは今回評価したAIを復元できない。既存の本体・設定・テスト・設計文書の変更、新規engine・回帰・reportsを保持し、Git操作は行っていない。

| 資産 | SHA-256 |
|---|---|
| A script | `a012c2b30c304b4716e5f3df2d0ffac4f689a5b35cb38fefe6c16f7b27428efe` |
| A engine | `3a6d69c35bf79d2a184cabde023b5274a4a1e34eb318aa4c4be42569e6401c5f` |
| 固定B script | `a485400c335a8731a0cafd1d77d98fbbcda1289194c4e13147cbaf3135b12b41` |
| 固定B manifest | `b1b7b65d93dd5b4178477c88c6fce854bd9f9eeb28b9f7963e7ad4b4522c7220` |
| development JSON | `59dcc82a4365dfbad5403a834558fe66d8522c3e9d8d3a4cfaee7f388db34c13` |
| harness/evaluate.py | `2ea2cc5c2a01227c51576be79e66300696d8e12ee13e19297cbf7fc57ad8adec` |
| harness/contracts.py | `556b05f2c62364554da5eb6925fbc150612e6e0b100cc6369847369bb69fe3d3` |

母集団hashは`e191fbfed938edca93431ce9efd897a6bfd78dbbc5d68077bcf7f77e7a802d78`、100局selection hashは`55fd7ac70e927b22a114b897cd70fdaf8fc9feedea27e80ebb481d99eb08588f`。harness取得fingerprintは`0ede509646a3cfb7ec7a82c4967f5088569e735a46adf78f1b60aa2f2e2dbd96`。個々のharness 7 source hash、runtime、役割seed、50 case IDは評価JSONに保存されている。

## 7. 正式資格と残る未検証

- 正式統計: 対象外（今回profileでは要求しない）。`formal_eligibility=not_requested`、statisticsは`descriptive_only`、CIなし。正式資格を獲得したという判定はしない。
- 内部探索予算・trace4: 未実装。budget / timing / metricsはunknown、`metrics_certified=false`。子起動・command内部時系列を完全には検証できない。
- 統計用pair資格: 全100 gameで`pair_status=unqualified`、理由は`budget_not_verified`。これは第4節の先後交換構造verifiedと別の判定であり、正式pair統計の成功へ読み替えない。
- GUI: package / Process / RequestHandler等の既存自動回帰は成功。実ブラウザ操作は未確認。
- 自動strict登録漏れ防止（品質移行Phase 10）、実AIの正式探索計測、同一work比較、正式優越性評価、学習モデルは未完了。現在の14 strict対象成功から任意の新規moduleまで保証しない。
- Mermaidの実レンダリングは今回未確認。責務・通信・評価契約は本総括で変えていないため図の変更は不要。

共通ゲートG0〜G5はPhase 1の実装・参考評価という範囲でPASS。G4は固定B・序盤・seed・環境・明示資産の識別と検証の成功であり、同一内部workを意味しない。正式資格は別に未達として維持する。

## 8. 次工程への推奨と残作業

1. Phase 1は完了として記録し、`positional_v1`を次工程の開発候補として暫定維持する。参考sample以外での一般的な優越性・最強版とは呼ばない。
2. 比較版を`baselines/positional_v1/`に固定した。`reversi.py`と`ai_engine.py`を評価時点のbytesで複製し、`manifest.json`に元ファイル・保存ファイルのSHA-256、実行環境、選択規則、評価結果への参照を記録した。hash照合で両コピーの一致を確認済み。random_v0も維持する。Gitへのcommitは行っていないため、独立した作業ツリーの破損・削除に対する保護は未完了。
3. Phase 2では仮局面生成・副作用のない盤面評価・評価視点・終局優先と特徴量の契約を先に確定する。対照はrandom_v0と固定positional_v1、条件・判定基準は再び結果を見る前に記録する。
4. 実AIの予算trace・時系列対応が必要な正式評価へ移る際は、未対応を実装・検証する。今回の参考PASSで正式条件を免除しない。

この総括ではPhase 2本番コード、テスト、設定、図、既存評価データを変更せず、報告・開発計画・進捗の状態記述だけを更新する。commit / push / merge / tagは行わない。

## 9. 文書更新後の最終品質確認と実行コマンド

総括文書の更新後にもWindows直接CPython 3.13.5で `python -B tools/quality.py` を実行した。最終品質JSON（ローカル保存・Git管理外：`../../../evaluation_results/quality-c1e5e48e0616406fb55c8274a9654107/quality.json`）は全10必須段階PASS。全pytestは **787成功・0失敗（492.24秒）**、独立smokeは **1成功・786 deselected（3.11秒）**。publication再検証=[]、開始/終了hash一致、現在検査対象hashの不一致0。第3節の先行成功runも削除せず保持する。

最終quality.json SHA-256: `8400df5320196eac74d58dde96f073c650c19e4430c3da172ce7898bf3e350aa`。

実行に使用した主要コマンドは以下。対局コマンドは直前の評価作業で各一度実行済みであり、この総括では100局を再取得せず保存済み結果を再解析した。既存output名をそのまま再実行すると上書き保護で拒否されるため、新runでは新しい名前にする。

```powershell
$phasePython = 'C:/Users/taaai/AppData/Local/Programs/Python/Python313/python.exe'
& $phasePython -B tools/quality.py
& $phasePython -B -m harness.openings validate benchmark_sets/phase0/development.json
& $phasePython -B -m harness.evaluate --a reversi_player/reversi.py --b baselines/random_v0/reversi.py --a-asset reversi_player/ai_engine.py --b-asset baselines/random_v0/manifest.json --games 2 --seed 20261021 --selection-seed 20261020 --openings benchmark_sets/phase0/development.json --evaluation-profile smoke --budget-policy descriptive --startup-timeout 5 --command-timeout 1 --genmove-timeout 10 --output evaluation_results/phase1b/position-vs-random-v0-smoke-20261020.json
& $phasePython -B -m harness.evaluate --a reversi_player/reversi.py --b baselines/random_v0/reversi.py --a-asset reversi_player/ai_engine.py --b-asset baselines/random_v0/manifest.json --games 100 --seed 20261021 --selection-seed 20261020 --openings benchmark_sets/phase0/development.json --evaluation-profile descriptive --budget-policy descriptive --startup-timeout 5 --command-timeout 1 --genmove-timeout 10 --output evaluation_results/phase1b/position-vs-random-v0-100-20261020.json
```

今回の保存証拠照合には `tools.evidence.verify_publication`、`contracts.inspect_evaluation`、`analysis.assess_evaluation` を使用した。報告書の相対リンクは全参照先の存在を確認し、事前登録は状態注記以外の本文が更新前と同一であることも照合した。
