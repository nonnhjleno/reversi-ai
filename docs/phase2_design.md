# Phase 2 盤面評価・1手選択の実装準備

作成日: 2026-10-21  
状態: 実装準備完了。以下は実装前の設計・比較条件。Python・テスト・設定は未変更。  
対象: `feat/ai-phase2`、開始HEAD `82f2c7b46d4150144fc58d1525fbfdd69277584f`

工程と移行条件の正本は[段階設計書のPhase 2](リバーシAI段階開発・評価設計書.md#phase-2盤面評価関数と戦略の分離)。本書でこのPhaseの具体的なAI内部契約・初期実験条件・テスト計画を定義する。正式評価schemaや探索予算の契約は[ハーネス契約](harness_contracts.md)を参照する。

## 1. 目的と作業範囲

各合法手を仮の盤面に置き、その結果を評価して1手を選ぶ。Phase 1の「着手するマスの固定重み」と、今回の「着手後の盤面全体の価値」を区別する。

実装対象は既存の `reversi_player/reversi.py` と `reversi_player/ai_engine.py`。新規本番moduleは作らず、ルール・コピー接続は本体、特徴計算・評価・選択はengineへ置く。既存の位置selectorとランダムselectorを残す。既定selectorの切替は、このPhase内で関連回帰を確認して行う。

MinimaxはPhase 3、Alpha-Beta・実AIのtrace4はPhase 4の工程。frontierの追加、局面ごとの重み切替、自動重み探索、学習は今回の基本版を評価してから検討する。

## 2. 開始条件と保全

- 開始時はmain、作業ツリーclean。開始HEADから `feat/ai-phase2` を作成した。
- `random_v0` と `positional_v1` は独立した比較相手として保持する。
- 固定AI、現行ソースとpositional_v1の一致、Phase 0のmanifest・序盤bytesの計8項目はハッシュ照合済み。
- 開発用1000局面は既存の `harness.openings.load_population` で検証済み。held-outはファイルhashだけを確認し、内容の解析・選択・対局には使っていない。
- 直接インストールしたWindows / CPython 3.13.5を使用する。通常sandboxにはPythonが公開されていないため、今回の実行では確認済み実体を絶対パスで指定する。

開始時の原証拠は[preflight.json](../evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/preflight.json)、準備作業の結果は[実施記録](reports/20261021_phase2_preparation.md)を参照する。過去のPhase 1品質PASSを現在の品質確認へ読み替えない。

## 3. ルール接続と副作用

`BoardRows = tuple[str, ...]` は8行、各行8文字の `B/W/E`。座標は `(x, y)`、盤面アクセスは `rows[y][x]`。色は `B/W`。型定義は既存engineのものを再利用する。

予定する接続は次のとおり。以下は実装済みAPIではない。

| 接続 | 予定する役割・入出力 |
|---|---|
| `LegalMovesFor = Callable[[BoardRows, str], LegalMoves]` | snapshotと色から完全な合法手集合をy/x順で返す |
| `ApplyMove = Callable[[BoardRows, str, Move], BoardRows]` | snapshotへの合法な1着手を反転込みで適用し、新しいsnapshotを返す |
| `make_one_ply_selector(legal_moves_for, apply_move) -> MoveSelector` | 上記のstatelessな関数だけを保持する3引数selectorを作る |
| `evaluate_position(rows, viewpoint, legal_moves_for) -> float` | 指定色の視点で盤面を評価し、盤面・乱数・標準出力を変更しない |

ルールcallbackは本体のmodule関数として実装し、独立した作業用Boardへsnapshotをコピーして既存の `_ask/_play` を再利用する。コピーの構築ではselectorを明示し、既定selectorの構築へ再帰しないようにする。実Boardへのbound methodや、そのcellを共有するclosureをengineへ渡さない。

callback内で返すrows・候補は不変値とし、元rows・実Boardは変更しない。不正な仮着手を適用成功として返さず、入力値の範囲・合法性の違反は `ValueError` にする。engineはBoard・Pos・web・harnessをimportしない。独立refereeはテスト側で照合する。

本体は現在どおりroot候補を生成し、非空ならselectorを1回呼び、戻り値を検証後に実Boardの `_play` を1回だけ呼ぶ。空候補ではselectorを呼ばない。新selector単体も空候補ならcallbackを呼ばず `None` を返す。例外・不正戻り値は既存の故障契約へ伝播させる。

## 4. 特徴・評価視点・初期重み

評価視点を固定した色 `p` とし、相手を `q` とする。着手後もrootの着手色の視点で評価する。次の手番へ切り替わるという理由だけで符号を反転しない。

非終局では次を使う。

```text
C = (pの角数 - qの角数) / 4
M = (pの合法手数 - qの合法手数) / 64
P = (pの位置重み合計 - qの位置重み合計) / 696
V = 40*C + 40*M + 20*P
```

各特徴の絶対値は1以下、非終局Vの絶対値は100以下。これは勝率ではなく着手比較用の得点である。合法手数の正規化は固定の64を使い、0除算や局面ごとの倍率変動を避ける。

Pの基本表は `positional_v1` と同じ値を使うが、4角を0にしてCとの重複加点を避ける。角以外の絶対値合計は696。ある角がB/Wのどちらかに取得されたら、その角に接するCマス2個とXマス1個の負の位置重みを0へ置き換える。空き角の隣接リスクは残し、取得後まで同じ負点を課さない。この置換後も分母は696で固定する。

40/40/20は本プロジェクトの初期実験値として固定する。最適性や強化成功の根拠にはしない。基本版の結果を見て変更する場合は別候補として条件・hash・結果を保存し、最初の結果を置き換えない。

### 終局とパス

両色とも合法手なしなら終局とする。盤面に空きがあってもこの条件で判定する。視点色からの石数差を `d` とし、勝ちは `1000+d`、負けは `-1000+d`、引分は0を返す。勝敗をすべての非終局推定より優先し、同じ勝敗なら石差も反映する。

片側だけ合法手なしなら非終局。mobilityのその側の個数を0として通常評価する。1手選択に追加の相手着手や再帰を持ち込まない。

### 同点と基本性質

各root候補を仮着手してVを計算し、Vが最大の手を返す。同点はPhase 1と同じ `(y, x)` 昇順。候補列の並びやseedで選択を変えない。有限のfloatを返し、視点交換では `V(rows, B) = -V(rows, W)` を満たす。テストの浮動小数比較は `abs_tol=1e-12, rel_tol=0` を初期条件とする。

## 5. 先行して用意するテスト条件

新規テストは `tests/test_ai_phase2.py` にまとめる。Phase終了時のレビューまではFullのみで実行する。[新規テスト方針](ハーネス.md#全phase共通の新規テスト方針)に従い、既存のQuick/Standard登録ファイルへ新規ケースを混ぜない。

| ID | 検証内容 | 期待値・独立した根拠 |
|---|---|---|
| P2-01 | snapshotコピー、合法手列挙、仮着手 | 既存Boardと独立refereeの一致、元入力と実cellの不変、複数Boardの分離 |
| P2-02 | 角差・位置差・mobilityの抽出 | 手計算可能な合法局面と独立refereeの合法手数。特徴関数自身の出力を期待値へ流用しない |
| P2-03 | 正規化・初期重み | 初期盤面は全特徴0。初期盤面への黒(3,2)後は角差0・合法手数差0・位置差9、黒視点V=15/58 |
| P2-04 | 角と位置の重複・取得済み角の隣接 | Pの角は0。角未取得と取得済みで隣接3マスの扱いを照合 |
| P2-05 | 勝敗と終局 | 全黒盤は黒1064・白-1064、引分0。空きあり両者合法手なしでも終局 |
| P2-06 | パスと視点 | 一方だけ合法手なしは非終局。視点交換で符号反転、回転・鏡映で値が一致 |
| P2-07 | 1手選択 | 手計算で最高評価と分かるケース、候補所属、同点順、候補順を変えた場合、空・単一候補 |
| P2-08 | 副作用と確定着手 | 仮着手中の実cell・乱数・stdout不変、実着手1回、コピー構築に再帰なし、callback故障の伝播 |
| P2-09 | 依存・起動・互換 | engine逆import禁止、3引数selector、直接実行・package・別cwd・seed adapter・GUI接続の既存回帰 |
| P2-10 | ハーネス対局と保存 | 下記の事前条件で完走・先後・異常・応答時間・明示資産・raw証拠を照合 |

固定局面は合法棋譜と局面IDを残し、独立refereeでも確認する。反転・pass・空きあり終局の盤面データはテスト作成時に確定し、対象実装から自動生成した期待値で通さない。

既存のランダム互換・位置selectorの挙動は維持して回帰する。既定Boardの位置評価を前提とする統合テストは、1手評価への仕様変更を明記して更新し、合法性・初手・実着手1回の検査を保つ。棋力結果を理由に正しさの期待値を変更しない。

## 6. 対戦比較の事前登録

候補Aの実装前と実装後を、同じ現行ハーネス・固定random_v0で取得する。Phase 1の100局はハーネス版が異なる可能性があるため、新しいbeforeの代用にしない。追加で候補を固定positional_v1と直接対戦させる。

| 条件 | 固定値 |
|---|---|
| 開発序盤 | `benchmark_sets/phase0/development.json`、1000件、8 placements、Population v1 |
| 抽出・先後 | 100局=50ケース×先後2局、非復元抽出 |
| selection seed / master seed | `20261020` / `20261021` |
| profile / policy | `descriptive` / `descriptive` |
| startup / 通常command / genmove | 5秒 / 1秒 / 10秒 |
| before/afterの固定B | `baselines/random_v0/reversi.py` とmanifest |
| 追加の固定B | `baselines/positional_v1/reversi.py`、同engineとmanifest |
| Aの明示資産 | `reversi_player/ai_engine.py` |
| 実行環境 | Windows AMD64、直接CPython 3.13.5 |
| 観測 | 完了・失敗、勝敗・勝ち点率・先後別、共通case/role seedの得点差、全genmove応答時間 |
| 未実施 | held-out使用、confirmatory、実AI内部予算のverified判定 |

準備時のbeforeは `evaluation_results/phase2/preparation-5dda5d5f47a143958d8a344ebadbdd55/` に新規保存する。実装後は `evaluation_results/phase2/candidate-<UUID>/` を作り、次の共通引数で実行する。A/Bのscriptと必要な資産は上表に従って追加する。

```powershell
python -B -m harness.evaluate --a reversi_player/reversi.py --a-asset reversi_player/ai_engine.py --b baselines/random_v0/reversi.py --b-asset baselines/random_v0/manifest.json --openings benchmark_sets/phase0/development.json --games 100 --selection-seed 20261020 --seed 20261021 --evaluation-profile descriptive --budget-policy descriptive --startup-timeout 5 --command-timeout 1 --genmove-timeout 10 --output evaluation_results/phase2/candidate-<UUID>/after-vs-random-100.json
```

各100局の前に初期盤面から2局smokeを実行する。対局JSONだけでなく隣接するruns配下を保存する。before/afterでハーネス・固定B・母集合・抽出・seed・環境が変わった場合は対応比較を保留し、新条件で取得し直す。

参考結果の判定は、異常0と全完了を前提に、random_v0への対応得点差およびpositional_v1への直接得点率を報告する。差の大小だけで実装PASSや正式採用PASSを決めず、優越が不明なら保留する。正式CIは資格が揃うまで計算・主張しない。ハーネスのdecisionを書き換えず、positional_v1を保持する。

## 7. 実装順序と完了条件

1. 本書、[予定図](harness_architecture.md#phase2-design)、[予定境界](harness_contracts.md#phase2-boundary)、開始hash・before記録を照合する。
2. P2-01〜09の意味のある独立ケースを作り、対象実装の前後で検証する。
3. コピーcallback→特徴・評価→1手selectorの順で最小実装し、本体既定への接続を確認する。
4. 既存Phase 1回帰と新規テスト、`python -B tools/quality.py` のFullを実行する。既存17 strict対象・Lint・formatを維持する。同じmodule内の変更なのでstrict登録の追加は不要。新moduleが必要になれば事前にscopeを更新する。
5. 2局smokeと事前登録の参考比較、保存証拠の再解析・hash照合を行う。
6. 実装・テスト・図・契約・進捗を照合し、G0〜G5とPhase固有条件を個別に報告する。

準備完了とPhase 2実装完了は別の状態とする。Phase 2の完了後に新規テストの目的・所要時間・Quick/Standardへの登録要否をレビューする。次のPhaseへの進行は[段階設計書](リバーシAI段階開発・評価設計書.md)の条件で判断する。
