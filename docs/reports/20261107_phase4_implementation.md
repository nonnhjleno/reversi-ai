# Phase 4 実装・検証・評価報告

実装・対局取得: 2026-11-06。fresh再解析・最終照合: 2026-11-07。ブランチ`feat/ai-phase4`、開始HEAD `bf8ae76ed6f71e3f2b0bd2bddab76f3b813dce99`。未コミット変更なしから開始した。commit・push・merge・tagは実施していない。

## 判定

Phase 4のG0〜G5と固有条件を満たした。Phase 5の設計へ進行可能。実AIの正式計測はローカルWindows・直接CPython 3.13.5・depth2・Alpha-Beta対保全Minimaxの4局で成立した。棋力優越を確認したという判定ではない。固定random_v0・positional_v1は正式計測未対応のため、その対局は参考評価に限定する。

## 変更と保全

[実装前設計](../phase4_design.md)で責務・API・図・計数・失敗・予算範囲・テスト・評価条件を先に定めた。参照Minimax・評価関数を残してAlpha-Betaを追加し、同順序版と内部順序版を比較した。rootは(y,x)順と厳密改善を維持し、fail-low境界値で同点を更新しない。パスはplacement plyを消費せず、双方合法手なしで終局とする。

既定は深度2のAlpha-Beta。探索はimmutable snapshotとstateless callbackを使い、実Board更新は一度だけ。新規search_trace.pyはdepth1〜4を受領し、未対応time/nodes/none・未知キー・モデル指定を拒否する。unknown＋空searchの既存経路は非計測のまま。新規本番moduleをstrict・lint・formatへ明示登録した。本番からharnessへ逆importはない。

元ai_engineの定数・import・全既存関数・クラスを含むAST全体の先頭部分の一致と、固定baseline・序盤・harness・GUI・fixtureのhash不変を確認した。held_outはbytesのhash以外に使っていない。schema5・trace4・validation3と正式ANDは変更していない。

## 検証

- 必須Full: 全1391 pytest、独立smoke、strict18本を含む11工程成功。ソース44件の開始・終了hash一致、証拠公開検査成功。
- 専用テストファイルは227件でFull専用。既知盤面10区分、白黒、深度0〜4、ordered有無の200比較は独立Fraction Negamaxと参照Minimaxに厳密一致。合成木のfail-low同値、パス・終局・同点、入力/RNG不変、確定1回、例外、実プロセスtrace、不正設定を検査した。
- 初期の関連実行は新規テスト側のAPI/context・fixture名・例外型で12件失敗。修正後に該当14件成功、最終Fullでも成功した。最初のFullは旧docstringを発見して中断し、修正後に取り直した。中断記録を成功に数えていない。
- このPhaseで実ブラウザを手動操作していない。既存GUI回帰をFullで検証した。過去に利用者が確認した1局を今回の新規実測として数えていない。

Full証拠: `evaluation_results\quality-d9ed36897eea4eff9809265eebb11edd`。コマンドはルートから直接CPythonで`python -B tools/quality.py`。原記録・厳密なargv・環境・Git状態は後述の原証拠と機械要約に保存した。

## 探索量

固定代表10局面×深度1〜4の40条件で、同じ評価関数・深度・本番callbackを用いた。root/leaf/passを含む再帰実呼出しをnodeとし、架空の省略nodeは加算しない。全条件でroot値・最善手は一致した。

| 方式 | Minimax比node削減の中央値 | 削減範囲 |
|---|---:|---:|
| alphabeta_same | 26.90% | 0.00%〜96.40% |
| alphabeta_ordered | 40.71% | 0.00%〜95.77% |

内部順序付き版は同順序版より中央値で削減が大きいため既定に採用した。全局面で改善する保証はなく、ordered=Falseも比較用に維持する。orderedの同順序版より悪化した例も`search-diagnostics.json`へ全件保存した。20%は暫定目標であり、条件を緩和して合格にしていない。実時間は各探索の単発測定で、厳密な時間短縮率の統計推定ではない。

## 対局と正式計測

変更前のPhase 3原証拠204局は、変更前のコード・依存資産hashの一致とfresh再解析を確認して再利用した。変更後は固定2相手に各2局smoke＋100局を実行した。ハーネス・B資産・development序盤・先後・selection_seed20261020・seed20261021・外部期限5/1/10秒を揃えた。各100局の棋譜・勝敗が変更前と完全一致し、参考対応差は0。棋力向上は認めない。先後別勝敗・全応答時間・異常は機械要約および原報告に保存した。

参考100局でのAの成績と、パス・終局問い合わせを含む外部genmove応答は次のとおり。時間単位はms。

| 相手 | A黒 | A白 | 平均genmove | 最大genmove |
|---|---:|---:|---:|---:|
| random | 50勝0敗 | 50勝0敗 | 10.002 | 69.493 |
| positional | 40勝10敗 | 40勝10敗 | 11.426 | 62.498 |

name・ask・play・showboard・result・genmove・quitを含む全コマンドの個別応答時間は原報告に保持し、変更前後の役別・コマンド別集計を`response-times.json`に保存した。単発の時間差から一般的な速度保証を主張しない。

別枠の正式計測は実AI同士で4局・2case、confirmatory/equal-work・depth2。両者のengine・producerを明示資産登録した。raw traceを修復せず、取得・保存後にfresh再解析した。budget・clock・child lifecycle・host timelineは全8対局プロセスverified、決定性は選択された1局面を各2回試すprobeで両役verified、モデルなし・fallbackなし・資産一致を確認。通常着手、パス、終局問い合わせを区別し、探索しない問い合わせは0node・0深度を記録した。時計適合のない環境へこの資格を一般化しない。

形式上の区間は0.0〜1.0で、2caseの計測適合試験から優越性を主張しない。固定相手への参考戦を正式優越へ昇格させていない。初回の実装確認でも4局を取得したが、その後にimport整形とdocstringを修正したため、最終コードでは別の4局を取得し直した。初回原証拠は残し、最終資格の代用にしていない。

## 完了条件と残る範囲

| 条件 | 判定・根拠 |
|---|---|
| G0 構成・設計 | PASS。実装前の図・責務・API・比較条件を確定 |
| G1 正しさ | PASS。独立オラクル、既知局面、参照探索、protocol/GUI回帰 |
| G2 品質 | PASS。最終Full全工程と証拠検証 |
| G3 対局健全性 | PASS。参考204局＋正式4局、予定局の不正手・同期失敗・timeout・異常終了なし |
| G4 比較可能性 | PASS。変更前証拠の再検証と同条件100局×2の対応比較 |
| G5 証拠・判断 | PASS。hash、条件、原trace、fresh再解析、改善なしの判断を保存 |
| Phase 4探索 | PASS。厳密root値と最善手、パス/終局/同点、不変性、探索量分布・悪化例を検証 |
| Phase 4正式計測 | PASS。実AIによる取得・保存・fresh再解析で必須資格成立 |

time/nodes予算・中断時応答・全局面の外部時間保証・search_v1固定は未実装でPhase 5以降の課題。学習、TT、bitboard、反復深化、held_out評価は未実施。今回の正式計測は計測基盤の適合証拠であり、学習や棋力向上の成功ではない。

## 証拠

[機械要約](20261107_phase4_metrics.json)を参照。原証拠はGit管理外の`evaluation_results/phase4/run-327c43552a074fb79d0aaed6314615e1/`。`preflight.json`、`baseline-verification.json`、`search-diagnostics.json`、`driver-journal.json`、`formal-final.json`、各参考評価JSON、各fresh再解析、`verification.json`、yomiyasu記録を保存した。取得時のrawは配下`runs/`にあり、原traceを後から修復していない。バックアップ時はこの領域とFull証拠を一緒に保存する。
