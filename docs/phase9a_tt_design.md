# Phase 9-Aの置換表

## 事前設計

比較元はPhase 8終了時の `354b522` である。固定深度と時間探索は根の色を固定して最大化と最小化を繰り返す。深さは石を置いた回数で数える。置換表が保持するのは根視点の値である。盤面は既存の行tupleをそのまま保持し、手番と組にする。Pythonのhashは添字にだけ使い、再利用前に盤面と手番を照合する。D4正規化や盤面の表現変更は加えない。乱数によるhash塩の差は衝突や性能に影響し得るため、ベンチマークでは `PYTHONHASHSEED`、対局では既存の局面別seedを固定する。

```text
reversi.py / search_trace.py → 固定深度 / 時間探索
                             → 1着手の置換表 → 同一盤面・手番・残り深さの値
                             → 既存の盤面規則・根視点の葉評価
```

根では照会も登録も行わず、行優先の順序と厳密比較で手を選ぶ。パスでは手番だけを交代するので、同じ盤面の両手番が混ざらない。連続パス回数は状態として持たず、両側の合法手が無い場合を終局とする。置換表は固定探索または1回の `genmove` の中だけに置く。時間探索では反復をまたいで共有し、根の色、評価関数、モデル、探索モードはその間固定される。

完了した葉にはそれ以上探索しないときの正確な値を保存する。その他の値は開始窓の下限以下ならUPPER、上限以上ならLOWER、窓の内部ならEXACTとする。EXACTはそのまま返し、LOWERは現在のbeta以上、UPPERは現在のalpha以下の場合に返せる。照会で窓を狭めず、値の比較以外に利用しない。再帰訪問に入った時点で1ノードと数える。置換表から返したノードも訪問数に含まれ、再評価しなかった葉は葉評価数に加えない。

固定長で、各スロットに1エントリを保持する。異なる状態の添字衝突では新しい結果を登録する。同一状態では深い結果を優先し、同一深度のEXACTを境界値から守る。世代は着手ごとの新規作成で区切る。値を登録するのは、必要な子探索が正常に戻り、既存の窓で値または境界が確定した後。時間探索の照会・登録は期限確認で挟み、途中中断したノードは保存しない。既に独立して確定した子の値はその着手内で保持する。中断時は最後に完了した反復の着手を返す。

## 評価の事前登録

- Phase 4の固定10局面×深度1〜4を基本とし、転置を確認できる補足局面は合法履歴から先に登録する。凍結手作業評価とZero-TD 10万局の701・702・703を照合し、容量比較は手作業と事前指定の701、容量0・1,024・4,096・16,384で行う。初期化込みで毎回空の表から始め、ウォームアップ後に順序を変えて反復する。
- 独立refereeと全幅Minimaxで根の値・同点の選択・合法性を確認する。境界値、同じ深さ、衝突、容量、パス、終局、期限中断は専用テストで検証する。
- 第一候補は4,096。採用検討の目安は同値性と異常0件を前提に、対応する固定深度4の探索時間比の中央値で10%以上短縮、p95応答の悪化が10%以内。条件を満たす容量のうち最小を選ぶ。満たさない場合、4,096は互換性と費用確認の試走に使用する。仮の効果を見込んだ大容量化は行わない。
- Phase 8の開発序盤から固定seed 20261119でまず1ペアを取り、手作業と、必要な場合は同じZero-TDモデルを両側に使ってTTの有無を比較する。双方とも探索上限6.5秒、最大深度4、応答目標7秒、外部genmove上限10秒。起動5秒、通常応答1秒。各対局は独立refereeとtrace4で検証し、直列で取得する。
- 手作業で固定局面の時間基準を満たし、試走で正常なら12ペアへ進む。n-tupleも両条件を満たし、追加対局によって採否が変わり得る場合に3 seed各12ペアへ進む。試走の実時間から見込む全費用が手作業30分、n-tuple合計90分を超える場合はその範囲を保留する。正式採用には既存の独立評価が必要。Phase 0 held-outとPhase 6.5凍結testは開発時に使わない。

## PowerShellでの再現

リポジトリのルートで、Windowsに直接インストールしたCPythonを指定する。以下の相対パスはリポジトリのルートを基準とする。通常起動で `REVERSI_TT_CAPACITY` を省くか `0` にすると、置換表は無効になる。指定できる値は0または1〜16,384の2の累乗である。1,024、4,096、16,384は今回の比較対象である。

```powershell
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python313\python.exe'
$env:REVERSI_TT_CAPACITY = '0'
@('name', 'genmove B', 'quit') | & $py -B reversi_player/reversi.py
$env:REVERSI_TT_CAPACITY = '4096'
@('name', 'genmove B', 'quit') | & $py -B reversi_player/reversi.py
```

n-tupleを使う場合は `REVERSI_SEARCH_MODEL` にPhase 6形式のモデルの絶対パスを指定する。例ではPhase 8で照合した台帳から、Zero-TD 100,000局のseed 701を選ぶ。台帳をコピーする場合は、記載されたパスでモデルファイルを開けることを確認する。

```powershell
$registry = 'evaluation_results/phase8-20261125/assets-audit.json'
$record = (Get-Content -Raw -Encoding utf8 $registry | ConvertFrom-Json).models | Where-Object {
    $_.series -eq 'zero' -and $_.seed -eq 701 -and $_.games -eq 100000 -and
    $_.experiment -eq 'phase7-longrun-100k-rerun-20261120'
}
$env:REVERSI_SEARCH_MODEL = $record.path
@('name', 'genmove B', 'quit') | & $py -B reversi_player/reversi.py
Remove-Item Env:REVERSI_SEARCH_MODEL, Env:REVERSI_TT_CAPACITY
```

評価ハーネスでは環境変数を外し、評価設定の `search.tt_capacity` を使う。予算は既存の形式のまま指定する。置換表が有効な探索のJSONLに `tt_diagnostics` が加わる。探索量、応答時間、予算検証には既存のtrace4とschema 5を使う。

計測結果の出力先には新しいディレクトリを指定する。以下は探索時計測と、それぞれ1先後交換ペアの短い比較である。

```powershell
$env:PYTHONHASHSEED = '20261119'
$population = 'evaluation_results/phase7-longrun-100k-rerun-20261120/openings.json'
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
$bench = "evaluation_results/phase9a-benchmark-$stamp"
$manual = "evaluation_results/phase9a-match-manual-$stamp"
$learned = "evaluation_results/phase9a-match-zero701-$stamp"
& $py -B -m tools.phase9a_compare benchmark --registry $registry --output $bench
& $py -B -m tools.phase9a_compare match --population $population --output $manual --pairs 1
& $py -B -m tools.phase9a_compare match --population $population --output $learned --model $record.path --pairs 1
Get-Content -Encoding utf8 (Join-Path $bench 'summary.json')
Get-Content -Encoding utf8 (Join-Path $manual 'summary.json')
Get-Content -Encoding utf8 (Join-Path $learned 'summary.json')
Remove-Item Env:PYTHONHASHSEED
& $py -B tools/quality.py
```

計測の詳細は `run.json` と各条件のJSONに保存する。対局の `raw/` に棋譜、通信と予算の検証に使う記録が残る。参考対局の勝敗・時間は `summary.json` にまとまる。
