# 提出AIのPython 3.7以降への互換性修正

## 症状と再現

利用者は `ranger-phase5-7s-py310-20261113.zip` を再アップロードしたが、表示名は `nan` のままで、2つある `nan` の棋譜はどちらも着手前に `invalid move` だったと報告した。本人のAIがどちらかは未特定である。その後、利用者から結果は定時更新と確認できた。現在の順位表や棋譜が修正版を実行した結果かは不明であり、「修正版も実機で起動失敗」とは判定しない。

公開ページからはログイン案内だけを取得でき、大学アカウントで認証する必要がある。公開HTTPヘッダーは `Server: Werkzeug/3.0.1 Python/3.11.7` だったが、提出AIの子プロセスにも同じPythonを使うかは不明である。提出AIのPython・起動ログ・具体的な結果更新時刻は取得していない。

初回修正版の実bytesを空のcwdから起動し、Windows CPython 3.7.9・3.8.10・3.9.13で `typing.TypeAlias` のImportError、stdout空、終了1を再現した。Python 3.10.11・3.11.9では `ranger` を返して終了0だった。このローカル再現はサーバー実機の原因確定ではない。

## 修正範囲

- `ai_engine.py` と `timed_search.py` の `TypeAlias` importを `TYPE_CHECKING` 内に移した。注釈自体は `from __future__ import annotations` によって実行時評価を避ける。
- 型エイリアスの右辺では、実行時に評価される `tuple[...]`・`Move | None`・`collections.abc.Callable[...]` を、Python 3.7で利用できる `typing.Tuple`・`typing.Optional`・`typing.Callable` に変更した。
- `timed_search.py` と `search_trace.py` の `cast` の型引数を文字列化した。`cast` は値を変換せず、そのまま返す。
- 通常提出経路の名前、盤面、探索、評価、7秒設定、上限深度4は維持した。関数・クラスのASTは固定search_v1と比較し、`cast` の型文字列を同じ型式に解析した場合だけ同一として扱った。型式が別の型へ変わっても比較で検出する。トップレベルの通常代入定数も初回修正版と一致した。

型指定の実行時表現だけの変更であり、責務・依存先のモジュール・通信・状態・評価契約の意味は変えていない。アーキテクチャ図と契約の変更は不要と判断した。Python 3.6以前は対象外で、3.7未満にも対応できたとは扱わない。

## ZIPと実行検証

新しい提出物は `submissions/phase5/ranger-phase5-7s-py37-20261113.zip`。ZIP直下の5ファイル、CRC、全memberの実bytesと現行ソースの一致を確認した。SHA-256は `55d1bb68cd2ecc8bd140314cc7fde96e3a9a4e2b74453cab4202981a96486671`、サイズは18535 bytes。

Windows CPython 3.7.9・3.8.10・3.9.13・3.10.11・3.11.9・3.13.5の各環境で、全7コマンドの応答と固定random_v0との先後交換2局、計12局を確認した。全局で名前はranger、合法手・showboard・resultは独立refereeと一致し、パス・終局後のgenmove・正常quit、終了0・stderrなしを確認した。seedは20261113、候補の評価用環境変数は除去し、通常の提出経路を使った。既存Playerのstartup・通常1秒・genmove10秒・quitの期限は緩めていない。

Python 3.7〜3.9はPython公式のWindows embeddable配布物を新規検証領域へ展開した。3.10と3.11の既存検証runtimeは新規領域へ複製してから使用し、過去の `_pth` を変更していない。各コピーのisolated `_pth` に当該ZIPの展開先を追加し、通常CPythonのscriptディレクトリ検索を再現した。projectやharnessを子のimport先へ追加していない。ダウンロードURL・配布物hash・runtime・実argv・cwd・全stdin/stdout/stderr・12局の通信と盤面を保存した。

旧ZIP・旧manifest・固定baselineの18ファイルは開始時hashと一致した。新ZIPによって旧提出物を差し替えていない。これらは互換性とプロトコルの参考検証であり、棋力向上・正式AI評価資格・未知サーバーでの期限遵守を示すものではない。

## 回帰と品質

Python 3.7〜3.11の文法解析を回帰へ追加し、実行時のTypeAlias importを拒否した子プロセスでも名前が取得できることを確認する回帰を追加した。固定版の関数・クラスAST比較はsearch_traceにも広げた。engine全体のhashは、探索AST一致を先に確認してから変更後の値へ更新した。

初回関連テストは249成功・1失敗だった。追加テストで新Pythonのtyping.TypeAlias自体を削除すると、標準ライブラリ内部の参照まで壊したため、実行時のfrom-importだけを拒否する方式へ変更した。stderrの文字コードとWindows改行の扱いもテスト側で修正した。修正後の関連250件はすべて成功し、lintとstrict19ファイルの型検査も成功した。失敗ログは保持する。

対応する直接CPython 3.13.5で `python -B tools/quality.py` を1回実行した。実終了値0、必須全11工程PASS、全pytest1635成功、独立smoke1成功、failure/error/skipは0だった。strict対象19ファイル、モデル検査、lint・限定formatも成功した。pytest工程の外部経過は565.0112554999941秒、独立smoke工程は6.961255800008075秒だった。

品質原証拠は `evaluation_results/quality-2c4e3d4eed0246379af3eace8e69bedc/`。48sourceの開始・終了・最終確認時hashは一致し、`verify_publication` のエラーは0件。Full後の追記は定時更新の説明と検証報告・manifestであり、対象の実装・設定は不変なのでFullを再実行していない。

12局の候補AIのgenmove応答は計408回、最大1.6403227秒。起動前から最初の名前応答の検証完了までは最大0.0895338秒だった。各runtimeの同じ先後条件の全棋譜・盤面・結果も一致した。これは今回のWindowsでの測定範囲であり、未知サーバーでの上限保証ではない。

| Windows CPython | genmove件数 | genmove最大外部応答（秒） |
|---|---:|---:|
| 3.7.9 | 68 | 1.6403227 |
| 3.8.10 | 68 | 1.3471685 |
| 3.9.13 | 68 | 1.3325001 |
| 3.10.11 | 68 | 1.5641235 |
| 3.11.9 | 68 | 1.2554092 |
| 3.13.5 | 68 | 1.3591377 |

## 証拠と残る確認

開始HEADは `12a6d8baf9ca5000fd2df842b98e7ccbf3cde000`、開始時はclean。検証時のブランチは `fix/server-python37-compatibility`。その後、利用者の指示で未コミットの修正9ファイルを `fix/server-python-compatibility` へ移し、旧ブランチの参照を削除した。移管前後の9ファイルの実bytes/hashは一致し、テストは再実行していない。manifestの `source_branch` はZIP作成時の履歴として保持する。

原証拠はGit管理外の `evaluation_results/server-compat37-f1db579a9f8a48bc9b244406917e3ffc/`。`verification.json`、`final-verification.json`、`runtime-preparation.json`、`protected-start.json`、各runtimeの全コマンドstdout/stderr、12局のJSON、実行driver、初回・最終の関連テストとruntime検証の完全ログ・実終了値を保存した。途中の短時間診断2回は会話上のツール出力だけで、個別のrawログファイルは保存していない。これらを完全保存済みの証拠としては扱わない。

サーバー実機のPythonと起動方法、今回の結果が最新アップロードを反映しているか、実機で名前と着手が復旧するかは未確認。外部アップロードは行っていない。再アップロードする場合は新ZIPを選び、次回の結果更新後に名前と実際の着手を確認する。

## 提出手順の推敲記録

`docs/提出パッケージ.md` の最新版の案内と初回修正の履歴見出しだけをyomiyasuの対象にした。既存本文全体を推敲していない。初稿は実ZIPの検証JSON・コード差分・利用者発言と照合し、数値・識別子・出典候補14件を確認した。相対リンクの対象、SHA-256とサイズ、6環境×2局の合計、旧runtimeの失敗ログ、サーバーの未確認範囲を照合した。読点を1つ削り、主張は変えていない。

定時更新という新しい利用者の回答を受け、表示が最新ZIPの結果か未確認であることを別稿に追記した。これは表現だけの推敲とは分けた情報追加であり、先の検査記録と資料は上書きしていない。

### 確認の記録

- 書き手・読み手・場面: 書き手: 決めていない／読み手: 決めていない／場面: manual（推定）
- 実行した検査: lint、diff、資料との照合、最終稿の照合
- 実行しなかった検査: なし
- 硬化の検査: 候補なし
- 勢いの検査: 候補なし
- 値の検査: 候補なし
- 見直し候補: 原文 0件 → 最終稿 0件（件数は点数ではない。増減は読みやすさの良し悪しを示さない）
- yomiyasu-check: executed via=verify run=20261113T095603Z-792ff866 attempt=1 final=sha256:4f93bca2ab0b mode=rewrite doc_type=manual checks=lint,diff,materials meaning_risk=0 replay=skipped
