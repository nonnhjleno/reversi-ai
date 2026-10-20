# 品質移行の停止事項

## 品質監査Aの確認事項（2026-10-20）

利用者がreversi_01.py・102400-games/data.pyを保管/参考用途と承認し、旧Phase 10案の用途未分類は解消した。initializer2件・feedbackを加えた17対象strict、必須scopeと補助profileを実装した。下記の未実装/13対象の記述は各日付時点の履歴であり、現行値ではない。

fixtureコピー拡張に伴う既存CLIテストのディレクトリ衝突は、期待値を変えずmkdirのexist_ok指定で修正し、関連149回帰と全829回帰で再確認した。現在の最終確認は品質図の静的検査→collect-only順序表記を実行順へ補正した資料版に対する再検証。コードの既知の停止事項はなく、A完了は最終証拠照合後に判断する。B/CやAI強化へは進まない。

追補・最終状態: 上記資料版の再検証を完了しA1〜A3はPASS。Full `quality-9285910144e444a4bff556b67e5571d1` は11工程/829ケース/独立smoke成功、同一sourceのQuick231/Standard253も成功し、保存証拠再検証にエラーなし。今回確認できた未解消の停止事項はない。これは全未知入力の保証や、過去の各Phaseを一律にPASSへ変える意味ではない。web.pyの意図的な非strict例外（Phase 3非PASS）は維持する。残る検証範囲と限界は[実装記録](reports/20261020_quality_feedback.md)を参照する。

## 現行方針（2026-10-16）

Phase 9は解消済み・PASS。strict移行Phase 3 / reversi_player/web.pyは、教員配布で変更/拡張を原則予定しないというユーザーの判断により、**意図的な対象外（方針上の例外・非PASS）**へ変更した。今後の実装blocker/再開待ちには数えない。web.pyの既存Lint・GUI関連テストと既存13ファイルのstrictは維持する。web.py以外の本番例外を無断で追加せず、将来web.pyを機能追加/大規模修正するときはstrict化を再検討する。

Phase 10は対象漏れ防止の設計検討のみで未実装・非PASS。最小案は既存品質ゲートへ必須typing_scope検査を1つ加えること。既存10必須は維持する。着手前の確認事項はinitializer2件の明示対象化（現行13＋2=15の案）とreversi_01.py/102400-games/data.pyの用途分類であり、今回これらを例外に追加しない。設計・予定回帰・承認/完了条件はQUALITY_PROGRESS.mdの「現行strict方針」「Phase 10 最小変更の設計案」を参照する。

以下の「Phase 3保留」「次フェーズへ進まない」等は各実行時点の履歴で、現行方針に読み替えない。今回も文書の記録・設計だけを行い、コード/テスト/図/型設定/品質ゲートを変更せず、commit/push/branch操作はしない。

## Phase 9: 取得途中形状・未知の序盤/統計fieldの型境界（解消済み）

最終品質証拠はevaluation_results/quality-f55f69dd9b6e436bb943b2cd66594f4e/。81→28→6→1→0診断、常設13source成功、全pytest745成功・0失敗（416.09秒）、独立smoke1成功・0失敗（10.58秒）。30回帰を追加し、関連51/品質29・新旧117比較・実対局60手/各187応答一致・全公開入口の正常/起動前拒否・実CLI0/1/2・旧schema5保全を確認。10必須成功・31source開始/終了/現在hash一致・収集complete/errors=[]・publication/model=[]・Markdown一致・pendingなし。実行時の検証核は契約を保ち、共有型の精緻化と限定表現だけを採用した。ReadOnlyは静的field制約で深い凍結ではない。残る保証限界と途中の検証スクリプト誤りもQUALITY_PROGRESS.mdのPhase 9最終監査へ記録した。以下は開始時の判断と選択肢の履歴である。

開始HEAD=27d1b36・clean。前段階の実証拠/現在31hashと通常12strictを確認。単独81診断、独立注釈probe7診断、既存関連30テスト全成功。安全な接続方針が未確定だったため本番修正前に止めたもので、3回失敗や実動作の新規不具合を確認したものではない。

第一推奨は既存述語/局所束縛/限定操作ビューと通常形状を使い、設計・互換回帰を先行する最小変更。副作用はhelperのコストとtracebackフレームで、性能完全一致を保証しない。第二案は実行時ロジック変更禁止のまま保留し、12strictを維持すること。全report/traceのcast・Any/ignore・検証緩和は代替にしない。

ユーザーは今回へ第一案を個別承認した。architecture第3節/契約第8節の先行設計は依存・取得→fresh検証・正式AND・状態を維持する。共有型の調整は生成途中None/部分診断/既存phase追記だけで、未知値を検証済みとしない。元コードの互換回帰→注釈/限定表現→単独strict→13対象登録→関連/全pytest/独立smoke/全ゲート/原証拠・モデル照合を再開条件とする。途中で安全な契約接続が得られなければ再停止する。詳細と実argvはQUALITY_PROGRESS.mdのPhase 9と新規一時領域reversi-strict-phase9-jwn5hllxへ残す。

現在のPhase 8（analysis.py）は**解消済み・PASS**。個別承認・設計/互換回帰先行の限定変更、常設12source strict、全品質ゲートと保存証拠の照合を完了した。過去の承認を流用していない。以下の停止条件は承認前の履歴、Phase 7/6/5は解消済みの履歴である。Phase 3のGUI保留は継続し、次フェーズ・commit・pushには進まない。

## Phase 8: analysis.pyの未知metadata・数値述語・正式分岐の型境界

個別承認により第一案で再開した。architecture第5節・contracts第7.1節を実装前に更新し、原構造全体のcastなしで相互運用する設計に確定した。新規35互換回帰は元コードで全成功。analysisと直接依存contracts/statisticsを限定変更し、46→残診断5→0、既存11対象を保持した常設12対象のstrict成功、関連99互換回帰成功を確認した。共有の未検証field/IDはobject、派生gameの要素は未知Mappingのまま。713比較、全公開APIの正常受理/起動前拒否、実CLI0/1/2と原証拠保全を確認した。

最終 `python -B tools/quality.py` は終了0 / QUALITY PASS。新規証拠は `evaluation_results/quality-05c58c782b164273bae80aea60071e0d/`、全pytest715成功・0失敗（1064.19秒）、独立smoke1成功・0失敗（2.59秒）、必須10検査全成功。31sourceの開始/終了/現在hash一致、収集complete・errors=[]、publication/model=[]、Markdown再生成一致、pendingなし。最初のゲートは検査用probeの命名/docstring修正のため中止し、その不完全証拠も保全した。未解決のPhase 8 blockerはない。長時間化の根本原因は未特定であり、少数の新旧profileから性能不変を断定しない。詳細・途中の検証上の誤り・局所ビューの範囲・保証外はQUALITY_PROGRESS.mdのPhase 8最終監査を参照する。以下は停止時の分析と選択肢の履歴である。

2026-10-15、開始HEAD=a3c6bf2、Git clean。前段階の保存済みPASSと現在31source hash、publication/model、Markdownを再照合し、現在11source strictも成功した。analysis単独は46診断、開始時関連112テストは全成功。19関数とcontracts/statistics・evaluate/CLI/テストの利用関係を調査した。本番・テスト・設定・モデルは未変更であり、失敗している既存動作を発見したという報告ではない。

注釈のみの独立プローブで7診断を再現した。analysis._confidenceのsnapshot.analysis_specへのget、type membership後の数値比較/返却、all後の差分演算、_estimate_inspectionのderived.gamesとverified_Nから統計への引渡しが対象である。共有contracts._Inspectionは意図的にsnapshotをMapping[str, object]、derived.gamesをobject、verified_Nをint | Noneとしている。正式資格のboolは数値/構造の静的な型絞込みを発行しない。これを理由に共有型を無条件で有効形状へ狭めたり、snapshot/report/原trace全体をcastしたりしてはならない。プローブは単純な注釈だけで解決しない根拠であり、他の安全な静的解決全般の不可能性を証明したものではない。

### 解決案（最大2案）

1. **境界の限定表現変更を個別承認し、設計・互換回帰を先行する（第一推奨）**。既存の数値述語を型ガード等へ接続し、局所束縛/既存演算の局所ビューと通常形状を使う。原metadataは未知のまま保持し、必要な型関係の根拠を正式分岐と既存検査へ対応付ける。型付けのための新しい値補充・許容条件・検証省略・例外握り潰し・原構造全体のcastは禁止する。局所ビューも個別の前提と限界を記録する。副作用としてhelper呼出コストやtracebackフレームが変わり得るため、性能完全一致は保証しない。共有型の調整が必要なら既存消費側の全影響を調査し、rawを有効と過剰保証しない。実装案の詳細は承認後の設計照合で確定し、安全な方針が得られなければ再停止する。
2. **実行時ロジック変更禁止を維持し、Phase 8を保留する**。既存11対象の成功と正常評価経路を維持できる一方、analysisの常設strict追加は未完了のままになる。raw全体のcast/Any/ignoreで見かけだけ12対象へ増やす代替案は採用しない。

### モデルとの照合・再開条件

architectureの第2節DAG、第3節データフロー、第5節正式入口、第6節状態図、およびcontractsの第7/8/9/12節に照合した。analysis→contracts/statistics、fresh Inspection→Assessment→適格時のみ数式→結果組立/CLI保存の順を維持する。取得/再解析、unknown/failed、参考/正式、computed/available、要求成否/採用は分離し、原証拠を修復しない。第一案もこれらの経路を変える計画ではないが、共有型や内部表現の具体案が確定したら実装より先に正本の説明を更新・再照合する。今回は提案未承認・コード未着手なので、現在の図や契約を実装済みとして書き換えない。

再開にはPhase 8への個別の限定変更承認が必要。承認後、confidenceのbool/非有限/欠損/不正metadata、差分の非数値/None、両側条件不一致、旧schema/別root、raw改変/不正summary/起動前通信、caller結果の上書き拒否、保存失敗とCLI0/1/2を含む互換回帰を先行する。元実装との戻り値/例外・原入力不変性、全公開入口の同値性を確認し、単独strict成功後だけ常設に追加する。既存11対象、全pytest/独立smoke、全品質ゲートと保存証拠/source/model一致まで確認するまではPASSにしない。診断・一時証拠・実コマンドと未実施範囲はQUALITY_PROGRESS.mdのPhase 8を参照する。commit/push/branch操作は行わない。

前段階のPhase 7（contracts.py）は**解消済み・PASS**。199診断と注釈のみの数値/時間detailプローブ3診断を確認後、設計・互換回帰先行の限定変更を個別承認されて実施した。Phase 6の承認を自動流用していない。常設11source strict、最終全品質ゲートと保存証拠の照合を完了した。下記Phase 6/5は過去の解消記録であり、Phase 3のGUI保留は継続する。次フェーズ・commit・pushには進まない。

## Phase 7: contracts.pyの未検証値と部分結果の型境界（解消済み）

第一推奨・採用案は、既存の値域/失敗順序を表す型ガード、検査後の局所束縛、通常形状と未知object/部分結果の区別である。母集合/trace/時間/集計と公開入口のANDを維持する。副作用はhelperの呼出コストとNone境界のtracebackフレームが増えることであり、性能同一性を主張しない。再開条件だった個別承認・先行契約・元コードでの互換回帰を満たして実装した。

局所ビューだけで全不一致を結ぶ代案は、castが増えて検証済みとの混同を起こしやすく単独では不採用。生成時間結果の全体cast案は安全審査で拒否されたため撤回し、固定4軸の型付き構築と直接依存の読み取り専用Sequence interfaceに設計を先行更新した。別名の全体cast・Any・ignoreへ迂回していない。依存方向・通信・探索・正式資格・状態経路は不変で、新しいMermaid遷移を追加する必要はない。protocol/search_budgetの全関数は注釈/docstring除去後のASTが開始版と一致する。

単独strictは199→105→18→6→0、新規互換は元実装で29成功、追加後は34と既存56の計90成功、型境界等19成功。旧実装との1,517差分比較、旧正常資料の公開API/CLI受理と起動前通信の拒否を確認した。最初の全ゲートはpytest670成功でも未使用型importのLint1件でFAILとなり、完走後にそのimportだけを除去した。

最終 `python -B tools/quality.py` は終了0 / QUALITY PASS、常設11source strict、全pytest670成功・0失敗（414.78秒）、独立smoke1成功・0失敗（2.65秒）、必須10検査すべて成功。保存先は `evaluation_results/quality-d3fea7fb669a4314bd854cca454c4e66/`。31sourceの開始/終了/現在hash一致、収集complete・errors=[]、publication/model=[]、Markdown再生成一致、pendingなし。未解決のPhase 7 blockerはない。実際の変更・局所castの前提・コマンド・残る保証外はQUALITY_PROGRESS.mdのPhase 7を参照する。

前段階のPhase 6は**解消済み・PASS**。限定変更の個別承認、設計/互換回帰先行、単独strict、常設10対象、全品質ゲートと保存証拠の照合を完了した。以下のPhase 5および停止経緯は履歴として維持する。Phase 3のGUIは保留のまま。次フェーズ・commit・pushへは進まない。

## Phase 6: search_budget.pyの未検証数値・端点境界（解消済み）

2026-10-15、開始HEAD=c537542、Git clean、前段階PASSのsource/publicationと現在9source strictを再確認。対象の81診断には、type membership/anyで検査してもmypyが元の数値/端点へ型を伝播できない問題がある。実装由来の注釈のみの独立プローブでも10診断が残り、rawを有効型と断定するだけの移行は安全でないと判断した。3回の無進展による停止ではない。

検討した案と再開条件:

1. **既存述語の型ガード化と検査後の局所再束縛（第一推奨・採用済み）**。意味と副作用の維持条件を設計/契約に先行記録し、元実装の互換回帰を固定してから最小変更する。原値とunknown/failed/exemptを維持し、Any/ignore/raw全体のcastを使わない。副作用はhelper呼出・局所束縛の処理コストや不正dict subclassのlookup回数の差。正常JSONと既存の例外・区間・型境界を回帰/差分検証し、実行コスト0は保証しない。既存図の依存・データフロー・sequence・状態経路は不変で、新たな正常化/正式資格を描かない。
2. **元の検査を残し、個別に根拠を示した局所型ビューだけで結ぶ（単独案としては不採用）**。検証済み局所値に限るなら候補だが、raw全体のcastで通すことは不可。元変数へanyの結果が伝わらない多数箇所にビューが増え、静的保証と意味検証を混同しやすい。castにも実行時呼出があり、無変更保証の代わりにならない。採用するなら箇所・前提・例外維持・型の過剰保証防止を先行確定し、同じ正式入口へ合流する必要がある。

ユーザーが「限定変更を許可し、設計と互換回帰を先行する」と回答し、第一案の再開条件を満たした。Phase 5の承認を今回へ自動適用していない。実装では既存演算/型関係に限定した6か所のcastを明示し、違反追加と未検証metadataのint保証を拒否する回帰を付けた。型ビューは原値を変換せず、原辞書/trace全体を有効型へcastしていない。

修正前の既存関連131成功、修正前の新規互換29成功、対象漏れ1失敗を記録後に実装。最終の `python -B tools/quality.py` は終了0、常設10source strict、全pytest622成功・0失敗（312.45秒）、独立smoke1成功・0失敗（3.10秒）、必須10検査すべて成功。保存先は `evaluation_results/quality-867259de5da94e1087c5314a10cdb93b/`。31sourceの開始/終了/現在hash一致、収集complete・errors=[]、publication/model=[]、Markdown再生成一致、pendingなし。

未解決のPhase 6 blockerはない。旧正常報告の公開API/CLI受理と起動前通信の拒否、原証拠保全を確認した。性能同一性、全未知入力、dict subclass副作用、型introspection、別OS実機、GUI実操作、棋力改善までは保証しない。詳細な実コマンド・修正過程・完了条件照合はQUALITY_PROGRESS.mdのPhase 6を参照する。

## Phase 5: protocol.py のpipe型とOS分岐

状態: **解消済み・Phase 5 PASS**。2026-10-15、開始HEAD=5329275。
ユーザーの限定変更承認を受け、モデル/契約先行更新、旧実装に対する互換回帰、最小実装、全品質再検証を完了した。常設strictは既存8対象を保持してprotocolを加えた9対象。全pytest584成功・0失敗、独立smoke1成功・0失敗、必須10検査すべて成功。証拠は `evaluation_results/quality-0604dc9258ae432ead2a48b894884236/`。31sourceの開始/終了/現在hash一致、収集complete、publication/model検査、Markdown再生成一致を確認した。詳細はQUALITY_PROGRESS.mdのPhase 5最終監査を参照する。
未解決のPhase 5 blockerはない。Phase 3のGUI保留は継続し、POSIX実機・性能不変・型introspection等の未検証範囲をPASSへ含めない。次フェーズ・commit・pushには進まない。

### 以下は承認前の停止履歴

以下の停止記録は承認前の調査履歴として維持する。ユーザーから「境界検査・OS条件の最小変更を、互換条件の設計と回帰を先行する条件で許可する」と回答を得たため、承認前の「無変更・保留」を今後の禁止指示に読み替えない。
停止時の対象はリポジトリ相対の `harness/protocol.py`。停止時点ではコード・テスト・設定・モデル図・契約は変更していなかった。
3回の修正失敗による停止ではなく、「型チェックのためだけの実行時ロジック変更禁止」を維持した安全な解決方針を確定できないため、実装前に停止した。

### 停止時の開始条件

- Gitは開始時clean、mainはorigin/mainより3commit先。既存8対象のstrictを実際に再実行して成功した。
- Phase 4のquality-a0408e7ff61d4136a53bd24b747d4624はpassed=true、31sourceの開始/終了/現在hash一致、verify_publication=[]、validate_model=[]。
- AGENTS、QUALITY_PROGRESS、pyproject.toml、現行architecture/contractsを確認した。本停止記録は開始時には存在しなかった。
- Phase 3のGUIは保留のまま。今回の停止はPhase 5の通信ハーネスを独立に調べた結果であり、Phase 3を完了扱いしたものではない。

### 診断と依存

単独strictは107診断:

| 種別 | 件数 | 主な内容 |
|---|---:|---|
| no-untyped-def | 23 | 時計・形式・時間検証、HarnessError、Playerとcontext managerの未注釈 |
| no-untyped-call | 69 | 未注釈関数/メソッド間の呼出 |
| assignment | 2 | 4/7キー、3/4端点のtuple長の推論 |
| var-annotated | 3 | commands、stderr_tail、queueの要素型 |
| operator | 1 | 未検証limitのNone候補と数値比較 |
| union-attr | 7 | stdin/stdout/stderrのreadline/write/flush/close |
| attr-defined | 2 | Windows型定義に存在しないos.killpgとsignal.SIGKILL |

依存はstdlibのみで、Windows周波数取得のctypesは関数内import。上流はevaluate、search_budget、contracts（関数内importも含む）。protocolから上流へ逆importしない。
PlayerはAIの独立起動・reader thread・queue・期限・終了回収・host証拠を管理し、GUIのweb.Processとは別経路。
純粋なclock_status/temporal_order等の結果はsearch_budget/contractsから正式評価に伝播するため、型付けを理由にunknown/failedの条件を変えてはいけない。

### 注釈だけでは解消しないことを確認した境界

#### A. PIPE指定とnullableなPopenの型

実装は3pipeすべてをPIPE、text=Trueで起動する。しかし現行mypy/typeshedのPopen[str]メンバーはIO[str]|Noneであり、引数条件から非Noneの属性型にはならない。
全関数が注釈済みの独立プローブでもwrite/flush/stdout・stderrのreadline/closeで5件のunion-attrを再現した。
元モジュールは同種の参照が7診断ある。プローブの5件と元の7件を混同しない。

通常の実Playerでは3pipeが存在し、name/ask/play/showboard/quit、正常終了・全reader停止を確認した。
したがってこれは**通常のPopen成功時にpipe欠損が発生したという不具合報告ではなく、型定義が引数依存の不変条件を表現していない問題**である。

一方、診断用の模擬stdin/stdout/stderr=Noneでは、_read_stdout/_read_stderrはreadlineのAttributeError、abortはcloseのAttributeErrorとなり、_closed=Falseを保持した。
assert追加なら例外種が変わり、Noneをskipするcloseなら終了状態やcleanup_end_nsの生成まで変わり得る。元の異常時動作を調べず「正常に同じ」と報告しない。

#### B. os.nameによる実行時分岐とmypyの静的OS判定

abortのPOSIX側は `os.name == 'nt'` のelseにあり、Windowsでは通常実行しない。しかし現行mypyはこの分岐でPOSIX専用属性を利用可能とは判定せず、killpg/SIGKILLを拒否する。
注釈済みの独立プローブでも2件を再現した。未注釈定義だけが原因ではない。
WindowsでPOSIX停止分岐を実行したという観測ではない。分岐条件の書換え、getattrによる取得、局所interface/cast、検査除外はいずれも「型注釈を足すだけ」と同一ではないため、無断で採用しない。

### 推奨する解決案（未実装）

#### 1. 境界検査とOS条件の限定変更を別途承認してから再開する（第一推奨）

1. 3pipeの存在を確かめた値を型付き通信処理へ渡す最小の境界検査を設計する。欠損時の例外種・表示・終了回収・保存状態を先に確定し、既存例外を変更するなら明示承認を得る。
2. OS条件は、正式対応環境と未対応OS、既存のos.name変更テスト等を照合し、mypyが認識するsys.platform条件等へ変更する可否を決める。単にPOSIX分岐を削除・検査対象外にしない。
3. clock descriptor、command/lifecycle/reason辞書、queueのstr|None、deque[str]、例外の後付け診断等を、未検証objectと通常の辞書形状に分離して注釈する。未知値を有効な端点と断定する型にしない。
4. 型付け前後で各時刻の採時順・所属・差分、タイムアウトと回収の結果を照合する。SP/Popen/PRの間へ勝手に処理を挟まず、既存のPR採時を後ろへ動かさない。guardがhost応答区間内へ入る場合は計測範囲への影響も明記する。

副作用: 実行時分岐・わずかな処理時間、異常系の例外/終了状態、公開process属性の変更を観測する時点に影響し得る。pipeを一度cacheする案は毎回process属性を読む旧実装と同じではない。
新しい「計測値欠損を補う」処理や「取得失敗を正常終了へ直す」処理を追加しない。新しい入力制限や時間猶予も導入しない。

モデルとの関係: 単なる注釈であれば図は維持できるが、境界検査を追加して失敗時の責務・状態・採時位置が変わるなら、実装前にsequence/state/data-flowと契約第5節を更新する。
新しいfailure経路が正式資格の検証を迂回しないこと、unknownとfailedの扱いが変わっていないことを正式入口の回帰で確認する。
正式評価schema5/trace4/validation3の変更が必要だとは現時点で判断していない。実装前設計で不要と確認できた場合は版を変えない。

再開条件: 上記の必要最小限の実行時変更の許可と、異常系で厳密に維持すべき観測を明示する。現在の禁止条件のままこの案を実装しない。

#### 2. 実行時検査を増やさず、限定的な型境界を明示承認する

PIPE指定の成功時不変条件を根拠に3pipeだけの局所castを、OS分岐内の属性について必要最小限の静的interfaceを検討する。
Popen/通信/時系列検証全体をAnyへ変更するのではなく、適用場所・値・前提を列挙し、その範囲を回帰で固定する。

副作用: 実際のpipe存在やOS APIを型自体は検査しない。公開process属性の不正な変更まで安全とは保証できず、cast呼出等も実行時処理を追加する。
独自stubで既知のOptionalやOS差を無根拠に消す、TYPE_CHECKING内の仮定だけで検証済みとする、module単位のignore/excludeで通すことは不可。
第一案より実行時の保証が弱く非推奨。限定cast等の承認がない現状では、この案も実装しない。

モデルとの関係: 動的な信頼境界が残ることを契約へ明記する必要がある。時計・raw・正式資格まで検証済みとする図へ書き換えてはいけない。

### 再開後の検証と順序

1. 変更可能範囲・例外/ログ/状態の維持条件を決め、必要なら先にモデルと契約を更新する。
2. 既存の通信形式・起動/通常/genmove/quit期限・異常終了・reader回収に加え、pipe欠損とOS分岐の挙動、clock/command/lifecycle辞書の不正値を回帰固定する。
3. 23定義へ注釈し、record/状態を表す型を必要最小限追加する。unknown/failedを静的な「検証済み」へ偽装しない。
4. 単独strict成功後にのみ既存8対象を保持してprotocolを9件目へ追加し、既存の対象漏れ回帰も拡張する。
5. 全品質ゲート、保存trace再解析、正常/不正時系列の全公開入口・CLI同値性を確認する。採時差の実測値完全一致は要求せず、意味・順序・計測範囲と状態契約で比較する。
6. source/publication/model照合と進捗更新を完了して初めてPhase 5をPASSとする。

### 今回実行した検証と保存先

- 通常strict: 8source成功。対象単独strict: 107診断・終了1（常設strictの失敗ではない）。
- 注釈済み境界プローブ: union-attr 5、attr-defined 2の計7診断・終了1。プロジェクトの設定は変更していない。
- 既存関連回帰: 通信形式、プロセス停止、起動/genmove/全応答期限、QPC曖昧区間、不正端点を選択して45成功・320除外、3.83秒。
- 実Player: 3pipe存在、時計descriptorの低水準verified、通常着手/盤面要求と正常quit、全reader停止。これだけで正式評価の全ANDを検証したとはしない。
- 模擬pipe欠損: reader/abortの既存AttributeErrorと_closed=Falseを確認。正常Popenでの欠損発生率や実故障の検証ではない。

一時領域は `C:\Users\taaai\AppData\Local\Temp\reversi-strict-phase5-ga0igyny/`。
baseline.json、protocol-before.py、strict-before.txt、protocol-before.txt、boundary-probe.py/.txt、baseline-related.xml/.txt、runtime-boundaries.jsonを新規保存した。
後日消失し得るため再開時に再検証する。POSIXの実回収、すべての107診断への注釈後の結果、変更後の正式資格・性能は未検証である。

今回はPhase 5をPASSにせず、次フェーズ、コミット、push、ブランチ操作へ進まない。

### 停止時の全体品質再検証

`python -B tools/quality.py` は終了0 / QUALITY PASS。
新規保存先は `evaluation_results/quality-b3c1e184f354462f992400f1f8ae74fb/`。
全pytest **549成功・0失敗、355.56秒**、独立smoke **1成功・0失敗・548除外、2.54秒**、typingは既存8source成功。
必須10検査すべて終了0、31sourceの開始/終了/現在hash一致、full/smokeの収集complete・errors=[]、verify_publication=[]、validate_model=[]、Markdown再生成一致、pendingなし。
これはprotocol未型付けの現行環境の成功であり、Phase 5のPASSを意味しない。
変更は本停止記録とQUALITY_PROGRESS.mdだけで、その他67trackedは開始hash一致。HEAD=5329275のまま、ステージ/コミット/push/ブランチ操作は行っていない。
