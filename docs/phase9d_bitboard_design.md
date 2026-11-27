# Phase 9-D　盤面処理の計測とBitboard

## 進行条件

対象はPhase 9-Bの研究用Zero-TD 701 D5。モデル、6.5秒の内部予算、静的な着手順、置換表無効を共通にする。比較する14局面はPhase 9-Cから取り、独立refereeで棋譜を再生する。凍結版との照合では各局面の探索値・手・カウンターを確認する。性能比較では同じソースから配列とBitboardを切り替える。

先にcProfileで手作業評価と701、それぞれのD4・D5・D6を調べる。関数の自己時間を一度ずつ分類し、累積時間は呼出関係の確認に使う。701の通常着手を対象に、固定D5で盤面処理の自己時間が20%以上ならBitboardへ進む。例えば割合が50%でその処理だけを2倍速にできた場合、探索全体の短縮は25%が目安になる。変換や評価との接続費用は実測で確かめる。

## 表現と探索の境界

盤面の左上をビット0とし、既存座標 `(x,y)` のビット番号を `8*y+x` とする。黒石と白石は重複のない64bit相当の非負整数で持つ。空きは64bit全体から両色を除いたビット集合とする。左右と斜めのシフトでは、移動前に隣の行へ回り込む端列を除く。左シフトしたビットは64bitの範囲に収める。

合法手を求めるときは、自色から各方向へ相手石の連続部分を伝え、その先の空きを集める。反転は着手先から相手石をたどり、自色で閉じる方向に限って適用する。合法手は下位ビットから既存の行・列順で返す。着手後の2整数は新たに生成する。パスと終局は両色の合法手で判定し、石数はビット数から求められる。

~~~mermaid
flowchart LR
    B["Board と外部プロトコル"] --> R["行表現と根の合法手"]
    R --> S["共通の時間探索"]
    S --> A["配列の合法手・仮着手"]
    S --> P["Bitboardの合法手・仮着手"]
    P --> L["葉で行表現へ変換"]
    A --> E["既存の評価関数"]
    L --> E
    S --> O["確定手・trace4"]
~~~

Bitboardを選んだ場合は根で一度だけ行表現から変換し、探索中の盤面はBitboardのまま保持する。既存の手作業・n-tupleの葉評価に渡すときだけ行表現に戻す。葉評価が合法手を必要とする場合も、その葉のBitboardから列挙する。評価の式、黒視点、LUT参照順、浮動小数点の集計、終局スコアは既存実装を使う。探索の根の順序、同点規則、枝刈り、パス時の深さ、計数、時間切れ時の確定手を共通にする。

時間探索の通常起動では `REVERSI_BOARD_BACKEND`、評価では `search.board_backend` に `array` または `bitboard` を指定する。既定は `array`。BitboardはTT無効・静的着手順の実験設定とする。公開固定深度設定、既存プロトコル、trace4、schema5は従来の契約に従う。

## 検証と評価

独立refereeと全幅Minimaxで、着手の順序・反転・全合法着手後の盤面・石数・値・着手・同点・パス・期限中断を確認する。perftは着手で深さが1減り、強制パスは深さを消費しない。双方着手不可と深さ0では、それぞれ現在の局面を葉1件と数える。

固定深度の全条件で値・手・探索カウンターを揃える。701の固定D5で局面ごとの対応時間比の中央値から10%以上の短縮を求める。同一時間探索はD5とD6で完了率を維持し、候補の全genmoveが7秒以内、起動が5秒以内、代替着手や予算異常がなく、ピークRSSの増分が8 MiB以内であることを進行条件とする。

D5の先後交換smokeを1ペア行い、全着手でD5が完了し、棋譜が同じで、固定局面でも双方がD5をすべて完了した場合は、結果の重複する残りのD5対局を打ち切る。D6の完了率が15ポイント以上高まれば、D6同士で先後交換を比較する。それでも実用上の採用判断に必要な場合に、Bitboard D6と凍結版D5を別条件として比較する。開発用12序盤を使い、held-outは候補選定に使わない。

## Windowsでの再現

Phase 9-Dの実験worktreeを起点に、別のPowerShellで実行する。事前登録が指す凍結モデルを共通に使い、盤面バックエンドだけを切り替える。両方式で `ranger`、黒の `3 2`、白の `2 2` が返ることを確認した。実行後は、そのPowerShellを閉じると実験用の環境変数も消える。

```powershell
$phase9Python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$phase9Registration = Get-Content -Raw 'evaluation_results/phase9d-20261127/preregister.json' -Encoding UTF8 | ConvertFrom-Json
$env:REVERSI_SEARCH_MODEL = $phase9Registration.frozen_config.model
$env:REVERSI_MAX_DEPTH = '5'
$env:REVERSI_TT_CAPACITY = '0'
$env:REVERSI_MOVE_ORDERING = 'static'
foreach ($phase9Mode in @('array', 'bitboard')) {
    $env:REVERSI_BOARD_BACKEND = $phase9Mode
    @('name', 'genmove B', 'genmove W', 'quit') | & $phase9Python -B reversi_player/reversi.py
    if ($LASTEXITCODE -ne 0) { break }
}
```

生データとコマンドは `evaluation_results/phase9d-20261127/` の `preregister.json`、`bench-protocol.json` と各実行ディレクトリに置く。Windowsの直接CPython 3.13.5と、検証用に隔離した3.7.9の両方で実モデルの起動・連続着手を確認した。
