# 品質ゲートのテスト分類記録

実施日: 2026-11-06。ブランチ: `refactor/quality-gate-test-classification`、開始HEAD: `12e46eeb387b004cebaa1b369cc238fe5b6a5cbf`、開始時clean。状態: **Quick・Standard_AI・Fullの実行と保存証拠照合に成功**。

## 1. 前回の整理との関係

`refactor/quality-gate-optimization` の[補助選択・scope整理](20261020_quality_feedback.md)と[自動routing・CI整理](20261020_quality_ci_implementation.md)を引き継ぐ。Full必須11工程、既存候補、型プローブ、未知変更のfallback、補助結果とFullの区別を維持する。未分類だったPhase 2/3の239ケースを終了後レビューし、今回の分類回帰19ケースも明示登録した。

開始時に既存Fullの41sourceを作業ツリーと照合して一致し、verify_publication=[]だったため、同じ開始状態の全1145件成功を基準にした。終了時は変更後の全1164件を別のFullで検証した。過去の成功を変更後の成功の代用にはしていない。

## 2. 分類基準と件数

| 最初に実行する段階 | Phase 2 | Phase 3 | 分類回帰 | 目的 |
|---|---:|---:|---:|---|
| Quick | 82 | 33 | 19 | 入力境界、手計算、短いパス・終局・不変性、基本計数、深度1互換、選択規則 |
| Standard追加 | 53 | 41 | 0 | 全固定局面の特徴・コピー・対称性、深度1/2の独立探索、実Board接続 |
| Full専用 | 0 | 30 | 0 | 全区分での深度0/3/4の独立全列挙 |

旧Quick274件と旧Standard296件を一つも除外していない。Quick408件、Quickを含むStandard524件、全件を含むFull1164件となった。Fullは分類による除外を行わない。

Phase 3の深い全列挙は開始時JUnit合計40.392秒・最大16.055秒だったためFull専用に残した。同じ関数の深度0もまとまりとしてFullへ置き、Quickでは別の既知ノード計数テストで深度0を確認する。短い処理だけを選ぶのではなく、検証目的と依存も考慮した。今回の分類回帰19件は関連回帰で目的と時間を確認してから登録した。

## 3. 選択契約と変更対象

登録先は `tools.feedback.REVIEWED_AI_TESTS` のQuick・Standard追加・Full専用の3群。ファイル全体の追加選択にはせず、関数の基底nodeidで選択する。登録群の重複と、非選択群も含む登録済み名の欠損を拒否する。パラメータ表示名に別テスト名が含まれていても、最初の `[` より前の基底名で判定する。

今回登録した3ファイルの未登録の新関数は短時間profileへ自動追加しない。登録済み関数へパラメータを足す場合はその関数の群を引き継ぐため、レビュー前の重いcaseは別関数・別ファイルへ置く。今回登録したテストファイルの編集も従来どおりFullへroutingする。名前別の選択登録を、ファイル差分が安全だという認定へ読み替えない。

実装前に[構造図](../harness_architecture.md)・[契約](../harness_contracts.md)・[操作](../ハーネス.md)を更新した。本番変更は既存strict対象の `tools/feedback.py` のみ。新規本番module・逆依存・正式入口の変更はない。AI・harness・固定資産・元のPhase 2/3テスト・品質設定・CIの計40ファイルは開始時hashと一致する。元テストの期待値・skip・Full要件は緩和していない。

補助報告の形式とrouting policyは維持し、選択規則の変更をpolicy hashで識別する。旧補助証拠のhashを書き換えず、当時の記録として保持する。Quick/Standardの成功はFull合格や正式AI資格を意味しない。

## 4. 同一sourceでの直列実行

Windowsの直接CPython3.13.5、既存の固定開発依存でQuick→Standard→Fullを直列実行した。42sourceは3実行の開始・終了・現在で同一。各pytestの選択計画・実collection・JUnit・journal・実行件数を照合し、失敗・error・skipは0。

| profile | 成功 | 未選択 | CLI外部wall秒 | pytest外部秒 | JUnit合計秒 |
|---|---:|---:|---:|---:|---:|
| quick | 408 | 756 | 39.039 | 30.087 | 26.099 |
| standard-ai | 524 | 640 | 82.029 | 73.851 | 71.110 |
| full | 1164 | 0 | 448.826 | 433.708 | 430.109 |

CLI外部wallは今回の補助driverが各コマンドの開始から終了まで測った値。証拠の後続再検証時間は含めない。1回の測定で速度改善や上限保証を主張しない。分類前の件数は旧selectorを同じcollectionに適用して確認し、過去の異なるAI・負荷条件の時間と速度差を計算していない。

Quick/Standardは `--measure-profile` による独立計測。通常のこの作業差分は品質基盤・資料・新規テストを含むためFullへ戻る。両補助reportはmeasurement_only=true、full_quality_passed=falseを保持した。Fullは必須11工程、全1164pytest、独立smoke、strict17、モデル・scope・snapshot・証拠に成功した。verify_feedbackは両方=[]、verify_publicationも=[]。

## 5. 保存証拠と再現

原証拠とdriverはGit管理外の `evaluation_results/test-classification/run-c14491ccdd124cd28c79aaf01d020c1c/` に保存した。`execution-journal.json` に実argv・終了値・CLI wall・各新規出力先、`classification-plan.json` に追加nodeidと包含確認、`verification.json` に照合結果を記録した。元のpytest参照先が消失しても再照合できる範囲は既存の証拠保持契約に従う。

- Quick: `evaluation_results/feedback-e68871d9e7564fed96f2d8d936814ba2/`
- Standard: `evaluation_results/feedback-1464bc82cab54f32b5a8022f3a31afdd/`
- Full: `evaluation_results/quality-8ccab88c9e3246909af731efec350763/`

[機械可読な分類・実測の要約](20261106_quality_test_classification_metrics.json)には各レビュー済みcaseの所属、基底名別の開始時実測、現在の件数・時間・source hash・原証拠パスを記録した。要約だけで原記録を代替せず、新規checkoutにローカル原証拠があるとは扱わない。

```powershell
python -B -m tools.feedback --profile quick --measure-profile
python -B -m tools.feedback --profile standard-ai --measure-profile
python -B tools/quality.py
```

## 6. 回帰と途中の補助処理

実装前は新規5ケースの失敗で登録機構の欠落を検出した。実装後の分類回帰19と既存feedback84、計103件が成功した。旧監査候補の包含、合成collection、原証拠の欠損・改変拒否、未知変更のfallback、未登録関数・登録名欠損・パラメータ群・選択順を確認した。

挿入helperは最初に空行数の前提で止まり、本番変更前だった。正確な定数名を目印に修正した。集計helperは深い探索名を含む分類テストのパラメータ表示名を誤検知したため、完全な基底nodeidで照合し直した。いずれも補助処理の訂正で、選択実装やテスト期待値を緩和していない。訂正内容と初回失敗JUnitは原証拠に保持した。

docs直下の変更3文書はyomiyasuのlint・diff・最終稿照合を実行した。既存の専門用語と対象外の過去本文は維持し、文書リンクの専用回帰も成功した。最終照合はローカル原証拠の `final-audit.json` に記録する。

## 7. 基底名ごとのレビュー

以下の時間は分類根拠にした開始時FullのJUnit値で、今回の実行時間の代用ではない。各関数のパラメータ全件を同じ群へ割り当てた。

| 基底nodeid | 最初の段階 | case数 | JUnit合計秒 | 最大秒 |
|---|---|---:|---:|---:|
| `tests/test_ai_phase2.py::test_fixed_positions_replay_and_independent_expected_values` | standard-ai | 10 | 0.010 | 0.001 |
| `tests/test_ai_phase2.py::test_snapshot_callbacks_match_independent_rules_without_mutation` | standard-ai | 10 | 0.027 | 0.005 |
| `tests/test_ai_phase2.py::test_snapshot_copy_cells_are_separate_and_constructor_is_not_recursive` | quick | 1 | 0.001 | 0.001 |
| `tests/test_ai_phase2.py::test_snapshot_boundary_rejects_invalid_shape_or_cells` | quick | 27 | 0.001 | 0.001 |
| `tests/test_ai_phase2.py::test_snapshot_boundary_rejects_invalid_color` | quick | 21 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_apply_rejects_invalid_or_illegal_move_without_mutation` | quick | 13 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_features_evaluation_and_each_candidate_match_independent_oracle` | standard-ai | 10 | 0.028 | 0.004 |
| `tests/test_ai_phase2.py::test_initial_features_and_hand_calculated_first_move` | quick | 1 | 0.001 | 0.001 |
| `tests/test_ai_phase2.py::test_corner_adjacency_mask_is_local_and_does_not_mutate_phase1_table` | quick | 8 | 0.008 | 0.001 |
| `tests/test_ai_phase2.py::test_terminal_outcome_dominates_nonterminal_heuristic` | quick | 4 | 0.001 | 0.001 |
| `tests/test_ai_phase2.py::test_reachable_pass_and_terminal_with_empty_squares` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_evaluation_is_antisymmetric_and_invariant_under_eight_symmetries` | standard-ai | 10 | 0.023 | 0.004 |
| `tests/test_ai_phase2.py::test_integer_numerator_preserves_mathematical_ties` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_one_ply_selector_matches_independent_candidate_scores_and_order` | standard-ai | 10 | 0.029 | 0.006 |
| `tests/test_ai_phase2.py::test_one_ply_hand_calculated_white_reply_and_difference_from_positional` | standard-ai | 1 | 0.003 | 0.003 |
| `tests/test_ai_phase2.py::test_empty_selector_calls_neither_rule_callback` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_selector_evaluates_children_from_root_color` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_rule_callback_exception_propagates_without_side_effects` | quick | 2 | 0.001 | 0.001 |
| `tests/test_ai_phase2.py::test_default_board_keeps_real_cells_during_analysis_and_commits_once` | standard-ai | 1 | 0.002 | 0.002 |
| `tests/test_ai_phase2.py::test_default_factory_receives_only_stateless_module_callbacks` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase2.py::test_nonterminal_bounds_over_independent_legal_trajectory` | standard-ai | 1 | 0.051 | 0.051 |
| `tests/test_ai_phase3.py::test_all_fixed_positions_match_independent_negamax` | standard-ai | 40 | 0.698 | 0.098 |
| `tests/test_ai_phase3.py::test_depth_zero_through_four_and_terminal_early_stop` | full | 30 | 40.392 | 16.055 |
| `tests/test_ai_phase3.py::test_initial_tree_has_known_counts` | quick | 3 | 0.012 | 0.007 |
| `tests/test_ai_phase3.py::test_root_and_internal_pass_preserve_placement_depth` | quick | 1 | 0.004 | 0.004 |
| `tests/test_ai_phase3.py::test_depth_one_matches_one_ply_and_order_is_deterministic` | quick | 10 | 0.038 | 0.009 |
| `tests/test_ai_phase3.py::test_search_rejects_invalid_depth` | quick | 6 | 0.001 | 0.001 |
| `tests/test_ai_phase3.py::test_factory_rejects_invalid_depth` | quick | 7 | 0.000 | 0.000 |
| `tests/test_ai_phase3.py::test_search_rejects_invalid_snapshot` | quick | 2 | 0.000 | 0.000 |
| `tests/test_ai_phase3.py::test_selector_empty_input_has_no_callbacks` | quick | 1 | 0.000 | 0.000 |
| `tests/test_ai_phase3.py::test_callback_failures_propagate` | quick | 2 | 0.001 | 0.001 |
| `tests/test_ai_phase3.py::test_search_is_immutable_repeatable_and_silent` | quick | 1 | 0.016 | 0.016 |
| `tests/test_ai_phase3.py::test_default_board_searches_copies_and_commits_once` | standard-ai | 1 | 0.021 | 0.021 |
