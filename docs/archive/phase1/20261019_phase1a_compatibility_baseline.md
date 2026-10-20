> **Archived record:** This is a historical Phase 1 record. Use [Phase 1 handoff](../../phase1_handoff.md) for the current consolidated status. Machine-readable execution evidence remains under evaluation_results/.

# Phase 1A 実装前のランダムAI互換性基準

## 対象と条件

- 検証日: 2026-10-19
- 実行環境: Windows 11 x64、CPython 3.13.5、pytest 8.4.2
- 分離前本体: `reversi_player/reversi.py`
- 固定参照: `baselines/random_v0/reversi.py`、manifest version 1
- 両ファイルのSHA-256: `a485400c335a8731a0cafd1d77d98fbbcda1289194c4e13147cbaf3135b12b41`
- 固定seed: `0, 7, 31, 73, 20261016`
- seedは `harness.seeded_player` 経由で各プロセス起動時に適用。対局ごとに両scriptを独立起動し、同じコマンド列を送る。
- 比較対象はname応答、全genmove応答、各着手後の盤面、result。プロセス応答時間は比較対象から除外する。
- 固定基準の完全一致保証はmanifestのWindows CPython 3.13.5に限り、他Python版・実装間の乱数列一致を保証しない。

## 実行結果

分離前の既存関連テストを先に実行し、`76 passed, 0 failed in 2.30s` を確認した。

```text
python -B -m pytest -q tests/test_stdin_commands.py tests/test_result.py tests/test_invariants.py -p no:cacheprovider --tb=short
```

互換性テスト追加後、同じ既存テスト群と新規テストを実行し、最終版で `85 passed, 0 failed in 5.45s` を確認した。

```text
python -B -m pytest -q tests/test_ai_compatibility.py tests/test_stdin_commands.py tests/test_result.py tests/test_invariants.py -p no:cacheprovider --tb=short
```

5 seedの各対局で分離前本体と固定random_v0の着手列・盤面・終局結果が一致した。時間計測値は棋譜に含めていない。

| seed | 着手数 | 結果 | 固定版棋譜SHA-256 |
|---:|---:|:---:|---|
| 0 | 63 | B | `31df0b8b2008266cd4642eff35daa7a6e21de37d17215f52a9b88a4691296dcf` |
| 7 | 61 | W | `cf7ff0f4b66a8a59527cb6fdc07972a5584b665c2d87b68d33b4a4ed0afb4137` |
| 31 | 61 | W | `50a895ccf2d821aa893dcb44e89e067cd53fd4b211fe087e4cb603eaeb94c33c` |
| 73 | 61 | B | `bc9b112f82aec04c4182b115c30f0d4068d23abd328f8e934ebf2e27493e271b` |
| 20261016 | 61 | B | `3bbcf52556471c8ce7112a12e6aa9732d9093fc374f429ddb076208653ed162e` |

全体品質ゲートを最終版で実行した。pytestは `759 passed, 0 failed in 764.27s`、独立smokeは `1 passed, 758 deselected in 7.36s`。依存、モデル、Lint、format、strict mypy、snapshot、evidenceを含む全項目がPASS。

```text
python -B tools/quality.py
```

品質artifact: `evaluation_results/quality-606c4b02b37c4e5799671a234152ec2a/quality.json`

このJSONで `passed: true`、新規互換テストを含むsource hashの開始時・終了時一致を確認した。検証記録は評価結果ディレクトリ内にあり、通常のGit追跡対象ではない。

## 固定した互換条件

新規 `tests/test_ai_compatibility.py` は以下を検証する。

- manifestの固定版SHA-256を照合し、参照実装が入れ替わっていないこと。
- 同一seedで全局のプロトコル応答、棋譜、盤面、resultが固定版と一致すること。
- 初期黒番の候補列挙順が `[(3, 2), (2, 3), (5, 4), (4, 5)]` であること。
- 合法手がある呼出しで `random.shuffle` を1回だけ使い、標準 `random.Random(seed)` のshuffle後候補とmodule-level RNG stateが一致すること。
- 合法手なしではshuffleも乱数消費もなく、盤面が変わらないこと。
- Board構築、合法手照会、通常着手、result照会で乱数stateが変わらないこと。

既存の `tests/test_stdin_commands.py` は直接script起動、標準出力・標準エラー、quit、seed付きPlayer、入力応答と期限を検証する。`tests/test_result.py` は本体のpackage importとGUIのProcess/RequestHandlerによる盤面・パス・終局応答を検証する。`tests/test_invariants.py` は独立refereeに対する合法手、盤面遷移、不正着手の不変性を検証する。

## 未検証範囲と次段階への引継ぎ

- Phase 1A前なので選択器自体は存在せず、選択器の不正戻り値・例外・不正値適用前の拒否はまだ検証できない。設計に従い、分離実装後の回帰テストで確認する。
- GUIはProcess/RequestHandler経由のコード試験を確認した。実ブラウザ操作とHTTPサーバーへのPOST統合はこの基準に含めない。
- 対局の経過時間は環境変動があるため互換性の完全一致条件にしない。通常の期限遵守は既存テスト・ハーネスで別に検査する。
- 本作業では本番コード、既存テスト、設定ファイル、既存Phase 0資産を変更していない。既存の未コミット設計資料の変更も保持した。
