# Phase 5最終レビュー・最小改善・Phase 6への引き継ぎ

2026-11-09。AI本体と固定資産を保全し、品質stepの実経過上限判定だけを補強した。修正後のFull全11工程、pytest1611件、独立smoke1件が成功した。新規7回帰（経過時計5件と参照共有2件）はFull専用とし、既存1604件、Quick/Standard分類、strict19本、1800秒上限、正式評価条件は維持した。

依頼されたレビューと最小改善は完了した。未知サーバーでの7秒保証、外部媒体へのバックアップ、固定B timeout原因は未達のままである。Phase 6は後述の制限を保持した別Goalで開始できるが、提出環境適合や正式優越性の証明とは分ける。

## 開始状態と保全

`feat/ai-phase5`、開始HEAD `7867f02291ffbdc7c4126e4328f775fb4ada6eac`、作業ツリーclean。main/origin/mainは `518cd2aa263fbed869ed17ea9da64e126d78c1a3`。AGENTS、[設計](../phase5_design.md)、[実装・評価報告](20261108_phase5_implementation.md)、契約・図・品質記録、原証拠・ZIPの実在とbytesを照合した。他worktree（品質publication用detached b85d43d、最適化用5c28d85）は操作していない。ブランチ切替・reset・clean・merge・tagは行わない。

新規証拠領域は `evaluation_results/phase5_review/run-7ab11429a5e94675b0aecd5c20c3e6ce`。以下これを `REVIEW`、既存 `evaluation_results/phase5/run-0f94162b39214839bcff342fe327a2f0` を `OLD` と呼ぶ。開始時の実bytes/hash8577件と、調査した過去品質領域294件を記録し、最終監査で再照合する。固定baseline、AI実行5資産、AGENTS、過去の失敗・原trace・ZIPを保護した。held-outはhash照合だけに使い、調整に使っていない。

[機械可読報告](20261109_phase5_review.json)に集計と原証拠hashの対応を保存した。原traceや品質ログはGit管理外で、このJSONから復元できない。既知の外部Temp9件欠損は未解決の記録を維持し、復元調査を繰り返さなかった。今回の検証はその欠損9件に依存しない。

## Full長時間化と最小修正

既存5実行のログ・JUnit・events・実argvを先に解析した。約31749秒はfixture setupではなく、`test_temporal_all_child_events_are_revalidated[search_before_receive-failed]` のcallだった。setup0.004599秒、call31748.790248秒、teardown0.000632秒。

| quality先頭8桁 | 開始件数 | pytest外部秒 | phase合計秒 | 差分秒 | 結果 |
|---|---|---|---|---|---|
| 1bebe779 | 1391 | 860.071 | 853.902 | 6.169 | 当時PASS |
| dadc40e2 | 1486 | 1800.589 | 1789.131 | 11.458 | 当時FAIL |
| 4354c58d | 1592 | 33049.245 | 33041.501 | 7.743 | 当時PASS |
| d64f7dcb | 1600 | 616.977 | 612.800 | 4.178 | 当時PASS |
| 84907388 | 1604 | 1676.013 | 1666.895 | 9.118 | 当時PASS |
| d0fbbbdc | 1490 | 1800.656 | 未完了 | — | 今回初回FAIL |
| b5bab628 | 1611 | 1750.288 | 1740.283 | 10.006 | 今回PASS |

System電源ログには残電池3%・AC非接続・policy action3、続いてS4休止が残る。Event1のSleepTime/WakeTimeはJST 2026-11-08 08:26:24.842503〜17:15:21.122537。休止31736.280034秒は異常callの99.9606%、残差12.510214秒である。Event107のTimeCreatedは時計再同期前なので42/107の差は使わなかった。状態値5は[MicrosoftのPowerSystemHibernate定義](https://learn.microsoft.com/en-us/windows/win32/api/winnt/ne-winnt-system_power_state)に対応する。

インストール済みCPython3.13.5のsubprocess._waitはWaitForSingleObjectを呼ぶ。[Windows8以降の待機timeout](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)は低電力時間を含めず、[QPC経過](https://learn.microsoft.com/en-us/windows/win32/sysinfo/acquiring-high-resolution-time-stamps)はstandby/hibernateを含む。電源記録とこの時計差から、異常callの大部分は休止時間と判断する。ただし各テストの絶対開始時刻は保存されず、区間はevents累積による推定である。CPU/I/Oの正確な内訳は復元できず、31749秒の演算が必要だったという証拠ではない。

旧runnerはnative待機が終了コード0を返すと、実経過が上限を超えてもPASSにできた。契約・図を先に追記し、[quality.py](../../tools/quality.py)へ終了後のperf_counter照合を加えた。実終了値・原ログと既存tree回収を維持する。OS休止中にPythonが即時停止できる保証は加えない。

| 経路 | 判定 |
|---|---|
| native TimeoutExpired | 従来のtimeout・所有tree回収・FAIL |
| 待機後の実経過超過 | 実終了値が0でも保持し、timeout_elapsed・FAIL |
| 上限内の非0終了 | 実非0を保持しFAIL（errorがなくても失敗） |
| 上限内の0終了 | 他の必須証拠検査へ進む |

[独立時計回帰5件](../../tests/test_quality_phase5_review.py)は上限未満・等値・超過・33049秒相当・非0終了。旧処理で2失敗3成功を確認し、修正後は既存の終了・起動失敗・timeout・子tree・taskkill失敗を含む先行関連10件、共有追加後の最終関連12件が成功した。ホストを休止させる実験ではなく、native待機と経過時計の差を制御した回帰である。

通常1676秒実行の最大callはPhase3/4の参照探索65.146秒、48.213秒、46.888秒。独立Fractionオラクルの式・全列挙、候補探索、期待値・assertは変更していない。Phase5既存のメモ化は完全な不変入力をkeyにした参照結果だけで、候補探索はcacheされず、候補出力から期待値を作っていない。全ケースは今回も実行した。今回初回Fullは1489件完了後に1800秒で失敗した（開始1490件、失敗assertは観測せず、最終testは未完了）。原ログ・不完全JUnit・証拠失敗を保存した。この実測を受け、Phase4比較表でorderedのFalse/Trueに共通する参照計算だけを、盤面tuple・色・深度keyで共有した。候補200ケースは引き続き毎回探索し、Minimaxと独立有理数値の式・最善手・計数・全assertは維持する。別盤面/色/深度のkey、結果不変性、両orderingで候補が実行される2回帰を追加した。test内の純粋計算共有だけで実行時の意味・構造・通信・評価契約は変えないため、追加のruntime図変更は不要と判断した。変更前後の同じmiddle/黒/深度4/両ordering診断は `reference-sharing-comparison.json` に保存した。参照root計算2→1、候補2→2を検証し、実時間とCPU時間を残した。各1回・固定順の局所比較で、Full全体の速度改善率は断定しない。

証拠pluginのwrite/flushはイベントごと、fsyncはsession終了時で、テスト内hash・証拠生成もcallに含まれる。外部時間とphase合計の差は全I/O負荷の上限ではない。CPU/disk/process別の連続telemetryがなく通常負荷の内訳は未確認。今回の修正を高速化や利用枠削減の実証とは報告しない。 取り直しFullのpytestは1750.288秒で上限まで約49.712秒の余裕だった。旧最終1676.013秒より短い結果ではなく、負荷条件も揃っていないため、局所比較からFull全体の短縮率は推定しない。将来の負荷による再timeoutの可能性は残る。

## 7秒目標の検証範囲

偽時計テストは中断境界・未完了破棄・完了済み最深手・安全候補・一回適用を再現可能に検査する。既存5/7秒設定400件は全件深度4完了で、自然な期限到達を示さない。今回はAIのbytes、通常設定7.0/6.0/6.5/0.5秒、最大深度4を保全し、別processのcallbackへ実sleepを注入して各条件を1回実行した。

下表はms。CPUはprocess_time_ns、待機は実sleep合計。停止遅延は要求期限から停止確認まで、復帰は確認後selector返却まで。返却後は実着手・trace・pipe出力・ホスト読取/検証を含む区間で、flush単体ではない。

| 条件 | 完了/開始深度 | 停止理由 | 外部応答ms | CPU ms | 注入待機ms | 停止遅延ms | 復帰ms | 返却後ms |
|---|---|---|---|---|---|---|---|---|
| probe | 4/4 | max_depth_completed | 64.6184 | 62.5000 | 0.0000 | — | — | 0.2410 |
| soft_depth1 | 1/1 | new_depth_deadline | 6052.2167 | 0.0000 | 6048.2370 | — | — | 0.4436 |
| hard_depth0 | 0/1 | time_limit | 6551.6184 | 0.0000 | 6550.4161 | 51.2406 | 0.0281 | 0.3034 |
| hard_partial | 3/4 | time_limit | 6513.0122 | 62.5000 | 6412.7203 | 12.3589 | 0.0381 | 0.5898 |
| trace_partial | 3/4 | time_limit | 6191.2873 | 78.1250 | 6078.2550 | 15.2856 | 0.0617 | 0.7372 |

probeは待機なし。soft_depth1は深度1完了を6.05秒まで遅らせ新深度を止めた。hard_depth0は最初のcallbackを6.55秒まで待たせ、合法手・value=Noneへ退避した。hard_partialは深度4を破棄し完了済み3を採用した。trace_partialは明示time=6.5秒の別診断で、5%予約により停止6.175秒、新深度開始5.2秒までとなる。通常7秒設定の試行と混ぜない。

全5条件でname/ask/genmove/play/showboard/result/quit、独立referee盤面、完了深度のFractionオラクル一致、genmoveの実Board更新1回、終了0・stderrなしを確認した。外部7/8/10秒超過は各0/5。trace条件のbudget/lifecycleはverifiedだが、人工wrapperの診断で正式実AI資格へ昇格させない。

これはcallback待機であり、CPU競合・探索演算・未知サーバーを再現していない。全条件初期盤面の黒・n=1で、自然負荷7秒、各局面/先後/高負荷の最悪時間は未確認。協調的中断にはcallback中の停止確認遅延が残る。深度や予約分は変更しない。

## 100msの再解析

参考集合の表は候補Aの探索イベントを対象とする。固定Bの内部予算はunknownのまま、Aの100ms超過へ混入させない。normal_moveだけを安全候補率の分母とし、pass/terminalは分けた。commandsを独立refereeで再生しshowboardを照合。空き44以上=序盤、17〜43=中盤、16以下=終盤、合法手0〜4/5〜10/11以上、角有無、黒白、相手でも分けた。quantileはnearest-rank（ceil(p*n)-1）。探索時間SE-SSは全応答時間と異なる。

run_id・親report hash・原raw hash・QPC区間・コードsnapshotとの一致はJSONと `REVIEW/100ms-v2/100ms-derived-events.jsonl` に保存した。QPCはUTCへ未校正のため日付を捏造しない。先行資産は現行pathへ無理に照合せず保管bytesをhashで特定した。

| 集合 | 全問い合わせ | 通常着手 | 完了深度0/1/2/3/4件 | 安全候補率 | 100ms超過 |
|---|---|---|---|---|---|
| initial5pct | 5741 | 5528 | 0 / 0 / 2058 / 1553 / 1917 | 0.000% | 2 |
| guard25 | 5744 | 5531 | 0 / 0 / 2603 / 1247 / 1681 | 0.000% | 6 |
| guard120-initial | 2849 | 2747 | 101 / 835 / 1199 / 192 / 420 | 3.677% | 0 |
| guard120-accepted | 5717 | 5508 | 0 / 1417 / 2701 / 461 / 929 | 0.000% | 0 |

| 集合 | 平均ms | 中央値ms | 最大ms | p95 ms | p99 ms |
|---|---|---|---|---|---|
| initial5pct | 72.4730 | 95.0373 | 102.9202 | 95.1310 | 95.2162 |
| guard25 | 59.9065 | 75.0496 | 134.3252 | 75.1626 | 75.8439 |
| guard120-initial | 18.1152 | 20.0549 | 95.6302 | 20.2814 | 21.0536 |
| guard120-accepted | 17.7838 | 20.0488 | 22.0220 | 20.1379 | 20.2778 |

初期5%・guard25は各204局で超過2/6件。guard120-initialは102局試行・固定B timeoutで1局失敗した集合で、受入へ混ぜない。受入はreference-retry1の204局。深度1/2/3/4は25.726%/49.038%/8.370%/16.866%、深度0は0件。一方同じ最終コードの先行集合では101/2747=3.677%の安全候補を使い、退避が不要とは言えない。

受入の開始深度は `{'4': 1350, '3': 2606, '2': 1552}`、停止理由は `{'time_limit': 4404, 'new_depth_deadline': 175, 'max_depth_completed': 929}`。pass5・終局204を通常分母から外した。受入の統計は以下。

| 指標 | 単位 | n | 平均 | 中央値 | 最大 | p95 | p99 |
|---|---|---|---|---|---|---|---|
| search_elapsed_ns | ms | 5508 | 17.7838 | 20.0488 | 22.0220 | 20.1379 | 20.2778 |
| stop_delay_ns | ms | 4404 | 0.0452 | 0.0292 | 1.9855 | 0.1068 | 0.2533 |
| unwind_ns | ms | 4404 | 0.0132 | 0.0117 | 0.4357 | 0.0203 | 0.0345 |
| nodes | 件 | 5508 | 89.849 | 81.000 | 251.000 | 172.000 | 202.000 |
| leaves | 件 | 5508 | 63.405 | 60.000 | 143.000 | 110.000 | 123.000 |
| passes | 件 | 5508 | 0.656 | 0.000 | 40.000 | 4.000 | 9.000 |
| cutoffs | 件 | 5508 | 11.534 | 10.000 | 63.000 | 31.000 | 46.000 |

| 受入の区分 | 通常n | 完了深度0/1/2/3/4件 | 探索中央値ms |
|---|---|---|---|
| color:B | 2776 | 0 / 709 / 1382 / 227 / 458 | 20.0487 |
| color:W | 2732 | 0 / 708 / 1319 / 234 / 471 | 20.0490 |
| position_group:endgame | 1773 | 0 / 0 / 400 / 444 / 929 | 18.1747 |
| position_group:middle | 2801 | 0 / 886 / 1903 / 12 / 0 | 20.0603 |
| position_group:opening | 934 | 0 / 531 / 398 / 5 / 0 | 20.0837 |
| mobility_group:0-4 | 675 | 0 / 0 / 31 / 18 / 626 | 2.4798 |
| mobility_group:11+ | 2976 | 0 / 1213 / 1762 / 1 / 0 | 20.0625 |
| mobility_group:5-10 | 1857 | 0 / 204 / 908 / 442 / 303 | 20.0437 |
| corner_available:False | 4704 | 0 / 1339 / 2371 / 351 / 643 | 20.0512 |
| corner_available:True | 804 | 0 / 78 / 330 / 110 / 286 | 20.0340 |

全先行集合の同じ区分・開始深度・停止理由・counter分布と相手別統計もJSONに保存した。80%予約は100msで停止を20msへ早め、以前の95/75msより探索範囲が小さい。浅い完了深度はこの制約と整合するが、CPU負荷・局面・後続棋譜が違い、因果寄与は分離できない。安全余裕を減らして深度を上げる変更は行わず、同一固定局面の独立候補実験へ引き継ぐ。

正式time100ms各4局は両役のtraceを対象とする別集計で、参考集合とpoolしない。資格は原記録の範囲を維持する。

| 正式集合 | 全問い合わせ | 通常n | 完了深度0/1/2/3/4 | 開始深度件数 | 停止理由件数 |
|---|---|---|---|---|---|
| initial5pct-formal | 224 | 207 | 0 / 0 / 123 / 44 / 40 | {'3': 133, '4': 73, '2': 1} | {'time_limit': 155, 'new_depth_deadline': 12, 'max_depth_completed': 40} |
| guard25-formal | 222 | 208 | 0 / 0 / 65 / 82 / 61 | {'3': 68, '4': 140} | {'time_limit': 144, 'max_depth_completed': 61, 'new_depth_deadline': 3} |
| guard120-formal | 224 | 206 | 0 / 80 / 73 / 23 / 30 | {'2': 87, '3': 66, '4': 53} | {'time_limit': 169, 'new_depth_deadline': 7, 'max_depth_completed': 30} |

初回解析scriptはreport hashを着手ごとに重複計算したため、所有processだけを中断した。部分派生ファイル・失敗を保持し、v2でreportごと一度へ改め全4集合を解析した。原traceは修復していない。Git管理外の調査scriptだけの変更で、同条件の速度改善率は未測定。

## 固定B timeoutの限定調査

`OLD/guard120/reference-random-100.json` game index31でrandom_v0の `play W 7 2` が1.1008752秒となった。直前B showboard142.3339ms/result26.8106ms、対応A showboard3.8334ms/result1.5922ms、A直近genmove26.2769ms。Aの保存探索最大95.6302msとBの通常command超過は別事象である。

Bの子telemetryはなくstderrも空で、CPU/pipe/I/O/OS待機を分離できない。終了はharnessの失敗時回収で、自然crashとは断定しない。既存の同seed・同1秒期限72コマンド再生は一致した。追加同型試行では原因を区別できる見込みがなく、今回新規再生0件。結論は原因未特定・今回の追加再現なし。休止異常との時計対応もなく同一原因にしない。1秒期限・固定B・失敗判定は変更しない。

## 固定資産・ZIP・バックアップ

manifest8資産とmanifestを含む9ファイルのGit blob bytesが一致した。search_v1を別cwdへコピーし、候補importを例外にするshadowと異なる候補設定を置き、7コマンド・合法盤面・終了0を確認した。ソースと実行の両方で候補の動的依存を照合した。

depth2は同深度比較・正式available、time100msはdeployment計測、設定なし7秒は提出の全応答目標として区別する。正式資格はWindows/直接CPython3.13.5と既存clock/lifecycle条件だけ。random/positionalの内部予算unknownは未解消で、参考対局を正式優越性へ昇格しない。 最終depth2はprotocol/budget verified・statistics availableだがperformance_criterion=not_met、decision=undetermined。time100msはprotocol/budget verified・statistics descriptive_only、performance_criterion=not_requested、decision=undeterminedである。計測成立と性能基準達成を混同せず、両者のstatusを集計JSONへ別記した。

| ZIP（OLD/guard120/packages/） | 実SHA-256 |
|---|---|
| ranger-phase5-7s.zip（5資産） | a9570f18e9b912694e9e1a385e60cc4442ef01d94cf73c1b859303eb1a012f1d |
| ranger-phase4-rollback.zip（3資産） | ad76a6aae9315f0b9759332c40987dbe18823afba98363c49db90b3d6c199da3 |

実在ZIPと全member hashを元記録へ照合し、再生成・差替え・uploadは行っていない。新checkoutにはGit管理外の原証拠・ZIPは含まれない。

外部媒体へのバックアップは未実施。今回FullのTemp依存14件は `REVIEW/full-artifacts/` にhash付きbytesで保管し、元path対応を `full-artifact-backup.json` に残した。旧Fullの対応14件もguard120の既存保管bytesを検証した。同一PC内の保管を外部バックアップと呼ばず、欠損9件とも混同しない。

最終監査で `REVIEW/backup-manifest.json` にpath/size/hash/対象rootを保存し、そのhashを `backup-manifest.sha256` に分ける。外部へは相対階層を保って掲載全ファイルとmanifest/sha256をコピーし、異なる既存bytesを上書きしない。コピー先rootで下記を実行する。実コピーと外部側検証は未実施。

```python
from pathlib import Path
import json, hashlib
m = Path('evaluation_results/phase5_review/run-7ab11429a5e94675b0aecd5c20c3e6ce/backup-manifest.json')
assert hashlib.sha256(m.read_bytes()).hexdigest() == m.with_suffix('.sha256').read_text(encoding='utf-8').split()[0]
records = json.loads(m.read_text(encoding='utf-8'))['files']
for r in records:
    p = Path(r['path'])
    assert p.stat().st_size == r['size']
    assert hashlib.sha256(p.read_bytes()).hexdigest() == r['sha256'], str(p)
print('verified files:', len(records))
```

## 修正の分類・サーバー確認・Phase 6

| 分類 | 実施・判断 |
|---|---|
| A：正しさ・安全性 | 品質runnerの実経過超過見逃しを修正し独立5回帰・関連12件・Fullで確認。AI側の新不具合は確認していない。 |
| B：局所的な利益 | Phase4比較表の不変な参照計算だけ共有し、全候補・assert・件数を維持。新規調査scriptの重複hashも除去。長時間実行前のAC・残電池・休止条件の確認を記録。OS設定の変更や常駐wrapper追加は行わない。 |
| C：別Goal候補 | 自然負荷7秒、別OS/Python、CPU/I/O/awake計測、同一固定局面の予約分・深度比較、Phase6モデル契約。固定版へ混入させない。 |

今回の初回Full開始前はAC接続・電池80%を観測した。取り直しFullの電源状態は連続記録しておらず、全実行中ずっと同条件とは保証しない。今後もACと意図した休止設定を確認し、復帰後の超過は失敗へ残す。1800秒上限を引き上げて成功にはしない。

サーバー確認は[既存試行様式](20261108_phase5_server_trials.md)で利用者が行う。

1. OS・Python実バージョン・展開先・起動ファイル名/argvを確認する。こちらは直接CPython3.13.5/Windowsだけが検証済みで、古いPython/Linuxは未確認。
2. ZIP SHA-256、直下reversi.pyと必要な兄弟資産の階層、name応答rangerを確認する。ZIPを手編集して同一検証物と扱わない。
3. 10秒がgenmove受信〜応答である前提を確認し、可能なら各応答時間・timeout command・stdout/stderr・負荷を保存する。ログが得られなければ成功局数だけで最悪時間は証明できない。
4. 7秒設定の全7コマンド、先後交換の複数局、パス・終局・再起動を確認する。新uploadで旧ファイルが消えるので先に提出・復旧ZIP/hashを別媒体へ保存する。
5. 後日0.1秒ずつ増やすときも別候補ZIP・設定hash・条件を残し、超過は失敗として保管する。現行版やsearch_v1を上書きして過去評価へ読み替えない。今回upload・設定増加は行っていない。

Phase 6は現行計画の学習評価（n-tuple等）を別Goalで具体化する。訓練/推論の責務、特徴・座標、モデル形式/version/hash、seed、予算/停止/失敗時合法手、読込時間・traceを先に決める。search_v1を固定比較相手として保全し、depth/time/提出7秒を使い分ける。dev/held-outを分離し、同一相手・序盤・seed・先後・外部期限で複数局比較する。学習損失やsmokeだけで棋力向上を判定しない。サーバー適合は別条件で、Phase 6のコード・モデルは今回実装していない。 代表局面の5/7秒設定400件は深度4で早期完了しており、予算だけ0.1秒ずつ増やしても着手や棋力が変わらない可能性がある。深度拡張・評価変更は予算調整と同時に混ぜず、別候補・別検証として扱う。

## 今回の品質・終了条件

Full証拠 `evaluation_results\quality-b5bab628186d4e089fcb3a086c31003c`、quality.json SHA-256 `e89b1f747408f6806542f93f42e0a862f65ce0029a1cfb79f71d5828937087e2`。実終了0、必須11工程PASS、pytest1611失敗/error/skip0、独立smoke1成功。48sourceの開始/終了/現在hash一致、publication errors0、fresh collect成功、Temp依存14件のbytes保管を確認した。開始時のdirty状態とHEADは原記録のまま保持する。

契約・図・参照共有・新規回帰・runnerは今回Fullのsourceへ照合した。Full後の本報告・集計・README索引・QUALITY_PROGRESSの結果追記は実行sourceを変更しない。旧PASSを流用せず修正後初回Fullの1800秒失敗を保管し、参照共有の実測・回帰後に必要なFullを取り直した。最終sourceのFullは一度だけ実行し、報告追記だけを理由とする再Fullは行わない。

commit/push/PR後の終了HEAD・URLは最終返答と `REVIEW/publication-result.json` に記録する。CI完了待機・反復polling・auto-merge・main mergeは行わない。

未解決はB timeout原因、自然負荷7秒近傍・未知サーバー/別Python、実ブラウザ手動対局、外部バックアップ、過去Temp9件欠損。棋力向上・Codex利用枠削減は主張しない。これらを明示してPhase 6の設計へ進めるが、正式相手への優越性や提出環境適合が完了したとは扱わない。
