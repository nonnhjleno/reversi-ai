# 品質監査 第12節Aの実装・検証記録

## 対象と設計決定

2026-10-20、branch `refactor/quality-gate-optimization`、開始HEAD `0a7ab1a1bc121a0f16dcd279051de1c48590c559`。開始時作業ツリーはclean。A1（scope/証拠境界）、A2（Quick）、A3（Standard_AI）のみ実施する。B/C、AI変更、CI変更、commit/pushは対象外。

Fullの既定CLIと既存10工程を保ち、過去のPhase 10設計どおりtyping_scopeを必須追加して11工程とする。quality_gate v1の形式と正式AI評価資格は変えない。strictは既存14＋initializer2＋新feedbackの17ファイル。利用者が旧reversi_01.pyと102400-games/data.pyの保管/参考用途を確認した。本番の例外は配布web.pyだけ。

モデル/契約/操作をコードより先に更新。補助CLIはquality_feedback v1、Full未合格を固定。通常の未知/共有変更は別Fullへfallbackし、計測指定だけ独立profileを実行する。監査の190/212候補を保持し、追加した補助回帰も選ぶ。選択/未選択は全nodeidで記録し、skip、空、欠損、誤選択、raw/summary矛盾を成功扱いしない。原参照のpathは書き換えず、同一bytesのrun内保管先を再解析に使用する。

## 変更と回帰

- `tools/quality.py`: inventory、strict/Lint scope照合、必須結果と開始/終了の照合。旧10工程は削除しない。
- `tools/feedback.py`: 明示選択・Git差分の保守的fallback・pytest hook・直列実行・別kind保存・原参照保管・再収集検証。
- `tools/evidence.py`: collect_stepの原参照読取先mapを任意追加。未指定のFull経路は従来どおり。map欠損は拒否する。
- `pyproject.toml`: strict入力に3件追加。他の型緩和は追加しない。
- `tests/test_feedback.py`: scope、選択、原証拠改変/欠損、保存summary、Full誤表示、skip、fallback、collection変化、新登録の実mypy負例。
- `tests/test_quality.py`: 本番scopeを持つ現実的なfixtureへ拡張し、必須工程追加の期待件数だけ10→11へ更新。
- architecture/contracts/操作/進捗と本報告: 現行と履歴を分離して記録する。

実装前の追加回帰は17失敗（1.16秒）で未実装を検出。初期実装後17成功、既存gate関連を含む限定46成功（17.87秒）。最終追加回帰40成功（15.74秒）。strict17対象、Lint、format成功。途中に注釈不足1件と、整形差分の取込時の末尾構文エラーを検出し修正・再検証した。これらは修正前の正式Full成功として数えない。

## 完了判定

**A1・A2・A3はすべてPASS。** 最終3実行は同じ38 source hashに対して、Windowsの直接CPython3.13.5で直列実行した。pytest 8.4.2 / Ruff 0.17.0 / mypy 2.4.0 / Hypothesis 6.169.0の固定条件を維持する。旧監査時点の「すべて未実装」は当時の記録であり、現行の状態は本報告と正本の図・契約・操作・進捗で区別する。

| 対象 | pytest成功/失敗/skip | 未選択 | 時間と計測範囲 |
|---|---|---:|---|
| Quick | 231 / 0 / 0 | 598 | runner内wall 36.793秒、JUnit 23.311秒 |
| Standard_AI | 253 / 0 / 0 | 576 | runner内wall 82.981秒、JUnit 69.979秒 |
| Full pytest | 829 / 0 / 0 | 0 | pytest表示737.49秒、JUnit 737.438秒、外部step 740.478秒 |
| Full独立smoke | 1 / 0 / 0 | 828 | JUnit 4.033秒、外部step 5.086秒 |

Full全11工程の秒数和は753.881秒。CLI全体のwallではなく、独立smokeと証拠収集も含む工程値の和である。smokeの1件は全pytestに含まれる同じcaseの別実行で、固有830件とは数えない。strict17 source / Lint / format / model / scope / snapshot / evidenceもすべて成功した。

監査のQuick190 / Standard212候補は全保全し、両方に補助回帰41ケースを追加した。Fullは既存787ケースを一つも落とさず、新規42ケース（feedback41＋品質1）を追加した。新規品質1件は、全外部検査と証拠収集が成功でもscope失敗をFull成功へ昇格できない回帰である。

### 証拠の保存先

- Quick: `evaluation_results/feedback-9f6311cccf7a4607a19b0b4babe66ab5/`。
- Standard_AI: `evaluation_results/feedback-8bf64766bef94d94ac97a583f95245cc/`。
- Full: `evaluation_results/quality-9285910144e444a4bff556b67e5571d1/`。
- [機械可読な検証集計・全実argv・38 source hash](20261020_quality_feedback_metrics.json)。

各補助runにfeedback.json・selection.json・collection.json・工程ログ・JUnit・events.jsonlを保存した。Standardは原参照12件を同一bytesのままartifacts/へ保管（12ファイル）。Quickの明示原参照は0件で、全テストの任意のローカル変数や一時ファイルを保存したという意味ではない。Fullは従来のquality.json/evidence.json/evidence.mdと全工程ログを保持する。

Full quality.json SHA-256: `d506759cc5709677a1eecdf153b9280a77dce89671cc1329514014734faa8315`。

### 完了ゲートと根拠

| ゲート | 根拠と結果 |
|---|---|
| A1 scope/証拠境界 | validate_scopeと未登録/ネスト/抑制/stub/別名/例外/未知配置/initializer回帰、登録後の実mypy負例が成功。新しい例外や型緩和なし |
| A2 Quick | 231件の計画/実行/原journal/JUnit一致、未選択598を保存。空/欠損/重複/誤選択、Full誤主張、summary/raw改変と欠損を拒否 |
| A3 Standard_AI | 253件成功。固定seed互換、GUI接続、schema5保存CLI、正式入口の正常系、盤面同期/再現対局、通信失敗回収と期限を含む |
| 保存再解析 | verify_feedback両方=[]、Full verify_publication=[]、Full collect_step再収集complete/errors=[]。原参照を消した合成回帰でも保管先から再検証できた |
| 共通結果/条件 | Quick231件・Standard253件の全caseがFullに存在しoutcome不一致0。38 sourceは開始/終了/現在/3実行間で一致 |
| Full必須条件 | 従来10工程を残してtyping_scope追加の11工程すべて終了0・error=None・passed=true。既存787件を保持し、独立smokeも成功 |
| 既存機能の保護 | Git差分でharness/・reversi_player/・baselines/・CI workflowの変更0。GUIは自動接続回帰で確認し、実ブラウザは未確認 |

ソース照合には設定・課題仕様・図・契約とコードを含む。図の補助経路の矢印順は、実装の環境/モデル/scope→pip/Ruff/strict→collect-only/計画→選択pytest→snapshot/証拠照合に補正した。依存方向はfeedback→quality/evidenceとpytest→feedback/evidence_pluginのみで、harnessの逆依存・新たな正式資格入口は追加していない。

### 実行した主要コマンド

すべてルートで直接CPythonを使用した。以下の`$python`は`C:/Users/taaai/AppData/Local/Programs/Python/Python313/python.exe`。

```powershell
& $python -B -m pytest -q tests/test_feedback.py -p no:cacheprovider --tb=short
& $python -B -m pytest -q tests/test_quality.py::test_scope_failure_cannot_be_covered_by_successful_commands tests/test_feedback.py -p no:cacheprovider --tb=short
& $python -B -m pytest -q tests/test_quality.py -k 'not promoted_modules and not opaque and not typing_preserves_readonly and not budget_typing_preserves and not protocol_typing_preserves' -p no:cacheprovider --tb=short
& $python -B -m mypy --no-incremental --config-file pyproject.toml
& $python -B -m ruff check . --no-cache --config pyproject.toml
& $python -B -m ruff format --check . --no-cache --config pyproject.toml
& $python -B -m tools.feedback --profile quick --measure-profile
& $python -B -m tools.feedback --profile standard-ai --measure-profile
& $python -B tools/quality.py
```

149成功・16除外（149.22秒）の品質回帰を先行し、除外16件も最終Fullでは実行した。通常のprofile CLIは共有基盤変更をFullへ戻すため、この実装作業の独立計測ではmeasure-profileを明示した。通常fallbackの委譲・終了値・Fullとのkind分離・連続呼出のrun ID非重複はCLI回帰で確認した。未知変更で本物のFullを追加でもう一回起動する独立実験は行っていない。

### 途中の失敗と再検証

- 新規17失敗は実装前の意図的なred。初期実装17成功、追加40成功、最終追加回帰42成功（17.62秒）を区別した。
- strictのリスト注釈不足とmetadataの非公開re-export参照は、注釈と直接importで解消。Any/cast/ignoreで隠していない。
- 整形差分取込時の末尾構文エラー、およびmetadata import変更後のformat差分を検出し修正した。format失敗の補助報告71bad5d5/b10ae276は保存したまま、最終成功と混同しない。
- 再現コマンドがFullを指す継承metadataは補助CLIへ修正し、実行環境も保存した。fallbackの固定名run IDはUUIDを引き継ぐ名前へ修正し、連続CLI呼出で非重複を検証した。
- scope用fixture拡張により既存の実CLI回帰でFileExistsErrorを検出。toolsディレクトリの再作成だけをexist_okで修正し、失敗期待値は変更していない。
- 自分の検証チェーンのみをPIDとコマンドで確認して停止したFull試行3件（quality-ecafe030、quality-d0489b3d、quality-fdd06c64）は未公開/未完了として残した。これらを完走成功に数えない。
- コード修正後のFull quality-42109c9cは829成功・11工程PASSだったが、最終モデル順序補正より前の資料hashである。図・操作表記を補正し、上記92859101のFullと両profileを同一の最終sourceで再実行した。旧試行の原証拠やhashは修復・上書きしていない。

## 利用方針と次の段階

日々の小変更はQuick、AI機能の作業完了はStandard_AI、PR/マージ前や共有・未知変更はFull。Quick/Standard成功をFullの代わりにしない。今回の値は最終版の単発実測であり、旧監査のphase和や別runから同一保証の時間短縮率を算出しない。Full自体は最適化しておらず、旧runとの時間差の因果やcold/warm差は統制していない。

次の候補は監査B1（mypy自己テストの起動共有）の独立した設計・同値検証であるが、**今回は実装しない**。CのCI trigger/並列化、品質基準緩和、既存case削除も行っていない。commit/push/merge/tagは行わず、Aの完了で停止する。

最終の報告更新後にも両保存先へ`python -B -m tools.feedback --verify <保存先>`を実行し、両方終了0 / errors=[]。文書リンク回帰は1成功・0失敗（0.84秒）、git diff --checkはエラー0。集計JSONを実際に読み直し、38 sourceの現在hashと一致、scope/model/publication/feedback再解析にエラーなし、追加4ファイルの存在・UTF-8読込・末尾改行・余分な行末空白なしを確認した。最後の追記は進捗・停止記録・報告/集計で、検証済みの38 sourceは変更していない。Git HEADは開始時の0a7ab1aのまま、今回の変更はすべて未コミットである。

## 保証境界

Quick/StandardはFullの代用ではない。全trace変異、全公開入口異常系、重い型検査自己テストはFullへ残す。原参照の同一bytesの保管は署名・真正性や正式AI資格を証明しない。旧policyの再解析は未知扱いで成功にしない。依存/生成物内へ本番を偽装配置する意味の自動判定、実ブラウザ、別OS、CI再実行、反復cold/warm測定は未確認。CIアップロード許可範囲/保持期間は変更しない。
