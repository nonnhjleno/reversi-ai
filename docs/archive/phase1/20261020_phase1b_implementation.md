> **Archived record:** This is a historical Phase 1 record. Use [Phase 1 handoff](../../phase1_handoff.md) for the current consolidated status. Machine-readable execution evidence remains under evaluation_results/.

# Phase 1B 位置評価AI 実装・検証報告

> 2026-10-20最終追記: 以下は実装直後の履歴。直接CPython 3.13.5による再検証では全pytest 787成功、品質ゲート全10段階PASSを確認し、2局smokeと100局descriptiveも失敗0で完了した。「直接Pythonが見つからない」「比較未実施」は当時の確認状態であり、現在の判定ではない。訂正・証拠・参考採用判断は[Phase 1完了報告](20261020_phase1_summary.md)を参照する。

## 実装内容

- `reversi_player/ai_engine.py`に事前登録済みの8×8 `POSITION_WEIGHTS`と`select_positional_move`を実装した。
- 評価値は`POSITION_WEIGHTS[y][x]`。評価値が同じ場合は`(y, x)`昇順で選び、合法手が空なら`None`を返す。selectorは入力盤面・候補・乱数状態を変更しない。
- `reversi_player/reversi.py`の`Board()`既定selectorを位置評価へ接続した。合法手列挙、選択結果の検査、盤面への確定適用、標準入出力の所有は従来どおり本体に残した。
- `ai_engine.select_move`と`baselines/random_v0`は変更せず、互換回帰では旧random selectorを明示注入する。
- Minimax、mobility、Alpha-Beta、自動重み最適化、ハーネスの評価契約変更は行っていない。

## 検証

修正前に追加した位置評価回帰は、未実装のselectorと本体既定動作を検出して8件失敗し、既存テスト19件は成功した。これは変更前の再現記録である。

実装後の`tests/test_ai_engine.py`と`tests/test_ai_compatibility.py`は **36 passed**。固定random_v0とのseed付き全対局、候補順、shuffle回数、乱数状態、位置選択の評価・同点・非変更性、既定selectorの一回適用を確認した。`pytest -m smoke`は **1 passed, 786 deselected**。

個別Lintは成功、format確認は修正後 **12 files already formatted**、strict mypyは **14 source files / no issues**。

venv上の全pytestは **726 passed, 61 failed**（373.96秒）。失敗は主に正式評価fixtureのtemporal/baseline適格性と品質ゲート自己テストに集中した。プロジェクトの正式環境契約ではWindows venvランチャーはPID照合に未対応であり、この実行結果は正式PASSではない。位置評価・random_v0互換の専用36件は別途成功しているが、全体失敗を今回の変更によるものではないと断定もしない。

必須の`python -B tools/quality.py`も実行したが、最初のenvironment検査が「venvランチャーPID契約未対応、対応CPythonで実行」としてFAILし、後続のdependency・lint・format・typing・pytest・smoke段階はスキップされた。最終snapshotのレポートはローカル保存・Git管理外（`../../../evaluation_results/quality-d0df7e163a9a44e0b61597027e36e68d/quality.json`）。直接CPython 3.13.5実行ファイルはこの環境で見つからず、必須統合品質ゲートをPASSにできていない。

## 固定比較実験

事前登録済みの100局（50先後交換pair）は本実装作業では実行していない。序盤集合、seed、評価profile、閾値を変更していない。試合結果に基づく重み変更や性能優越性の主張も行わない。

比較条件の正本は[Phase 1B事前登録](20261020_phase1b_design.md)。この時点では統合品質ゲートが未PASSのため、事前登録の比較対局へはまだ進めない。直接CPythonで全品質ゲートを通し、既存全体テストの失敗原因が今回の変更と独立であることを確認した後に開始する。比較は正式な棋力向上・予算認証の証拠ではない。
