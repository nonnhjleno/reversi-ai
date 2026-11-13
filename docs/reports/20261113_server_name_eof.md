# 名前取得後のEOFによる異常終了の修正

開始ブランチは`fix/server-python-compatibility`、HEADは`dda8556`、作業ツリーはcleanだった。同ブランチで修正し、角接続辺石評価候補は取り込んでいない。

## 再現と修正

利用者から、互換性修正版が対局できているように見える一方で表示名は`nan`のままと報告された。サーバーの名前取得処理と更新時刻は未確認である。

従来版は`name\nquit\n`には`ranger`を返して正常終了するが、`name\n`だけ送って標準入力を閉じると、`ranger`を出力した後の`input()`で`EOFError`となり、終了コード1・stderrにtracebackを返す。終了コードを確認する名前取得処理では失敗になる可能性がある。ただし、そのような処理をサーバーが使っていることは確認していない。

`reversi_player/reversi.py`で標準入力のEOFを正常終了として扱うようにした。捕捉対象は`EOFError`だけで、コマンド処理や探索・評価・時間設定は変更していない。名前文字列は修正前後とも`ranger`である。通常の通信責務内の入力終了処理なので、ハーネスの契約・構造図の変更は不要と判断した。

`tests/test_stdin_commands.py`に、LF・CRLF・末尾改行なしの`name`単独送信、および空入力終了の回帰を追加した。終了コード0、名前の一致、stderrなしを確認する。関連38テストは成功した。

## 提出物と証拠

新ZIPは[EOF修正版](../../submissions/phase5/ranger-phase5-7s-name-eof-20261113.zip)、manifestは[こちら](../../submissions/phase5/ranger-phase5-7s-name-eof-20261113.manifest.json)。ZIP直下に従来と同じ5ファイルを収録した。旧ZIP内と比較して`reversi.py`以外の4ファイルは実bytesが一致する。旧ZIP・manifest・固定baselineは上書きしていない。

ZIPのSHA-256は`18ba263c97341cfb220c32fdec702bbb66b138799ecf39c2e096a55d1b7a57a8`、サイズは18,611 bytes。Windows直接CPython 3.13.5で、別の空cwdから新旧の展開物を起動し、旧ZIPの名前取得後終了1、新ZIPの終了0を確認した。新ZIPでは全7種類のコマンドも実行し、名前`ranger`・正常終了・stderrなしを確認した。環境変数`REVERSI_*`と`PYTHONPATH`を除去した。旧Python実機での今回の再検証や、棋力比較は行っていない。

原証拠はGit管理外の`evaluation_results/server-name-eof-20261113/`に保存した。作成・検証driver、全stdin/stdout/stderr、実argv、ZIPとmemberのhash、保護対象のhash、検証JSONを含む。サーバーへのアップロードは行っていない。

初回Fullは1638成功・1失敗、独立smokeは成功だった。失敗は報告書のリンク先manifestをまだ作成していなかったための文書リンク検査であり、修正実装のテスト失敗ではない。manifest作成後、このリンク検査単独は成功した。初回の品質記録は`evaluation_results/quality-b1c8e094ef0e48c8bdba51585f029414/`に保持する。

全成果物を揃えて`python -B tools/quality.py`を再実行し、全11工程PASS、pytest 1639成功、独立smoke成功、実終了コード0を確認した。pytest工程は1356.51秒、smoke工程は15.40秒。記録は`evaluation_results/quality-2d5ad5e6b9d542bbbfa4b3db8078532f/quality.json`にあり、sourceの開始・終了hashも一致した。以後はmanifestへの品質結果と本報告の追記だけで、実装・テスト・設定・ZIPのbytesを変更していない。

再提出にはこの新ZIPを使用する。結果は定時更新なので、次の更新後に名前を確認する。表示名が直るかは未確認であり、`nan`の実機原因を特定済みとは扱わない。
