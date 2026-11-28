# Phase 9-E　Bitboardからのn-tuple直接評価

## 比較条件

Phase 9-Dの最終コミット `3cc3365` と、そのBitboard＋行形式の葉評価を主要比較元にする。凍結したZero-TD 701、内部予算6.5秒、置換表無効、静的着手順を共通にする。累積効果の追加比較にはPhase 9-Bの凍結701 D5、正式提出版には手作業D4を使う。

14局面の計装なしの葉評価では、行への変換を含む中央値は約161.6 µs。行変換単体は約8.9 µsだった。範囲の重なる計測値を足し合わせず、変換とtuple符号化の両方を直接経路で見直す。

## 評価の経路

通常起動は `REVERSI_NTUPLE_BACKEND`、時間評価は `search.ntuple_backend` を選ぶ。既定は `rows`。明示した `bitboard` は検証済みn-tupleモデルとBitboard盤面を必要とする。公開固定深度と時間予算・通信・trace4/schema5の契約は維持する。

黒・白の整数から `8*y+x` 順の64桁を取得する。空き0、黒1、白2とする。現行のD4展開済み参照表から、LUT先頭位置とビット位置を起動時に作る。2-tupleでは `3*a+b`、3-tupleでは `9*a+3*b+c` を加える。既存参照表の順・重複と手番別のLUT開始位置を保つ。参照した重みは同じ順で `math.fsum` に渡し、`tanh` を適用する。

~~~mermaid
flowchart LR
    T["Bitboardの葉"] --> D["64桁を取得"]
    D --> L["2・3-tupleのLUT参照"]
    L --> V["fsum、tanh、根視点"]
    T -->|"終局"| S["厳密な石差と既存の終局値"]
    T -->|"rowsの設定"| A["従来の行形式へ変換し評価"]
~~~

終局では学習評価を呼ばず、Bitboardの石差を既存の終局値へ渡す。片側だけのパス、根視点への変換、合法手の順と計数、時間切れで採用する確定手は従来どおりとする。

## 評価と対局の進行条件

全448件の参照列・重み列とbinary64の集計結果、最終値を比較する。固定D4〜D6では値・着手・探索量を比べる。直接評価が20%以上、固定D5の探索全体が10%以上短縮し、D5・D6の完了率と事前登録した応答・メモリ条件を満たした場合にD6の対局へ進む。D5の両方式がsmokeでも全着手で完了し、棋譜が一致した場合は追加のD5対局を省く。累積棋力比較の条件は事前登録を参照する。

## PowerShellでの再現

Phase 9-Eの実験worktreeで別のPowerShellを開き、次を実行する。凍結モデル、最大深度、盤面方式をそろえ、葉評価だけを切り替える。両方式で `ranger`、黒の `3 2`、白の `2 2` が返ることをCPython 3.13.5と3.7.9で確認した。

```powershell
$phase9Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$phase9Registration = Get-Content -Raw 'evaluation_results/phase9e-20261128/preregister.json' -Encoding UTF8 | ConvertFrom-Json
$env:REVERSI_SEARCH_MODEL = $phase9Registration.frozen_config.model
$env:REVERSI_MAX_DEPTH = '5'
$env:REVERSI_TT_CAPACITY = '0'
$env:REVERSI_MOVE_ORDERING = 'static'
$env:REVERSI_BOARD_BACKEND = 'bitboard'
foreach ($phase9Mode in @('rows', 'bitboard')) {
    $env:REVERSI_NTUPLE_BACKEND = $phase9Mode
    @('name', 'genmove B', 'genmove W', 'quit') | & $phase9Python -B reversi_player/reversi.py
    if ($LASTEXITCODE -ne 0) { break }
}
```

実験用の環境変数は、そのPowerShellを閉じると消える。モデルと設定の記録は `evaluation_results/phase9e-20261128/` にある。
