# 深度5の実効性とFull timeout調査

## 結論

- A1の `response_config.json` は `max_depth: 5` で、対局時にA1用コピーの `reversi.py` が起動対象になっていた。`harness.protocol.Player` は対象スクリプトの親ディレクトリをcwdとして子プロセスを起動し、親からの `REVERSI_EVAL_CONFIG` を引き継がない。通常対局では `timed_search.load_policy()` が同じディレクトリの設定を読み、`ResponsePolicy` に深度上限を渡す。このため、設定の伝播経路はコードと実験資産で確認できた。
- ただし、保存済み540局の各手には `completed_depth`、`max_depth_started`、`stop_reason` がない。従って、各手で深度5を開始・完了したか、深度5の結果が最終着手として採用されたかは遡及確認できない。
- 保存済み対局の対応270組はすべて棋譜全体と最終盤面が一致し、最初の着手分岐はなかった。直接対局の先後交換50組ではA0・A1とも勝ち点は平均1.0/2局、組ごとの差は50組すべて同点だった。実戦棋力の改善はこの標本では確認されない。結論は「設定は実対局プロセスへ伝播しているが、深度5の各手の実効完了は証拠不足。保存局ではA0とA1の着手・結果は同一」である。
- 2026-11-14のFullはpytest上限1800秒に達してFAILだったが、2026-11-18の同じ品質ゲートは上限を変えずにPASSした。調査した証拠は単一の停止テストを原因として示さない。過去Fullとの実行時間差は広範な実行変動を示し、ディスクコントローラーエラーも時間帯に記録されていたが、ワークスペースディスクとの対応と遅延への因果は確認できない。根本原因は特定不能であり、1800秒上限やテスト契約を変更する根拠はない。

## 調査対象と分岐元

調査ブランチ `fix/depth5-quality-investigation` は、最新の `origin/main` `a4365a4d0c97e16b1131ac40475cc5f434c2781a`（2026-11-14にPR #19として統合された深度5実験）から作成した。今回、Rangerの探索実装・評価関数・時間配分は変更していない。既存の品質契約、trace4、validatorの深度上限4、提出資産にも変更はない。

## A1設定と保存棋譜

実験資産のA0/A1はAIソースが同一で、設定ファイルだけが異なる。

| 資産 | A0 | A1 |
|---|---|---|
| `response_config.json` SHA-256 | `092b2c88fdc3c341c3bbb72e88eb8df853135c882a0f8e0f7bd4535ee28526ef` | `05876f0e281bc9e54ec895fbf4c1333f2ec1d5460f9f8aae77fd59f704595424` |
| `max_depth` | 4 | 5 |
| `reversi.py` SHA-256 | `908e79f62b7ae8496fe2ce4fb18af8b669e0f6201cd48be309d538a5dfef34e0` | 同左 |
| `timed_search.py` SHA-256 | `a866c8405feea25f7924dac76acf720a4b9d9454e45d40b0be20d6abf8fb1e69` | 同左 |

保存済み `commands.jsonl` の実行先はそれぞれ `players/a0/reversi.py` と `players/a1/reversi.py` で、評価結果のtarget path・SHA-256とも一致する。両設定の目標時間7秒、新反復開始余裕1秒、停止余裕0.5秒も一致する。実行資産では深度5がA1へ設定されていた。一方、通常対局記録では探索traceは無効で、正式budgetの探索欄も空、`budget_status` は `unknown`。従って実行時の深度完了・着手採用を直接示す計測はない。

`evaluation_results/depth5-7s-20261114/results/` の保存JSONを `game-analysis/analyze_saved_games.py` で再解析した。A0/A1の直接対局100局（先後交換50組）は全50組で全手順と最終盤面が一致し、各組で勝ち点1.0対1.0、A1−A0差0。固定 `search_v1` との100対応組、`positional_v1` との100対応組、`virtual_d2` との20対応組も全て棋譜・最終盤面が一致した。総計270対応組、540局で、棋譜は異なるが集計結果だけが一致したケースはなかった。

プレイヤーコマンド記録にある全genmove応答時間は次のとおり。計測は外部応答時間であり、内部探索時間ではない。

| 対局条件 | AI | 応答数 | 平均 | 中央値 | p95 | p99 | 最大 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 直接対局 | A0 | 2748 | 45.64 ms | 32.70 ms | 133.52 ms | 182.34 ms | 374.65 ms |
| 直接対局 | A1 | 2748 | 45.21 ms | 33.43 ms | 129.74 ms | 183.11 ms | 281.39 ms |
| `search_v1` 対戦 | A0 / A1 | 2748 / 2748 | 43.46 / 43.06 ms | 30.36 / 30.70 ms | 132.22 / 128.29 ms | 175.37 / 169.86 ms | 265.78 / 309.22 ms |
| `positional_v1` 対戦 | A0 / A1 | 2778 / 2778 | 39.06 / 38.85 ms | 30.33 / 30.49 ms | 110.78 / 108.51 ms | 147.13 / 146.56 ms | 205.42 / 272.03 ms |
| `virtual_d2` 対戦 | A0 / A1 | 550 / 550 | 42.45 / 44.12 ms | 30.34 / 34.02 ms | 120.30 / 128.46 ms | 163.16 / 170.55 ms | 240.46 / 233.99 ms |

応答時間の小差は探索深度が同じだった証拠にも、深度5完了の証拠にもならない。直接対局のA1−A0同一局面応答差は2748件で平均−0.426 ms、中央値−0.005 ms。対戦相手別の差も小さく、環境ノイズを含む観測値として扱う。

### 追加診断の範囲

対象のGUI連携テスト `tests/test_result.py::ResultCommandTests::test_web_process_gets_board_legal_moves_and_result` を単独実行し、2.235秒で正常終了した。さらに `tests/test_result.py` と `tests/test_stdin_commands.py` の関連群も222件、6.387秒で正常終了した。原記録は `evaluation_results/depth5-investigation-20261118/target-web-process-only/` と `web-related-tests/` にある。これはGUIプロセス・通常コマンド経路の回帰確認で、過去540局の深度完了を復元するものではない。

同じ証拠領域の `target-web-process/` には、最初の診断呼出しでFull用pytest引数全体を渡し、対象nodeidを絞り忘れたため300秒で停止した記録がある。この呼出しは対象単独テストの結果でもFull品質結果でもない。続くnodeid指定の単独実行は別の新規証拠領域で正常に完了している。

## Full pytest 1800秒超過の調査

2026-11-14の原証拠 `evaluation_results/quality-4c2f604f661d472a83e3f73892130cfd/` ではFullはFAIL、pytest stepの終了コードはnull、実時間1800.61秒、品質上限によるtimeout記録だった。1644件を収集し、1551件のcall phaseが完了、完了分の失敗は0件。最後にsetupが記録された `tests/test_result.py::ResultCommandTests::test_web_process_gets_board_legal_moves_and_result` にcall結果はないが、後日の単独実行は2.235秒でPASSし、関連群も6.387秒でPASSした。したがって同テストを原因とは特定できない。

同日の完了1551 call phaseは合計1702.88秒、平均1.098秒、中央値0.009秒、p95 6.888秒、最大55.84秒。Fullのpytest wall timeにはこの合計に加えて約97.7秒の収集・setup・teardown等が含まれる。過去のPASS `quality-2d5ad5e6b9d542bbbfa4b3db8078532f` は1639 call、pytest 1356.51秒、call合計1289.52秒。両者で共通する1545 callでは、今回失敗したFullの完了分が約403.25秒長かった。新規depth5関連6 callの合計は約13.48秒で、約403秒の差を説明しない。単独のGUIテストやdepth5テストが長時間停止したという証拠はない。

Windows Systemログには該当時間帯に `\\Device\\Harddisk2\\DR2` のcontroller error（Event ID 11）が3件あった。Kernel-Powerおよび復帰イベントは同時間帯に見つからなかった。実行環境のディスク対応情報は権限のため取得できず、このエラーが対象workspaceのI/Oやpytest遅延を引き起こしたとは確認できない。CPU負荷やスリープの十分な計測も残っていない。よって特定できたのは「個別の失敗テストではなく、多数の共通テスト所要時間が広く増えたこと」と「同時期に記録された未帰属のディスクエラー」までで、timeoutの根本原因は未特定である。

### 最終Full再実行

最終ソースに対し、ルートで `python -B tools/quality.py` を1回実行した。1800秒上限を含む設定を変更していない。

- 品質結果: PASS（全11 step、終了コード0）
- pytest: 1644 passed、1165.52秒。pytest stepは1170.06秒
- smoke: PASS、14.03秒
- 原証拠: `evaluation_results/quality-e6ee334b75164169bcce4a22bada7536/quality.json`、`pytest/events.jsonl`、`pytest/pytest.xml`、`pytest.txt` および各stepログ

このPASSは今回の実行環境・実行時点での結果であり、2026-11-14のtimeout原因の特定や提出サーバーでの実行保証を意味しない。

## 判断

深度5設定の伝播経路は正しく、保存済み実戦棋譜もA0と一度も分岐していない。各手の深度5完了・採用が記録されていないため、深度5が全局面で実効したとは断言できないが、今回のGoalで追加の探索実装を行う根拠はない。深度5を再評価するなら、対局前に `completed_depth` と採用結果を外部診断ログへ保存する小規模計測を設け、同一時間条件の少数対局で設定と完了率をまず確認する。今回は深度6、評価関数変更、時間配分変更へ進まない。

Fullについては再実行が上限内でPASSしたため、ソース・テスト変更は行わない。次回に同様のtimeoutが出た場合は、同じFullのイベント・プロセス記録を保全し、実行ホストのI/O・CPU・電源状態を同時記録してから原因を絞る。品質上限や必須テストは変更しない。

## 証拠・変更

- 既存540局と過去Fullの原証拠は変更していない。
- 今回の再解析・診断記録は `evaluation_results/depth5-investigation-20261118/` と `evaluation_results/quality-e6ee334b75164169bcce4a22bada7536/` に保存した。棋譜分析の再実行スクリプトとJSONは `depth5-investigation-20261118/game-analysis/` にある。
- この報告は調査結果だけを追加する。AI、ハーネス、validator、評価契約のソース変更はない。
