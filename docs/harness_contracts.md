# ハーネスのデータ契約と不変条件

更新日: 2026-10-14。評価契約の基準: 8712843＋品質導入の非意味的lint整理・型注釈。現行実装はevaluation/analysis/comparison schema5、trace4、validation3。第13節は実装に先行して確定した開発用品質契約。旧schemaや品質ゲートの成功を正式AI評価資格に読み替えない。

外部コマンド・盤面・時間制限の正本は[課題仕様](../プログラム開発方法.md)、実行方法は[ハーネス.md](../ハーネス.md)、責務と図は[harness_architecture.md](harness_architecture.md)。実行確認と残件は[正常化完了報告](reports/ハーネス正常化完了報告.md)。旧定義・監査・整理前の本文は[修正モデルの履歴](archive/ハーネス整合性修正モデル.md)に保管する。

## 1. 形式・型と外部仕様

評価データの説明上の型名に対応する新しいクラスを追加したわけではない。実装は主にdict/list/tupleであり、全形式を網羅する独立JSON Schemaはない。refereeのRows/Position/BoardRows/Resultは静的な型aliasであり、盤面形状・値域の実行時検証や正式資格の証明を代替しない。

| データ | 生成元 → 消費先 | 現行の表現・境界 |
|---|---|---|
| Rows / Position | referee・openings・AI応答 → evaluate/contracts | 8行×8文字B/W/E。メモリtuple、JSON配列。座標はx列・y行、0～7。harnessのpassはNone |
| OpeningPopulation / SelectionManifest | openings → evaluate/contracts | 母集合/識別規則/抽出algorithmはv1。合法履歴・N/hash・重複・抽出を再検証 |
| BudgetConfig | search_budget → producer/検証器 | v1入力も対応しv2へ正規化。kindはunknown/none/time/nodes/depth。未指定はunknownで、none適用除外とは別。timeの上限は整数ns |
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
| pair_id | contracts.pair_id_forで照合。evaluateの取得側は同じpayloadを_hash_payloadで生成。pair_version=1、case_id、A/B seeds、role_swap_version=1 | 同じケース・役割seedの先後2局。run・leg・予算・AI版を含めず、前後比較でも対応付ける |
| run_id | evaluateのUUID | 1取得を識別。条件一致を意味しない |
| clock_domain_id | 取得run共通の時計比較scope | tokenだけで時計の比較可能性を証明しない。別runの絶対時刻を比較しない |
| process_session_id / process_generation | Popen試行のsessionと世代 | 同一run全体でsession一意。現取得はgeneration=0、同slot途中restart継続は正式対象として実装しない |
| command_id | protocol.command_id。run/purpose/pair/leg/role/session/generation/indexの結合 | 全commandの位置・実文字列・case/role seedを期待contextへ照合。probeとgameを混ぜない |
| experiment_id / evaluation_fingerprint | evaluate。conditions/runtime/targets/明示assets/取得コード | confidence等の解析条件と分離。seeded_playerも取得依存へ登録。全AI importの自動登録ではない |
| analysis_id / analysis_fingerprint | 新規取得はevaluate、再解析/比較はanalysis._analysis_lineage（詳細は下記） | confidenceは解析条件。取得時IDと再解析IDを同一の生成式とみなさず、原取得証拠を変更しない |
| board_sha256 | search_budget。要求前の行を改行連結したASCII bytesのSHA-256 | traceのgenmove要求前盤面と対応。JSON hashやcase/orbit IDとは異なる |

analysisの識別規則は経路で異なる。
- 新規取得のanalysis_fingerprintはanalysis.py/statistics.pyのhashとmethod/confidenceから生成し、analysis_idはexperiment_id・そのfingerprint・analysis_specから生成する。
- 再解析/比較のanalysis_fingerprintは解析コードhash・spec・fresh検証版/fingerprintを含み、analysis_idはsource(s)（原report hash等）とそのfingerprintから生成する。取得側と再解析側のIDが同じになる保証はない。
- _harness_infoの取得fingerprint対象はevaluate/protocol/referee/openings/search_budget/contracts/seeded_player。analysis/statisticsは解析fingerprint側で識別する。

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

新規取得のmeasurement_contractは`local_cpython313_perf_ns_lifecycle_v2`。そのclock_status登録はlocal CPython3.13.5/Windows/QueryPerformanceCounter、有効なqpc_frequency_hz、monotonic/adjustable等の属性を要求する。v2の別patch版はunknown。元のdescriptor・domain・session・計測threadを確認してから比較し、再解析環境の時計で原記録を書き換えない。

低水準clock_statusは旧`local_cpython313_perf_ns_enclosed_v1` descriptorも互換判定する（CPython3.13/Windows/QPC）。v2のpatch/周波数検査をそのままv1へ適用する実装ではない。異process辺のtemporal_orderは別途有効周波数を要求する。conditionsのmeasurement_contract、descriptorの登録、host/child/runtimeの一致、source版と全lifecycle/正式資格は別々の検査であり、低水準verifiedだけで正式資格を保証しない。

整数nsは1ns精度を意味しない。同一計測threadは整数の順序を厳密に検査し、同値/0経過を許す。親子など異threadの辺は、`U=ceil(1_000_000_000/qpc_frequency_hz)+1`nsを用い、後点−前点が>Uならverified、<-Uならfailed、[-U,U]ならunknown。このUを猶予として緩めたり、別時計比較へ流用したりしない。未知契約・不足はunknown、各型/一致検査で確認できた矛盾はfailed。clock_statusは非dict descriptorをunknownとする一方、temporal_orderは提供された非整数端点を時計不足より先にfailedとする。全不正型を一律に同じ状態へ分類する実装ではない。

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
| T-CMD | quit以外はHE0≤HS≤CC≤CR≤HR≤HE1。genmoveイベントではCC≤SS≤SE≤CRも要求。quitには応答CR/HR/HE1を要求しない |
| T-SERIAL | 親の前HE1≤次HE0、子の前CR≤次CC。正常終了の最後はquit。quitの親終点はEOで、quit後のcommandを認めない |
| T-EXIT | 正常quitのHE0q≤HSq≤CCq≤CS≤EO。子イベントはCSまで、通常host応答はquit前 |
| T-LOAD | startupはCE≤load_start≤load_end≤IR。対応command loadはCC〜CR内、使用モデルはload_end≤SS |
| T-DIFF | 通常要求はsearch=SE−SS、wire=HR−HS、external=HE1−HE0。quitのexternalはEO−HE0でwireなし。端点/差は整数ns、secondsは派生数値。bool端点や負の同thread差を認めない |
| T-CAUSE | collectorの予定順からA name→B spawn、actor genmove→peer play、同期検査、quit等を再構成。保存時刻をソートして正当化しない |
| T-GATE | 必須時間各軸と他の品質/統計条件のAND。時間verifiedだけ、hash一致、n=N、取得completeでは正式不可 |

親はready前にnameを送ってpipe待ちになってよい。Popen戻り前の子開始も正常にあり得る。異常終了・timeout/killでは正常quitの証拠を補造せず、取得済み点と診断を残す。子の終了後イベントと、終了後の親による既存pipe出力の回収を混同しない。

### 5.1 Phase 5の型境界（設計先行・実装反映済み）

承認された最小変更の設計をコードより先に記録し、現在のprotocol.pyへ反映した。検証の到達状態はQUALITY_PROGRESS.mdのPhase 5を参照する。

- PIPE指定の成功時不変条件を型だけで断定せず、readline/write/flush/closeの各境界で非Noneを確認する。欠損は既存と同じ操作名のAttributeError（message/name/objを維持）であり、Noneのskip・成功への置換・新しい正常終了状態を認めない。tracebackの行/フレームは注釈・helper導入によって変わり得る。
- SP/PR、既存の全perf_counter(_ns)呼出、EOとcleanup_endの順序・所属・計測範囲は維持する。境界確認は採時せず、送信側の処理時間は外部/wire区間に含める。整数nsの実測値完全一致や性能不変を要求する契約ではない。
- OS条件をsys.platformで表現し、Windowsは従来のtaskkill/必要時kill、それ以外は既存POSIX停止分岐とする。実Windowsの回収と模擬POSIX分岐を区別し、POSIX実機や未知OSの正式対応まで保証しない。
- 未検証のcontext/lifecycleは自由拡張のobject辞書として保持し、型付けのために再構築・正規化・キー削除しない。終了観測後の転記1か所のcastは通常生成形状の静的ビューであって、原時刻が検証済みという証明ではない。旧データ・改変データは従来どおり共通正式入口で再検証する。
- 時計登録・整数端点・正の有限期限の型ガードは既存の判定値域と短絡拒否を維持する。v1で未検証の周波数/patch metadataをintと過剰保証しない。bool端点や非有限期限を受理せず、QPC曖昧幅・unknown/failed・schema5/trace4/validation3・正式ANDを変更しない。

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

search_onlyという名称だけで実AIの純粋な探索範囲を保証しない。現行budget_playerのSS〜SEは合法手列挙・盤面適用も含み、模擬counterである。モデル読込、JSONL書込、stdout/queue・OS待ちはfixtureの探索端点の外で、外部応答には含まれる。実AIの予定scopeと同名だけで同等扱いしない。

モデルなしは明示not_required。trace4 headerのload検査窓はCE〜IR、command eventのload検査窓はCC〜CRで、command loadにはload_end≤SSも要求する。fixtureが実際に行うのはstartup read_bytesであり、command loadではない。headerの初期load証拠も別に必要なため、初回genmoveで初めてloadするlazy方式を、そのまま正式対応済みとしない。未知scope/background/GPUは必要な証拠・同期契約・テストなしにverifiedへしない。

## 7. 観測・集計・状態

contracts.inspect_evaluationは原report snapshotと同一呼出内のartifact bytes cacheを使い、母集合/棋譜/両通信/rawと保存cache/summaryを再照合する。取得summary前だけprivate _acquisition_summaryが保存summary照合なしのcoreを使う。公開skip flagはない。原証拠や不正summaryを正しい派生値で黙って置き換えない。

順序は取得→_acquisition_summary→summary設定→analysis.estimateのfresh検証→statistics/status設定→complete checkpoint→API return→CLIのfresh assess→final writer。保存済みsourceの再解析はsource JSONと原証跡を別に読み直す。checkpointの参考summarize、canonical summary、final保存成功を同一状態として扱わない。

| 状態・結果 | 意味 |
|---|---|
| verified / unknown / failed | 必要集合の検査完了 / 証拠不足・未対応・順序曖昧 / 確認済み矛盾。failed優先でunknown理由も保持 |
| exempt / not_required | 許可済みB内部予算や必要集合で除外した項目だけ。未起動roleやlifecycle全体へ拡張しない |
| game complete | 合法終局を得た実行状態。後続通信/quit/検証/保存成功とは別 |
| reference_available | 現行正常対局の参考集計可否。正式資格ではない。旧資料は互換分岐と保証不足を明示 |
| formal eligible / ineligible / not_requested | 品質・統計条件による資格。不成立と要求なしを区別 |
| statistics available / descriptive_only / unavailable | 正式資格＋数式computedで正式区間 / valid入力でCI未要求 / 正式計算不可。descriptive_onlyだけでreference_availableは保証しない |
| 保存成功/失敗、decision undetermined | writerの戻り値/ArtifactWriteErrorで判定。publicationを評価JSONの共通status fieldとして保存していない。decisionはevaluationのstatus内・comparisonのrootで、analysisには同じfieldなし |

参考分母は正常終局・通信verifiedの完了局。勝率=wins/completed、勝ち点率=(wins+draws/2)/completed、0完了はnull。A勝1・分0.5・負0で、色B/Wと役割A/Bを区別する。不正手/通信失敗を相手の勝ちに変換しない。

RTTは成功したgenmove通信の平均・最大で、失敗局に含まれる成功問い合わせやpass/terminal probeも対象となる。内部全genmoveとnormal_moveは別に集計し、内部sum/max nsとsample数をrawから再計算する。勝敗率の正常完了分母とは別の標本である。実AItraceなしは内部平均null/unknown。予算・測定・時系列のrole→game→report階層の保存矛盾も拒否する。

## 8. 正式資格と統計の推定対象

正式単独評価はconfirmatory/source schema5、母集合・抽出・全予定coverage・棋譜/通信・予算・時間・測定・source整合の全必須verified、固定nodes/depthと決定性条件等をANDで要求する。必要集合はprofile/policyから導出し、rawの自己申告除外を読んで緩和しない。

推定対象は保存された固定有限序盤母集合でのA先後平均得点、対応比較は同caseのafter−before平均差。先後2局で1ペア、単独range[0,1]・差range[-1,1]。実装はHoeffding–Serfling有限母集合bound、資格を満たす全数ではcensus。母集合生成分布を未知の全局面分布と同一視しない。

statistics単独APIはcomputed/arithmetic_only/not_assessedで、母集合やrawの証明書ではない。検証済みNと派生gameだけをanalysisから渡す。bootstrapや非決定AIの反復分布推定は現行方式ではなく、追加設計が必要。

品質検証、数式計算、数値基準met/not_met、採用decisionを分離する。取得evaluationのperformance_criterionはconfirmatoryでpoint_rate≥0.55かつ区間lower>0.5ならmet、区間なしはundetermined、他profileはnot_requestedという現在の固定規則。comparisonのimprovement_criterionは対応差のlower>0ならmet。これらは自動採用ではなく、数値基準未達だけでCLIを失敗にしない。再解析analysisは元evaluationのperformance_criterionを同じfieldへ再発行しない。

事前固定基準・未閲覧held_out・学習非混入・任意停止/多重比較の運用は検証器だけで保証しない。新しい採用基準を導入するなら、現在の固定規則との違いを明示する。

## 9. 保存・互換性・再解析

最終reportはCLIが排他新規作成・flush/fsyncする。evaluate APIはraw/母集合/checkpointを保存するがfinal JSONは返却辞書であり、final writerはCLI/呼出元の責務。純粋なanalysis APIは解析辞書を返す。checkpointのatomic replaceはrun所有領域だけで、既存finalを置換しない。取得完了と保存成功は別。途中ファイルや元の失敗を成功へ修復せず、共通保存例外のphase/path/causeを残す。

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

意味・構造・契約を変更するときは、実装前に図と本書を更新・照合し、実装後に双方向の対応を検証する。型注釈・format等で意味に影響しない場合は、図の更新不要の理由を品質進捗へ記録する。

## 12. 時間・公開入口契約の索引

時間の正本は第5・6節、必要集合/状態/資格は第7・8節。実行経路は[現行構造](harness_architecture.md)。公開入口は原evaluation reportと原証跡を受ける。

| 入口 | 共通核・役割 |
|---|---|
| contracts.inspect_evaluation | 毎回fresh検証。lifecycleを含む4時間軸、raw意味、全cacheを照合 |
| analysis.assess_evaluation / statistical_eligibility / estimate / reanalysis_report | fresh Inspection→private _assess_inspection。保存Inspection/Assessmentを認可しない |
| analysis.assess_comparison / paired / compare / comparison_report | 両原reportのfresh検証→private _assess_pair_inspections。単独資格＋相互条件を要求 |
| evaluate.compare_reports、evaluate/analysis CLI | 同じanalysis経路を使用。独自の保存flag資格判定をしない |

comparison_reportのcaller resultはfresh結果との一致確認だけで、予約fieldを上書きしない。unknown/failedを許可する互換モードはない。内部private関数の改造や全原証拠の整合した偽造を防ぐ認証機構ではない。

## 13. 開発用品質報告（実装前に確定した別契約）

品質runnerの結果は`report_kind=quality_gate / format_version=1`であり、evaluation/analysis/comparison schema5ではない。品質passedを正式評価のInspection/Assessmentへ渡さない。

| データ | 生成・利用 | 不変条件 |
|---|---|---|
| Step | tools.quality.required_steps→run_gate/run_step | 必須command名・argv・timeout。公開CLIに成功を偽装するskip選択を設けない |
| StepResult | 実プロセスまたは環境/モデル検査→集約 | name/command/exit_code/seconds/log/error/passedを保持。nonzero・不在・timeout・未実行はpassed=false。負値や未測定を推測補完しない |
| QualityReport | run_gate→新規quality.json | 全必須の結果、環境・依存版・開始/終了source hash・model_errors。passedは全必須成功とsnapshot一致を要求し、保存失敗ではCLI0を返さない |
| 原テスト/対局資産 | 既存pytest/smoke→各新規証拠 | 既存評価ファイルを上書きせず、失敗・不明値を成功へ補修しない |

strict型範囲はpyproject.tomlの明示files、format未導入範囲と意図的fixture E402は同設定とQUALITY_PROGRESS.mdに記録する。全体の未型付け箇所をAny/ignoreでstrict対象に見せない。
実行不能な環境・異なるtool版は品質gate失敗として明示し、harnessのunknown/failed状態を変更しない。対応環境は直接CPython3.13.5/Windows、venvランチャーは未対応。CI未実行やブラウザ未確認をローカルgate成功から保証しない。

source_hashesは開始時、source_hashes_endは終了時の対象コード・設定・モデル文書。変化はsnapshot検査の失敗として保持し、終了時の新しいhashだけで成功へ置き換えない。全時間・状態・呼出順の証明や、2観測間の改ざん認証ではない。実行中の編集を避ける。

### 13.1 自動検証証拠（先行契約後に実装、2026-10-14）

実装前に品質図と同時に確定する追加契約。旧9品質検査と判定は維持し、必須evidence検査を追加する。QualityReport v1のfield構造は変更しない。評価schema5、raw trace4、validation3、正式資格と無関係な開発証拠である。

| データ | 契約 |
|---|---|
| Evidence manifest | evidence format_version=1。UUID run_id、step_id=run_id:name、Git commit/dirty、argv/選択/timeout、限定環境、開始終了source hash・設定/モデル/課題仕様hash、開始終了UTCを保存。Git不可はunknown理由、内容hashをcommitの代用保証にしない |
| JUnit | pytest.xmlとsmoke.xmlは別step新規領域。標準pytestが生成した結果を使い、既存ファイルを上書きしない。error/failed/skipped、集計、時間、診断を保存。XMLだけから段階や未収集入力を推測しない |
| Journal | tools.evidence_pluginによるrun/step/nodeid/sequence付きJSONL。開始、phase、明示期待/観測、原成果物参照、終了、session終了を記録。逐次flush、最終sessionはfsync。欠損/破損/重複/別run混入をcompleteにしない |
| Test evidence | test_id=nodeid、execution_id=step_id:nodeid。同じtestのfull/smokeを分離。setup/call/teardownの最終outcomeとXMLを照合し、未完はinterrupted。skipはpassedと別で、全skipや未収集を成功の証明にしない |
| 明示check | 期待を対象呼出より前に登録、観測はassertより前に登録。期待種別=fixed/mathematical/independent_referee/consistency。input/expected/observations、根拠のあるmodel/contract/implementation参照。collectorが再判定してpytest結果を変更しない |
| 原成果物参照 | 明示登録したcheckpoint/run_id、母集合/rawのpath/hash。登録時と集約時のbytes・run対応を確認。保存flag/hashから正式資格を発行せず、原ファイルの修復/無条件複製をしない |
| collection_status | complete/incomplete。正常失敗やskipでも整合した記録はcompleteにできる。実行されたstepのXML/journal欠損・破損・対応不一致、成功重要testの記録不足はincomplete。未実行はnot_executedで明示 |
| publication | 派生JSON/Markdown/qualityは新規pendingをflush/fsync/closeした後にos.linkで排他公開し、qualityを最後に公開。証拠にquality予定bytes hashを記録し、その一致が完成条件。fsync失敗では完成名を生成しない。保存失敗時はevidence検査をfailedにしてqualityを書ける範囲で保存。hardlink不対応は失敗、既存winnerを置換しない。旧予定hash不一致/quality欠損は部分成果物 |

原品質検査の成功、各テスト結果、収集完全性、保存成否、全体gate成功、AI正式資格は別である。証拠失敗はテスト結果を変えずgateを失敗にする。失敗テストの期待/観測が途中で欠けた場合は未取得理由を残し、成功理由や欠損時刻を推測で補わない。

journal最終行の後のfsync/close障害はstepログの予約markerも照合して不完全とする。全skip/テストなしの終了0だけでは検証成功を証明しない。ログ/JUnit/journal/Markdownのhashとquality完成markerの照合APIは原bytesの対応確認であり認証・正式意味検証ではない。

JUnitのtests件数は常にtestcase要素数とは一致しない。pytest8.4.2はcall成功＋teardown失敗でpassed/errorを加算し、call失敗＋teardown失敗には二重失敗補正を行う。collectorはjournal段階からこの標準集計を再構成し、XML要素数だけと比較して正常失敗を拒否しない。error/failure/skipped各数と実nodeid/段階の照合も維持する。

完成名への直接write+fsyncは採用しない（fsync失敗でも予定hashの全bytesが残る反例）。pendingは新規作成に成功したownedファイルだけを回収し、回収不能は警告。os.link公開後のdirectory fsync/電源断まで保証しない。全step（内部検査含む）の識別・結果とlog hashを実行単位へ集約し、per-testへ重複させない。

publicationはfsynced_pending_exclusive_link_then_matching_quality_jsonを記録する。旧直接write方式の試作証拠はこの新公開プロトコルの証拠として昇格せず、verify_publicationでpublication_protocol_unverifiedとする。原品質JSON v1/原評価schema5の読込契約は変更しない。

collectorは容量制限を設け、診断を切り詰めた場合は原JUnit/log参照と切り詰めを明示する。全環境変数・任意localを収集しない。明示metadataは秘密キーを拒否しサイズ制限を適用する。既存原ログの無制限安全性や匿名性を保証しない。並列worker収集は現段階では未対応、qualityは従来どおり直列。直接pytestはplugin指定時だけ証拠収集する。

回帰対象は成功/失敗/skip/setup/teardown/collection、crash/timeout、XML/journal欠損・破損・別ID、原hash/run不一致、保存不能、同test複数step、既存出力保全、正式資格非干渉である。モデル→実装と実装→モデルの対応表・実結果はQUALITY_PROGRESS.mdへ残す。

### 13.2 journal状態遷移契約（先行確定後に実装）

format_versionは厳密な整数1、kindはsession_start/session_finish/collection/selected/test_start/phase/test_finish/expectation/observation/artifactのいずれかとする。未対応形式を無視してcompleteにしない。未取得端点や終了を補完せず、元journalは保存したまま検査する。

| 不変条件 | 検証規則 |
|---|---|
| session | 最初に開始1回、最後に終了1回。中間の再開始/終了、終了後イベントは不可 |
| collection/selected | collectionは選択確定前のみ。selectedは1回、nodeidsは空でない文字列の重複なし配列（配列自体は空を許可）。全test開始はselected後、選択されたnodeidのみ |
| 直列実行 | active testは最大1件。同一nodeidの開始/終了は各1回。別testとの交差や終了済みtestの再開は禁止 |
| phase/終了 | setup→call→teardown後にtest_finish。setupがfailed/skippedならcallなしのsetup→teardownも許可。早期終了、終了後phase、開始前phaseを拒否 |
| 明示metadata | expectation/observation/artifactは対象nodeidがactiveな区間内のみ。setup報告前等のfixture内登録や同一checkの複数観測は許可。観測が期待より後という既存規則も維持 |
| 欠損・未知・矛盾 | 既存のrun/step/sequence・JUnit照合に加えて状態違反理由を保持しcollection_status=incomplete。旧version/未対応versionも証拠不足であり自動昇格しない |
| 判定伝播 | collect_step→collect→run_gateの既存経路でevidence検査を失敗にする。記録されたpytest outcome/exitと証拠完全性を別に保ち、元assertや正式資格を変更しない |

正常なテスト失敗、skip、collection失敗は記録が整合すればcompleteにできる。crash/timeoutの部分記録は診断用として保持しcompleteにしない。verify_publicationの空エラー配列はbytes対応であり、JL検査の合格や元記録の真正性を表さない。並列worker/rerunは既存どおり非対応。保存構造v1は維持し、従来正常なv1を受理して不正な状態遷移だけを排除する。
