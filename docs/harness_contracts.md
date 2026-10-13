# ハーネスのデータ契約と不変条件

更新日: 2026-10-13。現行実装: evaluation/analysis/comparison schema5、trace4、validation3。本文は現行の定義・検査・保証境界をまとめる。旧schemaの成功記録を現行の資格に読み替えない。

外部コマンド・盤面・時間制限の正本は[課題仕様](../プログラム開発方法.md)、実行方法は[ハーネス.md](../ハーネス.md)、責務と図は[harness_architecture.md](harness_architecture.md)。実行確認と残件は[正常化完了報告](reports/ハーネス正常化完了報告.md)。旧定義・監査・整理前の本文は[修正モデルの履歴](archive/ハーネス整合性修正モデル.md)に保管する。

## 1. 形式・型と外部仕様

説明上の型名に対応する新しいクラスを追加したわけではない。実装は主にdict/list/tupleであり、全形式を網羅する独立JSON Schemaはない。

| データ | 生成元 → 消費先 | 現行の表現・境界 |
|---|---|---|
| Rows / Position | referee・openings・AI応答 → evaluate/contracts | 8行×8文字B/W/E。メモリtuple、JSON配列。座標はx列・y行、0～7。harnessのpassはNone |
| OpeningPopulation / SelectionManifest | openings → evaluate/contracts | 母集合/識別規則/抽出algorithmはv1。合法履歴・N/hash・重複・抽出を再検証 |
| BudgetConfig | search_budget → producer/検証器 | v1入力も対応し、defaultを含めv2へ正規化。kindはnone/time/nodes/depth、時間上限は整数ns |
| ProcessEvidence / CommandRecord | protocol.Player → evaluate/contracts/search_budget | 親lifecycle、実測整数ns、時計/thread、session/generation、全command index/ID。secondsは派生表示 |
| raw trace4 | 対応producer → search_budget/contracts | configヘッダ、全command/genmove、quit、process_exit_ready。保存bytesのhashと意味を同じsnapshotで検査 |
| Game / EvaluationReport | evaluate → inspection/analysis | case/pair/leg、role seed、色割当、棋譜、両host記録、trace、status/cache/summary。新規評価はschema5 |
| Inspection / Assessment | contracts → analysis private核 | freshな観測/理由/派生集計と、参考可否/正式資格/要求成否を分離。保存dictを資格入力にしない |
| Analysis / Comparison | analysis → 共通writer/利用者 | schema5。元source hash、検証器版/fingerprint、解析spec/IDを保持 |
| Checkpoint | evaluate → 中断調査 | run-state.json。局単位の進行記録で、全通信の即時永続化ではない |
| GUI JSON | web → ブラウザ | 黒1/白-1/空0、passは[-1,-1]、scoreは黒視点。harnessのA視点得点とは別 |

プロトコルのname/ask/play/genmove/showboard/result/quitは変更しない。GUIは独立したProcess通信経路で、harness.Playerの期限機能を利用していない。depth/temperatureのUI表示だけではAI設定への接続を意味しない。

## 2. 識別子とhash

canonical JSONはensure_ascii=False・sort_keys=True・空白なし・allow_nan=Falseに基づく。各生成関数の実装が正本であり、意味の違うIDを代用しない。

| 識別子 | 定義元・意味 | 不変条件 |
|---|---|---|
| case_id | openings.case_id。rules/identity版、初期盤面、全履歴、到達盤面、手番のSHA-256 | 履歴込みのケース。盤面だけが同じでも履歴が違えば別IDになり得る。AI・run・保存先を含めない |
| orbit_id | openings.orbit_id。盤面＋手番のD4対称8変換の代表 | 対称重複排除用。case_idやpair対応の代用にしない |
| population_hash / selection_hash | 母集合/manifestの自己hashを除く内容 | archive・報告・conditions・manifestの関連aliasを照合 |
| pair_id | contracts.pair_id。pair_version=1、case_id、A/B seeds、role_swap_version=1 | 同じケース・役割seedの先後2局。run・leg・予算・AI版を含めず、前後比較でも対応付ける |
| run_id | evaluateのUUID | 1取得を識別。条件一致を意味しない |
| clock_domain_id | 取得run共通の時計比較scope | tokenだけで時計の比較可能性を証明しない。別runの絶対時刻を比較しない |
| process_session_id / process_generation | Popen試行のsessionと世代 | 同一run全体でsession一意。現取得はgeneration=0、同slot途中restart継続は正式対象として実装しない |
| command_id | protocol.command_id。run/purpose/pair/leg/role/session/generation/indexの結合 | 全commandの位置・実文字列・case/role seedを期待contextへ照合。probeとgameを混ぜない |
| experiment_id / evaluation_fingerprint | evaluate。conditions/runtime/targets/明示assets/取得コード | confidence等の解析条件と分離。seeded_playerも取得依存へ登録。全AI importの自動登録ではない |
| analysis_id / analysis_fingerprint | analysis。元source hash、現検証器/数式/解析spec | 取得証拠を変更せず別解析を識別。confidence変更は解析条件として扱う |
| board_sha256 | search_budget。要求前の行を改行連結したASCII bytesのSHA-256 | traceのgenmove要求前盤面と対応。JSON hashやcase/orbit IDとは異なる |

opening_id、sample_set_id、execution_orderという専用フィールドは実装済みとして扱わない。現状のケースID、manifest配列、game indexを用いる。PIDは再利用されるため診断補助でありsessionの代用ではない。

hashは内容照合であって署名ではない。hashを更新した不正rawも、着手・盤面・応答・時系列の意味検証を通さない。

## 3. 母集合・抽出・coverage

- Nは検証済み母集合archiveのentries件数から導出する。report.population.sizeだけを統計へ渡さない。
- rules/identity版、purpose、placements、全履歴/到達盤面/手番、case/orbit重複、hashを検査する。生成用seedによる合法手歩行は、全可能序盤の一様生成ではない。
- 抽出は保存済み母集合からの一様な非復元抽出。選択manifestのN/n/hash/algorithm/seed/ID順を対応runtimeで再構成し、全aliasを照合する。
- 全予定pair/leg・先後・case/seedを照合する。欠落/重複/失敗を除いて標本数を縮めない。初期盤面反復はrepeat_indexを含む予定規則で再構成する。
- censusは全case・両leg・必須品質・固定workの前提を満たす場合だけ。n=Nという数値だけや、標本全同点だけで幅0にしない。

## 4. seed・比較条件・再現性

母集合生成seed、selection seed、master seedは別の目的を持つ。contracts.seed_forはseed_version=1、master_seed/case_id/A-B role/repeat_indexのhashからrole seedを導く。先後交換しても同じ役割に同seedを使い、固定序盤はrepeat=0、初期盤面反復はpair indexを使う。

PlayerはPYTHONHASHSEED、seeded_playerはPython randomを初期化する。numpy/GPU/独立Generator/時刻seedまで制御する契約ではない。決定性宣言と先頭caseの2回probeは、全caseの固定得点の数学的証明ではない。

比較はcontracts.comparison_conditions/comparison_mismatchesを共用し、ケース/seed・予算・計測契約・runtime・取得fingerprint・固定B本体/資産を照合する。候補Aの実装/モデル等は変更対象として扱えるが、Aのbudget・deterministic/counting定義の共通条件は保持する。単独適格でも条件不一致なら対応差は不可。

AIを分離したらengine・設定・モデル等を--a-asset/--b-assetへ明示登録し、baselineは依存を含め独立保存する。共通engineの変更で固定Bまで変わらないようにする。SHAだけでは元ソースを復元できない。

<a id="temporal-invariants"></a>

## 5. 時計・プロセス・通信の不変条件

現measurement_contractは`local_cpython313_perf_ns_lifecycle_v2`。対応登録のあるlocal CPython3.13/Windows/QueryPerformanceCounter契約で、元のclock descriptor・QPC周波数・run domain・session・計測threadを確認してから比較する。再解析環境の時計に原記録を書き換えない。

整数nsは1ns精度を意味しない。同一計測threadは整数の順序を厳密に検査し、同値/0経過を許す。親子など異threadの辺は、`U=ceil(1_000_000_000/qpc_frequency_hz)+1`nsを用い、後点−前点が>Uならverified、<-Uならfailed、[-U,U]ならunknown。このUを猶予として緩めたり、別時計比較へ流用したりしない。未知契約・不足はunknown、既知不一致・不正型はfailed。提供された不正型を別の欠損で隠さない。

| 記号 | 実測点・所属 |
|---|---|
| SP / PR | 親のPopen呼出直前 / 戻り |
| CE / IR | 子の最初の計測hook / 対象初期化後のprotocol ready |
| HE0 / HS / HR / HE1 | 親のexchange開始 / 送信直前 / 最終応答取得後 / 形式検査後 |
| CC / CR | 子のstdin行取得後 / 応答決定後・出力前 |
| SS / SE | 子の内部探索開始 / 終了 |
| CS / EO | 子の終了準備 / 親が最初に終了を観測した点 |

CSはOS死亡時刻ではなく、EOもabort全体の完了点とは違う。単一collector thread・単一producer計測threadという登録前提を、別threadへ勝手に拡張しない。

| 不変条件 | 比較可能な時計とidentityを確認した後の検査 |
|---|---|
| T-ID / T-CLK | run/slot/session/generation/command/case/role seed一致、session一意、時計/domain/thread所属 |
| T-SP | SP≤PR≤全host HE0。startup_endはHE1_name |
| T-INIT | SP≤CE≤IR≤CC_name。CEとPR、IRとHS_nameの相対順は固定しない |
| T-CMD | 通常全要求でHE0≤HS≤CC≤CR≤HR≤HE1。genmoveではCC≤SS≤SE≤CRも要求 |
| T-SERIAL | 親の前HE1≤次HE0、子の前CR≤次CC。最後はquit、quit後のcommandなし |
| T-EXIT | 正常quitのHE0q≤HSq≤CCq≤CS≤EO。子イベントはCSまで、通常host応答はquit前 |
| T-LOAD | startupはCE≤load_start≤load_end≤IR。対応command loadはCC〜CR内、使用モデルはload_end≤SS |
| T-DIFF | search=SE−SS、wire=HR−HS、external=HE1−HE0。実測差/表示alias/期限を照合し、bool/floatや負の同thread差を認めない |
| T-CAUSE | collectorの予定順からA name→B spawn、actor genmove→peer play、同期検査、quit等を再構成。保存時刻をソートして正当化しない |
| T-GATE | 必須時間各軸と他の品質/統計条件のAND。時間verifiedだけ、hash一致、n=N、取得completeでは正式不可 |

親はready前にnameを送ってpipe待ちになってよい。Popen戻り前の子開始も正常にあり得る。異常終了・timeout/killでは正常quitの証拠を補造せず、取得済み点と診断を残す。子の終了後イベントと、終了後の親による既存pipe出力の回収を混同しない。

clock/QPCの根拠は[Python 3.13 time](https://docs.python.org/3.13/library/time.html#time.perf_counter_ns)、[QPCの比較前提](https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps)。登録時の検討は[旧時系列計画](archive/ハーネス時系列整合性修正計画.md)に保管する。

## 6. 探索予算・model load・trace

要求設定、producerの実効設定、観測値を分け、設定hash・対象hash・contextへ結合する。kindごとのlimitと実効設定を照合し、nodes/depth/search_elapsed_ns・phase・盤面hash・応答・stop_reason/model/fallbackも検証する。全commandのCC/CRとquit/終了準備を含め、余剰・重複・欠損を検出する。

| policy | 適用条件・制約 |
|---|---|
| equal-work | 同じnodes/depth kindと上限。nodesは同じ非空counting_definition_id。上限一致は実仕事量や計数実装の真正性の証明ではない |
| equal-time | 同じ整数ns上限と対応clock/scope。time/deploymentの異なる合法棋譜を許すが、固定得点の正式CIは出さない |
| baseline-asymmetric | 理由と元入口hashが一致するBのnoneだけ内部予算exempt可。A/両probe/両host/通常Bの子lifecycleは免除しない |
| descriptive | 参考対局。none宣言だけ、trace存在だけでverifiedにしない |

外部はcommand既定1秒・genmove10秒、startup別既定5秒。初回nameのstartup期限と通常command期限は区別する。実際の条件は数値フィールドでありtimeout_policy説明文字列だけを正本にしない。

内部search_only、モデル読込、JSONL書込、盤面適用、stdout/queue・OS待ちを混同しない。モデルなしは明示not_required。startup loadはCE〜IR、対応command loadは利用前に完了し、内部探索合計へ加算しない。未対応のlazy/background/GPU scopeはunknownであり、必要な証拠/同期契約とテストなしにverifiedへしない。fixtureのscopeは実AIの予定scopeと同名だけで同等扱いしない。

## 7. 観測・集計・状態

contracts.inspect_evaluationは原report snapshotと同一呼出内のartifact bytes cacheを使い、母集合/棋譜/両通信/rawと保存cache/summaryを再照合する。取得summary前だけprivate _acquisition_summaryが保存summary照合なしのcoreを使う。公開skip flagはない。原証拠や不正summaryを正しい派生値で黙って置き換えない。

| 状態・結果 | 意味 |
|---|---|
| verified / unknown / failed | 必要集合の検査完了 / 証拠不足・未対応・順序曖昧 / 確認済み矛盾。failed優先でunknown理由も保持 |
| exempt / not_required | 許可済みB内部予算や必要集合で除外した項目だけ。未起動roleやlifecycle全体へ拡張しない |
| game complete | 合法終局を得た実行状態。後続通信/quit/検証/保存成功とは別 |
| reference_available | 現行正常対局の参考集計可否。正式資格ではない。旧資料は互換分岐と保証不足を明示 |
| formal eligible / ineligible / not_requested | 品質・統計条件による資格。不成立と要求なしを区別 |
| statistics available / descriptive_only / unavailable | 正式区間 / 参考のみ / 正式計算不可 |
| publication saved / failed、decision undetermined | 保存結果と採用判断。取得completeや数値基準metから自動採用しない |

参考分母は正常終局・通信verifiedの完了局。勝率=wins/completed、勝ち点率=(wins+draws/2)/completed、0完了はnull。A勝1・分0.5・負0で、色B/Wと役割A/Bを区別する。不正手/通信失敗を相手の勝ちに変換しない。

RTTの全genmove、内部全genmove、内部normal_moveを別に集計し、pass/terminal probeを明示する。sum/max nsとsample数から再計算する。実AItraceなしは内部平均null/unknown。予算・測定・時系列のrole→game→report階層の保存矛盾も拒否する。

## 8. 正式資格と統計の推定対象

正式単独評価はconfirmatory/source schema5、母集合・抽出・全予定coverage・棋譜/通信・予算・時間・測定・source整合の全必須verified、固定nodes/depthと決定性条件等をANDで要求する。必要集合はprofile/policyから導出し、rawの自己申告除外を読んで緩和しない。

推定対象は保存された固定有限序盤母集合でのA先後平均得点、対応比較は同caseのafter−before平均差。先後2局で1ペア、単独range[0,1]・差range[-1,1]。実装はHoeffding–Serfling有限母集合bound、資格を満たす全数ではcensus。母集合生成分布を未知の全局面分布と同一視しない。

statistics単独APIはcomputed/arithmetic_only/not_assessedで、母集合やrawの証明書ではない。検証済みNと派生gameだけをanalysisから渡す。bootstrapや非決定AIの反復分布推定は現行方式ではなく、追加設計が必要。

品質検証、数式計算、数値基準met/not_met、採用decisionを分離する。事前固定基準・未閲覧held_out・学習非混入・任意停止/多重比較の運用は検証器だけで保証しない。

## 9. 保存・互換性・再解析

最終reportは排他新規作成・flush/fsync。checkpointのatomic replaceはrun所有領域だけで、既存finalを置換しない。取得完了と保存成功は別。途中ファイルや元の失敗を成功へ修復せず、失敗phase/path/causeを残す。

旧schema1〜4は読込と可能な参考再解析を維持する。旧秒から新端点を補造せず、旧4の比較可能な起動前/要求間矛盾はfailed、不足する新lifecycle証拠はunknown。新analysis/comparison5や過去eligibleだけで旧sourceを正式昇格しない。

再解析はAIを起動せず、別出力にsource hash・現検証版/fingerprint・解析spec/IDを記録する。evidence_rootは同じ原bytesの移動先指定であり、別母集合/別棋譜への差替えではない。保存confidenceを既定として利用でき、比較は共通confidenceへ再解析する。

入力不正/未対応root/開始前の既存出力はCLI2、開始後の保存・必須品質/比較要求不成立は1、要求成功は0。正常descriptiveの0は正式資格を意味しない。数値基準未達でも要求と保存が正常なら0。

<a id="legacy-optional-budget"></a>

### 9.1 旧schemaのoptional予算設定の契約と修正設計

修正前は旧schema1/2のbudget_configsを検査せず参考分岐へ進む一方、比較側は辞書と仮定してgetを使っていた。schema2でbudget_configsを配列にすると、単独参考解析がvalidになるのに比較APIはAttributeErrorとなった。根本原因は共通decode_inputの版別検査の穴。現行は以下の共通検査を実装し、比較側で不正値を空辞書へ置換して隠さない。

| 入力 | 旧schema1/2（schema省略は1） | schema3〜5 |
|---|---|---|
| budget_configsなし | 従来どおり参考利用可能。内部証拠はunknown | 必須のためinvalid |
| 空辞書、AまたはBだけの辞書 | 任意メタデータとして許容。存在する設定だけを検査 | A/B両方が必須のためinvalid |
| 配列/null/bool等、A/B以外のrole | invalid / budget_configs_invalid | 同じ構造規則でinvalid |
| roleの設定が不正型、search/budgetの不正型、未対応設定版、不正limit | 現行normalize_configで検査しinvalid / budget_config_invalid | 同じ設定規則でinvalid |
| 正常な設定 | 原資料を変更せず受け入れる。正式資格には昇格しない | 従来の原証拠・正式条件も引き続き検証 |

修正対象はcontracts.decode_inputの共通予算設定検査。既存のschema3〜5の必須条件・時系列・母集合・品質/統計のANDは緩和しない。入力invalidではinspectionにconsumer用snapshotを渡さず、全単独/比較/builder APIでunavailable/ineligibleとして構造化拒否する。両CLIは開始前に終了2、出力なしとし、原JSONを保全する。欠損する旧traceや時計のunknownを入力不正に変えない。

設計照合: decode_input→fresh Inspection→Assessment→統計/保存という既存の依存方向・状態図と一致し、新規モジュールや循環依存は不要。取得/解析schema、trace、validationの版は据え置き、修正コードは既存fingerprintで識別する。旧原証拠を修復せず再解析する規則も維持する。

受入テストはtest_legacy_optional_budget_*の50ケース。旧1/2の配列/null/role/search/budget/版/limit改変、比較のbefore/after各側、全公開評価入口、実CLIの終了2/出力なし/原資料不変、正常な旧設定欠落/空/片role/両roleとschema省略の参考利用を検証する。修正前の回帰失敗を記録してから実装した。全体・独立smokeと実行結果は[正常化完了報告の追補](reports/ハーネス正常化完了報告.md#legacy-budget-fix)で確認する。旧形式の全未知フィールドを網羅する独立JSON Schemaや、旧rawの完全検証を追加したわけではない。

## 10. 回帰テストと保証境界

詳細は[tests/test_harness.py](../tests/test_harness.py)、固定盤面・標準入出力・GUI接続は[tests/test_result.py](../tests/test_result.py)、[tests/test_stdin_commands.py](../tests/test_stdin_commands.py)。契約変更時は正常schema5を対照にする。

| 契約 | 主要な既存回帰 |
|---|---|
| N/manifest/偽census | test_inspection_uses_archive_size_and_revalidates_saved_report、test_census_requires_all_archived_cases_and_both_legs |
| raw意味・保存階層・集計 | test_saved_trace_semantics_block_formal_statistics_even_with_new_hash、test_consistency_q1_saved_budget_hierarchy_cannot_bypass_entries、test_consistency_q3_internal_metrics_match_raw |
| 起動/要求間/終了/識別子 | test_temporal_host_lifetime_cannot_bypass_formal_entries、test_temporal_all_child_events_are_revalidated、test_temporal_reused_session_is_rejected_with_consistent_aliases |
| clock/QPC/初期化 | test_temporal_qpc_boundaries_preserve_ambiguity、test_temporal_popen_return_is_not_child_initialization_barrier、test_temporal_real_startup_model_load_is_separate_from_search |
| 全公開入口・CLI | test_temporal_public_apis_and_saved_clis_agree、test_temporal_public_assessment_requires_original_source、test_schema5_cli_saves_reloads_and_reanalyses |
| 運用障害・不正root | test_consistency_shared_writer_classifies_operational_failures、test_consistency_cli_invalid_root_is_exit_two |

成功範囲と未保証範囲は[最新完了報告](reports/ハーネス正常化完了報告.md#remaining-risks)。counter/全ログの真正性、全case決定性、全AI依存固定、未知OS/GPU、ブラウザ描画、停電時の完全性を保証しない。

旧schemaの予算設定に関する[過去監査F02](archive/AI開発着手前ハーネス監査.md#legacy-nested-input-review)は第9.1節の共通入力検査と専用回帰で対応した。旧形式の証拠不足はunknownとして残し、設定の正常化だけで正式資格を与えない。

## 11. 拡張時の更新規則

変更する責務・生成データ・必要role/phase・時計scope・source/cache/raw・旧schema・公開入口を先に確認する。新method/estimandを追加する場合も、共通Inspection/Assessmentと必要証拠のANDを迂回しない。失敗を除いて対象を再定義しない。実装・関連回帰・本書・設計図・操作手順を一緒に更新する。

## 12. 時間・公開入口契約の索引

時間の正本は第5・6節、必要集合/状態/資格は第7・8節。実行経路は[現行構造](harness_architecture.md)。公開入口は原evaluation reportと原証跡を受ける。

| 入口 | 共通核・役割 |
|---|---|
| contracts.inspect_evaluation | 毎回fresh検証。lifecycleを含む4時間軸、raw意味、全cacheを照合 |
| analysis.assess_evaluation / statistical_eligibility / estimate / reanalysis_report | fresh Inspection→private _assess_inspection。保存Inspection/Assessmentを認可しない |
| analysis.assess_comparison / paired / compare / comparison_report | 両原reportのfresh検証→private _assess_pair_inspections。単独資格＋相互条件を要求 |
| evaluate.compare_reports、evaluate/analysis CLI | 同じanalysis経路を使用。独自の保存flag資格判定をしない |

comparison_reportのcaller resultはfresh結果との一致確認だけで、予約fieldを上書きしない。unknown/failedを許可する互換モードはない。内部private関数の改造や全原証拠の整合した偽造を防ぐ認証機構ではない。
