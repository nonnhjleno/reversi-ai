# 品質監査C: CI頻度整理と保守的な自動選択

## 1. 先行設計と範囲（2026-10-20）

[監査第12節](20261020_quality_gate_audit.md)のCを実装する。開始HEADは`95ae51f`、作業ブランチは`refactor/quality-gate-optimization`。利用者のAGENTS.md追記1行がステージ済みであり、そのindex/作業ファイルを変更・取り消さない。A/Bの実装・ローカルFull成功を基準とし、今回はC以外の最適化を行わない。

利用者はGitHub適用・commit/push・CI確認・mainをPR経由/quality成功必須にする設定変更まで承認した。mainへのPR自体のmergeは承認に含めず、CI確認後にPRを残す。CはA/Bのfeedbackへ依存するので、現ブランチのA/B/CをPRとして検証する。既存のステージ済みAGENTS.mdをCのcommitへ混入させない。

## 2. CI契約

- `Quality` / `quality`のjob ID・Fullコマンド・Windows/CPython3.13.5・読み取り専用permissions・資格分離を維持する。
- pushはmainだけ。全PRはopened/synchronize/reopened/ready_for_review/editedで合成mergeをFull検査し、merge_group(checks_requested)とworkflow_dispatchもFullを実行する。PRのdraft・ファイル名・未知変更によるskipを導入しない。
- feature pushのFullは止める。PRがないfeatureの自動CIは実行されず、ローカル自動feedbackと手動Fullが代わりになる。これはbranch headとmerge treeが同じという仮定による証拠流用ではなく、統合ゲートの実行頻度の変更である。
- checkoutのref上書き、continue-on-error、matrix/worker、共有journal、イベント間cancelは導入しない。main/PR/merge_group/manualは全件Fullで、Quickの結果をqualityへ流用しない。
- 常時の診断uploadは保持し、quality/evidence/JUnit/journal/ログをrun単位で追加保管する。保管7日は恒久保存ではない。
- GitHub側のmain保護はrequired status `quality`、発行元GitHub Actions app15368、strict=true、管理者にも適用、PR必須（必要承認数0）を予定する。force push/deleteは許可しない。既存設定があれば上書き前に再照合し、別制約を黙って削除しない。

公開APIの開始時観測: main protected=false、effective rules=[]。既存quality checkのapp15368/slug github-actionsと成功を確認した。YAMLだけではサーバー上の必須チェックを保証しない。適用後のAPI応答と実PRのmerge tree・チェック成功を保存して初めて外部適用を完了とする。

## 3. ローカル自動選択契約

- 既存feedbackを拡張し既定profileをautoにする。selectorとその専用単体テストの既存MだけならQuick、その他の既知AI/盤面/入出力テストの既存MがあればStandard_AI。
- shared/unknown/新規/削除/rename/型変更/競合/Git不明・不正な差分はFull。仕様・モデル・契約などdocsの意味をファイル名だけで推測せず、docsもFullへ戻す。直接指定のquick/standard-aiもFull要求を迂回しない。測定用例外は従来どおり明示profileとFull未実行を保存する。
- base→HEAD、HEAD→index、index→working tree、untrackedを別々に取得し、和集合で判定する。相殺されたstaged/unstagedの追加・削除をnet差分だけで見落とさない。renameを無効化してD/Aを保持し、NUL区切りを厳密に解析する。Git情報不明は空変更と扱わない。
- base/head commit、各層の変更、判定理由、要求profile/解決profileをroutingへ保存する。autoは同じ保存条件で再判定できるようにし、実行profileと異なるsummaryを成功にしない。旧補助証拠のpolicy hash境界は維持する。
- profile内pytestは直列のまま。Fullは従来11工程・strict17・全pytest/独立smokeを維持する。AI探索・予算・時計・trace・統計・正式資格は変更しない。

## 4. 回帰・実施順序

1. 図/契約を先行更新し、現行Bの保存証拠とsourceの差を確認する（既存AGENTS変更は別記）。
2. CIの構造検証と負例、実Git repoによる変更行列/相殺/rename/untracked/不明base、CLIのauto/明示profile/Full fallbackを先に追加する。
3. 修正前の失敗を保存してからworkflow/feedbackを実装する。YAMLの実構造検証にはPyYAML6.0.3を開発依存として追加し、BaseLoaderで`on`をYAML1.1のboolへ誤変換しない。文字列検索だけでworkflowを正しいとしない。
4. 関連回帰、strict/Lint/format、Quick/Standardと通常Fullを実行し、原証拠・sourceを照合する。
5. 承認範囲内で対象だけcommit/pushしPRを作成。実GitHub CIのFullと合成merge SHA/sourceを確認してからmain保護を設定・再照合する。未実施はPASSにしない。

公式根拠: [workflow構文](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)、[イベントとPR合成merge](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows)、[pull_request_targetの危険性](https://docs.github.com/en/actions/reference/security/securely-using-pull_request_target)、[PyYAML6.0.3](https://pypi.org/project/PyYAML/6.0.3/)。外部ドキュメントは設計の根拠であり、このrepoの実CI成功の証拠ではない。

## 5. 結果

実装前: 36失敗・4成功・41除外（10.10秒、red.xml）。CI契約とauto未実装を検出した。修正後: 関連99成功（20.21秒、related-final.xml）、mypy17対象・Lint・変更3ファイルformat成功。今回追加58件はCI15・routing43。原ログ/実Git合成repoは`evaluation_results/quality-c-1792490955/`へ保存し、開始前2テスト成功（0.71秒）と混同しない。

Quickは`evaluation_results/feedback-8d7dfd1cc7f64b43b4951190f36ba5d9/`で成功、runner wall31.357秒。既存共有変更を明示計測した結果で、autoによる差分安全認定やFull合格ではない。Standard/Full・原証拠の再収集・実GitHub CI・保護適用は続けて検証する。

GitHub認証のrepo管理権限を確認した（秘密情報は保存・出力しない）。現在featureはmainのAGENTS方針commitに対してbehind1である。mainはmergeせず、必要ならPRのupdate-branchでmain→featureを反映してstrictの最新条件を満たす。元worktreeのステージ済みAGENTS bytes/indexは維持し、remote更新後のlocal/remote差も報告する。外部未確認を成功にしない。

### ローカル最終結果

- Standard: `evaluation_results/feedback-ecd4b07bb006452f863fb30e63e4aee2/`、296成功・610未選択・0失敗/skip、runner wall59.846秒。Quickは274成功・632未選択。いずれも補助成功でFull未合格の表示・保存を維持。
- auto実CLIは現在の共有/未知変更をFullへ戻した。`evaluation_results/feedback-4cece894747b45e6a6e08a44132ef006/`のrouting.jsonが要求auto・解決fullを保存し、配下の`quality-4cece894747b45e6a6e08a44132ef006/`に全11工程PASSを保存した。
- 全pytest906成功・0失敗/skip（外部工程394.484秒）、独立smoke1成功（外部3.784秒）。工程秒和402.906秒はCLI wallとは別。旧848 nodeを全保全し58追加。strict17・scope/model・Lint/format成功。Full速度の前後差は統制した比較ではなく短縮率の主張に使わない。
- Full publication=[]、pytest/smokeのfresh再収集エラー0、両補助verifyエラー0、3実行と現在の39source hash一致。AGENTS作業bytes・index保全と非変更範囲を記録した。結果は`evaluation_results/quality-c-1792490955/local-verification.json`、実行前状態は同pre-publication-state.json。
- AI/GUI/harness・tools/quality.py・pyproject.tomlはHEADから無変更。型対象の追加/除外・判定緩和・worker導入はない。新規CI15回帰はFullのみ、routing43回帰は既存feedbackファイル全選択のため補助にも含まれる。

GitHubの実PR/CI・保護設定の検証は次の外部適用記録まで未完了。main mergeは行わない。

## 6. 外部適用の最終記録（C PASS）

上記の「未確認」は途中記録。以下を実取得した。実装commitは`b370c79`。remote featureにmainの既存AGENTS方針commitだけを反映した`66dfb31`を使い、[PR #3](https://github.com/nonnhjleno/reversi-ai/pull/3)を作成した。feature push単独のworkflow runは発生せず、PRイベントのFullが1件起動した。

| 検証 | 実結果と証拠 |
|---|---|
| 実GitHub Full | [run39418825153](https://github.com/nonnhjleno/reversi-ai/actions/runs/39418825153)、quality job118614397230、全11工程成功、pytest906・smoke1成功。外部pytest440.638秒、smoke4.437秒 |
| 実checkout | artifact evidence.manifest.git.commit=`b418195abd8d728422846bd711e22357c351c955`、PR合成merge SHAと一致。check-run自体のhead SHAは`66dfb31d0d4cfebaff44de7360540a47eb4f6fb5`であり、merge SHAのcheck一覧が空でも省略と判断しない |
| CI保存証拠 | quality-evidence-39418825153-1 ZIP312068 bytes、SHA256 `415dcebff95f87fd35700b0110fbc390515c23518247d9fe502f0cd89109f7d6`。新規保存・展開しquality完成hash、11工程、906/1件、開始終了source一致を照合 |
| main保護 | PR経由必須、strict=true、required check quality/app15368、enforce_admins=true、承認人数0、force push/delete=false。PUT後にGET/branchを読み戻してprotected=trueを確認 |
| 非変更・保全 | main HEADは`b57485d8a0ace1532cae5c2d87937230a6aec791`のまま。PR open/未merge、保護適用後mergeable_state=clean。元AGENTSの作業bytes/indexは保存前と一致 |

原証拠のrootは元worktreeの`evaluation_results/quality-c-1792490955/`。remote-ci-final.json、remote-head-checks.json、remote-merge-checks.json（空のチェック一覧も保持）、remote-artifacts.json、github-quality-evidence.zip、同名展開dir、remote-ci-evidence-summary.json、remote-protection-request-v2.json、remote-protection-applied.json、remote-protection-readback.jsonを保管する。認証情報は保存していない。downloadのredirectへ認証を転送しない。ローカルとCIの改行・checkoutの違いがあるため、異なる環境間のraw source bytes完全一致は主張せず、各実行内snapshotと実Git commitを照合した。

保護APIの初回は旧contextsと新checksの同時指定で422（oneOf重複）になり、設定未適用を確認した。同じエラーの本文を診断保存し、公式RESTのchecks形式だけへ修正すると適用成功した。保護条件を緩めた対処ではない。設定前後と拒否応答も残した。[公式REST契約](https://docs.github.com/en/rest/branches/branch-protection)を参照する。

最終報告2文書は元AGENTS/indexを動かさない隔離worktreeでcommit/pushする。文書のみの追記で、検査対象source・モデル図・契約・テスト・Fullの合格条件は変更しない。最新の文書commitについてもPR CIを待ち、結果をPRコメントへ記録する。元作業ブランチは保全のため自動同期せず、remoteとの差を最終報告する。

### 未実施・残る制約

- PR自体のmain mergeは未承認につき実施しない。main保護は既に有効だが、main上のworkflowはPR mergeまでは旧版。CのPR/full自体は今回の更新workflowで動作済み。
- merge_group/manual/main pushの各イベントを実発火させる試験、別OS、ブラウザGUI、worker、性能反復比較は未実施。これらのイベント構造はCI契約回帰で検査したが、全イベントの実稼働を証明したとはしない。
- artifact7日保持は恒久保存でなく、CIのrun外一時原参照の全保存は未対応。取得artifactだけで全raw参照のfresh再収集が可能とは主張しない。ローカルFullのfresh再収集は今回成功した。
- 外部保護設定の定期的drift自動検出は未実装。今回のAPI再確認はその時点の状態を示す。設定の将来変更・許可されたworkflow改変を技術的に永久防止する仕組みではない。
- A/B/Cで停止。AI本体・探索・正式評価契約の追加実装を行わない。
