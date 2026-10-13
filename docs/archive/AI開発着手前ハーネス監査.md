# AI開発着手前ハーネス監査

> 履歴資料（2026-10-13整理）。本文の「現行」「未実装」、節番号・行番号、実行結果は記録当時のものです。今の作業指示には使わず、[操作手順](../../ハーネス.md)、[現行契約](../harness_contracts.md)、[現行構造](../harness_architecture.md)、[AI分離の設計案](../../リバーシAI分離設計.md)を優先してください。

監査日: 2026-10-11。実装・テスト・既存計画書は変更せず、読取り、既存テスト実行、隔離した診断、本文書の作成を行った。

## 1. 総合判定

**条件付きで続行可能。AI分離・探索・評価関数の開発を開始してよい。ただし、現時点の実AIの成績は参考評価に限定し、正式な優越性・採用根拠にしない。**

- 既存全体テストは **220成功・0失敗、62.71秒**。独立smokeは **1成功・0失敗、219除外、1.31秒**。仕様の固定期待値、独立プロセス通信、盤面同期、異常系、保存・再検証の主要経路が実行できた。
- 過去のQ1〜Q6の具体的な再現条件に対する修正と回帰は存在し、今回も成功した。ただし、追加診断では**通信列全体の時系列検査の欠落**と**旧schemaの入力検査の不足**が見つかった。「6件修正済み」と「全契約が完全」は別である。
- 模擬AIでschema4/trace3の取得・再解析・比較は成功した。一方、実AI `reversi.py` は予算trace未対応で、正常完走しても予算・内部探索時間・正式統計の保証は得られない。
- 有限母集合区間の式は原論文と対応する。しかし全ケースの固定得点、結果を見ない抽出、候補の事前凍結、held-outの未使用性は自動保証されない。

**正式統計の利用可否:** 正常な模擬AI資料に対する条件付き数値計算は確認したが、保存時刻の矛盾を正式入口が受理する問題が残るため、現在の正式資格表示を無条件に信用して実AIの優劣を主張することは不可。これは「勝敗記録が全部誤っている」という判定ではない。開発用の正常な実行・参考比較は利用できる。

「70%以上正しい確率」は推定できない。テスト成功数をその確率へ換算しない。開始判断は必須条件と用途制限に基づく。詳細は第8〜10節。

## 2. 監査対象・版・保護

### 2.1 対象snapshot

対象はbranch `main`、HEAD `1b934293ec66f1174f7f6dfbc47de5bbf66f34d1` に**既存の未コミット変更を加えた作業ツリー**。HEADだけでは現在のハーネスを再現できない。

開始時の変更済みtrackedファイルは `AGENTS.md`、`harness/evaluate.py`、`harness/protocol.py`、`tests/test_harness.py`、`ハーネス.md`、`docs/archive/ハーネス拡張計画.md`、`docs/reports/ハーネス検証記録.md`。未追跡は `docs/`、`harness/analysis.py`、`contracts.py`、`openings.py`、`search_budget.py`、`statistics.py`、`tests/fixtures/budget_player.py`、整合性修正計画・モデル、AI分離設計。今回の新規変更と取り違えない。

実環境は CPython 3.13.5 / Windows 11 (`Windows-11-10.0.26200-SP0`)、pytest 8.4.2。設定は `pytest.ini:1`、固定依存は `requirements-dev.txt:1`。新しいパッケージはインストールしていない。

| 項目 | 現行版・正本 |
|---|---|
| 評価・再解析・比較 | schema4 |
| 予算設定 / trace / 再検証 | config2 / trace3 / validation2 |
| 母集合・case・pair・seed | version1の意味を維持 |
| 時間契約 | `local_cpython313_perf_ns_enclosed_v1`。対応はlocal CPython3.13 / Windows / QueryPerformanceCounter |
| 整合性修正計画・モデル | revision3承認時点の履歴と、末尾の実装段階対応を分けて読む |
| アーキテクチャ・契約資料 | 現行正本は両資料の第9節。前節の旧版記述を現行保証にしない |

`AGENTS.md`、指定された設計・計画、harness全10モジュール、testsの3テストモジュールと2fixture、reversi本体・GUIを確認した。併せて課題仕様、実行手順、開発計画、2つのAI分離設計を照合した。モデル運用方針の製品情報は本監査の判定材料にしていない。

版の完全なSHA-256一覧とGit状態は次の監査専用ファイルに保存した。

`evaluation_results/pre-ai-audit-41699f6dd29549909d5fa5b2f10cc1c2/audit-manifest.json`

主要コードのSHA-256は以下。末尾の保全確認では開始時の全対象ファイルと照合する。

| ファイル | SHA-256 |
|---|---|
| harness/contracts.py | ac9da2beed7626906d2feba3ccd64634b0e651b3aad3d1160bb342a726daefa1 |
| harness/analysis.py | 5d91e6427711bbf5697bea6cfd9478cb3a19a82cc799852f3c94ffde61413473 |
| harness/evaluate.py | bc96732b7d41fbaaf2e8945d5f2a8b67d2407291c68edf4a0b5d80b16039d018 |
| harness/protocol.py | aadfe953616eef6f468bd7522633ce65c0af66c42d8565745d88859b2c2bd0eb |
| harness/search_budget.py | 9a23c4db6595e5582cd6e3e83ee5b625de4f490ef01d06666595688767c09c13 |
| harness/statistics.py | af82fb9ef56863eb3055cf307ecaa87ac724a724d3ff0aec19cee862ea90bb66 |
| tests/test_harness.py | 1b90f16a31efee85ae7dc4a8c8b167373e08db7f769f4816295f8bd311e93cc6 |
| reversi_player/reversi.py | 944865f7a8a4b52d8d72d482390a6d069ec347a12fc09d1376fff29849735443 |
| reversi_player/web.py | f563bfd39bdf612e0081d20b79c2d41f9aa8fccd99a360d0085f1d220df3f6e5 |

監査生成物は上記新規UUIDディレクトリへ分離し、排他的に新規保存した。既存評価JSONを上書きしていない。既存テストも固有run領域・pytest一時領域を使う。コミット、push、branch変更、未コミット変更の破棄は行っていない。

## 3. 設計モデルと実装の対応

「一致」は確認した範囲の一致であって完全証明ではない。「未実装」は提案・保証の未達で、通常動作の不具合とは限らない。「矛盾/不足」は実行またはコード照合で差が確認されたもの。

| 契約・モデル | 実装・根拠 | 分類・限界 |
|---|---|---|
| SourceArchive | `contracts.py:822`でdeepcopy、`:304`で原artifact bytesを呼出内cache、`:834`で読込先変更前のsource hashを保持 | 一致。専用クラスではなくdict/bytes。署名・書込み禁止の型ではない |
| Observations | `contracts.py:697`の通信再構成、`:987`/`:1068`の共通trace検証、`:1113`のfresh reducer | 一致。独立した公開DTOは未実装だが、軽量dictを使う合意と矛盾しない |
| Inspection | `inspect_evaluation` (`contracts.py:1162`) が毎回各軸・derived・cache照合を生成 | 主要経路一致。全体時系列と旧入力の不足は第5節 |
| Assessment | `analysis.py:56` / `:150` に単独・対応比較判定を集約、`:99`/`:137`/`:196`/`:256`/`:265`の正式報告入口はfresh検証 | 一致。ただし判定核関数自体の公開範囲が曖昧（F03） |
| player→game→report予算 | `contracts.py:252`/`:260`/`:271`、`:854`以降の保存状態照合、`evaluate.py:282`のA/B起動前slot | 一致。Bの許可済みexemptは局所状態。game/reportはexemptでない |
| verified/failed/unknown/exempt | 必要集合・既知違反・未対応証拠・B除外を別扱い。原unknown/failedは更正なしに昇格しない (`contracts.py:1081`) | 一致。内部計数や未測定をverifiedにしたことを意味しない |
| 型/版と検証不能 | `decode_input` (`contracts.py:141`) →unsupported/invalid、raw欠損→unknown、raw意味違反→failed | 新4の主要経路一致。旧1/2のnested型に未達（F02） |
| raw→測定→保存集計 | `search_budget.py:212`、`contracts.py:1090`/`:1119`/`:1172` | 一致。count/sum/max整数ns、秒は派生、空mean/maxはnull。異常局の部分測定は全体保証でない |
| 内部時間≤host応答 | `protocol.py:63`、`search_budget.py:14`、game/probeの実記録照合 | 1要求内は一致。要求間/起動前/quitの全体順序は不十分（F01） |
| 母集合・全数判定 | archive全履歴・N・重複・抽出manifest・予定pair/legを照合 (`openings.py:151`, `contracts.py:383`/`:894`) | 一致。報告Nだけで全数区間を作る旧問題は拒否 |
| 正式統計gate | `analysis.py:28`のAND条件、`:178`の両側資格と相互条件、数式はarithmetic_only | 主要経路一致。ただしF01の未検査証拠を通すので「矛盾なし」は保証できない |
| 評価条件と解析条件 | `evaluate.py:161`/`:529`、`analysis.py:201`、`contracts.py:1259` | 基本一致。confidenceは比較条件から独立。取得fingerprintにはcontracts等の再検証コードも混在し、保守的に比較を拒否し得る |
| 保存/CLI | 共通writer `contracts.py:26`、prepare以後の運用失敗1、preflight入力2、数値未達0 | 新4の回帰は一致。旧不正型は再解析0/比較1へ分岐する不足（F02） |
| 旧schema | 旧1/2はreference、旧3は端点を合成せず現在の正式区間不可 | 方針一致。旧参考値は現在rawから認証した結果ではない |
| 最終採用 | `evaluate.py:735`、`analysis.py:253`のdecisionはundetermined | 一致。自動採用、仕様テスト証跡との自動結合、held-out使用履歴の証明は未実装 |

### 3.1 既存図との照合

[アーキテクチャ第9節](ハーネス整合性修正モデル.md#snapshot-harness-architecture) の依存図をASTのimportから照合した。`analysis→contracts/statistics`、`contracts→openings/protocol/referee/search_budget`、`search_budget→protocol`、`openings→referee`で循環はなかった。evaluateのstatistics呼出は予備pair判定で、正式資格はanalysisを通る。subprocess/runpyの辺と通常importを混同しない。

同節データフローの「保存原資料→fresh検証→Assessment→数式→別報告」と状態図の「入力2 / 保存運用1 / 不適格保留 / 数値未達でも要求成功0」は主要実行と一致する。一方、CHECK箱が通信列全体の物理的時系列まで検証するように読むならF01が反例になる。変更が必要なのは**CHECKの保証範囲・通信契約表・受入試験**で、依存図を新しく作り直す必要はない。

[修正モデル第6節](ハーネス整合性修正モデル.md#6-formal-gateと公開入口の同値性) のfresh入力要求は正式報告wrapperに実装されているが、Assessment関数単体までsupported public入口と呼ぶならF03との境界説明が必要。図の点線「保存Inspectionから資格なし」をすべてのPython関数に拡張して読むことはできない。

資料の旧版スナップショットは先頭の案内で区別されている。しかし `harness_architecture.md:7`には第1〜4節を更新したという過去の案内も残る。現在は第9節優先と読み、旧行のA01等を未修正の現行不具合として数えない。AI分離設計は2026-10-10時点の未実装案でtrace2の記述を含むため、探索計測・モデルloadの実装時に現行trace3へ照合する必要がある。

## 4. 過去の6種類の整合性問題

IDは `docs/archive/ハーネス整合性修正計画.md:22` のQ1〜Q6。拡張計画第8節の「6修正」とは別の問題群である。

| ID | 現在の状況・今回の根拠 | 残る境界 |
|---|---|---|
| Q1: 予算階層/入口不一致 | 共通集約とsource照合あり。`test_consistency_q1_saved_budget_hierarchy_cannot_bypass_entries` (`tests/test_harness.py:84`) 成功。手動matrixでも階層矛盾は全正式入口拒否 | 判定核を任意dictで呼ぶ境界はF03 |
| Q2: 内部時間が外部を超過 | 実測端点包含あり。`:99`の9秒改変、`:201`の等号/逆順/型/clock/domain試験が成功 | **要求ごとの修正は有効だが、通信列全体の時系列にF01が残る** |
| Q3: 集計とraw不一致 | `:117`/`:294`のalias/cacheと5ns/15ns手計算回帰成功。負の保存平均を手動matrixでも拒否 | 実AIの内部計数の真実性や計測scope統一は別 |
| Q4: 0起動がexempt | `:134`/`:494`成功。必要A/B固定、未起動budget unknown、protocol failed | Windowsの全故障/子孫停止を普遍保証した試験ではない |
| Q5: 不正/未知入力をCI未要求と誤認 | `:152`/`:316`/`:456`成功。schema99は全正式入口不可、新4の不正型は構造化拒否 | **旧schema1/2の型検査はF02が残る** |
| Q6: 保存失敗の分類 | `:166`/`:402`/`:439`/`:519`/`:563`/`:610`成功。保存開始後1、race保護、二次失敗と回収 | partial書込の完全な原子公開、停電、hard killは保証対象外 |

220件の成功で各既知修正の回帰は確認したが、本監査の追加診断を既存pytestに追加していない。したがってテスト成功と新しい反例の存在は両立する。

## 5. 現在の問題・保証境界（重要度順）

P1は正式な優劣主張前の必須対応、P2は拡張時の事故を防ぐ境界整理、P3は運用・保守改善。これは故障発生確率ではない。

### F01 / P1: 保存通信の全体時系列が正式検証を通過する

**診断確認済みの不具合。** `contracts._validate_player_transcript` (`contracts.py:613`) は要求の順番、各要求内の端点、startup単体を確認するが、各要求の端点が起動以後にあり、前の応答確認後に次の要求が始まることを照合しない。`validate_command_record` (`protocol.py:63`) も1要求内だけを検査する。

正常な新4報告を複製し、最初のshowboardの4端点だけを同じ差分で平行移動した。duration/秒/応答/ID/clock/rawは変えず、終了がspawnより100ns前になるようにした。

- spawn: `790033744456100`、showboard終了: `790033744456000`。
- inspectはverified、eligibilityはtrue、estimate/paired/reanalysisはavailable、compare/builderも正式比較可。
- 再解析CLIも終了0、verification verifiedだった。

さらに「直前showboardの終了がgenmove開始より後」と「quit終了がspawnより前」の2条件でもverified/availableになった。証跡は `diagnostic-host-before-spawn.json`、`diagnostic-genmove-order.json`、`diagnostic-quit-order.json`、診断結果は `diagnostics.json` と `additional-diagnostics.json`。

これは全ログを整合した嘘へ偽造する認証問題ではない。**単一の保存要求の時刻を壊しても既存の他要求/起動時刻との矛盾が残ったまま通る**反例である。今回の勝敗・各durationは変えていないので、通常の勝敗記録の誤りを実証したものではないが、正式な保存時間証拠の完全性は損なわれる。

推奨修正: game/probe共通の通信validatorで `spawn ≤ 各exchange_start`、`前validation_end（または終了点）≤ 次exchange_start`、quitが最後で正常要求の後にあることを検証する。対局同期の因果順も期待通信列から照合する。異なるPlayerのdomain nonceを同一にする必要はない。同一対応clockの時系列という既存前提を維持する。新しい巨大なログモジュールは不要。

追加すべき回帰: 起動前要求、隣接要求の重なり/逆順、quit逆行、probe同様の反例、等号境界、別run間の時刻を比較しない正常例。元duration/aliasを正しく保ち、この理由だけで全正式入口とCLIが拒否することを確認する。今回修正・テスト追加はしていない。

<a id="legacy-nested-input-review"></a>

### F02 / P2: 旧schemaの不正nested型で入口・終了分類が違う

**診断確認済みの入力処理不足。** 次の有限JSONを旧schema2入力として渡した。

```json
{"schema_version":2,"summary":{"failed":0},"conditions":{"budget_configs":[]}}
```

`decode_input` (`contracts.py:208`) のbudget_config検査はversion≥3に限定される。旧分岐 (`contracts.py:837`) と参考可否 (`analysis.py:74`) はraw検証なしで扱うが、`comparison_conditions` (`contracts.py:1265`) はbudget_configsをdictと仮定する。

inspect unknown、estimate/reanalysis descriptive_onlyに対し、paired/compare/builderは `AttributeError: 'list' object has no attribute 'get'`。再解析CLIは0、比較CLIは1だった。正しい旧データに正式資格を与えた反例ではないが、不正型をpreflight入力2として統一できていない。`diagnostic-legacy.json` と `diagnostics.json` に保存した。

推奨修正: 旧1/2でも**存在する比較フィールドの型**を検査し、不正はinvalidにする。欠けた新4証拠を旧資料へ要求するのとは別。正常な旧参考入力は保持し、両API/CLIで不正型を構造化拒否する回帰を追加する。開発中は新4生成物を使い、旧未検査資料を正しい比較根拠として利用しない。

### F03 / P2: Assessment関数の信頼境界が型/APIで固定されていない

**診断確認済みの境界曖昧さ。** `assess_evaluation` (`analysis.py:56`) / `assess_comparison` (`:150`) はfresh Inspectionを渡すという前提の判定核だが、非private関数で通常のdictを受け取る。正常Inspectionからsnapshot・source hash・validator fingerprint等を除いても直接assessはeligibleを返した。同じdictをestimateに渡すとunavailableとなり、正式統計wrapperの迂回にはならなかった。

従って「正式報告wrapperは保存Inspectionを受理しない」は確認できるが、「全公開Python関数が原資料から再検証する」は言い過ぎである。任意callerがAssessmentのeligibleだけを表示すれば未確認の判定表示を作れる。改造private関数による攻撃とは区別する。

推奨: この2関数を呼出内専用の判定核として明示し、正式supported public APIをraw報告を受けるwrapper群に限定するか、public Assessment入口もraw入力へ接続する。証明書クラスの新設より境界の明確化を先にする。保存済みdictの直接assessと正式報告発行を別々に試験する。

### F04 / P1（正式実AI評価前）: 実AIは予算trace未対応

コード確認と実対局で確認した**未実装**。`reversi.py:233`は合法手のshuffle選択で、config受理・実効予算・trace3・node/depth計数はない。`tests/fixtures/budget_player.py:16`は模擬値を記録し、実探索の予算遵守の証明ではない。

現状の実AIで保証できるのは、今回実行した局の合法性、同期、終局、勝敗、外部期限・応答形式など。内部探索時間、同じ実効探索予算、全ケース固定得点の実AI証拠、正式固定母集合区間は保証不能。Bにhash付きnone除外を設定しても正式probeのtraceは免除されず、現ランダムBをそのまま正式baselineにできない。

探索開発自体は参考モードで開始できる。正式測定へ移る前に本体I/OとAI境界のconfig/trace3接続、各phase・起動・load計測、counter定義、未対応要求の明示失敗を実AIで検証する。測定値を架空の値で埋めてverifiedにしない。

### F05 / P1（分離版の比較開始前）: AI依存・baseline固定は明示登録に依存

`evaluate._file_info` (`evaluate.py:19`) とtargets構築 (`:517`) は入口scriptと `--a-asset` / `--b-asset` の明示資産をhash化する。AIの全import・pip依存・暗黙設定を自動収集しない。seed adapterは `_harness_info` (`:100`) に登録済み。

AI分離後、入口reversi.pyが同じでも未登録ai_engine.pyを変更すると本体hashだけでは差を表せない。baselineが候補の共通engineをimportするなら「固定B」の意味も崩れる。これは将来の条件付きリスクで、現単一ファイルAIに未登録engineが存在すると確認したわけではない。監査時に `baselines/` ディレクトリはなかった。

推奨: 分離と同じ作業でengine・設定・モデルを資産登録し、baselineは依存を含め独立した凍結snapshotにする。初版は明示manifestで十分。engineのみ/B依存のみの変更を比較が検出する試験を用意する。SHAだけでは元ソースを復元できないため、コード・設定・資産そのものも保存する。

### F06 / P1（正式主張前）: 固定得点・抽出独立性・最終評価の運用証跡不足

`_verify_determinism` (`evaluate.py:177`) は先頭選択caseの単発genmoveをA/B各2回確認するだけ。決定性宣言と有限probeが全対局・全seed・cache・環境に対する数学的証明にならないことは計画にも明記されている。必要になった段階で選択ケース/両色/終盤/cacheに拡げ、実装上の決定性を監査する。

`purpose=held_out` と生成時のorbit除外はあるが、学習への非混入、候補/抽出seedの事前固定、評価を見た後の調整を自動証明しない。正式使用には運用記録を併用する。この監査で作ったheld_out N=4は**試験素材**であり、今後の採用判断に使う未閲覧の最終集合とは扱わない。

### F07 / P2〜P3: その他の既知限界

- CPU/GPU負荷、クロック・電源制御、ライブラリ版、推論thread設定は網羅固定されない (`evaluate.py:80`)。同じruntime JSONでも同じ応答時間を保証しない。今回同じfixtureのA平均外部時間は0.227msと0.620msで変動した。改善・退行の証拠ではない。
- 取得fingerprintはソースbytesなのでコメント変更でも変化する。contracts内の検証変更も取得版へ影響し、新旧をそのまま対応比較できなくなることがある。比較を通すためhashを削除せず、両側再取得または旧資料双方の明示再解析を選ぶ。
- 正常通信ログはメモリ上のcommandsを保存し、stderrは末尾100行。局中の全raw stdout/stderr、hard kill、停電時の完全な追跡を保証しない (`protocol.py:235`, `evaluate.py:120`)。
- 共通writerは排他新規・flush/fsyncするが、最終JSONの完全な原子的公開は保証しない (`contracts.py:26`)。部分ファイルは成功扱いせず、別名で再実行する。
- Windowsのcheckpoint置換AccessDeniedは以前の検証記録にあるが、今回は再現しなかった。原因を確定した環境問題とはしない。
- GUIのProcessには通常応答timeoutがない (`web.py:16`)。試験用BoundedWebProcessの期限をGUI本番の機能と誤認しない。HTTP・ブラウザの全面確認は今回行っていない。

## 6. 今後のAI開発との衝突と対応時期

以下は原則として**予測/提案**。現状の不具合と区別し、全部を今修正する理由にはしない。

| 条件・論点 | 影響・重大度 | 対応時期・最小対応 |
|---|---|---|
| AI分離で実cell/Posを選択器へ渡す | 探索中に実盤面更新、genmove二重適用、ルールと選択の混在 / P1 | 分離時。immutable rows/合法手→座標、最終適用は本体1回。不正戻り値で盤面不変を試験 |
| passを終局と同一視、白視点の符号混同 | 不合法NG、誤った探索価値 / P1 | 探索着手時。両色・片色pass・両色終局・完全に解ける小局面で確認 |
| import時load/乱数消費、別cwd/package起動 | seed再現性・GUI起動が変わる / P1 | 分離時。直接script、seed adapter、package import、別cwdを試験。AIから本体へ逆importしない |
| 追加engine/modelをasset未登録 | 前後差・固定Bが追跡不能 / P1 | 分離版比較前。F05の登録・独立baseline |
| depth/nodeの意味がAI間で違う | 「equal-work」を同じ実仕事量と誤読 / P1 | 初めて探索を比較する前。node計数・depth/pass/quiescenceの意味を固定。現契約は同じ上限で、消費量の等しさは要求しない |
| 時間打切り、OS/GPU非決定性 | 同じseedでも棋譜が変わり固定得点CI不適合 / P1 | 時間版はdeployment/参考に限定。相互棋譜完全一致を要求しない。反復分布の新統計は別設計 |
| `search_only`の範囲が実AIとfixtureで違う | 内部平均の比較が不公平 / P2 | trace実装前。fixtureは合法手列挙・選択・実盤面適用を含む。分離案の選択完了までのscopeを同名だけで同等扱いしない。scope定義/版と比較試験を固定 |
| モデルを初回genmoveでlazy load | 現trace3のheader startup内load検証と衝突 / P2 | モデル導入前。下記6.1で契約を決め、未知をverifiedへ緩和しない |
| numpy/torch等の乱数・独立Generator | Python randomとPYTHONHASHSEEDだけでは制御不可 / P1 | その乱数源を導入する時。seed伝達範囲と決定性条件を明示 |
| GPU推論を非同期計測、別host/clock | enqueueだけを思考時間とし誤った包含/公平性 / P1 | 導入時。同期完了までの測定、device/library記録、新しいclock capability検証。現未対応は正式保留 |
| cache/モデルを対局間で共有 | ケース得点が順序依存、序盤再生とGUI cold起動の違い / P1 | cache導入時。現1leg1processを維持し寿命を固定、cold/warmを別条件化 |
| 学習集合に評価局面/対称局面が混入 | 見かけの改善、未知局面へ一般化不能 / P1 | 学習データを使う前。対局単位分割、対称/履歴リーク監査、モデルhash・訓練出典・集合利用履歴 |
| 同じ最終集合で改良を繰り返す | 選択バイアス、多重主張、任意停止 / P1 | 正式採用前。developmentで反復、候補と主要指標を凍結後に別の最終試験。必要なら事前の多重性計画 |
| 新統計・解析のみの変更 | 過去のavailableを現在保証として再利用 / P2 | 新手法追加時。method/estimand/spec/lineageを追加し、両側fresh検証と既存gateに接続 |
| 全資料/契約型の大規模分割 | 依存・版・更新対象増加 / P3 | 今は不要。既存モジュールに契約・小さな回帰を追加する方がゼミ開発に適する |

### 6.1 lazy model loadの具体的な未解決接続

`リバーシAI分離設計.md` は初回有効genmoveでのloadとheader確定を提案する。一方、現行 `search_budget.py:286`以降はheaderのloadを最初nameのspawn→validation区間に照合し、model_idあり/not_requiredはunknownにする。commandイベント側のload区間検査が通ってもheader側のunknownは残る。

したがって「command loadのhelperがある」だけでlazy初期化対応済みとは言えない。候補は、(a) 対応済みstartup内で初期loadしてname期限を測る、(b) 明示的なdeferred状態と実際の初回commandへの結合を設計しheader/event契約を更新する、の2つ。モデル導入前に選ぶ。今のランダム分離/探索開始を妨げない。今回は実モデル・GPU・lazy loadを実行していない。

## 7. 評価の正確性・公平性・統計的妥当性

### A. 対局結果の正確性

`play_game` (`evaluate.py:251`) はgenmove側を再playせず相手だけへplayし、毎手/pass後に独立refereeと両盤面/resultを照合する。128turn上限、合法pass、両色terminal probe、quit/exitも確認する。保存後は棋譜、役割、手番、両commands、rawのbefore盤面hash/phase/応答/要求IDを再検証する。

独立期待値は `tests/test_stdin_commands.py:14`/`:38`/`:77`、`tests/test_result.py:191`/`:210`/`:254` にある。8方向・初期両色64マス・不正着手/ask不変・黒白勝ち/引分を、本体出力の丸写しではなく固定盤面・手計算から確認する。長い棋譜の中間期待値には独立refereeを使うため、全ての局面が手計算で検証されているわけではない。

欠損・余分raw、hashを更新した意味改変も既存回帰 (`tests/test_harness.py:675`/`:706`/`:730`) が拒否する。N/rawどちらかだけ成功しても正式へ進まない。F01の時系列例外は残る。正常局の勝敗再計算の信頼性と、完全な保存証跡資格を分けて扱う。

### B. 公平性・再現性

両legは同一caseと役割seedで先後交換する。`case_id`は序盤履歴/盤面/手番、`pair_id`はcaseとA/B seed、run/leg/clockは別概念。genmove自身更新という仕様は維持される。固定序盤はD4対称重複を除き、標本は母集合内で非復元抽出する。黒白の局数は均衡するが、得点や局面分布が均衡する保証ではない。

比較は固定Bコード/明示資産、case/seed/母集合、予算policy/上限、runtime、取得fingerprintを照合する (`contracts.py:1259`)。A.searchの実装差は許容するが決定性/counting定義は残す。基準AIの固定は明示資産と保存運用の範囲に限る（F05）。同じseedで同じ標本を繰り返しても独立した標本数を増やしたことにならない。

equal-timeは同じ時間上限、equal-workは同じkind/上限（nodesではcounting定義）を要求する。深さ、ノード数、秒は相互換算できない。同上限は同じ計算消費量・CPU命令数の証明でない。baseline-asymmetricは「同予算」ではなく、許可された非対称条件での比較と説明する。

### C. 統計の対象と式

推定対象は**凍結した有限序盤母集合Nケースについて、固定したAI/設定/役割seedで先後交換したA平均得点**。1局のA勝1・引分0.5・負0の2leg平均をcaseの値 `s_i∈[0,1]` とする。変更前後は同じcaseで `d_i=s_after_i-s_before_i∈[-1,1]`。2n局を独立2n観測とせずnケースで推定し、別々のCIの端を引かない。

`statistics.confidence_interval` (`statistics.py:6`) のrho分岐と半幅は、Bardenet–Maillard (2015), Corollary 2.5の片側boundを両側へ適用した式に対応する。固定値の一様非復元抽出であり、標本間の非独立性は有限母集合bound側で扱われる。これは本プロジェクトのAI・traceが正しいことの証明ではない。[原論文](https://arxiv.org/pdf/1309.4029)

```text
alpha = 1 - confidence
rho = 1 - (n - 1)/N                 (n <= N/2)
rho = (1 - n/N) * (1 + 1/n)         (n > N/2)
h = (upper - lower) * sqrt(rho * log(2/alpha)/(2*n))
```

実装は得点区間を[0,1]、差分を[-1,1]へ切り詰める。非全数の全同点標本でも幅0にしない。n=Nは全archive case/両leg/必要検証が揃った固定得点の全数平均だけで幅0になる。未知の相手・全合法局面・確率的再実行への誤差が0とは言わない。

今回、独立に作ったN=8の固定値 `[0,0,0,0,1,1,1,1]` の全標本をn=1..8、confidence=0.70/0.90/0.95で列挙した。**765標本の算術確認**でrho/半幅を別計算と照合し、24組全てで真の平均0.5の被覆割合は指定水準以上だった。n=N/2、n>N/2、全数を含む。ただしこの一母集合の全列挙は、任意N/任意得点/実AIでの被覆率の証明ではない。根拠は式の原典との対応と適用前提である。

小数局の区間は非常に広い。今回N=4/n=1のfixtureは90%区間[0,1]、同fixture2回の改善差は95%区間[-1,1]。数値出力がavailableでも採用の証拠にはならない。失敗ペアを除いてnを縮める処理は正式入口では許可されない。

区間に必要な運用前提は、母集合/得点を抽出前に固定、標本と候補/抽出seedを結果を見て選ばない、予定n固定、全必要観測完備、計数/実行/モデルの固定である。生成の合法手歩行は全可能序盤への一様分布ではない。held-out名やD4除外だけでは訓練非混入・未使用性にならない。

同じ標本の結果を見て改良したAIを、その標本で再び通常の区間評価にかけると、候補選択と標本が依存する。現在のハーネスに選択バイアス・任意停止の自動補正はない。反復開発はdevelopment集合に限定し、最終候補と試験を事前固定する。[Dwork et al., Generalization in Adaptive Data Analysis and Holdout Reuse](https://arxiv.org/html/1506.02629)

### D. 正式資格と参考統計

母集合alias不一致、予算階層矛盾、負の保存平均、schema99、母集合欠損の手動matrixでは、eligibility/estimate/paired/compare/builder/reanalysisの正式可否が一致した。pairedが単独資格に相互条件を加えるのは意図された差で、入口不一致ではない。

低水準statisticsのcomputed/arithmetic_onlyと、analysis外側のavailable/validated_formalは意味が違う。区間内のarithmetic_onlyを外側の正式資格と矛盾と断定しない。`comparable=true`は参考比較の要求成功の場合もあり、`pair_statistics_available`とは別。今回実AIの参考比較0、strict比較1でこの分離を確認した。

ただしF01の不正時刻は全正式入口が**一致して誤受理**した。入口の同値性だけでは検証の正しさを保証しない。F03の判定核単体も正式報告wrapperと区別する。保存CI・positive status・数式のeligible=Trueを現在の認証として使用しない。

### E. 実AIの実証範囲

全体テストと独立smokeで実AIの仕様・GUI接続・同seed棋譜再現を確認した。今回の別CLI実AI2局も完走し、A黒白1局ずつ、Aは0勝2敗0分、エラー0、外部genmove平均約0.534ms/最大1.257msだった。A/Bは同じランダムAIでも役割seedが異なる。2局の偏りからBが強いとは結論しない。

内部探索平均/最大はnull、budget unknown、statistics descriptive_only、metrics uncertified。search_sum_ns=0は観測0件の和で、実AIの内部探索が0秒だったという測定ではない。モデル学習、新探索、異なる実AI版の優越性、実AItrace、GPUは今回実行していない。

## 8. 実際の実行結果と証跡

全コマンドはプロジェクトrootで実行。Python診断はPowerShellのhere-stringを `python -B -` へ渡し、既存APIを読み取り/新規監査領域だけに利用した。Python/テストコードのファイルは追加していない。

| コマンド・診断 | 結果 | 時間/証跡 |
|---|---|---|
| `git status --short`, `git rev-parse HEAD`, `git branch --show-current`, `git diff --stat` | 第2節の既存作業ツリーを確認。履歴操作なし | 開始/終了時照合 |
| `python -B -m pytest -q -p no:cacheprovider --tb=short` | 220成功・0失敗 | pytest表示62.71秒 |
| `python -B -m pytest -q -p no:cacheprovider -m smoke --tb=short` | 1成功・0失敗・219除外 | pytest表示1.31秒。全体内の1件を再実行したもの |
| fixture-before取得（下記） | 2完走・0失敗、A1勝1敗0分、各色1、budget verified、正式sample区間available | CLI壁時計2.080秒 |
| fixture-after取得（同条件/同AI） | 同上。強化前後のAIではない | 4.626秒 |
| 実AI取得（下記） | 2完走・0失敗、予算unknown、参考のみ | 3.461秒 |
| `python -B -m harness.analysis --report $A/fixture-before.json --confidence 0.95 --output $A/reanalysis.json` | 0、available、verification verified。元90%取得を共通95%で再解析 | 1.148秒 |
| `python -B -m harness.evaluate --compare $A/fixture-before.json $A/fixture-after.json --confidence 0.95 --output $A/comparison.json` | 0、差0、区間[-1,1]、criterion not_met、decision undetermined | 1.531秒 |
| 実AI同一報告compare（追加flagなし / strictあり） | 0/comparable true/正式なし、1/comparable false/正式なし | 1.217秒 / 1.084秒 |
| 不正時刻の再解析CLI | **0、available、verified（誤受理）** | 0.835秒 |
| 不正旧2の再解析 / 比較CLI | **0/descriptive_only と 1/AttributeError** | 0.529秒 / 0.340秒 |
| 正常+階層/N/metric/schema/母集合欠損の入口matrix | 正常だけ正式可、各破損/欠損は全正式wrapper拒否 | `diagnostics.json` |
| 起動前showboard、要求重なり、起動前quit | **3条件とも正式誤受理** | `diagnostics.json`, `additional-diagnostics.json` |
| 保存Inspection相当dictのdirect assess | eligibleだがestimateではunavailable | `diagnostics.json`。正式CI迂回成功ではない |
| N=8固定値の全標本列挙、3信頼水準 | 765標本の式照合・24条件の被覆確認成功 | `diagnostics.json` |
| harness AST importグラフ | 循環なし | 第3.1節の方向と一致 |

実行例の `$A` は今回の新規領域。再実行は**別の未使用ディレクトリ**を使う。下記同名出力を再利用すると既存保護のため入力拒否となる。

```powershell
$A = 'evaluation_results/pre-ai-audit-41699f6dd29549909d5fa5b2f10cc1c2'
python -B -m harness.evaluate --a tests/fixtures/budget_player.py --b tests/fixtures/budget_player.py --games 2 --seed 73 --openings $A/population.json --selection-seed 9 --a-config $A/config.json --b-config $A/config.json --budget-policy equal-work --evaluation-profile confirmatory --confidence 0.90 --output $A/fixture-before.json
python -B -m harness.evaluate --a tests/fixtures/budget_player.py --b tests/fixtures/budget_player.py --games 2 --seed 73 --openings $A/population.json --selection-seed 9 --a-config $A/config.json --b-config $A/config.json --budget-policy equal-work --evaluation-profile confirmatory --confidence 0.90 --output $A/fixture-after.json
python -B -m harness.evaluate --a reversi_player/reversi.py --b reversi_player/reversi.py --games 2 --seed 31 --output $A/real.json
python -B -m harness.evaluate --compare $A/real.json $A/real.json --output $A/real-reference.json
python -B -m harness.evaluate --compare $A/real.json $A/real.json --require-budget-verification --output $A/real-strict.json
python -B -m harness.analysis --report $A/diagnostic-host-before-spawn.json --output $A/host-before-spawn-analysis.json
python -B -m harness.analysis --report $A/diagnostic-legacy.json --output $A/legacy-analysis.json
python -B -m harness.evaluate --compare $A/diagnostic-legacy.json $A/diagnostic-legacy.json --output $A/legacy-comparison.json
```

母集合は `openings.generate_population(4,8,913,'held_out')` で新規生成した。configはnodes上限100、deterministic true、counting_definition_id fixture-node-v1。fixture Aの全genmove55件はpass/terminal probeを含み、normalは52件。内部平均/最大はbefore約0.071/0.143ms、after約0.193/0.426ms。これらは模擬計測であり、AI強化の試験結果ではない。

`command-log.json` は5正常CLIの完全なargv/終了値/stdout/stderr/壁時計、`run-summary.json` は各報告/inspectionの抜粋、`diagnostics.json` と `additional-diagnostics.json` は手動反例・式検算。既存pytestの成功数にこれら診断を加算しない。異常系の意図された拒否/終了1は正常動作の確認と区別し、新規反例は未修正として記録した。

## 9. 70%基準と大数の法則

| 概念 | 現時点で言えること |
|---|---|
| 1. ハーネスが正しい結果を出す確率 | 将来入力の分布、故障モデル、正解の定義がなく推定不能。70%や99%を作らない |
| 2. 独立な正解付き検証ケースへの正答率 | 現220ケースは無作為抽出/独立標本ではない。parameter展開・共通fixture・同じ実装依存を含む。220/220はこのテスト群の成功割合だけ |
| 3. 機能/契約の達成率 | 対象集合と重みを事前固定すれば工程指標にはなるが、正しさの確率ではない。本監査は恣意的な重みで70%を達成させない |
| 4. AI区間/誤差 | 条件付きの母集合平均についての被覆保証。confidence=0.70に変更してもハーネスの信頼度70%にはならない |
| 5. 開発開始の実用判断 | 基本仕様/完走/参考集計が動き、未保証を区別できるかで判断。正式用途の必須条件は点数平均で相殺しない |

通常の大数の法則を使うには、固定した対象分布と、適切な独立性（またはそれに代わる条件）、有限期待値などが必要である。現在の固定母集合の非復元抽出はIIDではなく、有限Nの抽出誤差を第7節のboundで扱う。n=Nなら対象集合の固定平均は既知だが、その集合が実戦を代表するかは別問題。

同じ決定的AI・同じcase・同じseedの棋譜を100回繰り返しても情報は100の独立ケースにならない。非決定的時間AIの反復は別の推定対象・実行環境・乱数/cluster設計を必要とし、現confirmatory CIへ流用しない。

適切な新規標本の追加は標本変動を小さくできる。一方、ルールバグ、誤った勝敗、F01、予算偽申告、未登録依存、偏った母集合、データリーク、候補/seedの事後選択を対局数だけで解消できない。誤った値を大量に集めると狭いが誤った結論を出し得る。固定された一比較の95%区間を繰り返し候補選択後にも同時95%保証として使わない。

従って「70%以上だから進む」より、**開発に必要な条件は満たした／正式主張の必須条件はまだ満たさない**という二つの判断が適切である。

## 10. 移行判断と必須条件

### A. AI分離・探索/評価関数の開発

**条件付きで続行可能。** 仕様回帰と実対局完走が実行でき、変更による破壊を早く検知する仕組みがある。F01は保存時間証拠の資格不足で、正常な実行の盤面同期を壊した反例ではない。F02は旧不正入力で、新4参考評価を使う開発は制限可能。全問題の解消を待ってAI開発を停止する費用は、ゼミの現段階では割に合わない。

開始前にハーネス実装の一括修正は不要。ただし、分離版を比較する前に**依存込みbaselineの凍結・engine資産登録・仕様回帰**が必須。参考成績だけから正式優越/採用を主張しないこと、unknownを隠さないこと、既存の基準を緩めないことが条件となる。

### B. 正式なAI優越性・採用判断

**現時点では保留。** 以下は達成率ではなく必須条件で、一つの重大欠落も他項目の成功で相殺しない。

| 必須条件 | 今回の判定・移行条件 |
|---|---|
| 基本ルール・stdin/GUI互換の回帰 | 現版の自動試験は成功。新AI版で再実行し、対象コード/資産hashへ結ぶ。GUIブラウザ未確認は別記 |
| 保存資料の意味・完全性・時系列検証 | 主要検査成功だがF01未達。修正と全wrapper/CLI反例拒否が必要 |
| 実AIの要求/実効予算・trace3・内部時間 | F04未達。模擬値でなく実AIで取得/保存/再検証する |
| baseline・依存・モデル・実験条件の固定 | F05の明示運用が必要。新旧Aと固定Bの全依存を保存/登録する |
| 母集合/抽出/coverage | harness検査は成功。実験で事前固定した適切な集合を使用し、全予定pair/legと異常0を確認 |
| 固定得点/決定性の根拠 | 先頭probeだけ。対象AIの全使用乱数・cache/環境を監査し、対象ケース範囲で追加確認。時間モードには現固定得点CIを使わない |
| 独立した最終評価・多重性/停止規則 | 自動証明なし。候補と指標/水準/nを結果を見る前に凍結し、利用履歴を残す |
| 公開APIの資格境界 | wrapperは主要反例で一致。F03のAssessment単体を資格発行入口にしない境界を確定 |
| 数値基準・実運用時間 | 今回の差区間[-1,1]は根拠不足。実AIで事前基準を満たし、別deploymentの外部期限/初回loadも確認 |

F01修正後も、実AItrace・基準固定・統計前提が未達なら正式主張へは進まない。逆にF02の旧不正型や資料の履歴整理だけのために、用途を限定した新4のAI開発を止める必要はない。

## 11. 修正の時期と推奨ワークフロー

**開発開始前/分離と同時に必須:** 現ランダム版と依存の凍結、AI入力/戻り値・実盤面不変・一回適用の契約、新engineのasset登録、分離前後の同seed棋譜一致と既存全体テスト。これはAI分離の安全条件で、未実装機能を今回実装したという報告ではない。

**AI開発と並行:** F01の小さな通信時系列修正と回帰（正式昇格前には必須）、F02の旧入力ガード、F03の公開境界整理。探索の計数・scope・実AItraceは初めて予算付き比較する前に接続する。GUIの実ブラウザ確認、資料の履歴参照整理、保存障害の運用改善も並行可能。

**必要になってから:** GPU/外部clock対応、numpy/torchの決定性契約、モデルload方式、学習データリーク監査、時間AI向けの確率的反復統計、複数相手/候補の多重比較、停電耐久性。今回のランダム分離のために全機能を先行開発しない。

推奨する短い反復手順:

1. baseline・仕様・対象のコード/資産を凍結し、変更を一つ（分離、探索、評価関数など）に絞る。
2. 固定局面の単体/関連試験、stdin/GUI互換、全体pytest、先後smokeで正常性を確認する。不正手・timeoutは敗北へ変換せず別に記録する。
3. development固定序盤・固定B・同じcase/seed/上限で新旧を参考比較し、勝敗・両色・正常局分母・異常数・外部時間を読む。内部unknownと正式区間不可を明示する。
4. 予算trace/時間scopeを接続し、実AIでも取得→保存→fresh再解析→比較を確認する。F01と公開境界の回帰を先に閉じる。
5. 候補を選んだ後、未使用の最終集合・抽出手順/n・主要指標・水準を事前固定する。前後両版を最終結果閲覧前に凍結し、CI・deployment・仕様結果を併せて人が採用判断する。

本監査は修正を実施していない。成果物は本文書と隔離した実行証跡のみ。**次の中心作業はAI分離・探索開発でよく、ハーネスの残件は正式主張を行う前の小さな必須修正として扱う**。

最終保全確認: 開始時に記録したコード・テスト・既存Markdown・pytest設定/依存の計34ファイルを終了時のSHA-256と直接照合し、変更0件。HEADとbranchも不変だった。本文書はUTF-8で開け、参照先ファイルに欠落はなかった。既存設計図のMermaid実レンダリングは今回行っていない。
