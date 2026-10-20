# 品質監査B: 型probe起動共有・追加計測

## 1. 範囲と先行設計（2026-10-20）

[監査第12節](20261020_quality_gate_audit.md)のB1とB2を対象とする。開始HEADは`3c4f1085e76794b80e991c9a6255fb854a657f40`、ブランチは`refactor/quality-gate-optimization`、作業ツリーはclean。Aの保存済みFull（829成功）を維持した状態で開始した。対応するWindows・直接CPython 3.13.5でenvironment/scope/modelのエラーがないことを再確認した。commit/push/ブランチ操作は行わない。

- B1: `test_promoted_modules_are_checked_by_configured_strict_gate`の9パラメータ・node IDを維持し、9ファイルへ同時に独立した未注釈関数を追加したshadowを、一つのmodule-scope fixtureで検査する。通常のtyping工程のargv・fresh実行は変更しない。
- B2: 残る17型probeのpositive/negative行列と診断を保存し、fixture・inspectionのread/parse/replayを限定profileしてから追加最適化の採否を判断する。異なるshadow同士やpositive/negativeを同一の「nonzero」にまとめない。検証前提を変えるcache・公開入口の共有inspection・並列化は導入しない。
- 変更対象は原則既存`tests/test_quality.py`と設計・結果資料のみ。本番AI/GUI/harness/quality/evidenceの実行契約・17strict対象は維持する。CのCI頻度・worker導入は対象外。

### B1の契約・検出能力

9shadowのsource bytes・設定hash・既存typing argvを保存し、追加関数のAST上の行番号を独立の期待値にする。共有実行の終了値1だけでは合格にしない。全9パス・追加関数の行番号・`no-untyped-def`の診断集合が過不足なく一致し、stderrが空であることを確認し、各パラメータ自身の証拠も照合する。別ファイルのエラー、1件欠落、重複、code/行の違い、構文エラー、起動異常は失敗する。

共有結果・shadowをcase側で更新しない。Full/plugin使用時は新規step領域の`typing-probes`へ原stdout/stderr・argv・元source/config/shadowのhash・shadow bytes・case別診断を保存する。通常の限定pytestでは独立tmp領域へ保存する。journalにはcaseごとの期待・観測と保存先/hashを記録する。hashは診断の正しさの証明ではなく、各caseのassertと原診断の照合が必要である。共有setup中の障害は成功9件へ変換せずsetup errorとして伝播する。

未検出反例は9パスそれぞれの診断だけを落とした保存出力、別パス/code/行への置換、重複で検証する。実mypyでもshadowの1つを外す（元sourceを検査する）反例を実行し、残り8件で終了1でも対象caseが失敗することを確認する。さらにimportだけで対象漏れが隠れないよう、独立した最小root群で明示rootを1件除外する負例を設ける。これは通常typingのroot変更ではなく、test helperの検出能力の負例である。

### 計測・同値性

新規計測先は`evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/`。計測専用pluginはmypy subprocessだけの経過・argv・原出力・shadow bytesを保存し、戻り値や診断は変更しない。正式時計・正式Fullにはこのhookを混入しない。

変更前は26型probeを1回、うちB1の9件を追加2回、変更後も同じ構成で実行する。各起動は`--no-incremental`と新規cache-dir（mypy内部cacheを使わないcold条件）。OS page cacheの消去やCPU/電源条件の統制は行わず、同時に別ベンチマークを走らせない。3回はbefore群→after群の順であり交互対ではないことを明記する。時間は中央値・範囲・subprocess和とpytest壁時間を分ける。元26caseの期待・結果・診断path/code/行をbefore/afterで照合する。

追加回帰・限定計測で効果と検出能力を確認した後、通常Fullを実行し全既存node IDと新規回帰、独立smoke、保存証拠、source snapshotを確認する。Quick成功でFullを代用しない。B2のprofileは計測試験であって正式評価の時間測定ではなく、原traceを変更せず新規複製した変異だけを使う。

## 2. 結果

### 2.1 実装と回帰

- 診断validator・module共有fixture・raw保存・元9caseの検証を既存`tests/test_quality.py`へ追加。新本番moduleや設定追加はない。
- 新規回帰は19case: 9パスそれぞれの診断欠落、code/行/path/重複/余剰/異常終了/stderrの7反例、実mypyのshadow除外・root除外2反例、旧shadow bytes互換1件。
- 実装前の16caseは16失敗・165未選択（1.64秒）。理由は未実装validatorのNameErrorで、既存本体の欠陥再現ではない。
- 最初の元9＋新規18caseは27成功（16.93秒）。Lint/formatの整形差分は修正後に成功。
- 初回afterの全26caseは成功したが、原shadow bytes比較で改行差（旧Path.write_textのCRLF対save_newのLF）を検出。意味上の同値性だけで完了にせず、旧OS改行へ合わせ専用回帰を追加した。初回afterの記録は削除せず、最終比較には`after-final-*`のみ使用。
- 最終の新規19caseは19成功・165未選択（10.52秒）、Ruff lint/format成功。既存関数のAST比較で、元9scope test以外の既存関数・クラスは変更していないことを確認した。

### 2.2 限定計測と診断同値性

原データは`before-1/2/3`と`after-final-1/2/3`。各1回目は26論理case全件、2・3回目は元9論理caseのみ。最終全26caseのnode名・成功結果が一致し、9scope全診断の集合、他17の診断path/行/code/messageと終了値、全shadow原bytesが一致した。全測定でsource開始/終了hash一致。before/afterで変わったのは`tests/test_quality.py`と2設計資料のみで、AI/harness/設定のhashは同一。

| 指標（秒） | before | after-final |
|---|---:|---:|
| 元9caseのmypy subprocess和、3回 | 42.299 / 49.001 / 42.928 | 5.971 / 5.946 / 5.631 |
| 同中央値［最小〜最大］ | 42.928［42.299〜49.001］ | 5.946［5.631〜5.971］ |
| 他17probeのsubprocess和、1回 | 79.074 | 85.526 |
| 全26caseのpytestプロセスwall、1回 | 125.184 | 95.281 |
| 元9caseだけのpytestプロセスwall、2回 | 52.200 / 47.709 | 9.317 / 8.900 |

起動共有対象の中央値は**36.981秒・86.15%短縮**。他17件は未最適化でむしろこの測定では長く、Full全体が86%速くなったとは言わない。全26caseのwall差29.903秒も統制実験の因果推定ではない。freshなmypy cache-dirと`--no-incremental`の条件であり、OS page cache・CPU/電源・他アプリ負荷は統制していない。変更前後を交互にした対測定ではなくブロック順の3反復で、時間閾値をテストの合否に追加しない。

| 他17probe群 | case数 | 期待checker終了0 / 1 | 維持した検出内容 |
|---|---:|---:|---|
| openings | 1 | 0 / 1 | 未検証generator metadataの索引・数値保証拒否 |
| contracts | 6 | 3 / 3 | object許可 / intの過剰保証拒否 |
| readonly commands | 2 | 1 / 1 | 非変更の利用許可 / append拒否 |
| budget | 4 | 2 / 2 | 原値object許可 / intの過剰保証拒否 |
| protocol | 2 | 1 / 1 | 旧frequencyの未知値拒否 / 意図的過剰注釈で検出感度を確認 |
| analysis | 1 | 0 / 1 | 未検証3fieldの数値保証拒否、許可側にはエラーなし |
| evaluate | 1 | 0 / 1 | 未検証4戻り値・不正引数1を拒否、正しい引数にはエラーなし |

正7/負10の終了値、診断内容、原shadowはすべて一致。非zero一本への縮約・既存期待値の緩和はしていない。小さい外部analysis/evaluate probe2件はimport経由の検査であり、17root指定のshadow群と混同しない。

### 2.3 B2: fixture / inspectionの限定profileと判断

専用`test_inspection_profile.py`は初回1成功（59.39秒、setup 2.68秒/call 54.69秒）。その後、一時ディレクトリの自動削除から原traceを保護するため、新規のrun内basetempへ切り替えて同じ限定計測を実行し、1成功（55.77秒、setup 2.01秒/call 52.03秒）。既存module-scopeの`integrity_report`を使い、正常inspection、不正inspection、既存`_assert_temporal_rejected`の全公開入口拒否を各3回cProfileし、元報告と原証拠10ファイルのhash不変を確認した。以下と集計JSONのprofileは最終保管版である。正式時計・期限の合格証拠ではない。

| 対象 | profile全体3回（秒） | 第1回の主要観測 |
|---|---|---|
| 正常inspection | 0.927 / 1.074 / 1.067 | read_bytes 14回・累積0.002秒、JSON loads 1726回・0.018秒、timeline 0.462秒、deepcopy 0.177秒、legal_moves 0.167秒 |
| 不正inspection | 1.132 / 1.089 / 1.171 | read_bytes 14回・0.003秒、JSON loads 1726回・0.026秒、timeline 0.546秒、deepcopy 0.242秒、legal_moves 0.192秒 |
| 不正・全入口拒否 | 15.032 / 14.806 / 15.529 | fresh inspection 12回、read_bytes 176回・0.056秒、JSON loads 20712回・0.349秒、timeline 6.831秒、deepcopy 2.905秒、legal_moves 2.679秒 |

累積時間には親子の重複があり、横に足して全体時間へ換算しない。1 inspection内のbytes cacheは既にあり、raw以外の読込もread_bytes回数に含まれる。全入口で12回のfresh inspectionは保存Inspection信頼による迂回の検出能力を持つ。これを1回へ置換する案は不採用。JSONやreadだけを共有してもこのprofileでは主因を除けず、呼出し間cacheはraw改変の再検出を壊し得る。

他17型probeは同じmoduleに対して異なるshadow/positive/negativeを使い、単純な同時shadowは置換が競合する。B1のような9独立moduleの起動共有をそのまま適用しない。新関数名・期待診断行・positiveにエラーがないことを別々に固定した群設計と実測が必要なため、追加batchは今回採用しない。fixture setupはこの限定例で初回2.68秒/最終2.01秒であり、既存module共有を維持し、改変用rawのcase別複製を共有しない。

最終原報告は`inspection-profile-final/sample-report.json`、原trace・母集合・checkpointは`profile-final-basetemp/`下に保持する。profile後に通常の`contracts.inspect_evaluation`で再読込しstatus=verified、10原ファイルhash一致も確認した。原報告の内容や時計を保存先移動のために修復していない。

## 3. 最終品質検証

### 3.1 実行結果

対応環境はWindows・直接CPython 3.13.5、pytest 8.4.2 / mypy 2.4.0 / Ruff 0.17.0 / Hypothesis 6.169.0。通常の`python -B tools/quality.py`を1回実行し、**全11工程PASS**。

| 検査 | 結果 |
|---|---|
| 全pytest | **848成功・0失敗・0skip**、表示644.69秒、JUnit 644.641秒、外部pytest工程646.217秒 |
| 独立smoke | **1成功**、JUnit 3.818秒、外部工程4.717秒 |
| strict | 既存17 source files成功、登録変更なし |
| dependencies / Lint / format / environment / model / scope / snapshot / evidence | すべて成功 |
| 工程秒の和 | 658.927秒。CLI全体wallとは異なる |
| Full内共有probe | 9論理case成功、mypy 1起動4.956秒 |

保存先は`evaluation_results/quality-762d0017b4304bac8391fcdc785a529b/`。quality.jsonのSHA256は`f69534c3ecb4bde59bce143c7341dad2eef50c324a40457b5104cd89f8ff2ead`。

既存AのFull（工程和753.881秒）より今回は短かったが、独立した時刻・環境負荷のrunであり、差94.954秒すべてをB1の効果とはしない。比較対象を固定した9probeの3回測定と、通常Fullの合格は別の根拠である。Full全体の統制反復比較やCI速度は未確認。

### 3.2 保存証拠の再検証

- `evidence.verify_publication`のエラー0、全pytest/smokeの`collect_step`を再実行し双方complete/errors=[]、全849実行（848＋独立smoke1）のoutcomeがpassed。
- 旧Fullの原journalの選択829nodeを新848nodeが全保全。差分19nodeはすべて新規回帰。元9scope node ID、他17型probe、既存smoke node IDの欠落なし。
- 38sourceの開始/終了/現在と最終限定測定のhash一致。before/afterで不変の35sourceにAI/GUI/harness/quality/evidence/feedback/設定/CIが含まれる。探索・予算・時計・統計の本番実装は無変更。
- Full所有領域`pytest/typing-probes/`の9shadow hashをbefore原bytesと照合し、source/config hash、AST上の追加行、stdout/stderr hash、case9件の診断を独立に再照合した。保存結果のexit1だけを信用していない。
- fresh再検証と実施項目は`full-verification.json`および[機械可読集計](20261020_quality_probe_metrics.json)の`full_verification`へ保存。hash真正性・署名・すべてのプロセス環境の証明ではない。
- Fullの既存原参照14件も`full-raw-backup/`へ同一bytesの監査用コピーとして保持。元quality.json・journal・参照pathを変更せず、既存のcollect_stepへ保管mapを指定した収集結果と元収集の一致を確認した。これは原証拠の寿命を保護する監査用複製で、正式評価の迂回入口や公開Fullの新しい再解析契約ではない。

### 3.3 Bの完了判定

| ゲート | 判定・根拠 |
|---|---|
| 元9caseの独立検出を維持 | PASS: 同じnode ID・元と同じ診断path/行/code/message、全9欠落反例と実mypyのshadow/root除外を拒否 |
| source/configとrawを固定 | PASS: 元shadow bytes一致、source/config hash一致、OS改行回帰成功、rawを新規保存 |
| 起動共有の効果 | PASS: 同じcache条件で3回、中央値42.928→5.946秒。Fullの時間短縮保証とはしない |
| 他probeの群別検討 | PASS: 元17を正7/負10と外部probeの混合検査に分類し、原診断と成功/拒否行列を維持 |
| fixture/inspection計測と判断 | PASS: 正常/不正/全入口の各3 profile、原bytes不変。fresh検証共有・呼出し間cache・単純なshadow batchは不採用 |
| 通常Fullと設計の整合 | PASS: 11工程・848件・独立smoke1・strict17、モデル/scope・source snapshot・証拠再収集成功 |

**B1/B2完了**。B2の対象は監査表にある「計測・群別設計・採否判断」であり、他17probeのbatchやproduction inspection最適化を実装したという意味ではない。

## 4. 実行コマンド・成果物・残る制約

以下の`python`はすべて`C:\Users\taaai\AppData\Local\Programs\Python\Python313\python.exe`の直接実行。限定計測の実argvは各measurement.jsonとmypy個別result.jsonにも保存した。

```text
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/measure.py before
python -B -m pytest -q -p no:cacheprovider tests/test_quality.py -k scope_batch --tb=short --junitxml=.../red.xml
python -B -m pytest -q -p no:cacheprovider tests/test_quality.py -k "scope_batch or promoted_modules_are_checked" --tb=short --junitxml=.../regression.xml
python -B -m ruff check tests/test_quality.py --no-cache
python -B -m ruff format --check tests/test_quality.py --no-cache
python -B -m pytest -q -p no:cacheprovider tests/test_quality.py -k scope_batch --tb=short --junitxml=.../regression-final.xml
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/measure.py after
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/measure.py after-final
python -B -m pytest -q -p no:cacheprovider evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/test_inspection_profile.py --durations=0 --tb=short --junitxml=.../inspection-profile.xml
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/summarize.py
python -B tools/quality.py
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/verify_full.py
python -B evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/retain_full.py
```

`.../`は新規計測ルートの省略表記。profile時だけtestsをPYTHONPATHへ追加しB_PROFILE_OUTPUTを新規領域へ指定。計測時だけprobe_captureをPYTHONPATHへ加え、mypy原出力を保存する。通常Fullへ試験hook/cProfileをロードしていない。初回集計でのshadow bytes差と、その調査時のUTF-8指定漏れは解消し、旧記録を破棄せず最終版を別領域に保存した。

最終profileでは同じコマンドに`--basetemp=evaluation_results/quality-b-b3d52730b80144d8acb5095571b49a4d/profile-final-basetemp`を追加し、B_PROFILE_OUTPUTを`inspection-profile-final`へ指定した。その後summarize/verify_fullを再実行して保管版へ集計を揃えた。既存の出力を上書きした再測定ではない。

変更ファイルは`tests/test_quality.py`、`docs/harness_architecture.md`、`docs/harness_contracts.md`、`docs/QUALITY_PROGRESS.md`、この報告と集計JSONの6件。raw計測・試験instrumentation・原JUnit/profileは上記evaluation_resultsに保存し、Git管理対象外。実装や旧証拠を書き換えた計測ではない。Full内の原probeはrunが削除されるまで保持するが、外部バックアップやCI保管を保証しない。

次の候補はcontractsのpositive3/negative3とsearch_budgetのpositive3/negative3を別群に設計すること。probe名の衝突・許可側の関数欠落・診断位置変化を検出する新しい契約が必要で、protocolの型過剰保証mutationは別起動を維持する。現状の原shadowを単に同時指定する方式は採用不可。今回そのbatch・共有cache・fixture scope変更は実装していない。

Quick/Standardの再実行、実GitHub CI、他OS、統制した反復Full測定、CPU/電源固定、OS page cache排除は未実施。新しいAIの正式予算trace資格や強さを保証しない。C・次の最適化・commit/push/mergeには進まず停止する。

報告確定後の文書リンク回帰は1成功（0.94秒）。新規documentation-basetempとJUnitを使用し、既存原証拠の自動削除を避けた。git diff --check成功、HEADは開始時の3c4f108、同じ作業ブランチを維持。文書・集計以外の本番/設定変更なし。B1のrawはFull所有領域、B2の原traceとFull監査用14参照は計測所有領域に保持し、最終保存状態をverify_fullで再照合した。
