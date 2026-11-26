# Phase 9-C　完了反復の主変化による着手順

研究用比較元は、Phase 9-Bで凍結したZero-TD 100,000局 seed 701のD5とする。内部予算6.5秒、外部の起動5秒・通常1秒・着手10秒、全genmove目標7秒、置換表無効で比較する。モデルID、SHA-256、ソース、局面は実験の事前登録に固定する。

## 着手順と同点

現行の反復深化は根で行・列順を使う。内部では隅、固定位置重みの降順、行・列順で並べる。新方式も根の順序を保つ。前の完了反復の主変化と一致する内部局面では、その手が合法であれば最初に探索する。残りの手は現行順を使う。根の比較は厳密な改善に限り、後続のfail-low境界が同値でも先行手を保つ。これにより完全探索時の根の値と既存同点規則を維持する。

探索中は深さに応じた作業用の着手列を持つ。パスは列では1項目として表し、placement深度を消費しない。前の主変化と同じ着手またはパスをたどった場合にだけ、その後の指し手を参照する。途中で分岐した経路や、無効な候補手には現行順を適用する。完了反復の列だけを次の反復へ渡し、genmove終了時に廃棄する。時間切れの採用手は従来どおり最後に完了した反復から選ぶ。

## 設定と計測

実験モードは `pv`、従来モードは `static`。通常起動では `REVERSI_MOVE_ORDERING`、時間評価では `search.move_ordering` を使う。既定は `static`。二重設定、固定深度の公開設定での新方式、置換表または内部順序無効との併用は拒否する。公開固定深度の受理範囲、trace4、schema5と正式資格の意味を維持する。時間評価の実効設定と反復の既存traceで動作を照合する。

固定深度4・5・6の診断は、同じ反復深化を進行しない診断時計で完走させ、別の単調時計で実測する。Zero-TD 701と手作業評価について、Phase 9-Bの代表局面で走行順を交互に入れ替える。初回を除き、同一局面の対応比を集計する。順序付け費用と主変化の利用回数は、時間比較とは別の診断で測る。

時間探索のD5・D6は実プロセスで測る。固定深度の全一致、701の固定D5の対応中央値で10%以上短縮、候補の7秒超過・代替着手・予算異常なし、D5の完了率低下5ポイント以内、ピークRSS増加8 MiB以内をすべて満たした場合にD5対局へ進む。指定した開発用序盤で1ペアを試し、安全性と費用を確認して残り11ペアを取得する。held-outは使わない。

## PowerShellで通常起動を確認する

Phase 9-C作業ツリーのルートで、別のPowerShellを開いて実行する。事前登録には凍結した研究用モデルの実ファイルを記録した。モデルと最大深度を共通にし、着手順だけを切り替える。次のコマンドは両モードで `ranger` と合法手 `3 2` を返した。

```powershell
$phase9Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$phase9Registration = Get-Content -Raw 'evaluation_results/phase9c-20261126/preregister.json' -Encoding UTF8 | ConvertFrom-Json
$env:REVERSI_SEARCH_MODEL = $phase9Registration.model
$env:REVERSI_MAX_DEPTH = '5'
foreach ($phase9Mode in @('static', 'pv')) {
    $env:REVERSI_MOVE_ORDERING = $phase9Mode
    @('name', 'genmove B', 'quit') | & $phase9Python -B reversi_player/reversi.py
    if ($LASTEXITCODE -ne 0) { break }
}
```

この確認では環境変数を設定したPowerShellから子プロセスを起動する。試験を終えたら、そのPowerShellを閉じる。記録は実験ディレクトリの `powershell-static.log` と `powershell-pv.log` にある。
