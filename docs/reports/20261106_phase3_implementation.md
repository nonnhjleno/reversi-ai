# Phase 3 浅いMinimaxの実装・評価記録

実施日: 2026-11-06。状態: **実装PASS・参考評価完了**。参考昇格条件を満たすが、正式採用は未達。探索基盤としてPhase 4へ進行可能だが、このGoalではPhase 4を実装しない。

## 1. 開始条件とPhase 2の判定

ブランチは `feat/ai-phase3`、開始HEADは `3ee20e3`、開始時のGit状態はclean。AGENTS・開発計画・段階設計書・課題仕様・現行契約・図・実装・テストを照合した。変更前ファイルと保護hashはローカルの `evaluation_results/phase3/run-01a13b34aab34ddb844ded6bd9bcdb40/preflight.json` に保存した。

Phase 2の現在の品質は開始時Full `evaluation_results/quality-4f8ed6b5972e4789b6f26f4d8c971ead/quality.json` で確認した。必須11工程・全1041pytest・独立smokeが成功し、保存証拠照合も一致した。保存済み固定相手各100局はfresh再解析で参考利用可能だった。random97勝3敗、positional39勝61敗を確認したが、一般的な棋力改善や正式採用は未達と判定した。段階設計書の構造的な進行条件を満たすため、Phase 3に着手した。

## 2. 先行設計と実装

[設計・実装契約](../phase3_design.md)、[内部構造図](../harness_architecture.md#phase3-design)、[探索境界](../harness_contracts.md#phase3-boundary)を実装前に確定した。本番変更はengineと本体のみで、新規本番moduleはない。既存17strict・lint対象に含まれ、scopeやignoreの緩和は行っていない。

剪定なしMinimaxはroot色の視点を固定し、root側で最大化、相手側で最小化する。石を置いたときだけ深度を減らし、パスは同じ深度、双方合法手なしは終局とする。通常動作は事前に固定した深度2。診断APIは深度0〜4、selectorは1〜4を受理する。同点は(y,x)順、評価重み・終局優先・石差はPhase 2から変更していない。

`SearchResult` のnodes/leaves/passesは不変の参考診断値で、計数定義は `minimax-call-v1`。実盤面の確定は従来どおり一回だけで、探索はコピーcallbackを使う。旧1手・位置・ランダムselector、直接起動、package import、既存GUI接続とプロトコルを保持した。Alpha-Beta・TT・bitboard・反復深化・trace4 producer・学習・search_v1固定は実装していない。

## 3. 正しさと品質

| 検証 | 結果 |
|---|---|
| 実装前の検出 | 新規API未実装を3ケースで検出 |
| 初回関連回帰 | 330成功・新規テストの仮定誤り1件 |
| 新規探索テスト | 104成功。深度0〜4、独立Fraction評価・Negamax、root/内部pass、終局、不変性、故障、接続 |
| 実装後Full | 必須11工程すべて成功 |
| 全pytest | 1145成功、失敗・error・skip 0 |
| 独立smoke | 成功 |
| strict | 既存17対象すべて成功 |
| source照合 | 41項目の開始・終了・現在hash一致 |
| 保存証拠 | verify_publication=[] |

参考対局は最初の実装後Full（1127成功）の後に開始した。対局実行中の終了前照合で全区分の深度0/3/4テストと旧構造図の履歴注記を追加し、全対局の保存後に最終Fullを実行した。対局中から最終Fullまで本番AI・ハーネスのbytesは変更していない。Fullは直接CPython3.13.5 / Windowsで実行した。全pytestの外部時間は463.17秒、独立smokeは8.84秒。品質原証拠は `evaluation_results\quality-c3247adcee58482ebee8200dd783a2b5/quality.json`、SHA-256は `be37dfbb8c6371263465e56abab0bf9e5fd20585c4534b59194f15269bf90b26`。

独立オラクルは固定表とrefereeの合法手から元の評価式をFractionで計算し、手番視点Negamaxを全列挙する。本番のroot固定視点Minimaxと符号処理を分けた。初期盤面は深度0で1node/1leaf、深度1で5/4、深度2で17/12に一致する。32件の保存診断で値・最善手集合・計数が一致した。新規テストの1件で、rootパス後の1着手を終局と誤認していた。独立refereeが両候補の継続を示したため、1placementでは通常評価、2placementでは終局評価となる正しい期待に訂正した。探索実装の値は独立オラクルと一致しており、期待値の緩和で不具合を隠したものではない。

32件の診断を生成した拡張前テストのbytesは `diagnostics-source/test_ai_phase3.py` に保全し、診断に記録したhashへ厳密照合した。探索本番のbytesは最終状態と同一である。診断・JUnit・訂正理由は `evaluation_results/phase3/run-01a13b34aab34ddb844ded6bd9bcdb40/` の `search-diagnostics.json`、`phase3-expanded.xml`、`related.xml`、`test-expectation-review.json` に記録した。related.xmlは初回失敗を含み、最終成功の代用にはしない。

## 4. 事前登録した参考評価

各相手に初期盤面2局smoke、その後development1000件から50ケースを非復元抽出して先後交換100局。selection seed=20261020、master seed=20261021、profile/policy=descriptive、startup/command/genmove上限=5/1/10秒。固定Bは依存とmanifestも明示登録し、Aはengineを明示した。held-outはbytes hash以外に使用していない。

| 対戦 | 正常完了 | A勝敗 | 勝ち点率 | A genmove件数 | 平均ms | 最大ms |
|---|---|---|---|---|---|---|
| after-vs-random-smoke | 2 | 2勝0敗0分 | 1.000 | 62 | 24.228 | 62.442 |
| after-vs-random-100 | 100 | 100勝0敗0分 | 1.000 | 2785 | 21.214 | 110.429 |
| after-vs-positional-smoke | 2 | 2勝0敗0分 | 1.000 | 63 | 20.757 | 99.852 |
| after-vs-positional-100 | 100 | 80勝20敗0分 | 0.800 | 2797 | 21.037 | 112.487 |

| 100局の相手 | Aの色 | 勝敗 | 勝ち点率 |
|---|---|---|---|
| after-vs-random-100 | B | 50勝0敗0分 | 1.000 |
| after-vs-random-100 | W | 50勝0敗0分 | 1.000 |
| after-vs-positional-100 | B | 40勝10敗0分 | 0.800 |
| after-vs-positional-100 | W | 40勝10敗0分 | 0.800 |

全204局が完走し、異常0。時間はpassや終局問い合わせも含む全genmoveの外部応答で、内部探索時間ではない。CPU負荷を専有・統制した速度比較ではなく、応答時間の因果的な改善は主張しない。測定した環境・入力の範囲で期限違反はない。固定深度を選択したことだけで未測定の全環境の上限を保証しない。

## 5. 変更前後の比較と採否

| 相手 | Phase 2 | Phase 3 | 対応する参考差 |
|---|---|---|---|
| random_v0 | 0.970 | 1.000 | +0.030 |
| positional_v1 | 0.390 | 0.800 | +0.410 |

両相手の公開比較はcomparable=true、condition_mismatches=[]。各50ケース・両leg・開始棋譜・role seedの一致を確認した。全4runは原証拠からfresh再解析でき、reference_available=true、request_success=true、descriptive_only、正式intervalなし。ハーネス・固定B・序盤集合と保護資産hashも一致した。

事前の参考昇格条件は、異常0に加え、両相手への得点率がPhase 2を上回り、positionalへの得点率が0.5を超えること。判定は「参考昇格条件を満たすが、正式採用は未達」。勝敗を見て重み・深度・序盤・判定基準を変更していない。今回の成績は選択済みの開発用50ケースに関する参考観測であり、有意差・一般的な強化成功・正式採用PASSを主張しない。内部予算・時計・trace4の正式資格は未達で、比較のdecision=undeterminedを保持する。

## 6. 完了条件と次工程

| 条件 | 判定 |
|---|---|
| G0 構成・設計 | PASS。先行設計、図、契約、深度、計数、固定比較条件を保存 |
| G1 正しさ | PASS。独立探索、旧評価、盤面・パス・終局、入出力・GUI接続の回帰に成功 |
| G2 品質 | PASS。Full必須工程、全pytest、独立smoke、strict、sourceと保存証拠が一致 |
| G3 対局健全性 | PASS。予定204局が正常完了、異常0、測定した全応答が期限内 |
| G4 比較可能性 | 参考比較としてPASS。固定条件・資産・対応ケース一致。同一workの正式資格は未達 |
| G5 証拠と判断 | PASS。原評価・runs・fresh解析・診断・hash・コマンド・採否を保存 |
| Phase 3固有条件 | PASS。参照値と最善手集合、深度ごとの回帰、固定深度の測定期限、参考成績を確認 |

Phase 4へ進行可能。次工程は同じ評価と深度でのAlpha-BetaとMinimaxの一致、計数の正式契約との対応、実AI trace4取得・保存・再解析である。本Goalでこれらを実装したとは扱わない。安定した固定positional_v1とrandom_v0は維持する。

## 7. テスト分類と残る制限

新規104ケースのJUnit時間合計は41.183秒、最大16.055秒。全件がFullに含まれた。Quickは274件、Standard_AIは296件を選択し、Phase 3新規ケースはどちらも0件。今回のprofile登録変更は行わず、独立探索一式をFullへ保持する。短時間profileだけでPhase 2/3の正しさを保証しない。詳細は `test-scope-review.json`。

実ブラウザでの手動GUI操作とMermaidの実レンダリングは未確認。接続回帰・静的図照合とは区別する。深度3〜4は固定局面の診断で、通常対局の安全性検証は深度2に限定する。時間中断や合法fallbackはPhase 5、正式資格はPhase 4以降に残る。

原証拠と補助driverはGit管理外の `evaluation_results/phase3/run-01a13b34aab34ddb844ded6bd9bcdb40/` にある。新規checkoutに証拠が存在するとは扱わず、別途保全する。commit・push・merge・tagは行っていない。

docs直下の変更5文書はyomiyasuのlint・diffと最終稿照合を実行した。技術用語と対象外の過去本文を保持した理由、検査記録は同証拠領域の `prose-review.json` に記録した。計画・報告・目次の更新後に文書リンクの専用回帰も1件成功した。最終的な変更範囲・保護hash・品質証拠の照合は `final-audit.json` を参照する。

## 8. 再現方法

リポジトリルートで、対応する直接CPythonを使う。

```powershell
python -B -X utf8 -m pytest -q tests/test_ai_phase3.py tests/test_ai_phase2.py tests/test_ai_engine.py tests/test_ai_compatibility.py tests/test_result.py tests/test_stdin_commands.py -p no:cacheprovider
python -B -X utf8 tools/quality.py
```

対局の実argv・実行時間・終了値は `evaluation-driver.json`、原証拠の再解析と対応差は `evaluation-verification.json` と各 `*-reanalysis.json` / `*-comparison.json` / `*-matched-differences.json` にある。補助driverは新規出力への排他保存を行う。再実験では新しいUUID領域と品質証拠を指定し、今回の出力を上書きしない。
