# Phase 9-B 最大探索深度の開発実験

## 設計

比較元はPhase 9-Aが完了した `ec2863a`。反復深化の最大深度だけを4・5・6で切り替える。固定深度APIとtrace4の固定深度受付は従来の範囲を使う。最大深度5・6は時間探索の実験設定とし、既定の応答設定と提出資産は維持する。手作業評価とZero-TD 100,000局の3 seedを扱う。各比較では同じ評価を両側に使い、着手順、同点規則、終局、パス、評価視点を揃える。

```text
通常起動  明示的な環境変数 → ResponsePolicy
評価起動  search.max_depth → SearchTrace → ResponsePolicy
既存の反復深化 → 完了済みの最深結果
               → 試行した深度の計測（明示した時間設定）
```

時間探索には既存の開始・停止判定を使う。公式の通常起動設定は7秒目標・開始6秒・途中停止6.5秒。ハーネスで `limit_ns=6500000000` を明示すると、既存の予約式により開始は探索入口から約5.2秒、途中停止は約6.175秒が上限になる。受付時刻側の期限が先ならそちらを使う。未完了反復の手と評価値は採用しない。深度1にも到達しないときは従来の合法な代替手を返す。追加の開始予測は導入しない。

固定深度診断では停止しない制御時計と独立refereeを使い、反復の最後に得た探索値と着手を独立の全幅Minimaxと照合する。パスは同じ配置深度で相手へ渡る。期限中断テストは時計を操作し、深度5または6の途中で停止しても直前の完了結果が残ることを確認する。

trace4の既存の完了深度・開始済み最大深度・探索時間の意味は維持する。時間設定に `search.max_depth` を明示した場合は、開始した各反復について深度、完了の有無、時間、追加ノード・葉数を補助診断として記録する。深度5・6の同一時間対局は開発評価として扱い、正式な固定仕事量評価や提出資格は追加しない。

## 実験の事前登録

- 比較元と候補で内部上限6.5秒、応答目標7秒、外部期限5・1・10秒、TT無効を共通にする。凍結モデルはPhase 8の台帳と実ファイルで識別する。
- 代表局面はPhase 4・9-Aの10局面、2026年11月18日の開発用3難局面 `4dc1311d`、`85305d9d`、`ca629f06`、Phase 8の同一時間評価から比較元の応答が最長だった1局面を事前に選ぶ。選んだ履歴は独立refereeで再生する。
- 手作業とZero-TD 701・702・703でD4〜D6を計測する。各条件はウォームアップ1回と測定2回。局面と反復で深度の順番を循環・反転し、CPU競合を避けて直列に取得する。固定深度診断の時間は同一時間計測に混ぜない。
- 代表局面で外部期限、合法性、通信、盤面同期または確定結果に異常があればその条件を止める。通常着手で深度上限を実際に完了した割合が25%未満の手作業候補は対局を保留する。手作業比較は合格した両候補を対象とする。Zero-TDは全3 seedでD6完了率75%以上ならD6を選び、満たさない場合は全seedでD5完了率50%以上ならD5を選ぶ。両条件を満たせない場合は保留する。
- Phase 8と同じ開発12序盤、評価seed 20261119、先後交換で、手作業D5／D6の各24局と、Zero-TDの選択深度の各seed 24局を計画する。先に1先後ペアで動作と時間を測り、棋譜の勝敗は続行判断に使わない。試走を含む総費用上限は手作業120分、Zero-TD 180分とする。10秒超過、対局異常、7秒超過が同じ候補で2件以上、または試走からその系列の上限を超えると見込む場合は追加の本評価へ進まない。
- 同一モデルの直接対局の勝ち点率を主要指標とする。手作業55%以上、Zero-TDは3 seed等重み平均55%以上、2 seed以上が50%超、異常0件を開発上の有望性の目安とする。Zero-TDが到達した場合はPhase 8のD4対提出版72局を条件確認の上でベースにし、同じ3 seed×12ペアで凍結提出版と比較する。追加比較の費用上限は180分。
- 先後交換のペアとseed別結果を保存する。開発の比較で基準を満たす候補も、独立評価と提出条件の確認を経て採用を判断する。Phase 0 held-outとPhase 6.5凍結testは使用しない。

## PowerShellで再現する

以下はPhase 9-Bの作業ツリー `evaluation_results/p9b` をカレントディレクトリにした例。直接インストールしたCPython 3.13.5を指定する。資料や証拠を別の場所に置いた場合は、そのパスを指定する。

```powershell
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
foreach ($depth in 4..6) {
    $env:REVERSI_MAX_DEPTH = [string]$depth
    @('name', 'genmove B', 'quit') | & $py -B reversi_player/reversi.py
}
Remove-Item Env:REVERSI_MAX_DEPTH
```

通常起動では環境変数を省くと `response_config.json` の深度4を使う。上の例でTTと葉評価の環境変数を指定していなければ、TT無効の手作業評価で起動する。ハーネスから比較する場合は `REVERSI_MAX_DEPTH`、`REVERSI_TT_CAPACITY`、`REVERSI_SEARCH_MODEL` を外し、評価設定で指定する。

```powershell
$registry = '../p8/evaluation_results/phase8-20261125/assets-audit.json'
$population = '../phase7-longrun-100k-rerun-20261120/openings.json'
$remaining = '../p8/evaluation_results/phase8-time12-20261125/openings-remaining11.json'
$evidence = 'evaluation_results/phase9b-20261125'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'

& $py -B -m tools.phase9b_compare bench --positions "$evidence/positions.json" --registry $registry --output "$evidence/bench-$stamp"
& $py -B -m tools.phase9b_compare match --scope pilot --depth 5 --population $population --output "$evidence/manual-d5-pilot-$stamp"
& $py -B -m tools.phase9b_compare match --scope remaining --depth 5 --population $remaining --output "$evidence/manual-d5-remaining-$stamp"
```

`--scope pilot` は1先後交換ペア、`remaining` はそのペアを除いた11ペアを取得する。手作業評価の深度6を試すときは `--depth 6` と別の出力先を指定する。n-tupleでは `--model` に凍結モデルファイルのパスを指定する。同一モデル内の比較では深度4側にも同じモデルが使われる。

```powershell
$model = ((Get-Content -Raw -Encoding utf8 $registry | ConvertFrom-Json).models | Where-Object {
    $_.series -eq 'zero' -and $_.seed -eq 701 -and $_.games -eq 100000 -and
    $_.experiment -eq 'phase7-longrun-100k-rerun-20261120'
}).path
& $py -B -m tools.phase9b_compare match --scope pilot --depth 5 --population $population --model $model --output "$evidence/zero-701-d5-pilot-$stamp"
Get-Content -Raw -Encoding utf8 "$evidence/zero-701-d5-pilot-$stamp/summary.json"
& $py -B tools/quality.py
```

ベンチマークは局面ごとの通信原本とtrace4を残す。対局の原本は各出力先の `raw/` にあり、`summary.json` に得点・深度・応答を保存する。固定14局面の由来と棋譜は実験領域の `positions.json` と `prepare-positions.py` を参照する。最大深度5・6は開発評価で使用できる。提出用の設定は深度4のままである。
