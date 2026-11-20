# Phase 7.1 自己対戦をWindowsで続ける

## この実験でできること

Phase 7で学習したZero-TDとPretrained-TDを、それぞれ3つのseed（701、702、703）で10,000局から100,000局まで続けます。seedは着手を抽選する乱数の出発点です。計6系列の追加学習と、節目の固定相手との対局、結果の集計をプログラムが行います。元のモデル、乱数の状態、対局記録は読み取り専用で使います。

局数を増やす目的は、両系列の強さがどこまで伸びるか、試行ごとにどれくらい差が出るかを測ることです。開始・停止・再開の時期は自分で決めてください。追加54万局には数十時間かかることがあります。CPUや同時実行数、他の作業、評価対局で変わるため、運転中の `status` で見積もりを確認してください。

## 実行前の準備

1. スタートメニューからPowerShellを開きます。リポジトリを置いたフォルダに移動し、`Get-Item .\tools\phase7_longrun.py` で今回のプログラムがあることを確認します。下のパスは授業用PCの例です。
2. Windowsの直接インストール版CPython 3.13.5（64ビット）を指定します。下の標準のインストール先に存在しなければ、実際の `python.exe` の場所を使ってください。
3. Phase 7の原証拠を残します。`evaluation_results\phase7-td0-20261119` と同じ階層のPhase 6・6.5の証拠も必要です。別の場所に置いた場合は、`doctor` と `init` のそれぞれに `--assets` を付け、3フェーズ分が入る `evaluation_results` フォルダを指定します。出力は元データとは別にします。
4. 保存先のボリュームに3 GiB以上の空きを確保します。運転中も空き容量を検査します。PCを電源につなぎ、運転したい時間帯のスリープ設定を確認します。

```powershell
Set-Location -LiteralPath 'C:\School\2026\後期\00_授業資料\情報学演習Ｂ'
Get-Item -LiteralPath '.\tools\phase7_longrun.py'
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
& $py --version
Get-PSDrive -PSProvider FileSystem
```

ここからの例はすべてリポジトリのルートを基準にしています。新しいPowerShellを開いた場合は、上の `Set-Location` と `$py` の設定をもう一度実行してください。`python` が同じ実行ファイルを指す環境では、`& $py` の代わりに `python` を使えます。

## 最初の起動

まず点検し、次に保存先を初期化します。この2つの操作では学習は始まりません。途中でエラーが表示された場合は、内容を確認してから次へ進みます。

```powershell
& $py -B tools/phase7_longrun.py doctor
& $py -B tools/phase7_longrun.py init
```

`doctor` は6件のモデルのSHA-256と設定、再開に使う乱数状態、評価資産、書き込みと空き容量を点検します。例えば途中で `空き容量 4.50 GiB。直接CPython 3.13.5。` と表示します。 `init` 後は実験先 `evaluation_results\phase7-longrun-100k` ができます。別名で作る場合は、全コマンドに同じ `--root '保存先のパス'` を付けます。

次の `start` が本番の開始操作です。追加学習を開始する日に実行してください。

```powershell
& $py -B tools/phase7_longrun.py start --max-workers 2
& $py -B tools/phase7_longrun.py status
```

起動に成功すると `管理PID 12345 起動確認済み。` に続いて実験先とログの場所が表示されます。PIDは実行中のプログラムを識別する番号です。 `run` は同じ管理処理を現在のターミナルで実行します。前面で運転するときは、タブを閉じる前に Ctrl+C で停止を要求し、保存が終わるまで待ちます。

## 学習中の進捗

`status` では系列ごとに「終了」と「保存」が表示されます。「終了」は現在の処理で対局を終えた局数、「保存」は障害後も再開に使える局数です。残り時間は今回の実測速度から計算した学習分の目安です。評価にかかる時間はこの見積もりに含みません。`EVALUATING` では節目のモデルで対局しています。

`start` と `resume` の起動確認後はPowerShellのタブを閉じても独立プロセスが動きます。スリープ中は計算が止まり、ログオフ・シャットダウンや再起動では継続を当てにできません。PC上で別の作業もできます。動作が重ければ安全停止し、再開時に `--max-workers 1` を指定してください。同時実行数は1〜6で、指定を省くと2です。

## 安全に停止する

```powershell
& $py -B tools/phase7_longrun.py stop
& $py -B tools/phase7_longrun.py status
```

`STOPPING` は停止要求の受付を表します。各学習処理は現在の対局を終えて、モデル・乱数状態・棋譜をひと組で保存します。評価中の停止は評価1件の終了後になるため、学習中より待つことがあります。 `status` に `STOPPED` が表示され、管理PIDが `生存 なし` になったらPCの電源を切れます。 `COMPLETED` でPIDが終了している場合も同様です。保存中はタスクマネージャーで強制終了したり、実験先を移動・変更したりしないでください。

## 再開する

```powershell
& $py -B tools/phase7_longrun.py verify
& $py -B tools/phase7_longrun.py resume --max-workers 2
& $py -B tools/phase7_longrun.py status
```

`verify` は保存済みの系列、局数、モデル、評価の対応を点検します。 `resume` は最後に確定したcheckpoint（モデルと乱数状態などの再開用の組）を探します。区間の途中で電源が落ちた場合、最後の確定局数以降を再実行します。通常の保存間隔は1,000局で、20,000・50,000・100,000局でも必ず保存します。復旧時の表示で、確定局数や再実行する範囲、不完全だった試行の場所を確認できます。検証で過去の異常が表示されても、有効な保存点が残っていれば再開できる場合があります。完了した系列や検証済みの評価は再実行しません。

## 結果を開く

```powershell
& $py -B tools/phase7_longrun.py report
```

このコマンドが表示する新しい `reports\report-…` フォルダを開きます。 `summary.md` はそのまま読める対戦成績の一覧です。 `results.csv` と `progress.csv` はExcelなどで開ける対局成績と学習進捗の表です。 `summary.json` には同じ集計に加え、先後・ペアごとの情報、試行の異常、モデルの識別情報などを保存します。節目の評価が終わると、同形式の報告を自動で作ります。

`games_trained` が累積学習局数、`series` が系列、`seed` が個別の試行です。勝ちを1点、引き分けを0.5点、負けを0点として、合計点を対局数で割ると勝ち点率になります。 `mean` は揃った3 seedの等重み平均です。 `TRAINING_PENDING` は学習待ち、`EVALUATION_PENDING` は評価待ち、`EVALUATION_FAILED` は評価の試行に異常があった状態です。未完了の勝ち点率は空欄です。

同じ局数・同じ相手の行を比べます。相手が `zero` の行はPretrained-TDから見た同じseedのZero-TDとの直接対局です。途中の成績が下がったら、他のseedや別の固定相手でも同じ傾向があるかを確認してください。Phase 8へ渡す候補は `progress.csv` の `checkpoint` フォルダにある `model.json` です。 `model_id`、`model_sha256`、対応する評価行を一緒に記録します。

## トラブルシューティング

| 状況 | 原因候補と確認方法 | 安全な対処 |
|---|---|---|
| Pythonが見つからない | `& $py --version` が実行できない | Windows用64ビットの直接インストール版CPython 3.13.5を探し、`$py` を設定し直す |
| 必要ファイルが見つからない | `doctor` が示すパス、`Get-Location`、`--assets` を確認 | 正しいルートへ移動し、原証拠のフォルダを指定する |
| checkpointが壊れている | `verify` が示す系列・世代・hashを確認 | 元の起点が破損した場合は同じSHA-256の原本を復元する。生成checkpointに問題があればログを残し、有効な直前の保存点が残る場合は `resume` の復旧表示を確認する |
| `start` が拒否された | すでに開始済み、または別管理プロセスが実行中 | `status` で確認し、運転中なら待つ。過去に起動した実験は `resume` を使う |
| 進捗が更新されない、表示が古い | `status` の更新時刻、管理PIDの生存表示、`EVALUATING` を確認 | 評価中はログを確認する。 `INTERRUPTED` なら子処理の終了を待ち、`verify` して再開する |
| 再起動した、電源が落ちた | 最新の試行が未確定で残っている可能性がある | `verify` の後に `resume`。保存済みの局数と再実行される範囲を確認する |
| 容量が不足した | エラー表示、`Get-PSDrive` を確認 | 実験と無関係のファイルを退避し、空き容量を確保して `verify`、`resume` |
| 評価が失敗した | `evaluations` の該当試行にある `process.log` と、`sessions` の `manager.log` を見る | 原因を解消して `resume`。失敗した評価1件を新しい試行として処理し、過去の成功結果は引き継ぐ |
| 学習が終わらない | 実測速度と最後に確定した局数を確認 | 進行していれば予定を見直し、安全停止して別の時間帯に再開できる |
| `stop` の後も実行中 | `STOPPING` や `FINISHING`。評価1件、対局1局、保存処理などが残っている | ログが伸びている間は待ち、PIDの終了を確認する。エラーが続く場合は実験ディレクトリとログを保全する |
| 一部のseedだけ失敗した | `status` と `logs` の系列名を照合 | 原因を直して同じ実験を `resume`。完了済みの系列や区間は引き継ぐ |

ロックファイルが残っていても、排他ロックはWindowsがプロセス終了時に解放します。ロックファイルの削除やcheckpointの手動編集は避けてください。待っても起動できない場合は、表示された管理PIDの生存状況とログを確認します。原因の調査に備え、ファイルはそのまま残します。

## コマンド早見表

各行を `& $py -B tools/phase7_longrun.py コマンド名` の形で実行します。

| コマンド | すること |
|---|---|
| `doctor` | Python、原証拠、書き込み、容量を点検 |
| `init` | 新しい実験の保存先を作る |
| `start` | 初回の学習を独立プロセスで開始 |
| `run` | 同じ管理処理を開いているターミナルで実行 |
| `status` | 現在の状態、局数、保存点、速度を表示 |
| `stop` | 安全停止を要求 |
| `resume` | 確定済みの続きを独立プロセスで開始 |
| `verify` | 保存点と評価記録を点検 |
| `report` | 保存済みの記録から集計を新しく出力 |

## 日をまたいで続ける例

次は表示例です。PID・局数・日付・勝ち点は実行ごとに変わります。

```text
初日  doctor → init → start
管理PID 12345 起動確認済み。
途中  status
zero-701 終了 10,850  保存 10,000/100,000 (10.0%)
夜    stop
STOPPING 停止を要求しました。
確認  status
実験 phase7-longrun-100k  STOPPED
管理PID 12345  生存 なし
翌日  resume → status
実験 phase7-longrun-100k  RUNNING
後日  report
50,000局の評価が完了した行は VERIFIED。未実施の行は空欄。
終了  status → report
実験 phase7-longrun-100k  COMPLETED
管理PID 23456  生存 なし
```

## 本番と別の保存先で短く試す

以下は元のZero-TDの10,000局地点から40局だけ学習し、短い評価を行います。 `--smoke pretrained` に替える場合は別の新規保存先を用意します。 `--smoke-games` は20〜100局です。同じ試験先で停止・再開を試すときは、`stop`、`status`、`resume` にも同じ `--root` を指定します。

```powershell
$trial = 'evaluation_results\phase7-longrun-smoke-zero'
& $py -B tools/phase7_longrun.py doctor --root $trial --smoke zero --smoke-games 40
& $py -B tools/phase7_longrun.py init --root $trial --smoke zero --smoke-games 40
& $py -B tools/phase7_longrun.py start --root $trial --max-workers 1
& $py -B tools/phase7_longrun.py status --root $trial
& $py -B tools/phase7_longrun.py verify --root $trial
& $py -B tools/phase7_longrun.py report --root $trial
```


## 任意の再現性確認

同じ起点から2,000局連続で学習した結果と、1,000局で保存してから別プロセスで再開した結果を比較できます。この例はZero-TDのseed 701で計4,000局分を処理します。所要時間はPCによって変わります。元のPhase 7資産や本番の保存先とは別の出力を使います。元データが別の場所にある場合は、`$assets` にその `evaluation_results` フォルダを指定します。

```powershell
$assets = (Resolve-Path -LiteralPath '.\evaluation_results').Path
$initial = Join-Path $assets 'phase6-ntuple-20261118\measurements-final\untrained-zero.json'
$origin = Join-Path $assets 'phase7-td0-20261119\train-10000\zero-701'
$trial = (New-Item -ItemType Directory -Path '.\evaluation_results\phase7-longrun-repro-2000' -ErrorAction Stop).FullName
foreach ($case in @('continuous', 'first', 'second')) {
    $parent = if ($case -eq 'second') { Join-Path $trial 'first' } else { $origin }
    $until = if ($case -eq 'first') { 11000 } else { 12000 }
    & $py -B -m tools.phase7_train $initial (Join-Path $trial $case) --until $until --seed 701 --rate 0.0000625 --epsilon 0.20 --resume $parent
    if ($LASTEXITCODE -ne 0) { throw "$case の学習に失敗しました" }
}
@'
from pathlib import Path
import json
import sys
root = Path(sys.argv[1])
a, b, c = [root / name for name in ("continuous", "first", "second")]
def state(path):
    return json.loads((path / "state.json").read_bytes())["payload"]
assert (a / "model.json").read_bytes() == (c / "model.json").read_bytes()
assert (a / "games.jsonl").read_bytes() == ((b / "games.jsonl").read_bytes() + (c / "games.jsonl").read_bytes())
for key in ("model", "rng", "counts"):
    assert state(a)[key] == state(c)[key], key
print("Continuous and resumed results agree.")
'@ | & $py -B - $trial
```

通常の動作確認には、前の節の追加40局試験を使ってください。

設計や保存の仕組みは [Phase 7.1設計](phase7_longrun_design.md) を参照してください。
