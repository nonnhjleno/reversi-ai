> **Archived record:** This is a historical Phase 1 record. Use [Phase 1 handoff](../../phase1_handoff.md) for the current consolidated status. Machine-readable execution evidence remains under evaluation_results/.

# Phase 1A 選択器分離 実装・検証報告

実施日: 2026-10-19<br>
ブランチ: `feat/ai-phase1`<br>
基準HEAD: `eb2e860f06ac731c13c7a0169e4de93f3b8e1108`

## 実装

- `reversi_player/ai_engine.py`を追加。盤面・合法手規則・入出力へ依存せず、受け取った順序付き合法手を複製し、非空時に標準`random.shuffle`を一度だけ行って座標を返す。
- `reversi_player/reversi.py`の`Board`にkeyword-onlyのselector注入を追加し、既定では新engineを使用する。候補列挙順、盤面・合法手の所有、着手適用、標準出力は本体に残した。selectorの戻り値は厳密に検査し、無効手や適用失敗を成功応答にしない。
- `pyproject.toml`のstrict mypy対象を13から14モジュールへ拡大。format除外を既存`reversi.py`・`web.py`だけに限定し、engineを通常のLint/format対象へ含めた。品質ゲートのstrict対象登録テストにも追加。
- `tests/test_ai_engine.py`と既存互換回帰`tests/test_ai_compatibility.py`で、選択契約、乱数消費、無手、戻り値異常、例外伝播、盤面・stdout不変、import方向、CLI互換、固定random_v0との棋譜を検査。

位置評価・探索・予算traceは追加していない。正式評価schema・trace契約も変更していない。

## 検証

修正前、`tests/test_ai_engine.py`は新規`ai_engine`をimportできずcollection errorとなり、意図した回帰検出を確認した。修正後の対象回帰・関連テストは**260 passed, 8 deselected**（264.74秒）。また、直接CPython 3.13.5のmypy strictは**14 source filesで診断0**。詳細な最終品質ゲート結果は下記に追記する。

分離前後の互換テストはseed `0, 7, 31, 73, 20261016`の完全対局を比較し、全棋譜・各盤面・終局結果・乱数状態の一致を確認する。期待値側は凍結済み`baselines/random_v0/reversi.py`を使用する。

短時間の独立対戦確認: `evaluation_results/phase1a-smoke-20261019-aiengine.json`。2局完走、A/B各1勝、失敗0、先後各1局。これはランダムAI同士のsmokeであり、強さの証拠・正式な探索予算認証ではない。両者の探索traceは`unknown`で、統計は`descriptive_only`。

変更前基準の条件・記録は[互換性baseline報告](20261019_phase1a_compatibility_baseline.md)に保持する。baselineの記録は本実装後のPASSを意味しない。

## 最終品質ゲートと判定

Windows直接CPython 3.13.5で`python -B tools/quality.py`を実行し、environment / model / dependencies / lint / format / typing / pytest / smoke / evidenceの全段階がPASSした。全pytestは**779 passed, 0 failed**（848.33秒）、独立smokeは**1 passed, 778 deselected**（7.62秒）。mypy strictは**14 source files、診断0**。品質報告は`evaluation_results/quality-ac381a97d44f4e60b010819bffc53d65/quality.json`、機械可読な証拠は同ディレクトリの`evidence.json`。公開検証`verify_publication(...)`の結果は`[]`。モデル整合性検査もPASS。

2局smokeは`evaluation_results/phase1a-smoke-20261019-aiengine.json`にあり、2局完走、A/B各1勝、失敗0。これはランダムAIの強さを示す採用判定ではない。

**判定: Phase 1A完了。** 分離前後の固定seed完全棋譜互換、乱数・盤面・標準入出力・合法手処理の回帰、品質対象登録、統合ゲートが成功した。実ブラウザ上のGUI操作は未検証であり、既存GUI関連回帰テストの成功と区別する。Phase 1B以降は未実装。Git操作は行っておらず、commit/push/mergeもしていない。
