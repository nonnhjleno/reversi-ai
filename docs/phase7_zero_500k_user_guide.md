# Ranger Zero-TD 50万局実験の操作手順

Zero-TDのseed 701・702・703を、それぞれ累積10万局から50万局まで続けます。追加は1系列40万局、合計120万局です。Phase 7.1の実験 `003aa0b66d2d4d10a36ad27589d3c779` から、モデル、累積更新数、乱数状態を引き継ぎます。

起動確認後はPCだけで動きます。ネット接続やチャットは不要です。保存、評価、次の区間への移行、集計が自動で進みます。Phase 7.1の所要時間を局数に比例させた目安は約23時間20分です。今回は並列数と評価数が異なり、実際には24時間を超えることもあります。

## 準備

授業用リポジトリのルートでPowerShellを開き、末尾のコマンドでPhase 7.2の作業用フォルダーに移動します。必要なのは、Windowsに直接インストールした64ビットのCPython 3.13.5です。画面に `(.env)` と出ていても、`$py` に指定したPythonが使われます。パスが違うPCでは `py -0p` でインストール先を確認し、`$py` の行を合わせてください。

PCを電源につなぎ、電源接続時の自動スリープを止めてください。画面の消灯は構いません。スリープ中は計算が進まず、再起動や電源断ではプロセスが終了します。起動確認後はPowerShellのタブを閉じられます。ログオフや再起動の前には安全停止を使ってください。

`doctor` は既存の3系列、設定、開発用序盤、書き込み可否と空き容量を確認します。本番の事前確認には8 GiBの空きが必要です。原本は `evaluation_results/phase7-longrun-100k-rerun-20261120` に置いたままにしてください。`init` は新規フォルダーに設定を保存します。出力先が既に存在する場合は、別名を指定してください。

## 開始後の操作

`start` の応答に「起動確認済み」と管理ログの場所が出たら、`status` で状態を確認します。3並列では最大3系列が同時に学習します。PCの応答が遅い場合は、停止を確認してから `resume --max-workers 2` または `--max-workers 1` を指定してください。

```powershell
& $py -B tools/phase7_longrun.py status --root $run
& $py -B tools/phase7_longrun.py status --root $run --verbose
& $py -B tools/phase7_longrun.py stop --root $run
& $py -B tools/phase7_longrun.py verify --root $run
& $py -B tools/phase7_longrun.py resume --root $run --max-workers 3
& $py -B tools/phase7_longrun.py report --root $run
```

- `RUNNING` は学習中です。「現在」は表示更新が届いた局数、「保存」は復元できる局数です。残り時間は学習速度から計算し、対局評価の時間は含みません。
- `EVALUATING` では3系列の節目がそろい、学習を休止して凍結モデルを対局させています。本番では固定序盤12組の先後交換で、`positional_v1`、深度2の `search_v1`、同じseedの10万局モデルと比較します。
- `stop` が受理されたら、`status` で `STOPPED` または `COMPLETED` を確認してから電源を切ります。学習中は局の境界で保存します。評価中は実行中の評価試行が終わるのを待つことがあります。
- 翌日や再起動後は、授業用リポジトリのルートで末尾の最初の4行を実行します。その後、`status` と `verify` で状態を確認してください。元の保存点が正常で管理プロセスと子プロセスが終了していれば、`resume` で続けられます。突然止まったときは、前回の確定保存点より後の未保存分を同じ乱数状態から実行し直します。
- 実験中は使用しているworktreeのファイルや固定相手、Pythonの環境を変更しないでください。実験の途中で別ブランチのスクリプトを使うと設定照合で止まります。

## 異常が出たとき

Windowsの共有競合で `live.json` を置換できない場合は、短時間だけ待ちます。それでも置換できないときは、その回の進捗表示を保留するため、画面の数値が古いままでも学習は続きます。起動前の一時的な共有違反で子プロセスが起動しなかった場合の再試行は、設定と入力の保存点を再検証したうえで最大2回です。履歴は試行ディレクトリのログと集計に残ります。

管理プロセスが運転中の `status.json` を更新できないときも、進捗表示を次回に回します。起動確認と終了時の状態は、保存できたことを確認してから表示します。

評価の生ログから、対局開始前の `name` に制限時間内の応答がなかったことだけが確認できた場合は、同じモデル・相手・評価条件で最大2回、別の試行としてやり直します。試行ごとのログを保存し、成績に集計するのは照合済みの1試行です。対局開始後の異常では自動再試行を行いません。

正式な保存の失敗、保存点の破損、乱数や設定の不一致、原因を判定できない子プロセスの終了では停止します。管理プロセスが落ちた場合も新たな管理プロセスを自動起動しません。`status --verbose` の管理ログと試行ログを調べてください。空き容量が不足していれば別の不要ファイルで確保します。実験フォルダーの設定やcheckpointを手動で編集すると再開できなくなります。

`verify` は確定した保存の連鎖、評価の未完了件数、過去の復旧記録を表示します。復旧済みの履歴だけでは失敗になりません。エラーが残るときは、ログと終了コードを保存してください。

## 結果とモデル

本番では1,000局ごとに局境界で保存します。保持する評価用モデルは15万・20万・30万・40万・50万局の各モデルです。結果は実験フォルダーの `reports` に節目ごとに保存されます。途中経過を手元で読みたいときは、`report` を実行してください。

- `summary.md` で各seedと3 seed等重み平均の勝ち点率を読めます。未評価の値は空欄です。
- `results.csv` には勝敗、勝ち点、石差、評価時間が入ります。`progress.csv` には保存点、更新数、学習指標が入ります。
- `summary.json` の `milestone_models` から、Phase 8へ渡す `checkpoint` の場所、モデルIDとSHA-256を確認できます。その保存先の `model.json` が推論用のモデルです。再学習に使うなら同じフォルダーの `state.json` と棋譜も保管してください。

勝ち点率は勝ちを1、引き分けを0.5、負けを0として対局数で割った割合です。10万局の結果を基準に、固定相手と10万局モデルへの成績を見比べます。開発用序盤での1手選択の結果なので、Alpha-Betaへ組み込んだ場合の採用は別の評価で決めます。

## 今夜実行するコマンド

以下の `start` が本番開始です。PowerShellで上から実行します。途中でエラーが出た場合は、その時点で止めてください。

```powershell
$course = (Get-Location).Path
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$run = Join-Path $course 'evaluation_results\phase7-zero-500k-20261121'
Set-Location -LiteralPath (Join-Path $course 'evaluation_results\p72')
& $py --version
& $py -B tools/phase7_longrun.py doctor --profile zero-500k --root $run --max-workers 3
if ($LASTEXITCODE -ne 0) { throw 'doctor で確認できなかったため停止しました' }
& $py -B tools/phase7_longrun.py init --profile zero-500k --root $run
if ($LASTEXITCODE -ne 0) { throw 'init が完了しなかったため停止しました' }
& $py -B tools/phase7_longrun.py start --root $run --max-workers 3
if ($LASTEXITCODE -ne 0) { throw '起動を確認できません。status と管理ログを確認してください' }
& $py -B tools/phase7_longrun.py status --root $run
```
