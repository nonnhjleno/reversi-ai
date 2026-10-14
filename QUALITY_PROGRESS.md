# 品質基盤導入の進捗と検証証拠

開始日: 2026-10-14。開始commit: `8712843ee81ac6fbf7ce2ab2d9e8da8f54b298ae`。
開始時Gitはclean、mainとorigin/mainは一致。commit・push・branch操作は行わない。

## 達成状況

| Goal | 状態 | 完了条件と現状 |
|---|---|---|
| 0 現状監査 | PASS | 全341件、global/venv独立smoke、対局基準、モデル照合を記録 |
| 1 lint / format | PASS | 全現行Pythonのlint・限定format、関連回帰を検証 |
| 2 strict型チェック | PASS | 2独立モジュールstrict、AST・53回帰・固定seed対局一致 |
| 3 回帰テスト | PASS | 追加25ケースと全366件成功、既存正式入口回帰を保全 |
| 4 不変条件 | PASS | 40例×2と明示例、既存53回帰・smokeを反復検証 |
| 5 統合ゲート | PASS | 実repoで2回成功、395全pytest・独立smoke・全9必須・snapshot一致 |

状態はPASS / FAIL / BLOCKED / NOT_STARTED。NOT_STARTEDの作業中は本文で区別する。
探索・評価関数・通信仕様・schema5 / trace4 / validation3を変更しない。

## Goal 0: 調査と基準

- Python: CPython 3.13.5 / Windows 11。pytest 8.4.2。Ruff / mypy / Hypothesisは未導入。
- 本体・GUI・ハーネスは標準ライブラリ。開発依存はrequirements-dev.txtのpytestのみ。
- README、pyproject.toml、既存lint/type設定、CI、下位AGENTS.mdは存在しない。
- 仕様正本はプログラム開発方法.md、モデル正本はdocs/harness_architecture.md、契約はdocs/harness_contracts.md。
- 実AIはランダム選択で内部予算trace未対応。探索結果型や学習モデルを実装済みと仮定しない。
- tests/test_result.pyはルール・GUIサーバー接続、test_stdin_commands.pyは独立プロセス通信、test_harness.pyは取得・時系列・原証跡・統計・保存・公開入口を検証する。
- 変更前証拠の保存先: `C:\Users\taaai\AppData\Local\Temp\reversi-quality-baseline-hfwdhzzw`。tracked-hashes.json、pytest.txt、smoke.txt、commands.jsonを新規保存。既存評価資産は上書きしない。
- 実行中: `python -B -m pytest -q -p no:cacheprovider`、`python -B -m pytest -q -m smoke -p no:cacheprovider`。
- PowerShellの制約モードではConsole.OutputEncodingの変更が失敗した。ファイル不良ではなく出力エンコード問題。以後PYTHONIOENCODING=utf-8のPythonで日本語資料を読む。

### モデルと実装の双方向照合

| モデル図の要素 | 対応する実装ファイル・関数 | 検証方法 | 結果 |
|---|---|---|---|
| import DAG / 別プロセス境界 | harness各15import辺、fixtureの3辺、protocol.Player、seeded_player.main、web.Process | ASTと図第2節、Popen/runpy経路の読解 | PASS |
| preflight→prepare→取得→ACQ→estimate→checkpoint→CLI保存 | evaluate._evaluate / main、contracts._acquisition_summary | 制御フローと図第3節照合、実行テスト待ち | 未確認 |
| SP/PR・CE/IR・要求・探索・quit/EO | protocol.Player、search_budget、budget_player | 図第4節と契約T-ID〜T-GATE照合、回帰待ち | 未確認 |
| fresh Inspection→Assessment→正式統計 | contracts.inspect_evaluation、analysis公開wrapper、statistics | 図第5節と共通入口読解、回帰待ち | 未確認 |
| unknown / failed / 正式資格 / CLI0/1/2の分離 | analysis / evaluate / contracts | 図第6節と既存回帰待ち | 未確認 |
| 本体・GUIは独立、実AItrace未対応 | reversi_player/reversi.py / web.py | 図・実装読解。ブラウザ描画は未確認 | PASS |

18import辺はASTと図で完全一致（不足0、過剰0）、Mermaidは5ブロック。全341件は成功し、上表の実行待ち項目も既存回帰で確認した。Mermaidの実レンダリングとブラウザ描画は未確認。

### 実行結果

| コマンド / 条件 | 結果 | 時間 |
|---|---|---|
| `python -B -m pytest -q -p no:cacheprovider` / 変更前global Python | 341成功、0失敗 | pytest 852.16秒、外部852.894秒 |
| `python -B -m pytest -q -m smoke -p no:cacheprovider` / 上記終了後 | 1成功、0失敗、340除外 | pytest 9.22秒、外部10.502秒 |
| `.venv/Scripts/python.exe -B -m pytest -q -m smoke -p no:cacheprovider` / 全体と並行 | 1失敗、340除外。2回目reportのresult=Noneで棋譜比較失敗 | 31.76秒 |
| venvで同fixture条件のevaluate×2 / seed31、同時に全体実行中 | 両回2局完走、error=None。診断JSON保存 | 3.768秒、2.940秒 |
| 固定seed20261014、CLI smoke 2局 | exit0、両局完走、失敗0、A/B各1勝1敗 | 6.565秒 |

固定seed対局のA応答平均1.109ms/最大5.224ms、B平均1.225ms/最大12.741ms。内部探索は未計測unknown。原evaluation.jsonも基準保存先に保存した。これは少数参考対局であり正式な強さ・性能改善を示さない。
全体と並行したvenv smoke失敗は新規コード変更以前の事象。低負荷の再確認を実施した。品質ゲートでは対局テストを並列起動しない。

venv独立再実行は1成功/0失敗/340除外、13.81秒。pip checkはNo broken requirements found。正常機能は確認済みだが、一過性の並行実行失敗の詳細原因は未特定の既知リスクとして残す（テスト緩和なし）。

### ツール診断と導入方針

既存環境を変更せず、Git対象外の.venvを新規作成しpytest8.4.2、Ruff0.17.0、mypy2.4.0、Hypothesis6.169.0を導入した。global pytest pluginなし、venvはHypothesis pluginあり。
変更前lint（E4/E7/E9/F）は13診断: 不要import2、不要代入3、fixtureのE402×8。fixtureのE402はCE採時と直接起動のsys.path設定に必要であり、移動すると時計契約を変える。根拠付きファイル限定除外を設け、F系列は除外しない。
formatは独立ルールreferee・seed adapterから導入し、大規模な報告辞書とGUIの一括整形は保留。lintはharness/reversi_player/tests全体に適用する。
mypyのWindows `--cache-dir NUL` は内部エラーとなった（プロジェクトエラーではない）。通常cache・`--no-incremental --show-traceback`へ変更すると、2モジュールに未注釈・未型付け呼出9件の正常な診断を得た。NUL指定をゲートに採用しない。
strict対象はreferee（盤面・着手・結果）、seeded_player、および後で作る品質ゲート。未型付けの動的report/通信/予算/statistics/GUI/本体は対象外として明示し、ignoreやAnyでstrict成功を偽装しない。本体の探索結果型は探索器未実装のため新設しない。
形式変更・不要な純粋計算の除去・型注釈は責務/schema/通信/評価条件を変えず、既存モデルの変更は不要。正規化の未使用戻り値を捨てても検査呼出自体は維持する。

## Goal 1: lint / format導入（検証中）

- pyproject.tomlにE4/E7/E9/Fを設定。harness/reversi_player/tests/toolsが対象。旧配布サンプルreversi_01.py・データ説明102400-games/data.pyは現行対戦経路ではないため対象外。
- formatはreferee、seed adapter、__init__、今後追加する品質基盤/独立テストが対象。大きな動的report・既存テスト・本体・GUIは未導入としてファイル単位で明記。既存警告をルール無効化で隠さない。
- fixture限定のE402除外は初期化前CE採時・直接起動時のsys.path調整を維持するため。F系列を除外しない。
- contractsの未使用import/辞書get、evaluateの未使用純粋all、search_budgetの未使用importを除去。normalize_configの戻り値だけ捨て、検査呼出・例外は維持。refereeだけ限定整形。AI/GUI/ルール/乱数選択は変更していない。
- `ruff check . --no-cache`: 成功。`ruff format --check . --no-cache`: 3ファイル成功。
- `pytest -q tests/test_result.py tests/test_stdin_commands.py -p no:cacheprovider`: 53成功、0失敗、7.03秒。
- 関連回帰55件: 54成功、1失敗、233除外、29.04秒。fixtureのroundtripでreference_available=False。Goal 1をまだPASSにしない。

### 実行環境差の原因（前の仮説の更新）

失敗した原fixture.jsonのfresh再検証で `temporal_identity_mismatch / trace.header.pid` を確認。Windowsの.venv/Scripts/python.exeはランチャー経路となり、Popen PIDと実行中AIのos.getpid()が異なる。rawを更新せず原因を調査した。これは起動経路の問題でありlint修正でPID対応を変えたものではない。
全体baselineは直接CPythonで成功し、venv側は参考対局だけなら成功する場合もあった。venv成功smokeだけではfixtureの正式時計/identityまで証明しない。このため品質コマンドは既存成功環境の直接CPythonを使い、必要なdev依存をuser領域に導入して再検証する。PID照合や正式契約を弱めない。venvランチャー正式対応は今回の対象外で、原証拠・版を改変して通さない。
並行smoke失敗のresult=Noneの個別原因は診断不足で未特定。PID不一致とは断定しない。

直接CPythonで同じ関連回帰を再実行: 55成功、0失敗、233除外、25.49秒。lint成功、format3件成功、pip check成功。venv原headerの4slotすべてでPopen PID≠trace PIDを確認した。新たな正式判定条件を追加・緩和せず、支持された直接起動で検証を続ける。
pipのuser Scripts非PATH警告は記録する。全コマンドを `python -m ...` で呼ぶためPATH変更は不要。

## Goal 2: 厳格な型チェック（着手）

refereeのRows=Sequence[str]、Position=tuple[int,int]、BoardRows=tuple[str,...]、Result=B/W/D/NGを定義する。Rowsの8×8・文字値は静的型だけで保証せず、既存入力検証と回帰に委ねる。不正colorの拒否を保つためcolor引数はstrとする。
seed adapterの副作用（乱数初期化、sys.path/sys.argv変更、runpy実行）をdocstringに明記する。
これらは表現の注釈と説明の追加だけで、処理順・盤面・戻り値・乱数消費・契約・依存方向を変えないためモデル図更新は不要。未型付けの通信/EvaluationReport/statistics・実AIは今段階のstrict対象外で、全プロジェクトstrict完成とは主張しない。
検証はmypy strict、注釈前後の関数本体AST照合、既存ルール・通信・GUIテスト、同seed対局の棋譜一致を使う。

実行結果: `python -B -m mypy --no-incremental` はstrict設定で2source files成功（ignore/cast/Any追加なし）。lint/format成功。53ルール・GUI・通信回帰成功、7.53秒。注釈/docstringを除いた4ルール関数とseed adapter関数のASTは開始commitと完全一致。
`python -B -m harness.evaluate --a reversi_player/reversi.py --b reversi_player/reversi.py --games 2 --seed 20261014 --evaluation-profile smoke --output <基準保存先>/after-types-evaluation.json`: exit0、4.017秒。両棋譜全着手/盤面/終局・勝敗が基準と一致。A平均0.746ms/最大1.449ms、B平均0.744ms/最大1.218ms。1ペアの参考測定であり性能改善とは扱わないが、この試行では重大な退行を認めない。
取得コードhashが変わったため正式比較APIによる同条件の優越性比較ではない。機能と少数応答時間の保全確認に限る。
モデルの依存辺・責務・データフロー・状態・schemaに変更はなく、戻り値の意味も不変。型aliasは実行時の8×8検証や正式資格の証明ではない。

## Goal 3: 回帰テスト補強（着手）

既存341ケースは母集合・不正hash更新trace・起動/世代/時計・旧版・保存障害・公開入口を既に検証するため再実装しない。
不足するコマンド別の応答container/空白/行数/文字集合/座標形式をtests/test_harness.pyでパラメータ化し、正常・異常と入力不変を直接検証する。形式検査と着手合法性の責務を区別し、形式上有効でも非合法な座標はevaluate側で拒否する既存回帰を維持する。
実装・契約・モデル変更は不要。新規試験を先に実行し、その後全体テストを実行する。

追加は正常形式8・不正形式17の計25ケース。`python -B -m pytest -q tests/test_harness.py -k 'response_contract or bad_player_is_terminated or evaluation_records_faults or startup_has_its_own_timeout' -p no:cacheprovider`: 36成功、0失敗、277除外、37.74秒。続くlint成功。
全体は `python -B -m pytest -q -p no:cacheprovider --tb=short` を直列実行中。goal3-pytest.txt / goal3-command.jsonを基準保存先へ新規保存する。全体結果待ちのためGoal 3はまだNOT_STARTED（作業中）とし、Goal 4へ進まない。

全体完了: 366成功、0失敗、781.64秒（外部784.015秒）。既存341件に新しい失敗なし。時系列・trace改変・母集合・保存再解析・正式公開入口・勝敗/予算/統計回帰も含む。モデル→実装と逆方向を再照合し、評価経路や意味に変更なし。初期venv検証との差は直接起動経路で区別する。

## Goal 4: 不変条件・性質ベース検証（着手）

新しい性質試験は固定期待局面の再実装ではなく、tests/test_invariants.pyへ分ける。refereeの合法手歩行を局面生成に使い、対象Boardの結果から期待局面を作らない。両実装の一致に加え、数学的な石数増加・反転・盤面形状・色集合・不正手の不変性を独立に検査し、生成器だけへの依存を避ける。既存手計算の方向別試験を保全する。
Hypothesisは40例/関数、derandomize=True、database=None、deadline=None、print_blob=True。選択整数列を縮小可能な入力とし、明示的60着手列も含める。時間制約は既存の独立プロセス試験が検証し、性質試験のCPU時間をコマンド期限に混ぜない。
合法手なしのpassと合法手ありのgenmove着手を区別する。AIのglobal random状態は保存/復元し、テスト内では固定seed0でgenmoveを行う。盤面生成・試験のみの追加で探索・契約・モデルを変更しない。

初回2成功/0失敗、1.62秒。既存53件と合わせた反復は55成功/0失敗、6.98秒、strict2モジュール成功、独立smoke1成功/367除外、13.56秒。
到達歩行でchoices=[0]×15は黒だけ合法手なし、choices=[63]×54は白だけ合法手なしを確認し、両関数の明示例に追加。最終反復2成功/0失敗、1.41秒、lint/format成功。
各関数40生成成功・0失敗＋4明示例（片側pass2、60着手2）。Hypothesisのinvalid生成3/5は内部生成の棄却件数で、ルール不整合や失敗テスト数ではない。同じ固定版と関数で反復の生成件数も一致した。発見したルール不具合なし、探索処理変更なし。

## Goal 5: 統合品質ゲート（設計先行）

既存の評価モデルは変更しない。新しい開発用runnerと静的モデル照合は評価経路の外に置く。実装前にdocs/harness_architecture.md第11節へ依存・データフロー・失敗遷移、docs/harness_contracts.md第13節へ品質報告の別契約を記載する。
設計上の入力は固定tool設定・直接CPython・現行モデル/コード、出力は新規ownedログ/quality.json。全pytest（性質試験を含む）→独立smokeを直列実行し、必須nonzero/ツール不在/timeout/未実行/保存失敗をゲート失敗へ伝播する。quality passedは正式AI評価資格・強さ・全モジュール型保証ではない。
自動モデル照合は全harness moduleのimport辺/DAG、主要公開wrapperのfresh inspection呼出辺の存在を双方向検査する。状態・順序の意味は既存回帰と人の対応表で補完し、ASTだけで証明済みとしない。
CIは未存在。Windows/CPython3.13.5に絞った同じ1コマンドの最小workflowを追加する方針。remote job実行はcommit/pushなしでは確認できず、ローカル結果と区別する。

実装前に7図と共通wrapperの呼出8辺を照合し、tools/quality.pyが未作成であることを確認してから実装開始した。評価モジュールへの逆importは追加しない。
初回診断の4型エラー（空setの型、Windows上のPOSIX API型、Popen型）を適切な注釈/対応platformの分岐で修正し、strict3source files成功。Any/ignore/castの追加なし。formatter差分はstdinで生成しapply_patchで反映した。数値hunk形式のpatch失敗と文書の行context不足は、未反映を確認して形式/contextを直し再適用した（ソースの失敗隠蔽ではない）。
ゲートの初回回帰21成功/0失敗、27.92秒。依存/回収異常を追加して26＋性質2は28成功/0失敗、32.08秒。strict3module、lint、format6fileが成功。
設計精査でsourceの終了hashだけでは検査中の変更を見落とすと分かったため、図と契約へ開始/終了snapshot一致を先に追加してから実装する。認証/ファイルロックの保証は追加しない。

開始/終了snapshotと品質passedの正式評価拒否を含め、27ゲート回帰＋性質2: 29成功/0失敗、35.69秒。strict3source files成功、lint成功、format6file成功。
実CLIの負例は新規一時repoへ故意のlint/type/format/pytest/smoke不具合を置き、終了1と各nonzero・失敗JSONを確認した。元コード/テスト/評価データは改変していない。実timeoutで所有子/孫を回収し、taskkill不在時も直接子を回収して失敗を保持することを確認した。
検査用上限はpip/lint/format/smoke120秒、typing300秒、全pytest1800秒。AI側1秒/10秒の契約は変更しない。

### 追加モデルの双方向対応と検証

| モデル図の要素 | 対応する実装ファイル・関数 | 検証方法 | 結果 |
|---|---|---|---|
| 既存評価経路を変更しない独立runner | tools.quality.run_gate、harnessのimport図 | validate_modelの18辺照合、importの静的読解 | PASS |
| 固定dev依存・直接CPythonの対応確認 | _environment_errors | 依存不在・版違い・宣言欠損/非固定、venv原PID差とbase経路 | PASS（Win/3.13.5限定） |
| 全必須結果のAND・直列・省略不可 | required_steps / run_gate / main | 各6検査の単独nonzero、実CLI負例、未実行・出力障害 | PASS（統合正常runは次） |
| source/モデルの開始終了一致 | _source_hashes / snapshot StepResult | model後の未検証module追加を拒否、終了hashで補修しない | PASS |
| 失敗後の診断・新規出力・exit1 | run_step / _terminate / run_gate / main | 実不在/timeout/子tree、保存不能、既存output保全 | PASS |
| 品質JSONと正式AI評価資格の分離 | QualityReport、analysis.assess_evaluation | 品質passedでもformal ineligible | PASS |
| 新module/図の辺差・循環・fresh迂回 | validate_model | 一時領域5種の改変、未検証モデル不在 | PASS（意味全体は既存回帰/対応表で確認） |
| CIも同じ入口 | .github/workflows/quality.yml | Windows/3.13.5・読み取り権限・同一commandを構文/内容照合 | 未確認（remote実行未実施） |

品質図のsource安定性・終了1分岐を先に更新し、型/コード/27回帰が一致することを確認した。評価モデルは同じ18import辺で循環なし、正式fresh入口10辺も実装に対応。時系列や全数の意味は図の存在でなくGoal 3全回帰で確認している。
これから `python -B tools/quality.py` を実際のrepoで実行する。実行中は対象コード/設定/モデルを変更せず、ログを読み取るだけにする。Goal 5は統合実行と最終再実行が成功するまでPASSにしない。

### 統合ゲートの実行結果（初回）

`python -B tools/quality.py`: exit0 / QUALITY PASS。
保存先: `evaluation_results/quality-9035353570134138bd262c2efbb56dcd/quality.json` と同dirの各ログ。
環境・モデル・依存・lint・format・typing・pytest・smoke・snapshotの9必須結果がすべて成功。開始/終了27source hashは一致、model_errorsは空。
全pytest395成功/0失敗、800.36秒（外部802.303秒）。独立smoke1成功/0失敗/394除外、10.48秒（外部12.927秒）。typingはstrict3source files、format6files、lint/pip checkも成功。
全341既存ケース＋25通信形式＋2性質＋27gate回帰=395ケースを実際に実行した。比較CLI/再解析/正常正式fixture/不正trace・起動・終了・予算・母集合・旧版の回帰は維持されている。
開始時の59tracked fileのうち変更は承認範囲11件だけ、他48件はbyte hash一致。AI本体・GUI・既存ルール/標準入力テスト・fixture・データ関連・旧報告/計画/仕様は未変更。HEADは開始commitのままでcommit/branch操作なし。
GitのLF→CRLF予告はあるがdiff --checkは成功。改行方針を変えるための追加設定や大量整形はしていない。

モデルの第11節を実装済みの現状として明示する。図の設計は事前確定したもので、検査を通すために後から意味を書き換えたものではない。文書状態確定後、最終gateをもう一度実行する（未実行段階でGoal 5をPASSにしない）。

## 変更ファイルと影響

| ファイル | 変更理由と範囲 |
|---|---|
| requirements-dev.txt / pyproject.toml | 固定dev依存と段階的lint/format/strict scope |
| harness/referee.py / seeded_player.py | 最小整形・型alias/注釈・日本語docstring。関数本体AST不変 |
| harness/contracts.py / evaluate.py / search_budget.py | 未使用import/純粋代入の除去。設定検査呼出は維持 |
| tests/test_harness.py | 通信形式25回帰追加、既存期待値/試験を保全 |
| tests/test_invariants.py | 到達可能局面・不変条件・pass/終局の独立性質試験 |
| tools/quality.py / tests/test_quality.py | 必須結果の伝播、モデル辺検査、失敗/保存/tree/snapshot回帰 |
| .github/workflows/quality.yml | 読み取り権限・Windows/3.13.5・同じgateの最小CI設定（remote未実行） |
| AGENTS.md / ハーネス.md | model-firstルールと実行可能な検証手順・対象/制約 |
| docs/harness_architecture.md / harness_contracts.md | 独立品質図・別契約を実装前に定義、現状との対応を確認 |
| QUALITY_PROGRESS.md | 各Goal・実コマンド・失敗/成功・保全・モデル/実装の証拠 |

## 残る制約・保証しないこと

- strictは3moduleのみ。通信/報告/statistics/本体/GUIの全面型付け、既存大規模ファイルの全面formatは未実施。範囲は明示しておりAny/ignoreで成功にしていない。
- 実AIはランダム選択・内部予算trace未対応のまま。品質passedはAIの強さ・正式予算対応・学習成功の証明ではない。
- 対応環境は直接CPython3.13.5/Windows。venvランチャーの正式PID対応・他OS/patch/GPU/別乱数源は未対応。試用.venvはGit対象外で残存し、品質gateに使わない。
- 初期の並行smoke失敗1件（result=None）の個別通信原因は未特定。直列baseline/独立smoke/統合gateで成功したが、負荷変動下の全面非flaky性は保証しない。
- Mermaidの実レンダリング、CIのremote job、ブラウザ描画・クリックは未検証。モデルの意味はASTだけでなく対応表/既存回帰で確認した。
- 性質試験は有限例であり全局面の証明ではない。time deadlineは別の既存統合試験で検証する。任意の空行/非整数座標など仕様未確定入力のAI本体挙動は変更・保証しない。
- sourceの開始/終了一致は観測時点の一致。途中編集と復元・環境の悪意ある改変の認証/連続固定は保証しない。gate実行中の編集を避ける。
- 少数対局の応答時間と同棋譜は保全確認であり有意な性能改善/強さではない。内部探索時間は実AIではunknown。

未解決の実装停止blockerはないためQUALITY_BLOCKERS.mdは作成していない。次の拡張では変更対象を明示してstrict scopeを増やし、構造/契約を変える場合はモデルを先行更新→対応回帰→品質gateを実行する。CIは別途push後の実結果を確認する。

## 最終検証と完了条件

全Goal 0〜5を、明示した段階導入scopeでPASSとする。全コードのstrict/format完成や未実行のCI/ブラウザを含めた無欠陥保証ではない。
最終 `python -B tools/quality.py`: exit0 / QUALITY PASS。
結果: `evaluation_results/quality-e19a9323170743eda0d72208cc7764ac/quality.json`。

| 必須検査 | 実結果 | 外部経過秒 |
|---|---|---|
| environment | 対応直接CPython・固定dev依存確認、終了0 | 0.102 |
| model | import/DAG/主要fresh入口正常、model_errors=[]、終了0 | 0.323 |
| dependencies | pip check成功、終了0 | 3.783 |
| lint | E4/E7/E9/F成功、終了0 | 0.347 |
| format | 対象6file成功、終了0 | 0.498 |
| typing | strict3source files成功、終了0 | 4.315 |
| pytest | **395成功・0失敗**、pytest内795.95秒、終了0 | 799.519 |
| smoke | **1成功・0失敗・394除外**、pytest内12.72秒、終了0 | 14.191 |
| snapshot | 開始/終了27source hash一致、終了0 | 0.025 |

最終JSONを再読込し、予定9結果の完全性・全終了0・error=None・passed=True・開始終了hash一致を検査した。現在の27fileの実bytesとも一致。
文書5件の内部リンク不足0、fence不整合0、Mermaid7ブロックを確認。意味/責務の対応は前記双方向表と全回帰で確認し、実レンダリングは実施していない。
最終Git確認もHEAD=8712843のまま、他48tracked fileは開始時hashと一致、diff --check成功。commit・push・branch変更なし。最終gate後はこの記録のみ追記し、検証対象コード/設定/モデルのbytesを変更していない。

### 実行した主要コマンド（順序と環境は各Goalの記録を参照）

```powershell
python -B -m venv .venv
.\.venv\Scripts\python.exe -B -m pip install pytest==8.4.2 ruff==0.17.0 mypy==2.4.0 hypothesis==6.169.0
python -B -m pip install --user -r requirements-dev.txt
python -B -m pip check
python -B -m ruff check . --no-cache
python -B -m ruff format --check . --no-cache
python -B -m mypy --no-incremental
python -B -m pytest -q tests/test_result.py tests/test_stdin_commands.py -p no:cacheprovider
python -B -m pytest -q tests/test_harness.py -k 'budget_config_rejects or trace_validation_rejects or consistency_reducer or legacy_optional_budget or schema5_reference_generation_roundtrip' -p no:cacheprovider
python -B -m pytest -q tests/test_harness.py -k 'response_contract or bad_player_is_terminated or evaluation_records_faults or startup_has_its_own_timeout' -p no:cacheprovider
python -B -m pytest -q -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_invariants.py -p no:cacheprovider --hypothesis-show-statistics
python -B -m pytest -q tests/test_quality.py tests/test_invariants.py -p no:cacheprovider --tb=short
python -B -m pytest -q -m smoke -p no:cacheprovider
python -B tools/quality.py
```

venv診断時と直接Python再検証の結果は別に記録した。初期baselineの全体は--tb指定なしで実行した。gateが実際に使った全argv・終了値・経過・ログ名は最終quality.jsonにも保存している。固定seed CLI評価2回とAST/保護hash照合はGoal 0/2の記録を参照。
手動Ctrl+C時の全子tree回収は今回未検証。runnerで明示的に実装・試験した回収はtimeout時であり、他の中断を保証したものではない。

## 設定の根拠

- [Ruff公式設定](https://docs.astral.sh/ruff/configuration/): lint/format個別scopeを使い、大量整形を避ける。
- [mypy公式設定](https://mypy.readthedocs.io/en/stable/config_file.html): strictと明示filesを使用。未対象は上記で区別する。
- [Hypothesis公式settings](https://hypothesis.readthedocs.io/en/latest/tutorial/settings.html): 固定版・derandomize・max_examplesにより再現条件を記録し、失敗は縮小入力/blobも保存する方針。
モデルの抽象度に影響しない注釈・formatだけなら図は変更不要と理由を記録する。
品質ゲートの新しい処理・依存・失敗遷移を導入するときは、設計図を先に更新・照合してから実装する。

## 次の作業と制約

Goal 0の実行結果・固定seed参考対局を記録してからGoal 1へ進む。
各段階の実コマンド・件数・時間・警告・未確認事項を追記する。
同じ問題に3回取り組んでも進展がない、または安全に解決不能なら実装を止め、QUALITY_BLOCKERS.mdへ分析と再開手順を残す。

## 検証証拠自動生成（2026-10-14、開始HEAD e1c55f4）

今回のGoal 0〜6は先の品質基盤Goalとは別。開始時Git clean、未コミットなし。commit/push/branch操作をしない。旧成功JSONは現在のcontracts.py（空行追加）とhashが異なるため現在の成功へ流用しない。

| Goal | 状態 | 証拠・完了条件 |
|---|---|---|
| 0 現状・計画 | PASS | 現行quality/JUnit/モデル/契約/27gate回帰を再読解。関連80件成功、26.64秒（外部27.479秒） |
| 1 先行モデル | PASS | 品質2図と契約13.1を実装前に更新。責務・依存・保存hash・正常/不完全状態を内部照合 |
| 2 個別結果 | PASS | 実pytestの正常/段階失敗/skip/collection、別step、crash/timeoutのJUnit/journal回帰 |
| 3 自動集約 | PASS | run_gateでJSON/Markdownとquality完成markerを自動保存・照合。最終実統合はGoal 6 |
| 4 重要証拠 | PASS | 重要48ケース成功、48明示期待/観測、原12参照、収集complete/errors=[] |
| 5 異常回帰 | PASS | 証拠39＋既存27=66回帰成功。実fsync失敗の誤公開も修正し再現回帰成功 |
| 6 統合照合 | PASS | 反例を回帰7件で再現・修正後、実gate全10成功。全434/独立smoke1、現行31hash・排他公開・原参照の再確認成功 |

### Goal 0の分類と最小設計

- A 既存自動: 各step argv/終了値/秒/log、環境/dev版、source hash、評価run/case/pair/seed/commands/raw/checkpoint。
- B 小規模追加: 標準JUnit、軽量phase journal、実行/step/test対応、Git読取、JSON/Markdown集約・原hash対応。
- C 明示登録: ルール・応答形式・拒否・schema/raw・正式資格の独立期待、観測、モデル/契約/実装参照、関連原成果物。
- D 未取得: 任意local/全assertの自動意味抽出、全Hypothesis成功例、全stderr、強制停止/停電時の完全保存、原証跡認証、CI保管。

変更候補はtools/quality.py、stdlib collector tools/evidence.py、限定pytest plugin tools/evidence_plugin.py、既存tests/test_quality.pyと少数重要test、strict対象の追加、現行2設計資料と操作説明。新依存なし、harness/本体/GUI/原データ/正式判定は変更しない。

必須成果物は新規quality領域のquality.json・各log・step別JUnit/events.jsonl・evidence.json/evidence.md。quality v1のfieldを維持し必須evidence StepResultを追加。テストが失敗しても整合した証拠はcomplete、保存不能はgate失敗。最後のquality.jsonをpublication markerにして循環hashを避ける（qualityはevidence hashを保持せず、evidenceがquality予定bytes hashを参照）。

### Goal 1の先行照合

品質フロー/状態2図と契約13.1を先に更新した。評価第2〜6節・正式AND・schema5/trace4/validation3に変更なし。quality→collector、pytest/選定test→pluginだけを追加し、harness逆import/collectorの正式資格発行を禁止。
XML集計だけではnodeid・段階・未完を証明できないためjournalを併用。setup失敗時にcallを要求せず、teardown失敗をcall成功で隠さない。欠損/別ID/未完は証拠不完全、負例の想定拒否はpytest成功。正常失敗を証拠保存失敗と混同しない。原参照は登録時hashと終了後bytesを照合し、正式意味検証の代用にしない。
実装コードはまだ変更していない。図の責務・依存・状態と契約を照合してからGoal 2〜5へ進む。Mermaidの実レンダリングは未検証。

先行照合完了: collectorはJUnit＋journalを同じstepで照合し、JSON/Markdownと最後のquality publicationを分離する。qualityは証拠hashを含めず、証拠が予定quality bytes hashを保持するため循環なし。XML/イベントが欠けた異常では部分証拠・gate失敗とし、テストの元終了値を保持する。重要テストの合否条件・harnessの正式ANDを変えない。新実装2ファイルはこの時点で未作成。

基準保存先は一時領域reversi-evidence-baseline-c74sl0vs。baseline.xml / baseline.txt / command.json / tracked-hashes.jsonを新規保存。既存80件（品質27・ルール/GUI40・標準入出力13）は失敗0。新依存を導入しない。Goal 2〜5実装着手: toolsのcollector/pluginとquality接続、strict対象を3→5へ拡張する（ignore/設定緩和なし）。

初回関連回帰80成功/0失敗20.46秒。新回帰追加後は50成功/3失敗45.68秒。2件は正常teardown失敗の標準JUnit件数規則をcollectorが要素数と誤認した新実装の不具合、1件は改変回帰がself-closing XMLへ実際の改変を行っていなかった試験生成不具合。pytest原実装・新規XML/phaseを照合し、契約へ正しい段階集計を先に追記して修正する。既存判定/期待値の緩和ではない。lint/format9file/strict5moduleは成功。

同じ回帰の修正後は53成功/0失敗42.90秒。重要48ケースの実plugin/JUnit試行はpytest48成功/0失敗83.00秒、しかし収集はincomplete（原理由辞書が3ケースで64KiB上限を超過）。保存済みjournalからpair/missing/extraのbudget観測不足を特定した。上限は維持し、明示観測をbudget状態・理由コード集合・件数へ限定、詳細未収集を明記して再検証する。probe原参照のループ位置も訂正して正式fixtureのprobeを関連付ける。正常AI/正式条件やテスト期待値は変更しない。

証拠異常回帰を追加した反復は59成功/0失敗59.39秒。setup/call/teardown（call＋teardown二重失敗含む）、skip/collection、crash/timeout、XML/journal欠損/破損/別ID、原run/hash、保存失敗、同nodeidのstep区別、秘密metadata拒否、最終fsync・marker不足、公開run_gate伝播を実行した。静的検査はlint成功、format9file成功、strict5module成功。重要48ケースの容量修正後の再検証と実統合gateはまだ未実行。

重要48ケースの容量修正後の再検証: 48成功/0失敗/318除外91.44秒、収集complete/errors=[]、明示48件・原参照12件（checkpoint3・母集合1・対局raw4・probe raw4）。新規一時領域reversi-important-evidence-ryzpvxad/important-runのJUnit/events.jsonl/collection.jsonに保存した。3件の巨大理由辞書はcode集合/count・details未収集へ限定しただけで、元期待値/判定/64KiB上限を緩めていない。

### 最終統合前の双方向照合

| 図/契約 | 実装 | 回帰/実確認 |
|---|---|---|
| Q/MAN | quality.run_gate、evidence.manifest | Git read-only、source/設定/モデル/仕様hash、step ID/argv/timeout。未コミット再現はbytesを要する |
| T/S→XML/JOURNAL | required_steps/run_step、pluginのpytest hook | 新規owned full/smoke、3phase/collection、nodeid/sequence/session、同testの別step |
| XML/JOURNAL/原参照→EC | evidence.collect_step/collect、record_harness_artifacts | 標準JUnitの二重失敗補正、原run/hash、原12参照、異常32ケース |
| EV→LOG/EXIT | render_markdown/save_new、qualityのevidence StepResult/最後のquality保存 | JSON/Markdown同一辞書、quality予定hash、保存失敗・既存ファイル保全、公開gate伝播 |
| 旧評価境界 | harness変更なし | 選定正式fixture/不正raw/全公開入口の既存assertを維持。品質passedで正式資格を与えない回帰 |

新構造→コードとコード→図の対応を読み、collectorが元pytest結果を再判定しないこと、原rawを改変しないこと、source hashに新toolを含むこと、追加strictを緩めないことを確認した。評価第2〜6節の責務/依存/条件は不変。manualなMermaidレンダリング・remoteCI・実ブラウザは未実行。
これから最終static/59回帰を再検証した後、実 `python -B tools/quality.py` を直列実行する。実行中は対象source/config/modelを変更せず、進捗/生成物の読取だけにする。Goal 6は実gateと現在hash/保存証拠の検査が終わるまでPASSにしない。

### Goal 6の実行結果・完成確認

最終static: `python -B -m ruff check . --no-cache` / `python -B -m ruff format --check . --no-cache` / `python -B -m mypy --no-incremental` は成功（format9files、strict5source files）。直後の `python -B -m pytest -q tests/test_quality.py -p no:cacheprovider --tb=short` は59成功/0失敗45.34秒。

**実際に `python -B tools/quality.py` だけで自動証拠まで生成し、終了0 / QUALITY PASS。**
保存先: `evaluation_results/quality-b38f6817521a4f9a9cc134bb8aa16092/`。

| 必須検査 | 終了 | 外部秒 |
|---|---:|---:|
| environment | 0 | 0.0213 |
| model | 0 | 0.0891 |
| dependencies | 0 | 0.4533 |
| lint | 0 | 0.1216 |
| format | 0 | 0.0882 |
| typing | 0 | 3.1760 |
| pytest | 0 | 265.8927 |
| smoke | 0 | 3.2367 |
| snapshot | 0 | 0.0052 |
| evidence | 0 | 0.1548 |

全pytest **427成功/0失敗/0error/0skip**（pytest内265.30秒）、独立smoke **1成功/0失敗/426除外**（pytest内2.84秒）。旧395件＋証拠回帰32件で427件。両JUnitの件数・エラー/失敗/skip数、journal/nodeid/段階・終了値、JSON結果が一致。pytestは明示48ケース/原参照12件、smokeは明示1ケース/原参照2件。JSON/Markdownは同じ辞書から再生成してbyte内容一致、全テストIDがMarkdownにあり、同じsmoke nodeidのexecution IDはstepで異なる。

evidence.collect_step / verify_publicationを実成果物に実行し、collection_status=complete、errors=[]、publication照合=[]。quality予定hash、Markdown hash、元XML/journal/log、原checkpoint/trace/母集合の参照bytesを照合した。quality.json v1のfield構造は旧と同じで、必須evidence StepResultだけを追加した。原参照は意味的認証・正式資格の証明ではない。

開始/終了31source hashが一致し、終了後の現在31fileのbytesとも完全一致。Gitは開始HEAD e1c55f4・dirty=Trueとして自動保存（承認済み今回変更を含む）。開始時65trackedのうち変更10件だけ、他55件は開始時hash一致。harness全コード、AI本体/GUI、fixture、既存性質試験、仕様/依存版/CI、配布データ/過去報告は未変更。追加はtools/__init__.py/evidence.py/evidence_plugin.pyの3件。commit/push/branch操作なし。

副作用範囲は新規owned成果物・既存試験の新規一時/run生成物。証拠出力は17files、約1.84MB（実byte合計は後述の照合値）、大きな原rawは複製せず参照。collector計測0.1548秒は保存前集約の経過で、pytest中journalコストや全保存I/Oを含む値ではない。今回の全体時間と過去795秒との違いを性能改善と解釈せず、同条件の収集on/off性能比較は未実施。AI探索/通信期限へのコード変更なし。

モデル→実装/実装→モデルは上の対応表と新32回帰＋既存全回帰で照合し、harnessの依存18辺/正式fresh10辺は維持。新3moduleのAST importと全関数/クラスの日本語docstring、設計7Mermaidのfence、内部リンクを読み取り確認。新しい重要設計矛盾は確認なし。実レンダリング、remoteCI、実ブラウザは未検証。

### 保証範囲と後続課題

- A 全テスト: nodeid/step/run/execution ID、phase結果・時間・失敗診断、JUnit/journal/hash、共通Git/環境/source/argv、未実行/中断/収集不足の区別。
- B 重要テストのみ: ルール・形式・拒否・schema/raw・正式資格・smokeの明示input/独立期待/観測/モデル/契約/実装。成功48ケース、smoke再実行1ケース。巨大理由はcode/countで保存しdetails未収集を明示。
- C 未取得/未保証: 任意assert/local・全Hypothesis成功例・全stderr、全証拠の真正性/完全匿名化、parallel worker収集、停電/OS障害/親強制停止時の完全保存、CI upload/保管、collector on/offの公平な性能比較。
- 原参照は長期保管しないためpytest一時領域の削除後は再確認不能となり得る。新規品質領域と関連原runを必要に応じて保管する。古い証拠を修復・昇格しない。
- toolsの明示package追加はmypyの同一ファイル二重module名診断の根本対処で、strict/ignoreを緩めたものではない。既存search_budget.validate_traceのnone/unknown読込docstringと実処理の説明差は範囲外として未修正（collectorはそのコメントを検証省略条件に使わない）。

未解決の作業停止blockerはないためQUALITY_BLOCKERS.mdは作成しない。Goal 0〜6を上記範囲でPASSとする。最後の追記はこの記録だけで、実ゲート対象31fileは変更しない。今後は `python -B tools/quality.py` の新規UUID領域でevidence.md→evidence.json→原参照を読む。verify_publicationの[]とquality.passed/collection_statusを併せて確認し、正式AI評価は従来の独立共通入口を使う。

### 最終監査での追加反例（再検証前）

直前のPASS記述は初回runの記録であり、以下の反例発見により現在Goal 6をFAILへ戻す。save_newが完成名へ直接writeしてからfsyncするため、fsync失敗でも予定hashの全bytesが残るとverify_publicationが完成と誤認し得る。検査結果を緩めず、公開図・契約を先にowned pending fsync/close→排他hardlink→quality最後へ修正した。新規回帰で実fsync/link障害・race winner保護・公開gate/CLIの保存障害を検証してから再統合する。原評価writerには変更を加えない。
初回容量の厳密値は1,834,014bytes（約1.83MB）である。全stepのID/結果/log hashも実行単位へ追加し、既存quality v1のfield構造は維持する。

新7回帰を旧save_newで先に実行: **7失敗/59除外9.43秒**。3派生名にfsync後の全bytesが残る、link/race/pending契約が未対応、実run_gate→mainが終了1でもpositive quality markerが残る反例を実際に再現した。図/契約の先行修正後にpending→fsync/close→排他linkを実装し、作成成功したown pendingだけを回収する。原harness writer/評価schemaには手を加えない。Goal 6は修正後回帰と新しい実gateが成功するまでFAILのまま。

公開修正後の静的検査はlint成功・format9file成功・strict5source成功。同じ新7回帰を含む全品質回帰 **66成功/0失敗95.15秒**。既存winnerと別writer pendingのbytesを保護、実fsync失敗でも完成名を生成しないこと、main終了1を確認した。判定条件を緩めず、NTFS上の排他hardlink公開を使う。現在の品質2図・契約・実装は一致し、これから新規UUIDの実qualityを再実行する。実行中はsource/config/modelを変更しない。

### 確定した最終結果（公開修正後）

`python -B tools/quality.py`: **終了0 / QUALITY PASS**。
最新成果物は `evaluation_results/quality-777d1c21d3c94015a8228f306e45a721/`。
初回b38f6817の427件成功は旧公開方式の途中結果であり、現在の成功として使わない。

| 必須検査 | 終了 | 外部秒 |
|---|---:|---:|
| environment | 0 | 0.0459 |
| model | 0 | 0.1507 |
| dependencies | 0 | 0.8747 |
| lint | 0 | 0.1990 |
| format | 0 | 0.1901 |
| typing | 0 | 7.7307 |
| pytest | 0 | 482.1850 |
| smoke | 0 | 5.7231 |
| snapshot | 0 | 0.0113 |
| evidence | 0 | 0.4250 |

全pytest **434成功/0失敗/0error/0skip（480.72秒）**、独立smoke **1成功/0失敗/433除外（4.95秒）**。旧395＋証拠39回帰=434。JUnitとjournalとJSONの件数/段階/終了値が一致。pytest明示48・原12参照、smoke明示1・原2参照。全10stepのID、旧9検査の元結果と9log hashを共通manifestへ保存した。

保存17files **1,866,000bytes（約1.87MB）**、owned pending残存0。evidence.json/evidence.mdは同じ辞書から再生成して内容一致、Markdownに全test IDが存在。`verify_publication` は **[]**。新公開mode、quality予定hash、Markdown、全log・JUnit/journal・原参照bytesを再確認した。NTFSの排他link成功を実gateで確認し、fsync失敗/公開raceは回帰で拒否する。directory永続化/停電までの完全性は保証しない。

31sourceの開始/終了hashと検証後の現在bytesは一致、差分0。開始65trackedのうち意図した10件だけ変更、他55件（全harnessコード、AI/GUI、fixture、仕様、依存pin、CI、配布資産/過去報告等）はhash一致。新規3toolファイルのみ追加。Git HEAD=e1c55f4、dirty=Trueを自動記録し、commit/push/branch操作なし。モデル2図・契約の先行修正→7失敗再現→修正→66回帰→新実gate→保存再照合の順序を守った。全Goal 0〜6を明示したscopeでPASSへ確定する。

時間/容量の影響: collector段階は0.4250秒、出力約1.87MB。pytest内journalや全fsyncコストまで分離したon/off比較ではない。過去runとの時間差を高速化と解釈しない。AI/正式判定/schema5/trace4/validation3/通信期限/探索予算は未変更。CI upload・実ブラウザ・他OS/GPU・parallel worker・全local/全成功生成例の記録・原証跡認証/長期保管は今回の追加範囲外。新方式未対応の保存先は安全に失敗し、旧直接writeの試作証拠を新しい完成証拠へ昇格しない。

最終追記はこのファイルのみ（gateの31source対象外）。未解決の作業停止blockerはなく、QUALITY_BLOCKERS.mdは未作成。今後の実行は `python -B tools/quality.py` 一つで新規UUID領域へ自動生成され、手動報告は不要。evidence.md→evidence.json→原参照を読み、verify_publicationとquality.passedを確認する。

## 追補：journal終了・状態遷移の検証漏れ修正

過去の434件成功後のレビューで、終了重複・早期終了・終了後metadata・test間の交差等をcollectorがcompleteにすることを確認した。公開run_gateでも既存の模擬runnerを用いてpassed=trueまで到達した。過去の成功をこの検査の完成証拠として扱わず、本追補の回帰・実gateで再判定する。

実装前にdocs/harness_architecture.mdの品質データフロー/状態図と11.2節、docs/harness_contracts.mdの13.2節を更新した。JLは既存evidence内のprivate検査とし、依存モジュール・AI/GUI・正式資格・保存v1を変えない。次に回帰を追加して修正前の失敗を記録し、その後実装する。commit/pushは行わない。既存未コミット13ファイルを保護し、変更前hashと関連テスト結果を新規一時領域へ保存する。

開始時の関連既存15件は15成功・51除外14.14秒。モデル先行更新後、実pytest生成journalを原本とする21改変、正常fixture/直列1件、公開run_gateのfull/smoke各3改変、mainの2改変を追加した。旧実装で `python -B -m pytest -q tests/test_quality.py -k journal_lifecycle -p no:cacheprovider --tb=short` を実行し、**29失敗・1成功・66除外4.28秒**。重複/早期終了で公開mainがQUALITY PASS/終了0になることを再現した。invalid_selectedは構造化incompleteでなくTypeErrorになることも再現した。元JUnit/phase/終了値は改変せずsequenceのみ振り直し、単なる連番不正の検出を新検査の成功へ読み替えない。新規一時領域reversi-journal-fix-fp70vmtxへbaseline/redのlog/XMLと開始hashを保存した。この失敗記録後に実装を開始する。

修正はevidence._journal_errorsの追加とcollect_stepからの呼出に限定した。session/選択/直列active test/phase/端点を検査し、unsupported形式も理由付きincompleteとする。selectedの不正型はエラーを保持したまま安全に診断集約する。模擬runnerのjournalにも実v1のformat_versionを明示した。plugin、qualityの判定コード、harness本体、AI/GUIは変更しない。setup skipとmodule-level collection skipの正常系2ケースを加え、**全品質回帰98成功・0失敗48.37秒**。新規32ケース（状態回帰30＋正常系2）で、元の66件のassertは維持した。

lintとstrict5sourceは成功。Ruffが示した整形差分だけをapply_patchで反映した。モデル2図/契約13.2/実装/回帰の対応を照合した後、全品質ゲートを新規UUID領域で実行する。実行中は対象source・設定・モデルを変更しない。過去のquality-777d1c21の成功は今回の修正後証拠として流用しない。

修正後の初回実gateはquality-4234d93dc49b4cefa3c64eca01bb8509へ新規保存し終了0。全466成功263.75秒、独立smoke1成功465除外2.82秒、必須10検査成功、publication照合[]、JSON/Markdown一致、31source開始/終了/現在hash一致、pending残存0を確認した。保存済み旧正常v1の2316イベント/full・12イベント/smokeも新状態検査のエラー0。既存68対象のうち今回変更は5ファイルだけで、他63件は開始hashと一致した。

最終文面照合で設計11.2節の「現行collector」を「修正前collector」へ訂正した。状態モデルや実装の変更ではないが対象文書hashが変わるため、最終文面に対して実gateを再実行する。初回結果を最終文面のhash一致として報告しない。Mermaidは構造/コード対応を確認し、実レンダリングは未実施。原記録真正性、全ローカル変数、並列worker/rerunは保証対象外のまま。

### journal修正の最終検証結果

最終文面の `python -B tools/quality.py` は **終了0 / QUALITY PASS**。成果物は `evaluation_results/quality-4c0edb02f46a412881f353676b4ede6f/`。全pytest **466成功・0失敗652.36秒**、独立smoke **1成功・0失敗・465除外2.57秒**。この再実行は初回より遅かったが、時間制限を緩めたり検査を省略したりしていない。速度低下の根本原因は未特定で、性能改善/悪化の測定結果とは扱わない。

| 検証 | 結果 |
|---|---|
| 修正前の新規状態回帰30件 | 29失敗・1成功4.28秒。公開mainの誤成功も再現 |
| 修正後の全品質回帰 | 98成功48.37秒（元66＋新32） |
| 必須品質ゲート10検査 | 全成功。environment/model/pip check/lint/format/strict typing/全pytest/独立smoke/snapshot/evidence |
| 最終保存・再照合 | verify_publication=[]、Markdown再生成一致、full/smokeのcollection_status=complete、errors=[]、pendingなし |
| 最終対象source | 31件の開始/終了/現在hash一致。最終追記は対象外の本ファイルだけ |
| 既存変更の保全 | 開始68対象中、今回変更は本記録・設計2件・tests/test_quality.py・tools/evidence.pyの5件。他63件hash一致 |

実行コマンドは、開始時の関連15件選択、`python -B -m pytest -q tests/test_quality.py -k journal_lifecycle -p no:cacheprovider --tb=short`、`python -B -m pytest -q tests/test_quality.py -p no:cacheprovider --tb=short`、`python -B -m ruff check .`、`python -B -m ruff format --diff tools/evidence.py tests/test_quality.py`（差分はapply_patchで適用）、`python -B -m mypy`、整形check、`python -B tools/quality.py`（修正後2回）、`git diff --check`。baseline/red/greenは新規一時領域にそれぞれJUnitとlogも保存した。

モデルのJL→EC→evidence検査→quality失敗が、21改変の単体回帰、full/smoke双方の公開run_gate回帰6件、実main回帰2件に対応する。正常fixture内metadata/複数観測/直列test、setup失敗/skip・collection失敗/skip、既存crash/timeout/保存失敗の回帰は成功。元pytestの記録された合否と収集完全性を分離し、不正journalを正常へ修復していない。unsupported versionもincompleteにし、別の品質unknown/failed型を導入しない。

現行の保存v1・AI正式評価schema5/trace4/validation3・正式入口・AI/GUIは不変。verify_publicationは引き続きbytes対応確認のみで、意味検証や真正性保証を発行しない。保存済み正常v1との状態契約互換性は確認したが、未対応形式・全plugin・並列/rerun・実Mermaidレンダリング・別OSは保証しない。ステージ・commit・push・branch操作なし。今回確認した検証漏れの修正と回帰は完了。

## mypy strict拡大 Phase 0・1（statisticsのみ）

### Phase 0：PASS / 開始状態と型付け方針

開始HEADはb34a3bf6af598e97fb527c7cb63798ee0b6b729b、main=origin/main、作業ツリーclean。AGENTS、現行architecture/contracts、品質runner、全本番import DAG、statistics全4関数とanalysis/evaluate/testsの呼出を確認した。新規一時領域reversi-strict-phase01-7b1gd12rへ全tracked開始hash、元statistics、診断log、解析graphとDAGを保存。既存成果物は変更しない。

変更前の明示strictはreferee/seeded_player/tools.quality/tools.evidence/tools.evidence_pluginの5件。通常mypyは終了0・5source成功。実graphはこの5件とharness/toolsのdocstringだけの__init__2件で、strict有効・ignore_all=False。現在の成功証拠quality-4c0edb02f46a412881f353676b4ede6fは全466件＋独立smoke1件成功で、31source開始/終了/現在hash一致、verify_publication=[]。したがって変更前の全gateを重複実行せず、この対応済み証拠をベースラインとする。

statistics単独の既存strict診断は10件：4関数のno-untyped-def、groupedのvar-annotated、pair_scores呼出3箇所とconfidence_interval呼出2箇所のno-untyped-call。数値関数→ペア集計→単独/対応推定の順で型付けする。statisticsはmath以外のharness依存を持たず、analysisとevaluateから呼ばれる低水準算術で、正式資格を発行しない。

全本番＋tests指定ではreversi/reversi_player.reversi重複で終了2。--explicit-package-basesをCLIだけで指定すると重複は消え、23source/16filesの1423診断となる。今回は既にpackage化されたharness.statisticsだけを追加するため、このflagの常設化や__init__追加は不要。Phase 2以後の名前解決方法として記録するだけで、他moduleをstrictへ追加しない。

#### 実装前の契約・モデル判断

対象は関数署名・ローカル注釈・既存辞書の静的TypedDict表現のみ。数式、比較、条件分岐、例外、dict/list生成、キーと戻り値、公開入口を変更しない。追加の型はTYPE_CHECKING内のprivate定義とし、実行時の新しいコンテナや型変換を作らず、他のharness moduleもimportしない。将来注釈を用いて型名の実行時評価を避ける。現行図のanalysis/evaluate→statistics、computed/arithmetic_only/not_assessed、正式ANDの境界・保存schemaは変わらないため、モデル図/データ契約の変更は不要と判断する。実装後はASTで処理同一性と図のimport辺を再照合する。操作文書のstrict範囲だけは常設追加時に更新する。

入力Gameのscore_aはobjectのままとし、型によって未検証値を合法得点へ昇格しない。現行evaluateはincompleteゲームにscore_a=Noneを含めてpair_scoresへ渡し、同関数は既存の状態検査と値域検査で除外する。mypyはany(value not in (0, .5, 1) ...)によるlist全体の絞り込みを追跡しないため、収集用ローカルvaluesだけlist[Any]を許可する方針とする。これは元の不正入力/None/JSON boolの振る舞いと検査順序を保持するための局所的動的境界であり、全入力/全出力をAnyにするものではない。既存値域検査は削除せず、None/文字列/NaN/範囲外等の除外とbool保持を回帰確認する。

代替は、(1)入力得点をfloat必須と宣言する案：実際のNone/未検証値を隠すため不採用、(2)絞り込みループ/追加assert/castで変換する案：処理追加や再設計が今回の制約に反するため不採用。Anyはこのローカル要素型に限り、関数署名・他のdict・算術結果型へ一括伝播させない。保存結果の宣言は既存形状の記述であり、JSON入力の検証済み証明や正式資格ではない。

### Phase 1：実施中

まず独立した手計算期待値と既存の例外/不正得点の回帰を既存test_harnessへ追加し、変更前実装で成功を確認する。その後statisticsへ注釈を追加し、単独strict成功後に既存filesへstatisticsを追加する。既存5対象、strict=true、品質ゲート判定/timeoutを維持し、Phase 2以降は実装しない。

型付け前に新30ケースと既存算術1件を実行し31成功・312除外0.85秒。census平均/after-before差の手計算、None/欠損/文字列/NaN/範囲外得点、JSON boolの元値保持、無効N/confidence、既存KeyError/TypeError/AttributeErrorを固定した。その後、TYPE_CHECKING内のprivate TypedDict4個、4関数の注釈、grouped/reasons/scoresと得点収集の注釈を追加。単独strictは10→0、1source成功。注釈を除いた4関数のASTは全て開始時と一致し、数式・分岐・例外・戻り値・呼出を変更していない。

常設scopeの回帰は通常quality.required_stepsのtyping argvに一時shadow-fileだけを追加し、statistics内の未注釈関数が検出されることを要求する。files追加前はmypyが5source成功してしまい、回帰が1失敗・98除外3.33秒となることを先に確認した。単独strict成功後にfilesへstatisticsだけを追加し、操作文書の5→6と未型付け範囲を更新した。他module、型flag、フォーマット除外、品質runnerは変更していない。静的型だけの追加なのでモデル図/データ契約は変更しない。

常設追加後の通常mypyは6source成功。実graphは元の7module＋harness.statisticsの8件で、全件strict有効・ignore_all=False。lint成功、既存format scope9files成功（statistics/test_harnessは既存の除外を維持し整形導入を別課題とする）。関連の統計/正式入口/母集合・全数/数値境界は35成功・308除外16.90秒、scope漏れ回帰は1成功・98除外3.34秒。通常argvのshadow probeが未注釈関数を拒否することを確認した。

追加互換確認では、注釈を除いたASTだけでなく4関数の実行bytecode・定数・参照名・ローカル名も変更前と一致した（行情報/注釈flagは比較対象外）。元実装を一時namespaceで読み、未検証JSON得点・各N/confidence・例外・入力不変性の1182ケースを差分照合して不一致0。これは既存挙動の比較であって独立した数式正しさの証明ではない。手計算期待値は別途30回帰で確認した。型名はTYPE_CHECKING内に置き実行時構築せず、現行にget_type_hints/__annotations__/inspect.signatureを利用する呼出は見つからなかった。将来その種の型introspectionを導入する場合はprivate forward referenceの解決を別途設計する。

モデル双方向照合はvalidate_model=[]、statisticsから他harnessへの新importなし、analysis/evaluate→statisticsと算術/正式資格の分離を維持。型注釈でもstatisticsのsource hashが変わるためanalysis_fingerprint/analysis_idは契約どおり変化するが、取得fingerprint・validator契約・raw/schemaを変更せず、過去hashを書き換えない。ここまでの変更はstatistics/config/既存テスト2件/操作文書/本記録の6ファイルだけで他62trackedは開始hash一致。最終の通常品質ゲートを新規UUIDへ実行し、その間は対象source/config/modelを編集しない。

### Phase 1：PASS / 最終結果と停止点

`python -B tools/quality.py` は終了0 / QUALITY PASS。新規成果物は `evaluation_results/quality-26c2ba70645344f1aaad11da22119b1c/`。全pytest **497成功・0失敗234.47秒**、独立smoke **1成功・0失敗496除外2.44秒**。旧466＋統計30＋常設scope1=497。既存assertを緩和せず、環境/model/dependencies/lint/format/typing/pytest/smoke/snapshot/evidenceの必須10検査がすべて成功。typing.txtは「6 source files成功」を記録する。

| 完了条件 | 根拠 |
|---|---|
| 既存strict維持・対象追加 | strict=trueと既存5件を維持しstatisticsだけ追加。明示5→6、間接2は同じ |
| 統計診断 | 単独10→0。通常設定でも6source診断0、実graphにstatisticsを含む |
| 常設対象漏れ防止 | 一時shadowで未注釈関数を注入し、通常gate argvが失敗することを新回帰で確認。設定追加前の回帰失敗も記録 |
| 算術/境界/例外 | 関連35件＋scope1件成功。4関数AST/実行bytecode同一、1182差分ケース不一致0 |
| 最終証拠 | publication照合[]、JSON→Markdown再生成一致。full/smokeともcomplete・errors=[]、pending残存なし |
| source整合 | 31source開始/終了/現在hash一致。本最終追記だけは対象外の進捗記録 |
| モデル/契約 | validate_model=[]。harness import DAG/数式と正式資格の境界・schema5/trace4/validation3は不変 |
| 変更保全 | 開始68tracked中6件だけ変更。他62件はhash一致。HEAD=b34a3bfのまま、ステージ/commit/push/branch操作なし |

変更ファイルはharness/statistics.py、pyproject.toml、tests/test_harness.py、tests/test_quality.py、ハーネス.md、QUALITY_PROGRESS.md。モデル図・契約の変更不要の理由はPhase 0で先に記録し、実装後の依存/処理/状態を双方向照合した。品質runnerと判定条件/timeout、AI/GUI、データ資産、過去結果は編集していない。

主な実行コマンド（キャッシュ/診断logは専用一時領域へ保存）:

```powershell
python -B -m mypy --no-incremental --config-file pyproject.toml
python -B -m mypy --no-incremental --config-file pyproject.toml harness/statistics.py
python -B -m mypy --no-incremental --config-file pyproject.toml harness reversi_player tools tests
python -B -m mypy --no-incremental --config-file pyproject.toml --explicit-package-bases harness reversi_player tools tests
python -B -m ruff check harness/statistics.py tests/test_harness.py tests/test_quality.py --no-cache --config pyproject.toml
python -B -m ruff format --diff tests/test_quality.py --config pyproject.toml
python -B -m ruff format --check . --no-cache --config pyproject.toml
python -B -m pytest -q tests/test_harness.py -k 'statistics_typing or finite_population_interval' -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_harness.py -k 'statistics_typing or finite_population_interval or census_requires or confirmatory_evaluation_verifies or consistency_formal_entry_closure or inspection_uses_archive_size' -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_quality.py -k statistics_is_checked -p no:cacheprovider --tb=short
python -B tools/quality.py
git diff --check
```

個別mypyは追加の--cache-dirをリポジトリ外へ指定し、関連統計pytestはJUnitも保存した。上記の全範囲指定は移行基盤の診断用であり、常設scopeを拡大したわけではない。前者の終了2は既知の名前衝突、後者の終了1は未移行moduleの診断であり、既存通常strictの失敗ではない。Ruff差分はapply_patchで適用し既存format除外は維持した。

未解決の作業blockerはない。ただし得点収集ローカルのlist[Any]は、既存any(...)値域検査の型絞り込み限界と動作維持のための明示的動的境界として残す。任意の非JSON数値/独自オブジェクトまでの静的保証や、結果の全状態を区別する精密な型化は今回行わない。JSON boolを0/1相当とする既存挙動も、型付けを理由に修正しない。将来の型introspection、全本番の自動対象化、残り8本番ファイル、全テストのstrict化は未実施。性能比較は未測定であり、実行時間差を改善/悪化と解釈しない。

次の第一候補はopenings（依存するrefereeが既に型付きで、プロセス/時計契約へ手を広げずに進められるため）。再開時は本6対象のstrict成功と対応済み証拠を確認し、公開入力/戻り値と母集合IDの契約を調査する。データ契約を変更するなら必ずモデル先行で合意する。今回は**Phase 0 PASS・Phase 1 PASSで終了し、Phase 2以降の実装へ進まない**。

## mypy strict拡大 Phase 2（指定対象reversi_player/reversi.py）

### 開始確認・型付け方針（実装前）

今回の指定はAI本体であり、前回記録の次候補openingsは変更しない。開始HEAD=a199ab0、mainはorigin/mainより1commit先、作業ツリーclean。AGENTS、現在のPhase 1 PASS記録、設定、仕様の入出力、architecture/contractsを確認。QUALITY_BLOCKERS.mdは存在しない。専用一時領域reversi-strict-phase2-m1883whcへ全tracked開始hash・元reversi.py・診断logを新規保存した。既存6対象の通常strictは成功。前段階のquality-26c2ba70645344f1aaad11da22119b1cはpassed=true、31source開始/終了/現在hash一致、publication照合[]。過去の記述だけで開始可否を決めていない。

本体単独の修正前strictは47件。20関数の注釈と呼出先型の不足が中心で、現行依存はrandomだけ。Pos座標/盤面はint/list[list[str]]、無効色を拒否するcolor引数はstr、反転終点と内部genmoveはPos|None、標準出力専用関数/mainはNone、resultは既存4文字列のLiteralとして表現する。探索・shuffle回数・ルール・例外・通信処理は変えず、注釈以外の実行AST/bytecodeを比較する。

設計判断：型注釈だけの変更で、AIがreferee/予算traceをimportする設計へ変えない。GUIからのPopen、seed adapter経由runpy、harnessからの外部通信という図の境界、schema5/trace4/validation3、name/ask/play/genmove/showboard/result/quitは不変。よってモデル図/データ契約の変更は不要と判断し、実装前に理由を記録する。常設対象の追加と型検査用の名前解決設定だけは更新し、実行時package構造や__init__.pyを変更しない。

#### Pos.__eq__の限定的な動的境界

既存比較はp.x/p.yを直接参照し、同じ座標を持つ別型を受け付ける。x不一致ならyを読まず、欠損時はAttributeErrorとなる。さらに他方の座標オブジェクトの比較が文字列を返す場合は、andがその文字列を返すことを実測した。単純にboolへ宣言すると既存の動的戻り値を偽る。Pos引数に狭める案はobject.__eq__とのoverride違反、object引数案は属性アクセス違反となる。Any引数＋bool戻り値案もno-any-returnとなり、Any引数＋Any戻り値だけが実際の任意比較結果を正確に記述する。

したがってAnyは__eq__の引数/戻り値2注釈だけに限定し、他の19関数・座標・盤面へ拡大しない。既存のduck typing、短絡、非bool結果、例外を回帰固定し、ASTベースでAnyを他の位置へ追加すると失敗するguardも用意する。全関数をAnyへ逃がす/検査を除外する方針ではない。isinstance/NotImplemented/bool()/castを追加する案は実行時の比較・例外を変えるため不採用。型文字列プローブの最初の-c実行は自動検出filesと競合したため、プローブだけ--config-file=として再解析した。既存プロジェクトのstrict設定を無効化したわけではない。

単独strict成功後に本体だけをfilesへ追加する。既知のreversi/reversi_player.reversi二重名を避けるためexplicit_package_bases=trueを採用し、既存6対象と新本体の解析graph、runtime import不変を確認する。これはmypyのmodule名解決だけの設定で、Python実行時importを変えない。Phase 2以外へ対象拡大しない。型付けが完了するまで既存成功証拠を新しい実装の証拠として流用しない。

本体変更前の新Pos回帰21件は21成功・40除外0.69秒。常設scope回帰をstatistics/本体のパラメータ化へ拡張し、元statisticsのcaseと動的境界guardは成功したが、本体の未注釈shadow probeだけは「6source成功」で1失敗・2成功・98除外7.35秒となった。対象漏れを先に実測し、期待値を緩めずに常設追加する。20関数、盤面と候補listへ注釈を追加し、__eq__docstringは実際の動的挙動に合わせた。単独strictは47→0で成功したため、本体だけを7件目へ追加しexplicit_package_basesを有効化した。既存の6対象・strict=true・format除外・runner/timeoutは変更していない。

通常mypyも7source成功、関連lint成功。Ruffが示したtest_qualityの整形だけapply_patchで反映し、既存format scope9files成功。本体/GUI接続/標準入出力/不変条件の3既存testファイルは76成功2.48秒。常設scope2ケースと動的境界guard・その5改変（引数Any、別alias、type ignore、cast、no_type_check）は8成功・98除外7.02秒。guardはast.parse(type_comments=True)でignoreも検出し、元ソースを変更せず検査自身の有効性を確認した。

処理互換確認では20関数の注釈/docstringを除いたASTと実行bytecode/定数/参照名がすべて一致。固定seed 0/1/2/17/42の5完走対局は全着手出力・各盤面・勝敗・最終random状態が変更前後で一致した。これは棋力評価ではなく、shuffle呼出や選択の不変性確認である。実seed adapterを使った別プロセスstdin比較も正常一連コマンド、EOF、不正整数、空行、未知コマンドの5ケースでstdout/exit/末尾例外情報が一致した。注釈で変わるtracebackのファイル/行番号の一致は要求しない。

実graphの明示7件にはreversi_player.reversiが含まれ、旧6件は同じmodule名でstrict有効/ignore_all=Falseを維持。加えてdocstringだけのharness/toolsの__init__と、ソース本体を持たないreversi_player namespaceが解決される。reversiという二重名も__init__.py追加もない。validate_model=[]、AIからharnessへの新importなし。from __future__とTYPE_CHECKING内の型はper-callの処理を増やさず、20関数の実行命令同一を確認した。型introspection利用や任意外部オブジェクトの完全静的保証は対象外である。

対象scriptのsource hashは注釈でも変わるため、新評価のtarget識別/fingerprintは契約どおり変化する。旧source hashや評価データは書き換えず、同じ強さ/同じbytecodeを同じコード版と偽らない。これから通常の統合品質ゲートを新規UUIDへ実行し、最終sourceと証拠の一致を確認する。実行中は対象source/config/modelを変更しない。

### Phase 2：PASS / 最終証拠と完了監査

`python -B tools/quality.py` は終了0 / QUALITY PASS。新規証拠は `evaluation_results/quality-a3dea815f3c4439fb5464dcdd4992b1a/`。全pytest **525成功・0失敗272.93秒**、独立smoke **1成功・0失敗524除外2.99秒**。旧497＋Pos21＋scopeパラメータ追加1＋境界guard1＋guard改変5=525。typing.txtは7source成功を記録し、依存/lint/format/strict/model/全pytest/独立smoke/snapshot/evidenceを含む必須10検査がすべて成功した。

| 要件 | 現在の根拠 |
|---|---|
| 前段階のPASS・現行状態 | Phase 1証拠と開始31sourceが一致。開始時の通常6対象strict成功。HEAD/作業ツリーを実確認 |
| 診断・依存の分析 | 本体47診断、randomだけの元依存と20関数、Noneを返す経路、動的比較を調査 |
| 最小型付け・既存動作 | 単独47→0。20関数の実行AST/bytecodeが一致、5固定seed完走対局と5実stdinケースが一致 |
| 常設scope・既存品質 | 明示6→7、既存6件/strict=true維持。explicit_package_basesで本体の正規名を確定。型検査用__init__追加なし |
| 回帰・guard | 本体関連76件、scope/限定Any/guard改変8件成功。追加前には本体scope漏れを1失敗で再現 |
| 統合・証拠 | 全525＋独立smoke1成功。full/smokeともcomplete・errors=[]、publication照合[]、Markdown再生成一致、pendingなし |
| モデル双方向整合 | validate_model=[]。外部script/GUI/seed adapter境界・random選択・正式契約・schema5/trace4/validation3は不変 |
| 最終ソース対応 | 31source開始/終了/現在hash一致。最終追記は対象外の本進捗記録だけ |
| 保全・範囲 | 変更6ファイル、他62trackedは開始hash一致。HEAD=a199ab0のまま、ステージ/commit/push/branch操作なし。次フェーズ未実装 |

変更した6ファイル：reversi_player/reversi.py（20関数・盤面/候補の注釈と比較docstring）、pyproject.toml（本体追加/名前解決）、tests/test_result.py（既存比較/座標21回帰）、tests/test_quality.py（scope拡張と動的境界/guard自身の回帰）、ハーネス.md（7対象・制約の操作説明）、QUALITY_PROGRESS.md（本記録）。ハーネス本体・statistics・web.py・学習/対局資産は編集していない。モデル/データ契約に意味変更がないため図を変更しない判断を先に記録し、実装後に依存/処理/公開境界を照合した。

主要な実行コマンド：

```powershell
python -B -m mypy --no-incremental --config-file pyproject.toml
python -B -m mypy --no-incremental --config-file pyproject.toml reversi_player/reversi.py
python -B -m ruff check reversi_player/reversi.py tests/test_result.py tests/test_quality.py --no-cache --config pyproject.toml
python -B -m ruff format --diff tests/test_quality.py --config pyproject.toml
python -B -m ruff format --check . --no-cache --config pyproject.toml
python -B -m pytest -q tests/test_result.py -k pos_typing -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_quality.py -k 'promoted_modules or reversi_dynamic_annotations' -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_quality.py -k 'promoted_modules or reversi_dynamic' -p no:cacheprovider --tb=short
python -B -m pytest -q tests/test_result.py tests/test_stdin_commands.py tests/test_invariants.py -p no:cacheprovider --tb=short
python -B tools/quality.py
git diff --check
```

個別mypyは一時領域へ--cache-dirを追加し、個別pytestは同領域へJUnit/logを保存した。mypyの文字列プローブはmypy.api.runで実行し、比較契約と宣言型の診断をequality-design-isolated.json/equality-runtime-evidence.jsonへ保存。互換性・解析graphはcompatibility-and-graph.jsonに記録。別プロセス比較はsys.executable -B harness/seeded_player.py <元/現在script> 17を使用した。初回の整形差分はapply_patchで適用し、既存のformat除外は広げたり狭めたりしていない。

残る制約：Pos.__eq__の引数/戻り値2注釈だけは既存の任意比較値を正確に表すAnyであり、完全な静的保証の外に残る。これをbool()/isinstance/NotImplementedで変更することは今回は禁止された実行時変更となるため行わない。単なる型検査の抑制や全関数のAny化ではなく、正常/異常/非bool/短絡を回帰で保護し、他位置へのAny/ignore/cast/no_type_check追加はguardで拒否する。ただしguardは任意の悪意ある隠蔽を証明する認証器ではない。既存statisticsの局所動的境界も今回変更していない。型introspection、ブラウザ実操作、CI実行、別OSや実行性能の比較は未確認で、ローカルgate成功から保証しない。実行速度の差を棋力/探索性能改善としない。

未解決の作業blockerはなく、QUALITY_BLOCKERS.mdは新設していない。残りの本番型付け/全本番自動対象化は次の承認対象である。再開時は7対象の通常strictと対応済み証拠を再確認し、新しい対象を明示してから着手する。今回は**指定されたPhase 2のみPASSで終了**し、次のフェーズ・commit・pushには進まない。
