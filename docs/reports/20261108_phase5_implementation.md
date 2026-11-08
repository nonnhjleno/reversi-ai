# Phase 5実装・検証・評価報告

ローカルの実装・品質・参考評価・search_v1固定・ZIP準備を完了した。正式計測と応答安全性は測定した条件に限って成立し、サーバー実機確認と一般的な棋力向上は未確認である。Phase 6の別途設計へ進行可能とする。Phase 6以降の実装、commit・push・merge・tag、外部アップロードは行っていない。

## 開始条件と保全

開始・終了HEADは `518cd2aa263fbed869ed17ea9da64e126d78c1a3`、作業ブランチは `feat/ai-phase5`。開始時clean、Phase 4はmain統合済み。終了時は今回の変更が未コミット。開始時の保護資産37件、ai_engine.py、GUI、ハーネス本体、旧baseline、developmentとheld-outは開始hashを維持した。held-outはチューニング・対局に使っていない。既存worktreeを変更していない。

初期資料の「Phase 4以降未着手」と実装済みdepth計測の不整合を、実装・原証拠に照合して訂正した。過去のFullを今回のPASSとして流用せず、開始時Full1391件と最終Fullを別に実行した。

## 設計・実装と変更範囲

先行した設計・図・契約を[Phase 5設計](../phase5_design.md)、[境界契約](../harness_contracts.md)、[責務・状態図](../harness_architecture.md)へ記録した。時間対応探索だけ最大depth4とし、固定depth API 0〜4・selector1〜4は維持した。反復深化は完全rootだけを採用し、未完了時は最後の完了手または決定的合法候補を返す。パスでplacement plyを減らさず、同点(y,x)、固定評価、実盤面一回適用を保全した。

新規timed_search.py、提出用response_config.json、tests/test_ai_phase5.pyを追加。本体mainは明示設定なしだけ時間対応を選び、Board()とunknown＋空searchは固定depth2を維持する。search_trace.pyは既存depthと新timeを区別し、実計数・時計・全command・lifecycleを取得時に保存する。schema5/trace4/validation3、正式資格AND、harnessソースは変更していない。TT・bitboard・学習・終盤solverは未実装。

pyproject.tomlはstrict対象を19本へ登録。tools/quality.pyはresponse_config.jsonのsource hash登録一行だけ追加し、検査・上限・必須条件は変えていない。新213件はFull専用で、Quick/Standard登録は不変。新しいsearch_v1だけ.gitattributesで-textを指定し、autocrlf true/false/inputのGit変換が同じbytes/OIDを保つことを別途検証した。

## 時間条件と実測を受けた改訂

提出既定は受付から全応答7.0秒目標、新反復6.0秒まで、停止要求6.5秒、後処理0.5秒予約。単一のperf_counter_ns期限を全反復で共有する。明示search_only timeは通常intで1,000,000〜6,500,000,000ns。未対応kind・未知キー・不正型・モデル・time決定性trueは拒否する。1msは入力境界であり、実時計の正式資格は未検証である。

初版の5%予約ではpositional100局中2件が100msを超えた（102,920,200ns / 101,228,100ns）。全100局完走・94勝6敗でも失敗扱いとし、CLI/fresh終了1と原traceをそのまま保存した。停止遅れ観測11.276msの2倍を5ms単位で切り上げた25msを根拠に、仕様・独立整数期待値とred試験を先行し、reserve=max(100000,min(25000000,limit_ns//4),limit_ns//20)へ変更した。100msでは停止要求75ms、新反復74ms。7/6/6.5/0.5秒と要求100ms上限、評価値オラクルは変えていない。

25ms予約分で再取得したpositional100局でも6着手が100msを超え、探索最大134.3252ms・停止確認遅れ最大59.2659msを観測した。この失敗もCLI/fresh終了1のまま保持した。第二次修正では観測遅れの2倍118.5318msを10ms単位で切り上げた120msと、短予算80%の上限、従来5%の大きい方を予約分にした。現行式はreserve=max(100000,min(120000000,limit_ns*4//5),limit_ns//20)。100msは20ms停止要求・19ms新反復限界となる。独立境界を先にred検証し、評価値オラクルは変更していない。最終の全品質・正式計測・678応答・参考408局はguard120領域で取り直した。旧失敗を削除・修復していない。第一次修正の負荷試験では停止確認遅れ最大14.3401msを観測した。予約分はOS停止の普遍的保証ではない。

## 品質・独立検証

直接CPython 3.13.5 / WindowsでFull全11工程成功。pytest1604件、失敗・error・skip 0、独立smoke1件、strict19本、lint・限定format・モデル照合・GUI/プロトコル回帰を含む。pytest外部時間は1676.013秒。47 sourceの開始・終了・最終照合が一致し、publication検証エラー0。外部原証拠14件は別途bytesコピー・hash照合した。

最終Full: `evaluation_results\quality-8490738854b140f684bddfd6d9d24e4b`。quality.json SHA-256: `d5b7c28224f17de6f630d84cea4653f1d520bc95416cdf4841150fd3ff49c727`。新規213件は独立Fractionオラクル、保全Minimax/Alpha-Beta、全時計点の偽時計中断、pass/終局、1合法手、root後半、入力/RNG/副作用、例外伝播、実プロセスtrace・不正設定を検査した。偽時計は実時間保証と区別する。

途中の失敗も保管した。最初の関連実行は監視600秒とdict記録誤りで失敗、最初の実装後Fullは既存1800秒で未完了だった。新規テストの純粋なオラクルと固定参照値のみをメモ化し、候補実行・201 node ID・期待値・既存上限は保全した。import欠落の収集失敗は補正後に再検証した。初期応答測定のdeque記録エラーも成功へ数えていない。

改訂前1592件Fullは全件成功した一方、pytest33049.244秒、既存fixture一件31748.79秒という経過時間異常があった。原因は未確定で、設定1800秒の実壁時計保証や高速化の実証には使わない。最終1604件Fullの実時間は今回のfull-verification.jsonに保存した。このGoalで共通プロセス管理を拡張していない。従前の外部Temp証拠9件欠損は未解決のまま、同じ復元調査は繰り返していない。

## 実時計・負荷測定

事前固定13局面（既知10＋development由来2＋単一合法手1）、通常と1論理CPU＋同CPU競合workerで678件。各負荷で0.1/0.5/1秒×13件、5/7秒候補各100通常着手、評価環境変数なしの提出既定100通常着手を取得した。全件正常・不正手/異常/timeoutなし、7/8/10秒超過0。平均・中央値・最大・p95・p99と全試行を保存。分位点はnearest-rank sorted[ceil(p*n)-1]。

| 条件 | 経路/目標 | 件数 | 平均秒 | 中央値秒 | p95秒 | p99秒 | 最大秒 |
|---|---|---:|---:|---:|---:|---:|---:|
| normal | traced/5s | 100 | 0.228351 | 0.121870 | 0.949572 | 0.962243 | 1.097804 |
| normal | traced/7s | 100 | 0.229695 | 0.124570 | 0.945031 | 1.031549 | 1.052052 |
| normal | submission-untraced/7s | 100 | 0.223650 | 0.120350 | 0.952966 | 0.966959 | 0.986794 |
| normal | short/7s | 39 | 0.120495 | 0.067604 | 0.733893 | 0.880805 | 0.880805 |
| single_cpu_with_competing_worker | traced/5s | 100 | 0.242256 | 0.127815 | 1.027001 | 1.043350 | 1.087112 |
| single_cpu_with_competing_worker | traced/7s | 100 | 0.243271 | 0.126794 | 1.018125 | 1.054685 | 1.061398 |
| single_cpu_with_competing_worker | submission-untraced/7s | 100 | 0.241939 | 0.125568 | 1.020478 | 1.071053 | 1.072877 |
| single_cpu_with_competing_worker | short/7s | 39 | 0.113957 | 0.061278 | 0.678004 | 0.880483 | 0.880483 |

全678件の最大は1.0978042秒。traceあり478件はbudget/lifecycle verified。通常・弱条件の短予算で中断を観測し、停止遅れ最大1.3583ms。5/7秒trace候補400件は全て最大depth4完了で、7秒近傍の自然中断は観測していない。提出既定200件の内部計数・深度は未取得（null）であり、traceありの値を移植しない。rawには全反復・中断分のnodes/leaves/passes/cutoffsと開始/完了深度がある。模擬負荷は提出サーバーの再現ではない。

## 固定相手との参考評価とPhase 4照合

固定random_v0/positional_v1の全依存とdevelopment母集合1000件を保全。100局は非復元50pair、selection_seed=20261020、master_seed=20261021、外部期限は起動5/通常1/genmove10秒。先後別・全棋譜・全command応答時間を保存し、8reportをfresh再解析した。

現行コードの最初のrandom100局は99局完了・1局中断だった。固定B=random_v0のplay W 7 2が通常コマンド1秒の期限を超え、Aの探索予算超過ではなかった。失敗reportとfresh終了1を保持し、同seed・同期限のB側72コマンドを単独再生して全既存応答と一致・問題のplayもOKを確認した。原因は未確定であり、OSやCPUが原因とは断定しない。実装・設定・相手・ケース・seed・期限を変えずreference-retry1へ408局全体を取り直した。99局と再試行1局の継ぎ足しではない。再取得408局とは別に、先行102局の101完了・1timeoutも報告へ保持する。全試行で異常0とは主張しない。

| 経路 | 相手 | 局数 | 勝/敗/分 | 黒の勝/敗/分 | 白の勝/敗/分 | A最大genmove秒 |
|---|---|---:|---|---|---|---:|
| 100ms iterative | random_v0 | 2 | 2/0/0 | 1/0/0 | 1/0/0 | 0.021034 |
| 100ms iterative | random_v0 | 100 | 100/0/0 | 50/0/0 | 50/0/0 | 0.095842 |
| 100ms iterative | positional_v1 | 2 | 1/1/0 | 0/1/0 | 1/0/0 | 0.021293 |
| 100ms iterative | positional_v1 | 100 | 76/22/2 | 40/9/1 | 36/13/1 | 0.024872 |
| 旧固定depth2 | random_v0 | 2 | 2/0/0 | 1/0/0 | 1/0/0 | 0.029384 |
| 旧固定depth2 | random_v0 | 100 | 100/0/0 | 50/0/0 | 50/0/0 | 0.040480 |
| 旧固定depth2 | positional_v1 | 2 | 2/0/0 | 1/0/0 | 1/0/0 | 0.028271 |
| 旧固定depth2 | positional_v1 | 100 | 80/20/0 | 40/10/0 | 40/10/0 | 0.038648 |

現行time参考204局のA探索計測はverified。旧経路204局はPhase 4原報告とcase・先後・seed・着手・全盤面・最終結果が一致し、相手資産hashも一致した。Phase 4の5reportもfresh再解析済み。相手Bは内部予算unknownのため、参考戦は正式な優越性や正式equal-work比較へ昇格させない。100ms timeとPhase 4 depth2、提出7秒の棋力を同一視しない。一般的棋力向上は未証明。

## 実AIの正式計測

depth2 Alpha-Beta対保全Minimaxの4局confirmatory/equal-workはavailable・eligible・metrics_certified。これは同値の対照でありperformance_criterion=not_met、優越性の採用PASSではない。time100msの実AI同士4局deployment/equal-timeはbudget/protocol/clock/lifecycle/assetsが認証されmetrics_certified。statisticsはdescriptive_only、formal CI/determinismは要求しない用途であり、固定得点CIの資格を主張しない。各AND条件と資産hash、原traceとfresh report bindingを保存した。

資格はWindows/QPC/直接CPython3.13.5と測定した設定・局面に限る。fixtureの実測代用、後処理によるtrace修復、validator緩和はしていない。

| 個別資格 | depth2 confirmatory | time100ms deployment |
|---|---|---|
| 探索予算 | verified | verified |
| 時計・区間 | verified | verified |
| 親lifecycle | verified | verified |
| 子lifecycle | verified | verified |
| 因果順序 | verified | verified |
| source整合 | verified | verified |
| 実資産hash | verified separately in stage5 audit | verified separately in stage5 audit |
| 母集合 | verified | verified |
| 抽出 | verified | verified |
| coverage | verified | verified |
| 棋譜 | verified | verified |
| プロトコル | verified | verified |
| 決定性 | verified | not_requested; time deterministic=false |
| モデル | none/not_required; fallback=false | none/not_required; fallback=false |
| 統計資格 | available | descriptive_only |
| 計測資格 | verified | verified |

再取得した参考time204局のAと正式time4局の両役について、実trace212ファイルのhashを照合した。genmove 5941件、探索最大22.022ms、停止確認遅れ最大1.9855ms、巻戻し最大0.4357ms。全探索100ms以内。開始/完了深度・停止理由・nodes/leaves/passes/cutoffsの分布と各原traceへの対応はreference-time-raw-summary.jsonに保存した。この集計は受入れたコホートの範囲で、先行失敗を含む全試行の最大値とは呼ばない。

## search_v1固定と提出ZIP

[独立固定版](../../baselines/search_v1/README.md)は5つの実行時ファイルとdepth2/time100ms設定を保持し、manifestに全資産hash・Git開始HEAD/dirty状態・実行環境・用途を記す。候補と同名の例外module/異なる設定をshadowとして置き、別cwd起動と全コマンドを検査した。固定depth2の先後交換smoke2局を2回実行し、時刻を除く棋譜・盤面・結果が一致。提出既定mainも先後交換2局完走した。以後の候補・提出予算変更で固定版を更新しない。

保存ZIPはルートにreversi.pyと全実行時依存を置くローカル検証構成。展開物だけを使い、harness/pytest/venv/候補側絶対パスに依存しない。別の空cwdで全7コマンド、通常・パス・終局を検査し、展開物による各ZIP先後交換2局も完走した。ZIP作成後の再生成・差し替えはしていない。

- `C:\School\2026\後期\00_授業資料\情報学演習Ｂ\evaluation_results\phase5\run-0f94162b39214839bcff342fe327a2f0\guard120\packages\ranger-phase5-7s.zip`
  SHA-256: `a9570f18e9b912694e9e1a385e60cc4442ef01d94cf73c1b859303eb1a012f1d`
- `C:\School\2026\後期\00_授業資料\情報学演習Ｂ\evaluation_results\phase5\run-0f94162b39214839bcff342fe327a2f0\guard120\packages\ranger-phase4-rollback.zip`
  SHA-256: `ad76a6aae9315f0b9759332c40987dbe18823afba98363c49db90b3d6c199da3`

復旧ZIPは開始HEADの正常Phase 4ソースを開始raw hashと一致させて作り、同じ独立検査を通した。サーバー上に旧ファイルが残ることへ依存しない。提出/復旧の操作と0.1秒調整記録は[実機試行手順](20261108_phase5_server_trials.md)。サーバーOS、Python、必要なZIP階層、起動名、時間ログ可否は未確認であり、ローカルZIP準備完了と実機確認済みを区別する。

## G0〜G5と固有条件

| Gate | 判定 | 範囲 |
|---|---|---|
| G0 | PASS | 実装前にAPI・時計・状態・計数・設定と図を確定。実測後の予約分改訂もコードより先に根拠を記録。 |
| G1 | PASS | 新規213件と保全したPhase 2〜4・プロトコル・GUI自動回帰。偽時計の中断証拠と実時計を区別。 |
| G2 | PASS | Full11工程・pytest1604・smoke1・strict19、47source一致、publication/fresh成功、外部14資産をコピー。 |
| G3 | PASS_current_cohort_only | A応答678件と再取得408局・隔離対局の範囲で合格。先行102局の固定B通常play timeout1件は原因未確定のまま保持し、全試行で異常0とは主張しない。実機保証なし。 |
| G4 | PASS_scoped | 固定相手・development・seed・外部期限を維持。旧経路204局の棋譜一致。100msとdepth2は異なるworkで、固定Bの内部予算はunknown。 |
| G5 | PASS | 旧失敗、fresh解析、hash、資産、ZIP、限界を保存。過去の外部Temp9件欠損は未解決。 |

| No. | Phase 5条件 | 判定 | 根拠・限界 |
|---:|---|---|---|
| 1 | 反復深化の正しさ | PASS | 全完了時は独立オラクル・保全参照と一致。実行した反復と中断分の計数を合算する。 |
| 2 | 協調的中断と完了結果の保護 | PASS | 専用中断と全時計点の偽時計試験。部分rootは採用しない。 |
| 3 | 深度未完了時の合法応答 | PASS | 深度1未完了も最初の(y,x)合法手、厳密値はNone。 |
| 4 | 実盤面の一度だけの確定 | PASS | selectorはsnapshotだけを探索、本体で検査して一度適用。 |
| 5 | 固定depthでのPhase 4との一致 | PASS | 参照bytes不変、独立値・同点手と旧経路204局の棋譜・盤面・結果一致。 |
| 6 | 実測した応答期限への適合 | PASS_測定範囲 | 678件の7/8/10秒超過0。実機や未測定負荷への保証はしない。 |
| 7 | time予算と実AI trace4の資格 | 対象限定PASS | Windows直接CPython3.13.5、100ms deployment4局をfresh認証。depth2 confirmatory4局はavailable。 |
| 8 | 参考棋力の結果と解釈 | 完了 | 固定2相手各2+100局。100msと提出7秒の棋力を同一視せず、正式優越性は未確認。 |
| 9 | search_v1の独立固定・再現性 | PASS | 全依存hash・shadow候補・別cwd・固定depthの再対局一致・Git改行変換保全。 |
| 10 | 提出ZIPの保存・復旧可能性 | ローカルPASS | 同じZIP bytesを展開、全コマンド・パス・終局・先後交換。正常版ZIPとhashを保持。 |
| 11 | 提出サーバー実機での未確認事項 | 未実施_予定どおり | OS、Python、ZIP階層、起動名、応答ログは未確認。外部提出なし。 |
| 12 | Phase 6へ進むための前提充足 | 進行可能_ローカル | 固定探索相手と学習前の比較基準を用意。Phase6実装・学習・Git統合は未実施。 |

判定：実装PASS、参考評価完了、正式計測は対象限定PASS、応答安全性は実測範囲PASS、search_v1固定完了、ローカルZIP準備完了、サーバー実機確認は未実施、Phase 6の別途設計へ進行可能。優越性・未知サーバーでの期限遵守は未確認のまま。実ブラウザ手動対局とMermaidの実描画は今回実施していない。

追加の監査で、source_report_sha256がcanonical JSONのhashであることを原ファイルbytesのhashと取り違え、初回監査が止まった。既存harness.contracts.digestと独立のcanonical計算で定義を確認し、両hashを区別して再監査した。原report・raw・schema・validator・期待着手は変更していない。

## 原証拠・監査・バックアップ

現行原証拠：`evaluation_results/phase5/run-0f94162b39214839bcff342fe327a2f0/guard120/`。Stage1〜旧失敗を含む親領域：`evaluation_results/phase5/run-0f94162b39214839bcff342fe327a2f0/`。代表入口はpreflight.json、full-verification.json、stage5-verification.json、reference-summary.json、response-measurements/summary.json、stage6-stage7-verification.json、package-final-verification.json。全raw trace・棋譜・実argv・設定・hash・OS/Python・各コマンド時刻は同領域のreport/runs/試行ファイルに残る。機械可読な管理対象サマリーは[20261108_phase5_implementation.json](20261108_phase5_implementation.json)。

Git管理外の親原証拠領域全体と、最終Fullのqualityディレクトリ全体を外部媒体へコピーする。開始時/旧失敗/最終の外部Temp依存14件は対応するartifact-backup.jsonと実bytesのbin群も保持する。これらは新規checkout/cloneだけでは復元されない。提出・復旧ZIP、package manifest、search_v1の全ファイルを同じバックアップに含める。

最終Git状態・変更一覧・各ファイルhash・保護hash・品質source一致・リンク存在・ZIP固定はguard120/final-audit.jsonに記録する。最終文書は実行ソースや検査対象境界を変えず、結果を追記した。品質対象47sourceに変更がないことを照合し、理由のないFull再実行はしない。
.gitattributesの新固定資産ルールは実行/品質選択を変えず、Git変換の全27条件を独立検査した。

変更したdocs直下7文書にはyomiyasuを適用した。境界2文書はFull前、結果を反映した5文書は最後の編集後にrun・候補照合・verifyを実行した。技術用語と必要な未達・未確認の否定表現を保持し、旧本文の範囲外候補は変更していない。CLI成功だけを意味保持の証明とは扱わない。
