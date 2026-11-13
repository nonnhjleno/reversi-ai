# 提出AIのPython互換性修正

## 結論と原因の確度

2026-11-13、利用者から、`ranger-phase5-7s-20261110.zip`をサーバーへアップロードすると表示名が`nan`となり、対戦できないとの報告があった。

旧ZIPにはPython 3.12以降の`type`文が含まれていた。Windows上のCPython 3.10.11・3.11.9で旧ZIPの展開物を起動すると、`ai_engine.py`の`type BoardRows = ...`でSyntaxErrorとなり、標準出力が空のまま異常終了した。CPython 3.13.5では旧ZIPも`ranger`を返した。`timed_search.py`の`type Clock = ...`も同じ互換性問題を持っていた。

Python公式の[type文の仕様](https://docs.python.org/3.12/reference/simple_stmts.html#the-type-statement)でも、導入は3.12とされている。`from __future__ import annotations`はこの構文を旧Pythonへ追加しない。

サーバーのPython、起動時stderr、ZIP展開・起動方法、未取得の名前を`nan`表示にする仕組みは未確認。今回確認した互換性問題は両症状を説明できる原因候補だが、サーバー実機での原因確定・復旧確認とは区別する。外部アップロードは実施していない。

## 修正と保全

`reversi_player/ai_engine.py`と`timed_search.py`の型エイリアスを、Python 3.10でも利用できる`TypeAlias`付き代入に変更した。型の表す内容は維持した。通常提出経路は標準ライブラリだけを使い、5ファイルの構成も維持する。

評価・探索・期限処理の関数とクラスは固定`search_v1`とASTで照合した。さらに旧ZIPと新ZIPを比較し、importと型定義以外のモジュールAST（探索定数も含む）が一致することを確認した。探索アルゴリズム、評価、7秒設定、上限深度、盤面規則、標準入出力仕様、正式評価契約は変更していない。責務・依存・通信・状態・データの意味が変わらないため、アーキテクチャ図と契約の更新は不要と判断した。既存のstrict範囲と品質条件も維持した。

旧ZIP・旧manifestと固定baselineの計16ファイルは開始時hashから不変。旧ZIPを差し替えず、修正版を[別名ZIP](../../submissions/phase5/ranger-phase5-7s-py310-20261113.zip)と[別manifest](../../submissions/phase5/ranger-phase5-7s-py310-20261113.manifest.json)で保存した。

- ZIP直下: `reversi.py`、`ai_engine.py`、`search_trace.py`、`timed_search.py`、`response_config.json`
- ZIP SHA-256: `7349b9c3b5b779460536815ebd001dfa0f0e026de798e57135b71c630bf0ff74`
- ZIPサイズ: 18502 bytes
- 作業ブランチ: `fix/server-python-compatibility`
- 作業開始時Git状態: `docs/submission-strength-policy`、未コミット変更なし
- commit・push・merge・tag: 未実施

## ローカル検証

| Windows CPython | 旧ZIPのname | 修正版の全7コマンド | 固定random_v0との先後交換 | 候補の最大genmove外部応答 |
|---|---|---|---|---|
| 3.10.11 | SyntaxError・stdout空・終了1 | PASS、名前ranger | 2局完走、候補2勝 | 1.8394569秒 |
| 3.11.9 | SyntaxError・stdout空・終了1 | PASS、名前ranger | 2局完走、候補2勝 | 1.0222678秒 |
| 3.13.5 | ranger・正常終了 | PASS、名前ranger | 2局完走、候補2勝 | 0.8413042秒 |

ZIPのallowlist・CRCと各memberの実bytes/hashを照合し、新規領域へ展開した。起動確認は空cwdから評価用環境変数とprojectのPYTHONPATHを除去して実施した。3.10.11と3.11.9はPython公式のWindows embeddable版を検証用領域だけへ展開し、isolated `_pth`へ当該ZIPの展開先を追加した。通常CPythonのscriptディレクトリ検索を再現する設定であり、projectのコードやharnessを子のimport先へ加えていない。実argv・cwd・`_pth`設定・stdin・完全なstdout/stderrを保存した。

各runtimeで2局の新規プロセスを起動し、候補の先後を交換した。相手は直接CPython 3.13.5の固定random_v0、seedは20261113。既存Playerの起動・通常1秒・genmove10秒・quitの期限を維持し、両AIのshowboard・resultと独立refereeを各手で照合した。パス・終局後の両色genmove、quit、終了値0、stderrなしも確認した。各色の全棋譜・盤面は3runtime間で一致した。

この6局は互換性とプロトコルの参考検証であり、棋力向上・正式AI評価資格・未知サーバーでの時間保証を示すものではない。最大応答はこの試行の実測値であり、全局面の上限ではない。

関連テストは初回260成功・1失敗。失敗はengine全体の旧hashを固定した検査で、型定義の変更を検出した。探索の関数・クラスを固定版とAST比較する回帰テスト2件を追加し、変更後ソースのhashへ更新した。仕様の期待値や時間制限は緩和していない。Python 3.10・3.11の文法で提出4moduleを検査する回帰テスト8件も追加した。再実行は263件すべて成功した。

直接CPython 3.13.5で`python -B tools/quality.py`を1回実行し、実終了値0、全11工程PASSを確認した。全pytestは1621件成功、独立smokeは1件成功で、いずれもfailure・error・skipは0件。strict typingは19ファイルで成功した。全pytest工程の外部経過は531.0038973000192秒、独立smoke工程は6.077713400009088秒だった。

48対象sourceの開始時・終了時・最終確認時hashは一致し、`verify_publication`のエラーは0件。品質検証後もZIPのhash・CRC・全5memberと現行ソースの実bytes一致を確認した。追加した文書・manifestの確定記録は検証対象sourceを変更していないため、同一sourceに対するFullの再実行は行っていない。

## 証拠の保存先

機械可読な原証拠はGit管理外。移動・保管にはログと参照ファイルを含める。

- 互換性・ZIP・全6棋譜: `evaluation_results/server-compat-5bb8abe313da4dcdb2b8d9e6bb2ed041/`
- 個別driverと全ログ: `evaluation_results/server_compat_check_20261113.py`、`evaluation_results/server-compat-package-check-20261113.txt`および同名`.result.json`
- 初回関連テスト: `evaluation_results/server-compat-related-2fed406622fc46c890e9dab0a0b061ae.txt`と同名`.result.json`
- 再実行関連テスト: `evaluation_results/server-compat-related-5e2a5c91d8464fa68488d712d82f1d20.txt`と同名`.result.json`
- Full: `evaluation_results/quality-fbbc8f83f90a47fbb093d57e586d314f/`
- 検証用Python配布物: `evaluation_results/server-python-compat-runtime-20261113/`

## 提出手順の追記と推敲記録

`docs/提出パッケージ.md`には修正版ZIPへの案内と互換性問題・検証範囲だけを追記した。既存本文は保ち、旧ZIPと過去の検証記録も残した。`yomiyasu`はこの追記にだけ適用した。

### 変えたところ

- 実測資料をもとに、症状、再現した互換性問題、修正ZIP、未確認の実機条件を文章にした。意味が変わる恐れの候補は、識別子のコード書式化・相対リンク化、資料に基づく原因確度、既存の制約の言い換えであり、主張や確度を変えていないことを目視で確認した。

### 確認の記録

- 書き手・読み手・場面: 書き手: 決めていない／読み手: 決めていない／場面: 決めていない
- 実行した検査: lint、diff、資料との照合、最終稿の照合
- 実行しなかった検査: なし
- 硬化の検査: 候補なし
- 勢いの検査: 候補なし
- 値の検査: 意味が変わる恐れ 2件（資料の保存先・ZIP名と実在を照合済み）
- 見直し候補: 最終稿 0件（件数は点数ではない。増減は読みやすさの良し悪しを示さない）
- yomiyasu-check: executed via=verify run=20261113T083153Z-73a88128 attempt=1 final=sha256:244944dfc370 mode=compose checks=lint,diff,materials meaning_risk=8 replay=skipped
