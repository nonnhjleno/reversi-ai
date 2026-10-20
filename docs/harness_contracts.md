# ハーネスのデータ契約と不変条件

更新日: 2026-10-14。評価契約の基準: 8712843＋品質導入の非意味的lint整理・型注釈。現行実装はevaluation/analysis/comparison schema5、trace4、validation3。第13節は実装に先行して確定した開発用品質契約。旧schemaや品質ゲートの成功を正式AI評価資格に読み替えない。

外部コマンド・盤面・時間制限の正本は[課題仕様](プログラム開発方法.md)、実行方法は[ハーネス.md](ハーネス.md)、責務と図は[harness_architecture.md](harness_architecture.md)。実行確認と残件は[正常化完了報告](reports/ハーネス正常化完了報告.md)。旧定義・監査・整理前の本文は[修正モデルの履歴](archive/ハーネス整合性修正モデル.md)に保管する。

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

### Phase 0で追加する開発資産の識別規則（評価schema変更なし・正本）

| 資産 | 固定条件・生成元 | 保証と限界 |
|---|---|---|
| `random_v0` | `reversi_player/reversi.py`を`baselines/random_v0/reversi.py`へbyte一致で複製。manifestに元/複製のSHA-256、元Git commit、Python条件、実行時依存を記録 | 実体と由来の同一性を再照合できる。hash単独は復元・署名・将来の正式資格を保証しない。Python標準ライブラリ以外のruntime importはないが、CPython版・OS条件は別途固定する |
| 開発用序盤母集団 | `harness.openings`のPopulation v1。purpose=`development`、生成seed・placements・generator版/Python・ソースhash・population hashを保存 | 合法履歴、D4対称重複、内容hashは`validate_population`で検証する。全合法序盤から一様抽出した集合ではない |
| 最終評価用序盤母集団 | Population v1。purpose=`held_out`、別seed、開発用母集団のorbitを除外し、そのhashをgenerator metadataに保存 | Phase 0では対局・調整に使わない。purpose/hashは分離の記録であって、秘匿・未閲覧・学習非混入を技術的に保証しない |
| Phase 0 smoke | `harness.evaluate`でbaselineをA/Bに指定、2局、同一script hash、共通master seed、予算設定なし、smoke profile。独立process・先後交換・初期盤面を使用 | 正常完了・盤面同期等の参考動作確認。random AIはtrace4未対応のため正式探索予算のverifiedや正式な強さ比較を意味しない |

これらは開発計画上の保全・識別契約であり、evaluation/analysis/comparison schema5、trace4、validation3、正式適格条件、品質ゲートの判定を変更しない。固定資産のbytesをGit保存・復元でも保つため、`.gitattributes`で`baselines/random_v0/`と`benchmark_sets/phase0/`の自動改行変換を無効にする。既存図のbyte一致・hash照合を保つための保存規則であり、新しい正式評価経路は追加しない。ファイル配置やseedを変えた場合はmanifestとPhase 0報告を一緒に更新し、保存済み評価JSONを編集して整合させない。具体的な固定値・実ファイルhash・実行IDは[`benchmark_sets/phase0/manifest.json`](../benchmark_sets/phase0/manifest.json)と[Phase 0報告](reports/20261016_phase0.md)に記録し、契約定義と実験インスタンスを分離する。

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

### 6.1 Phase 6の型境界（設計先行・実装反映済み）

search_budgetの厳格型付けは、新しい測定・検証規則ではない。既存の厳密int（boolを含めない）、有限正数、欠損と不正の優先順、loadと探索の非重複を、型ガードと検査後の局所再束縛へ表現する。v1→v2の厳密ns変換、search辞書の共有参照、理由文字列と例外の扱いを維持する。

原traceの未知fieldやsearch metadataはobjectであり、TypedDictの通常形状や局所的な型ビューを正式検証の証拠としない。未検証値を数値へ変換・補充せず、既存の不正入力を同じ失敗へ導く。必要な局所castは、既存の検査後の型関係または既存の演算要求だけに限定し、原辞書全体を有効traceへcastしない。型のために新しい正常化・許容差・例外握り潰しを導入しない。

実装のcastは6か所に限定する。temporal_orderが生成した不成立理由codeのstrビュー1、kindと対応済みwork limitキー1、既存load/search演算の端点ビュー4である。後者は原値のint変換や検査完了の宣言ではなく、既存の比較演算の入力制約を表すだけ。不正なraw operandによるTypeErrorも従来どおり直接呼出では伝播し、trace wrapperではfailedとなる。型のために新たな成功状態へ変えない。

全公開入口は従来どおりfresh検証を通る。取得/保存再解析・schema5/trace4/validation3・正式AND・予算policyの適用範囲は変更しない。正常系に加え、bool/欠損/逆転/不正search区間/過剰metadata保証の回帰と元ソースとの出力・例外比較で照合する。型introspectionや不正な辞書subclassの副作用までは新たな互換保証に加えない。

## 7. 観測・集計・状態

Phase 7の型契約（先行設計後に実装）: contractsの通常入力形状と生成途中/返却Inspectionの型を記述しても、原資料や保存状態を検証済みとはしない。既存のbool除外・有限数・欠損/矛盾・失敗優先を型ガード/検査後の局所束縛へ接続するだけとする。既知のfield型を持つ通常形状と、未検証metadata/返却snapshotのobjectを区別し、後者へ数値型や正式資格を過剰保証しない。

追加の型境界設計: 全体castで時間結果を返す案は採用せず、既存4軸の順番と参照を保つ型付き辞書として構築する。基礎時間理由の列に拡張reasonを格納し、detail/slot情報を削除しない。既存のkeyword updateは、内部生成の同じキー順/値を持つ辞書引数へ表現し直す。Noneのgetアクセスは元と同じAttributeError（message/name/obj）を送出し、空辞書への置換や新しい成功状態を作らない。追加helperのtracebackフレーム/実行コストの一致は保証しない。

直接依存search_budgetのcommand引数は、実際に行う読み取りだけを表すSequence[_CommandRecord]へ型注釈のみ調整する。phaseは未知値objectとしてgetで読む既存処理を維持し、返却用_BudgetCommandの型や実行本体は変更しない。これはPhase 7の相互運用のための既存interfaceの型精緻化であり、別フェーズの実装や予算/通信契約の変更ではない。原command列や全時間結果をcastして接続する方法を避ける。

基礎protocol.temporal_resultも、実際に書き換えない理由列をSequence[Mapping[str, object]]として受け取り、その同じ列を返す型へ注釈だけ精緻化する。statusは従来の3状態、理由metadataの内容はobjectとし、拡張detailの型保証を捏造しない。通常入力の実list・辞書と保存JSONの配列/キーは変えず、listの不変性を理由とする全体castやコピーを不要にする。この直接依存の型境界修正もPhase 7の相互運用に限定し、protocolの実行関数は変更しない。

型の通常形状は旧schemaを完全に記述する独立JSON Schemaではない。旧資料の期限欠落や参照用の空validation辞書も部分型で表し、必須証拠の欠損を有効値へ補充しない。整数nsの正式保証と、未検証cacheを読む参考reducerの演算結果を区別する。参考cacheの数値がfloatなら原値のまま扱うため、reducerのns集計の静的戻り値もint専用とは宣言しない。正式側の原trace整数制約や集計照合は従来どおり適用する。

時間理由のdetail、slot/session/command情報、途中測定値を保持する。局所的な型ビューが必要な場合も、生成済み構造/検査後の型関係/既存演算要求に根拠を限定し、原report/raw全体へのcastで代用しない。新しい欠損値補充、許容差、例外の置換、型のための追加成功/skip状態を導入しない。公開入口・旧schema・別root指定・保存例外・参考集計と正式集計の区別は不変で、対応回帰と旧実装比較で確認する。型専用定義は新しい保存schemaや証明書ではない。

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

### 7.1 Phase 8の型境界（先行設計を実装反映済み）

analysisは原入力の通常形状、fresh Inspection、Assessment、算術結果とserializerを区別する。snapshotやmetadataはobjectを維持し、原report/trace/生成結果全体のcastや型のためのデータ再構築を使わない。confidenceの厳密int/float判定と差分のall判定を同じ値域の型ガードへ接続し、bool除外・短絡順序・例外を保つ。未知のbudget要求の真偽評価も早期bool化せず、既存orの評価を維持する。

共有contractsの比較関数はMapping[str, object]の未知fieldを読む型へ合わせる。属性get/添字/反復の小さな境界は元と同じ操作を行い、正常値への変換・欠損補充・追加の認可をしない。局所ビューは属性method/標準operator.getitem・setitemの呼出形や既存scalar演算に限定する。operatorの型定義が要求する正常operandを原値へ強制castせず、同じ標準操作へ未知値を渡して従来の例外も残す。comparisonsのdeepcopy、lookup/短絡順、候補設定の変更対象は維持する。検証済みNやplanned countのscalarビューも既存正式分岐の前提に限り、資格入力に使わない。

derived.gamesは初期値[]か、schema3以後のdecodeでlist内の辞書と文字列keyを確認したgameのdeepcopyである。したがって共有型をSequence[Mapping[str, object]]へ精緻化できるが、各fieldはobjectのままとし、failed/unknownのgameを有効な得点/ID/予算へ昇格しない。旧schema1/2やdecode不正は初期値のままである。この生成根拠の回帰を用意する。statistics側の入力も同じ未検証Mappingへ接続し、case/ペアIDをstrへ変換しない。算術が返す理由/IDと既存の動的な得点演算境界は維持し、Anyを新しい入力全体へ拡大しない。

算術辞書と解析用の可変field集合を無理に同じTypedDictへcastしない。解析側は読取Mappingとして保持し、methodだけの局所ビューで同じ元辞書へupdateする。キーの順番/参照、既存writerへの伝播も維持する。helper呼出コストとtracebackフレームの完全一致は保証しない。通常JSONの辞書/配列と既存例外を照合対象とし、不正dict subclass/proxyの属性lookupや反復の副作用まで新たに保証しない。原入力、getの例外属性、confidence/要求/参考/正式の区別、算術辞書の同一性、旧資料/API/CLIを互換回帰と元実装比較で照合する。コードhash由来の解析/検証fingerprintは通常どおり変わり、旧保存hashを書き換えない。

## 8. 正式資格と統計の推定対象

### Phase 9の型境界（先行設計を実装反映済み）

先行設計に基づく共有型: 取得validationの必須fieldは弱めない。保存cacheの状態/理由/測定値は読取専用の通常形状とし、artifactだけは原値objectを保持する。生の親診断と正常evidenceが共通に持つhost fieldを共有し、stderr_tailも部分証拠として含む。生成中の辞書updateには元methodだけの境界を使い、原診断や算術結果を正常型へ全体castしない。先頭probeの不可能なNoneは元の添字TypeErrorと同じ失敗にし、空の正常caseを補わない。

取得中gameはscore_a=Noneやpair_id=Noneを持ち得る。序盤の原positionはobjectのまま、使う場所の反復/tuple操作だけを局所ビューで記述する。欠損traceのartifact.sha256=None、events/追加診断、probeの失敗で保存する部分host evidenceを正常完走へ昇格しない。HarnessError.diagnosticsとprobe attemptsの共有型を調整する場合は、親が実際に作る通常の部分証拠の形状として表し、lifecycle/拡張metadataの未知値や既存例外を維持する。新しいdecode条件や検証省略を追加しない。保存cacheのstatus/reason/events/measurementsはReadOnlyの通常形状とし、必須fieldを持つ取得_TraceValidationを緩めず受け渡す。artifactは両側ともobject、親host証拠のstderr_tail/phaseを基礎型で共有する。_FileInfoは実bytesから作る通常のpath/hash参照で、欠損traceの_Artifactとは区別する。

analysis.estimateが返すMappingのfieldは未知値であり、intervalが存在するだけで原report全体を有効とcastしない。evaluateの既存performance_criterionに必要な既存添字・scalar比較だけを限定境界とし、fresh検証→統計→数値基準→decision undeterminedの順序を維持する。原算術辞書・診断・取得報告をコピー/全体castして型差を隠さない。evaluateの8局所ビューはupdate method1、序盤の反復3と既存tuple座標1、既存数値基準のscalar2、CLIの生成済みID1だけ。直接依存contractsの4追加ビューは序盤反復/tupleと保存pathの既存操作に限り、既存33を維持して37とする。_callは同じ位置引数と元の返却値/例外を保ち、checkpointのJSON複製・flush/fsync/replaceと中断時の原例外/secondary noteも維持する。

実装前に元コードで互換回帰を実行し、設定省略/パス/不正値、局数/期限/信頼度、ラッパーの例外同一性、部分game/probeと原証拠、進捗と中断/保存障害を固定する。対象登録漏れ・型逃げ/未知値の過剰保証も検査する。型introspection、traceback追加フレーム、実行コスト完全一致は保証しない。schema5/trace4/validation3、正式AND、既存fingerprintの通常のソースhash更新と旧資料の非補修を維持する。

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

資料再配置の契約（2026-10-16、参照変更に先行して確定）: 課題仕様の正本はdocs/プログラム開発方法.mdへ移し、tools.quality._source_hashesはそのパスと実bytesを開始/終了ともに記録する。旧ルートパスをfallbackや別名キーとして残さず、新パスの編集・削除も既存snapshot照合で検出する。AGENTS.mdはルート、設計図・契約は既存のdocs/配下を維持する。manifest.model_contract_hashesは既存のdocs/接頭辞の抽出結果なので、仕様を含む3文書となる。QualityReport/Evidenceの形式、必須検査、snapshotの合否条件、AI正式資格は変更しない。過去のquality/evidence/trace内の記録パス/hashは当時の原証拠として保持し、今回の移動に合わせて修復しない。履歴Markdownのリンク先だけは新配置に追従させ、履歴本文や件数を現在の結果へ置き換えない。

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

### 13.A scopeと補助報告（2026-10-20先行契約）

- Fullはquality_gate v1のまま、既存10必須にtyping_scopeを加える。S=本番3ルートの全.py−{reversi_player/web.py}、既存14パスの保持、initializer登録、strict=true、正規filesの一意性、実在、stub/別名/未知配置/抑制設定なしを要求する。Lintの本番includeも維持する。scope成功は実mypy成功の代替ではない。
- 利用者確認済みのreversi_01.py・102400-games/data.pyは保管/参考用途。固定baselines、tests、依存環境、生成物は用途で区別する。他の本番例外を追加する手段は設けない。新用途・新ルートは契約レビューを要する。
- 補助報告はquality_feedback v1、feedback.jsonを最後に排他保存する。profile、routing理由、測定専用か、Git base/差分/未追跡、policy hash、全collection/選択/未選択nodeid、実argv/終了値、source開始終了hash、JUnit/journal再収集結果、skip、原参照と保管先hashを保存する。full_quality_passedはfalse固定。fallback時はrouting.jsonと別Full quality.jsonを保存し、feedback.jsonへ再公開しない。
- Quickは監査の190候補、Standard_AIは212候補を出発点とする。固定5 AI/盤面/入出力ファイル全件、明示契約基底名、新しいfeedback/scope回帰を選ぶ。基底名の欠損、重複、空、collect-onlyと実行collectionの差、journal/JUnit/予定の差、skipは補助成功にしない。件数は将来増加し得る。Standardはschema5 CLI/正式入口の正常系、独立対局smoke相当、失敗回収・期限も含むが、Full全異常変異を代替しない。
- 差分はGit baseと現在の追跡差分および未追跡を照合し、AI本体/選択器・既知の専用テスト・資料以外、rename/delete、新規Python、共有fixture、設定、品質基盤、ハーネス変更やGit情報不明ならFullへfallbackする。measure-profileは開発者による独立計測専用で、未知変更を安全と認定するものではない。
- collect_stepを補助testの再収集に再利用するが、quality専用collect/verify_publicationの完成markerは使わない。verify_feedbackは保存計画とpolicyから再選択し、原journal/JUnitを再収集、保存summary・実行終了値・選択集合・原参照bytesを照合する。hash再計算だけで不正summaryを通さない。公開済み証拠を修復しない。
- feedbackが参照した原証拠は取得後に同一bytesをrun配下へ排他コピーし、元path/hashと保管path/hashを双方保持する。journalの原pathを書き換えず、再検証時の読取先だけ同じhashの保管先に解決する。保存先はevaluation_resultsのUUID領域、明示削除するまで保持するが、バックアップ/永続保存は保証しない。欠損・不一致は再検証失敗であり、保存当時の成功を後から修復しない。CIのupload対象/保持7日は今回変更しない。

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

### 13.3 型probeの共有実行契約（B1/B2、2026-10-20先行設計）

- 対象は既存9件のpromoted module自己テストのみ。9パラメータとnode IDを維持し、source bytesと設定を固定した9shadowをmodule-scopeのfresh mypy 1回へ渡す。既存typing工程・17strict登録・Full 11工程・正式評価条件は変更しない。
- 診断の期待は各shadow末尾の未注釈関数のパス・AST行・`no-untyped-def`。終了値1に加え全9診断の過不足なしを要求し、各testのパス/行/codeも一致させる。余剰・欠落・重複・別code・別行・異常終了・stderrエラーは失敗。共有setup失敗は成功へ変換しない。
- Fullでの原出力/argv/source・設定hash/shadow bytes/case診断はowned step下`typing-probes`へ新規保存し、case別の期待と観測をjournalへ保存する。限定pytestでは独立tmp領域を使う。共有ファイルをcase側で書き換えない。hashは原出力の意味検証や真正性の証明ではない。
- 他の17型probeはpositive/negativeの別起動と従来の期待を維持する。B2はまず原診断・拒否行列・fixture/inspectionのparse/replay費用を計測し、追加最適化は効果と不変条件を確認して判断する。inspectionを公開入口間でcacheしたり保存Inspectionを信頼する変更は行わない。
- 計測用hook/cProfileの時間は正式時計/期限合格やFull合格へ転用しない。診断欠落を全9パス別に試し、実mypyでshadow・明示rootを外した反例でも残るnonzeroを合格にしない。before/after診断と全既存node ID、Full証拠・独立smokeの一致を完了条件とする。

実装・計測状況は[Bの実装記録](reports/20261020_quality_probe_optimization.md)を参照する。

<a id="phase1a-boundary"></a>

## 14. Phase 1AのAI内部境界（実装済み・2026-10-19確認）

> Phase 1Bのselector契約・固定重み・比較実験条件は[事前登録設計](phase1_handoff.md)に定義し、位置評価selectorは実装済みである。これは本書の正式評価schema、budget/trace資格、統計・採用判定契約を変更しない。`descriptive`評価の完走成績は参考値に限定する。実装検証は[Phase 1B実装報告](phase1_handoff.md)を参照する。

> 実装確認: 下記の選択器境界は`reversi_player/ai_engine.py`と`reversi_player/reversi.py`に反映済み。実行証拠と失敗条件の回帰結果は[Phase 1A実装報告](phase1_handoff.md)を参照。ハーネス正式評価契約、trace schema、探索予算の保証を追加したものではない。

設計日: 2026-10-19。[AI分離設計第13節](リバーシAI分離設計.md#phase1a-design)を選択器の型・意味・失敗条件の正本とする。これは今後のAI内部呼出契約であり、ハーネスschema5／trace4／validation3、正式資格、stdoutの契約を変更しない。

| 境界 | Phase 1Aで保証・検証する条件 | 現行評価との関係 |
|---|---|---|
| 入力の生成 | Boardが現在の実盤面・指定色の合法手を既存y/x順で生成し、不変のrowsと座標tupleへ変換 | refereeをAIへ共用しない。root候補生成を探索実装と呼ばない |
| 選択の呼出 | 非空候補で3位置引数を1回渡す。空候補・不正色では非呼出。未使用context／予算引数は設けない | passと終局は既存resultで区別。外部NGの形式は維持 |
| 選択の結果 | engineはmoveまたは空候補時のNoneのみ。本体は厳密なtuple／int、範囲、候補所属を確認して既存_playを1回だけ呼ぶ | 不正な非空候補の結果をpass・正常NG・勝敗へ置換しない |
| 乱数 | Python module randomを共有し、非空候補のshuffleは従来と同じ順序・回数。stateをseed／復元／別Generatorへ置換しない | 同じruntime・seed・盤面・履歴なら棋譜と次の乱数状態を照合。無seedの別起動の同一棋譜や時間一致は保証しない |
| 故障 | engine例外は伝播。不正戻り値・_play失敗はRuntimeError。通常CLIはstderrと非0終了で既存harnessへ通知 | 新しい正式公開入口や資格状態を作らない。任意の不正callableの外部副作用を巻き戻す保証ではない |
| 明示資産 | 実装後の候補Aには`reversi_player/ai_engine.py`を`--a-asset`で登録。変更前Bは独立したrandom_v0の実体を使用 | 全importの自動登録は保証外。固定Bを候補側engineへ依存させない |
| 評価と証拠 | 分離前後の棋譜・盤面・RNG stateの互換回帰と2局smokeを保存。実装後に全品質ゲートを実行 | 参考互換性の確認であり正式CI・内部予算・探索時間の認証ではない。100局の位置評価比較はPhase 1Bの別工程 |

将来の探索callbackは実Boardに束縛しないコピー用ルール接続とし、AIから本体への逆importを増やさない。予算・clock・metrics・model loadはPhase 1Aの3引数境界に自己申告値を追加して認証せず、該当Phaseで実producerと既存の正式ANDを照合する。型成功はrowsの合法性、intからのbool除外、原証拠の意味的正しさの代用ではない。
