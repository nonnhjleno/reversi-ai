# Codexの長時間検証・待機の最適化

## 現在の判定

実行・待機方針の変更、最終ソースのFull、現存確認したプロジェクト資産の保護は検証できた。2026-11-08の指示に従い、今回の運用改善はmainへの統合対象とする。過去Fullの外部Temp証拠9件の欠損・保護未証明は未解決事項として維持し、今回の統合可否とは分けて判断する。欠損の再構築や同じ復元調査は繰り返さない。

作業は `chore/codex-test-optimization` の独立worktreeで行った。最適化検証の開始・終了HEADは `c1cda2cee8889836b4d7afca2f6f400cd7f0ad11` で、この検証時点では元の `feat/ai-phase5` とmainの参照・作業ツリーを維持していた。2026-11-08の明示指示により、3ファイルをコミットしてローカルmainへ統合し、Phase 5には未コミット変更がなく安全な場合だけ取り込む。pushとtag作成は行わない。変更は `AGENTS.md`、本報告、[集計JSON](20261107_codex_test_optimization_metrics.json) の3ファイルだけである。

## 観測と採用方式

従来の会話には、1000ms指定のwrite_stdinが実際には約5秒で返り、その待機を反復した例があった。大量の証拠・エラー表示が切り詰められた例もあった。一方、既存Fullは `tools/quality.py` が直列実行・完全ログ・JUnit・events.jsonl・quality/evidence JSON・実argv・終了コード・所有tree回収をすでに提供していた。

新しいラッパーは追加せず、既存session/cellの約60秒待機と、必要な結果だけを返す運用を採用した。新しい常駐・独立プロセス管理は、寿命・完了マーカー・孤児化の責務が増え、今回必要な機能は既存runnerで満たせるため採用しなかった。AI・評価関数・探索設定・ハーネスの責務/API/正式契約は不変なので、その図と契約も変更していない。

## 検証結果

| 項目 | 結果 |
|---|---|
| 最終Full | PASS、全11検査成功 |
| 全pytest | 1391件、failure/error/skipは0 |
| 独立smoke | 1件成功、全pytest後に独立実行 |
| テスト集合・必須argv | 保存済み従来集合と一致、選択・分類・閾値不変 |
| 前後source hash | 一致、最終Fullの原証拠公開検証も成功 |
| 元のプロジェクト | 117ファイルの開始時hashと一致 |
| 既存evaluation_results | 8578ファイル・3190561333 bytesの開始時hashと一致 |
| 故障注入 | 11項目成功、追加の別cwd入口も成功 |
| Codex利用枠・トークン削減率 | 未測定 |
| 過去の外部Temp証拠 | 10参照欠損、9件は同一hashの複製なし。保護時点は未証明 |

故障注入は正常0、異常7、急終了9、開始不能、既存ログ拒否、JUnit衝突、timeout、所有親子回収、別cwd、別出力再実行、gate中断を含む。stdout/stderrはNUL・非UTF-8 bytesも既知のbytesと一致した。中断の試験では部分ログを保持し、成功quality/evidenceを公開しないことを確認した。これらの故障注入はFull成功の代替ではない。

## 実行・待機の使い方

Fullは従来の `python -B tools/quality.py` を対応する直接CPython3.13.5で一度起動する。継続session IDは保持し、原則 `write_stdin(session_id=同じID, chars="", yield_time_ms=60000, max_output_tokens=1000)` で待つ。この環境では起動待機が最大30秒のため、長時間処理はsession待機に移る。functions.execがcell IDを返したときは同じcellへfunctions.waitを使い、pendingの処理を重ねない。

実際の長時間監視では、55秒の既存session待機を一つのfunctions.execセルへまとめ、状態と出力を保持した。外側では同じcellの完了通知を待った。同期呼出しの上限を考慮して55秒にし、短間隔の状態確認とログの再読取りを避けた。観測timeout・一時的な取得失敗だけでコマンドを再起動しない。

開発中は既存の `python -B -m tools.feedback --profile quick` または `--profile standard-ai` を使えるが、Fullの代替にはしない。新規ラッパーの使用手順はない。個別の長時間コマンドには、必要なら既存APIをルートから次のように呼ぶ。

```powershell
python -B -c "import json,sys,uuid; from pathlib import Path; from tools.quality import Step,run_step; root=Path.cwd(); out=root/'evaluation_results'; out.mkdir(exist_ok=True); log=out/('related-'+uuid.uuid4().hex+'.txt'); argv=(sys.executable,'-B','-m','pytest','-q','tests/test_quality.py'); r=run_step(Step('related',argv,1800),root,log); print(json.dumps({**r,'log':str(log)},ensure_ascii=False)); raise SystemExit(0 if r['passed'] else 1)"
```

runnerは子の実終了値をJSONへ保存し、この例の外側終了値は成功0・失敗1とする。開始不能/timeoutのNoneを成功にしない。Stepの外部上限はAIのgenmove/探索予算と別である。別cwdでは明示rootとimportパスを設定する。上の個別例だけでFull用JUnitや原証拠を生成したことにはならず、必要な証拠は既存Full/feedback/harness入口で従来どおり保存する。

成功時は終了値・検査件数・失敗数・証拠パスを優先し、失敗時は該当stepのJUnitと必要なログだけを読む。巨大なerror文字列や配列も件数と先頭数件にする。完全な元ファイルは残す。最終Fullのサマリーは383 UTF-8 bytes、保存quality JSONは16109 bytes、evidence JSONは2577494 bytesだった。これは表示表現の大きさであり、以前のAI読込み量やトークン削減率を測った値ではない。

## 測定効果と限界

同じ30秒コマンドを2回使い、保存先と待機間隔だけを変えた。両方の終了値は0、完全ログ各1048608 bytesは同一だった。待機呼出しは5回から1回（80%減）、起動を含む内部ツール呼出しは6回から2回になった。ただし両試行ともfunctions.exec内でまとめたため、モデルへ戻るexec/waitは各2回で同じだった。モデル起動回数、実トークン、5時間利用枠の削減は実証していない。

OpenAI公式資料でもcontext・reasoning・tool use・retrieval・caching等が利用量に影響すると説明されているが、本試験から内部計算や個別消費量は求められない。[OpenAI公式Pricing](https://learn.chatgpt.com/docs/pricing)（2026-11-07確認）。API料金やbytesを利用枠へ換算しない。

## 失敗・追加実行を含む履歴

Fullは合計4回実行した。最初は1800秒でtimeoutし、1207件完了の部分結果とevidence未完了を失敗として保持した。対応テストのcall時間は過去約441秒に対し約1710秒で、遅いオラクル1件も約104.8秒だった。単独再現は約57.8秒で成功したが、遅延原因は未確定でOS設定は変更していない。

2回目は成功したが、最終照合でGit checkoutの改行変換による43ファイルのbyte差を検出した。固定positional依存も含まれるため、元の作業ツリーに触れず、作業側を開始時の正確なbytesへ復元した。Python ASTも一致し、Git差分はAGENTSだけだった。この開始時確認の不足により追加Fullが必要となり、再実行回数の削減を実証したとは主張しない。

3回目は1391件の記録が完了したが、既存100ms quit期限の1件が失敗した。同じテストを期限変更なしで独立に10回再現し、失敗0件を保存してからFullを取り直した。3回目を成功へ読み替えていない。4回目が現在のFull成功証拠で、pytestは約1130.6秒、smokeは約5.6秒だった。

孫プロセスの故障注入では、猶予だけでは出力を観測できず、Windowsの非表示孫へstdout/stderrを明示して成功した。遅延計測driverの最初は明示rootのimport不足で終了4となり、その失敗も保存した。診断で巨大エラーを表示してしまった点は、件数・先頭数件から読むAGENTS追記で対処した。これらは本番の閾値・期待値・ゲートを緩めたものではない。

## 証拠と未達事項

[最終Full](../../evaluation_results/quality-9afa72e6d87e4bb28f95dd03a1242ea1/quality.json) と、[証拠アーカイブ](../../evaluation_results/codex-test-optimization-evidence-ba1afcb2ea7a.zip)、[アーカイブhash・内容照合](../../evaluation_results/codex-test-optimization-evidence-ba1afcb2ea7a.zip.json) を保存する。アーカイブには今回の全試行、既存runner故障注入、比較、保護hash、外部artifactの同一bytes複製と対応表、最終ソースsnapshotを含める。ZIPとpublication JSONをセットでバックアップする。Git管理外なので新checkoutへ自動で引き継がれるとは扱わない。

外部Tempはpytest整理で消える可能性がある。今回作成した4品質報告の参照bytesは改変せず複製し、hash名の平坦な保存形式にしてWindowsパス長制限を避けた。raw参照を書き換えて資格を作らず、復旧時は対応表に従い同一bytesだけを扱い、異なる既存ファイルは上書きしない。

過去quality-c84f00…の10参照は欠損しており、9件に同一hashの複製はない。開始時に外部Tempの存在を採取しなかったため欠損時点は確定できない。利用者もバックアップなし・不明と回答した。保存済みXML/ログのhashとテスト集合は照合できたが、過去PASSの再認証・全範囲の原証拠保護は未達のままで、今回の新しい成功根拠には使わない。

未確認なのはCodex実利用枠・トークン効果、過去外部Tempの保護、別OS、アプリ終了/ホスト障害からの独立生存である。独立process wrapperは採用していない。origin/mainは開始時のローカル参照との一致までで、remoteをfetchしていない。過去原証拠の全範囲保護は未証明のまま残し、今回の運用改善と既存Fullの適合から判断する統合可否とは分離する。


## 継続監査：pytest一時領域の残存コピー（履歴）

参照元のpytest一時領域に範囲を限定し、同じファイル名の候補2609件をSHA-256で照合した。共通のopening-population.jsonには121件が一致したが、残る9件の原証拠には同一hashがなかった。読み取りエラーは0件。利用者の回答はバックアップなし・不明であり、原traceの再構築や期待hashの変更は行っていない。

[継続監査JSON](../../evaluation_results/continuation-temp-search.json)（SHA-256 `fe646e0ee52e583550cd9914117df5b00f044c47945cd392f710509705157093`）へ結果を保存した。既存のZIPは前ターン時点の封印snapshotであり、この追補JSONは別途保管する。Fullを再実行せず、現在のsource hashと最終Fullの原証拠公開を再確認した。9件の復元または開始時の存在を示す外部証拠がない限り、全範囲保護の完了は証明できず、Goalは未完了である。


## 当初のGoalブロック判定（履歴）

同じ外部証拠不足を3回のGoalターンで確認した。最後の確認でも9件の原パスは存在せず、利用者のバックアップ回答はなし・不明だった。現行Fullとsource hashは適合しているが、過去外部一時証拠の保護は証明できないため、Goalをblocked（外部証拠待ち）とする。再開には、欠損9件の期待SHA-256と一致する元bytes、または最適化開始前に既に欠損していたことを示す確実な資料が必要である。原traceの再構築、hash/validatorの変更、過去PASSの読み替えでは解消しない。

[3ターン目のブロック監査](../../evaluation_results/blocked-audit-third-turn.json)は追補として別途保管する。Fullの再実行は行っていない。


## 2026-11-08の統合判断

運用改善の統合と過去Temp証拠9件の欠損は、利用者の明示指示により分けて判断する。当初の未完了・blocked記録は履歴として維持するが、欠損の復元を今回の統合条件にはしない。現行品質の閾値や正式評価契約を緩和したものではなく、欠損を解消・再認証したとも扱わない。

対象はAGENTS、本報告、集計JSONの3ファイルのみ。AGENTSを含む検証対象44ファイルは最終Fullのhashと一致し、品質設定・テスト集合・探索・評価・ハーネス・固定資産に変更はない。報告・集計の更新はFullの実行対象を変えないため、同じコードへの理由のないFull再実行を避け、既存Fullの原証拠公開・source hashとGit差分を再検証して統合する。

[今回のローカル統合記録](../../evaluation_results/codex-test-optimization-integration-731dfd0ece9947d8a32649b881419b3a/integration-final.json)へ、実際の各HEAD、作業ツリー状態、変更ファイル、Phase 5への反映結果を記録する。自己参照するcommit hashを本文へ埋め込まず、統合後の値はこの別記録と最終作業報告で示す。元の証拠ZIPは検証時点の封印snapshotとして変更しない。Git管理外の必要な証拠は既存bytesのままmain側の評価領域へ複製し、別worktreeの削除でリンク切れになることを防ぐ。
