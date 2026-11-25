# Phase 8の探索統合と評価計画

## 接続と得点

起点は `c133b5f`。主要候補6本の出典と凍結値は[技術研究計画の台帳](phase8_plus_research.md#1-phase-8のモデル台帳)に従う。比較元は提出版 `ranger-phase5-7s-depth4-20261118-6773751` を展開したファイルとする。固定 `search_v1` は深度2で試す別の相手とする。

```text
reversi.py → search_trace.py → 起動時に ntuple_io で照合
           → 固定深度 / timed_search → ai_engine の葉評価 → 凍結された model.value
独立ハーネス → 既存 stdin/stdout と trace4 → referee、予算・証拠の検証
```

探索は根の色で最大化、相手の色で最小化する。非終局の葉は現在の手番を重み表の選択に使い、黒視点の値を根が白の場合だけ反転する。非終局の値域は閉区間 [-1, 1]。両色に合法手がなければ Phase 5 と同じ根視点の石差を使い、勝ちは 1000+石差、負けは -1000+石差、引き分けは 0。葉が非終局のパス状態なら現在の手番で評価する。残り深さがあれば手番を切り替えて探索を続け、着手深さは減らさない。既存の着手順、同点規則、期限、完了済み反復の採用と合法な代替手を保持する。

評価設定ではモデルの絶対パス、ID、ファイル SHA-256 の3項目を一組で指定する。モデルは各独立プロセスの起動時に読み込む。trace4は既存の起動モデル計測欄を使い、各着手で同じモデルIDを記録する。モデルの不一致、形式・読込エラーは起動失敗とする。通常起動でモデル指定を省略した場合は従来どおり手作業評価を使う。

## 事前登録した比較

- Phase 7.1 の固定12開発ペアと評価seed 20261119を使用する。既存1手評価はモデル・序盤・原証拠を照合して参照する。独立refereeで深度1の一致、深度2–3の全幅Minimaxとの一致と枝刈り、パス・終局・同点・期限を確認する。
- 主要候補すべてについて、まず深度2で提出側の手作業評価と直接比較する。続いて両側を同じ時間上限・最大深度4・外部締切で動かす。提出版の受付からの目標は7秒、内部上限の要求は6.5秒、外部 genmove 上限は10秒とし、同じ設定を両側に渡す。
- 短い先後交換で実測費用を確認してから12ペアへ進む。対局は直列で実行する。固定ランダム・位置重み・深度2探索は機能確認と既存結果との接続に使う。追加チェックポイントは、主要比較の結論を見直せる根拠がある場合に限り広げる。
- 通信・対局・予算照合の必須失敗があれば、その取得結果を判断に使わず証拠を残す。開発比較は各seedを掲載し、系列平均は3 seedに等しい重みを与える。時間比較には現行ハーネスの固定得点の正式区間を付けない。
- 開発候補を次へ進める最低条件は、同一時間比較で系列の勝ち点率が55%以上、3 seedのうち少なくとも2 seedが50%を超え、予算・通信・合法性・モデル読込の異常が0件であること。条件を満たせば同じ成果物と設定を固定して独立評価を検討する。開発の結果だけで提出版を変更しない。Phase 0のheld-outとPhase 6.5の凍結testは開発比較に使わない。

## 測定と判断

序盤・中盤・終盤・強制パスを含む合法局面でウォームアップ後の中央値・p95・最大・処理率を測る。純粋モデルと実際の葉側の合法手判定・盤面準備の費用を記録する。対局で勝敗、先後交換ペア、石差、深度、ノード、葉、探索と外部応答、起動、メモリを照合する。正式採用には同一時間の改善、既存の独立評価資格、外部期限と提出実行環境への適合が要る。固定深度だけ改善した場合は探索費用を優先して調べる。

## 試走後に確定した規模

同一時間の最初の先後交換2局は69.85秒。これを基にした12ペア×6候補の単純見積もりは約84分で、局面やCPUによって変動する。固定深度2は6候補を各12ペア比較し、同一時間は最初の1ペアを共通の序盤で各候補に適用して初期確認する。固定相手の短い対局と試走を採用候補の行へ混ぜない。開発で必要な規模へ広げるまで採用判断は保留する。

同一時間の計測では、双方が受付からの7秒目標と最大深度4の設定を使い、ハーネスから両側に同じ6.5秒の探索上限を渡す。既存の予約分を含む内部停止境界のうち早い方で止まる。外部計測は起動5秒、通常応答1秒、genmove 10秒。大学課題の1秒・10秒の制限は[プログラム開発方法](プログラム開発方法.md)を参照。7秒目標は既存Rangerの設定値である。

## Windowsでの再現手順

Phase 8をチェックアウトしたルートでPowerShellを開きます。次の例は今回の `evaluation_results/p8` で実行する場合です。別の場所にチェックアウトした場合は、`$archive` を実験原本がある `evaluation_results` の絶対パスに変更してください。

```powershell
$phase8Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
& $phase8Python -VV
$archive = (Resolve-Path '..').Path
$previous = Join-Path $archive 'phase7-longrun-100k-rerun-20261120'
$baseline = Join-Path $archive 'submission_packages\ranger-phase5-7s-depth4-20261118-6773751\extracted'
$population = Join-Path $previous 'openings.json'
$model = Join-Path $previous 'checkpoints\zero-701\attempt-e4a8fd9ad8c44cdf87ec09bf561a609f\checkpoint\model.json'
Get-FileHash -Algorithm SHA256 -LiteralPath $model
```

手作業評価はモデル指定を解除して起動してください。対話中は `name`、`genmove B`、`quit` などを入力できます。GUIも同じ環境変数を継承するので、以前の1手選択用の `REVERSI_NTUPLE_MODEL` はPhase 8の起動前に解除してください。

```powershell
Remove-Item Env:\REVERSI_SEARCH_MODEL -ErrorAction SilentlyContinue
Remove-Item Env:\REVERSI_NTUPLE_MODEL -ErrorAction SilentlyContinue
& $phase8Python -B reversi_player/reversi.py

$env:REVERSI_SEARCH_MODEL = (Resolve-Path -LiteralPath $model).Path
& $phase8Python -B reversi_player/reversi.py
# GUIで動かす場合
& $phase8Python -B reversi_player/web.py
Remove-Item Env:\REVERSI_SEARCH_MODEL -ErrorAction SilentlyContinue
```

checkpointを切り替える場合は `$model` を台帳にある別のモデルの絶対パスに更新してください。SHA-256を照合し、環境変数を設定してから新しく起動します。読込に失敗した場合は標準エラーで原因を確かめてください。

以下の出力先は実行ごとに新しく指定してください。先後交換の対局は直列で動きます。`--pairs 1` は2局、`--pairs 12` は24局です。同一時間の比較では他の学習・Full検証などのCPU負荷を避けます。

```powershell
$out = Join-Path (Resolve-Path 'evaluation_results').Path ('phase8-local-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $out | Out-Null
$common = @('--model', $model, '--baseline', $baseline, '--population', $population)
& $phase8Python -B -m tools.phase8_compare benchmark --model $model --count 150 --output (Join-Path $out 'leaf.json')
& $phase8Python -B -m tools.phase8_compare match @common --depth 2 --pairs 12 --output (Join-Path $out 'depth2-zero-701')
& $phase8Python -B -m tools.phase8_compare match @common --time --pairs 1 --output (Join-Path $out 'time-pilot-zero-701')
& $phase8Python -B tools/quality.py
```

12ペアの同一時間比較は `--time --pairs 12` とし、各seedに新しい出力先を指定します。対局の `summary.json` に勝ち点率、先後別・序盤ペア別結果、探索深度・ノードなどの分布を保存します。`run.json` は実行コマンドと資産ハッシュ、`report.json` と `raw/` はハーネスの検証に使う原記録です。失敗時は作成済みの出力を保管し、再実行には新しい出力先を使ってください。対局CLIで使えるのは登録済みの開発用序盤です。独立評価は候補を固定し、[ハーネスの採用評価契約](harness_contracts.md)に従って登録してください。


今回の監査ファイルが手元にある場合は、次の例で主要6モデルの12ペア比較を直列で実行できます。この例を実行すると全144局になり、試走からの所要時間の目安は約84分です。

```powershell
$auditPath = 'evaluation_results/phase8-20261125/assets-audit.json'
$audit = Get-Content -Raw -Encoding UTF8 -LiteralPath $auditPath | ConvertFrom-Json
$models = $audit.models | Where-Object {
    ($_.series -eq 'zero' -and $_.games -eq 100000) -or
    ($_.series -eq 'pretrained' -and $_.games -eq 50000)
}
if (@($models).Count -ne 6) { throw '主要6モデルがそろっていません。' }
foreach ($entry in $models) {
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $entry.path).Hash.ToLowerInvariant()
    if ($actual -ne $entry.sha256) { throw "モデルのSHA-256不一致 $($entry.path)" }
}
foreach ($entry in $models) {
    $label = "$($entry.series)-$($entry.seed)"
    & $phase8Python -B -m tools.phase8_compare match --model $entry.path --baseline $baseline --population $population --time --pairs 12 --output (Join-Path $out "time12-$label")
    if ($LASTEXITCODE -ne 0) { throw "評価に失敗しました $label" }
}
```

Phase 7.1・7.2の過去実験を再現する場合は、各実験のfingerprintに一致する旧worktreeを使います。モデル指定を切り替えるときも、過去のstate・モデル・原証拠はそのまま保管してください。
