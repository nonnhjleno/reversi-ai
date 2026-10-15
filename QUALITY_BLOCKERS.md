# 品質移行の停止事項

## Phase 5: protocol.py のpipe型とOS分岐

状態: **解消済み・Phase 5 PASS**。2026-10-15、開始HEAD=5329275。
ユーザーの限定変更承認を受け、モデル/契約先行更新、旧実装に対する互換回帰、最小実装、全品質再検証を完了した。常設strictは既存8対象を保持してprotocolを加えた9対象。全pytest584成功・0失敗、独立smoke1成功・0失敗、必須10検査すべて成功。証拠は `evaluation_results/quality-0604dc9258ae432ead2a48b894884236/`。31sourceの開始/終了/現在hash一致、収集complete、publication/model検査、Markdown再生成一致を確認した。詳細はQUALITY_PROGRESS.mdのPhase 5最終監査を参照する。
未解決のPhase 5 blockerはない。Phase 3のGUI保留は継続し、POSIX実機・性能不変・型introspection等の未検証範囲をPASSへ含めない。次フェーズ・commit・pushには進まない。

### 以下は承認前の停止履歴

以下の停止記録は承認前の調査履歴として維持する。ユーザーから「境界検査・OS条件の最小変更を、互換条件の設計と回帰を先行する条件で許可する」と回答を得たため、承認前の「無変更・保留」を今後の禁止指示に読み替えない。
停止時の対象はリポジトリ相対の `harness/protocol.py`。停止時点ではコード・テスト・設定・モデル図・契約は変更していなかった。
3回の修正失敗による停止ではなく、「型チェックのためだけの実行時ロジック変更禁止」を維持した安全な解決方針を確定できないため、実装前に停止した。

### 停止時の開始条件

- Gitは開始時clean、mainはorigin/mainより3commit先。既存8対象のstrictを実際に再実行して成功した。
- Phase 4のquality-a0408e7ff61d4136a53bd24b747d4624はpassed=true、31sourceの開始/終了/現在hash一致、verify_publication=[]、validate_model=[]。
- AGENTS、QUALITY_PROGRESS、pyproject.toml、現行architecture/contractsを確認した。本停止記録は開始時には存在しなかった。
- Phase 3のGUIは保留のまま。今回の停止はPhase 5の通信ハーネスを独立に調べた結果であり、Phase 3を完了扱いしたものではない。

### 診断と依存

単独strictは107診断:

| 種別 | 件数 | 主な内容 |
|---|---:|---|
| no-untyped-def | 23 | 時計・形式・時間検証、HarnessError、Playerとcontext managerの未注釈 |
| no-untyped-call | 69 | 未注釈関数/メソッド間の呼出 |
| assignment | 2 | 4/7キー、3/4端点のtuple長の推論 |
| var-annotated | 3 | commands、stderr_tail、queueの要素型 |
| operator | 1 | 未検証limitのNone候補と数値比較 |
| union-attr | 7 | stdin/stdout/stderrのreadline/write/flush/close |
| attr-defined | 2 | Windows型定義に存在しないos.killpgとsignal.SIGKILL |

依存はstdlibのみで、Windows周波数取得のctypesは関数内import。上流はevaluate、search_budget、contracts（関数内importも含む）。protocolから上流へ逆importしない。
PlayerはAIの独立起動・reader thread・queue・期限・終了回収・host証拠を管理し、GUIのweb.Processとは別経路。
純粋なclock_status/temporal_order等の結果はsearch_budget/contractsから正式評価に伝播するため、型付けを理由にunknown/failedの条件を変えてはいけない。

### 注釈だけでは解消しないことを確認した境界

#### A. PIPE指定とnullableなPopenの型

実装は3pipeすべてをPIPE、text=Trueで起動する。しかし現行mypy/typeshedのPopen[str]メンバーはIO[str]|Noneであり、引数条件から非Noneの属性型にはならない。
全関数が注釈済みの独立プローブでもwrite/flush/stdout・stderrのreadline/closeで5件のunion-attrを再現した。
元モジュールは同種の参照が7診断ある。プローブの5件と元の7件を混同しない。

通常の実Playerでは3pipeが存在し、name/ask/play/showboard/quit、正常終了・全reader停止を確認した。
したがってこれは**通常のPopen成功時にpipe欠損が発生したという不具合報告ではなく、型定義が引数依存の不変条件を表現していない問題**である。

一方、診断用の模擬stdin/stdout/stderr=Noneでは、_read_stdout/_read_stderrはreadlineのAttributeError、abortはcloseのAttributeErrorとなり、_closed=Falseを保持した。
assert追加なら例外種が変わり、Noneをskipするcloseなら終了状態やcleanup_end_nsの生成まで変わり得る。元の異常時動作を調べず「正常に同じ」と報告しない。

#### B. os.nameによる実行時分岐とmypyの静的OS判定

abortのPOSIX側は `os.name == 'nt'` のelseにあり、Windowsでは通常実行しない。しかし現行mypyはこの分岐でPOSIX専用属性を利用可能とは判定せず、killpg/SIGKILLを拒否する。
注釈済みの独立プローブでも2件を再現した。未注釈定義だけが原因ではない。
WindowsでPOSIX停止分岐を実行したという観測ではない。分岐条件の書換え、getattrによる取得、局所interface/cast、検査除外はいずれも「型注釈を足すだけ」と同一ではないため、無断で採用しない。

### 推奨する解決案（未実装）

#### 1. 境界検査とOS条件の限定変更を別途承認してから再開する（第一推奨）

1. 3pipeの存在を確かめた値を型付き通信処理へ渡す最小の境界検査を設計する。欠損時の例外種・表示・終了回収・保存状態を先に確定し、既存例外を変更するなら明示承認を得る。
2. OS条件は、正式対応環境と未対応OS、既存のos.name変更テスト等を照合し、mypyが認識するsys.platform条件等へ変更する可否を決める。単にPOSIX分岐を削除・検査対象外にしない。
3. clock descriptor、command/lifecycle/reason辞書、queueのstr|None、deque[str]、例外の後付け診断等を、未検証objectと通常の辞書形状に分離して注釈する。未知値を有効な端点と断定する型にしない。
4. 型付け前後で各時刻の採時順・所属・差分、タイムアウトと回収の結果を照合する。SP/Popen/PRの間へ勝手に処理を挟まず、既存のPR採時を後ろへ動かさない。guardがhost応答区間内へ入る場合は計測範囲への影響も明記する。

副作用: 実行時分岐・わずかな処理時間、異常系の例外/終了状態、公開process属性の変更を観測する時点に影響し得る。pipeを一度cacheする案は毎回process属性を読む旧実装と同じではない。
新しい「計測値欠損を補う」処理や「取得失敗を正常終了へ直す」処理を追加しない。新しい入力制限や時間猶予も導入しない。

モデルとの関係: 単なる注釈であれば図は維持できるが、境界検査を追加して失敗時の責務・状態・採時位置が変わるなら、実装前にsequence/state/data-flowと契約第5節を更新する。
新しいfailure経路が正式資格の検証を迂回しないこと、unknownとfailedの扱いが変わっていないことを正式入口の回帰で確認する。
正式評価schema5/trace4/validation3の変更が必要だとは現時点で判断していない。実装前設計で不要と確認できた場合は版を変えない。

再開条件: 上記の必要最小限の実行時変更の許可と、異常系で厳密に維持すべき観測を明示する。現在の禁止条件のままこの案を実装しない。

#### 2. 実行時検査を増やさず、限定的な型境界を明示承認する

PIPE指定の成功時不変条件を根拠に3pipeだけの局所castを、OS分岐内の属性について必要最小限の静的interfaceを検討する。
Popen/通信/時系列検証全体をAnyへ変更するのではなく、適用場所・値・前提を列挙し、その範囲を回帰で固定する。

副作用: 実際のpipe存在やOS APIを型自体は検査しない。公開process属性の不正な変更まで安全とは保証できず、cast呼出等も実行時処理を追加する。
独自stubで既知のOptionalやOS差を無根拠に消す、TYPE_CHECKING内の仮定だけで検証済みとする、module単位のignore/excludeで通すことは不可。
第一案より実行時の保証が弱く非推奨。限定cast等の承認がない現状では、この案も実装しない。

モデルとの関係: 動的な信頼境界が残ることを契約へ明記する必要がある。時計・raw・正式資格まで検証済みとする図へ書き換えてはいけない。

### 再開後の検証と順序

1. 変更可能範囲・例外/ログ/状態の維持条件を決め、必要なら先にモデルと契約を更新する。
2. 既存の通信形式・起動/通常/genmove/quit期限・異常終了・reader回収に加え、pipe欠損とOS分岐の挙動、clock/command/lifecycle辞書の不正値を回帰固定する。
3. 23定義へ注釈し、record/状態を表す型を必要最小限追加する。unknown/failedを静的な「検証済み」へ偽装しない。
4. 単独strict成功後にのみ既存8対象を保持してprotocolを9件目へ追加し、既存の対象漏れ回帰も拡張する。
5. 全品質ゲート、保存trace再解析、正常/不正時系列の全公開入口・CLI同値性を確認する。採時差の実測値完全一致は要求せず、意味・順序・計測範囲と状態契約で比較する。
6. source/publication/model照合と進捗更新を完了して初めてPhase 5をPASSとする。

### 今回実行した検証と保存先

- 通常strict: 8source成功。対象単独strict: 107診断・終了1（常設strictの失敗ではない）。
- 注釈済み境界プローブ: union-attr 5、attr-defined 2の計7診断・終了1。プロジェクトの設定は変更していない。
- 既存関連回帰: 通信形式、プロセス停止、起動/genmove/全応答期限、QPC曖昧区間、不正端点を選択して45成功・320除外、3.83秒。
- 実Player: 3pipe存在、時計descriptorの低水準verified、通常着手/盤面要求と正常quit、全reader停止。これだけで正式評価の全ANDを検証したとはしない。
- 模擬pipe欠損: reader/abortの既存AttributeErrorと_closed=Falseを確認。正常Popenでの欠損発生率や実故障の検証ではない。

一時領域は `C:\Users\taaai\AppData\Local\Temp\reversi-strict-phase5-ga0igyny/`。
baseline.json、protocol-before.py、strict-before.txt、protocol-before.txt、boundary-probe.py/.txt、baseline-related.xml/.txt、runtime-boundaries.jsonを新規保存した。
後日消失し得るため再開時に再検証する。POSIXの実回収、すべての107診断への注釈後の結果、変更後の正式資格・性能は未検証である。

今回はPhase 5をPASSにせず、次フェーズ、コミット、push、ブランチ操作へ進まない。

### 停止時の全体品質再検証

`python -B tools/quality.py` は終了0 / QUALITY PASS。
新規保存先は `evaluation_results/quality-b3c1e184f354462f992400f1f8ae74fb/`。
全pytest **549成功・0失敗、355.56秒**、独立smoke **1成功・0失敗・548除外、2.54秒**、typingは既存8source成功。
必須10検査すべて終了0、31sourceの開始/終了/現在hash一致、full/smokeの収集complete・errors=[]、verify_publication=[]、validate_model=[]、Markdown再生成一致、pendingなし。
これはprotocol未型付けの現行環境の成功であり、Phase 5のPASSを意味しない。
変更は本停止記録とQUALITY_PROGRESS.mdだけで、その他67trackedは開始hash一致。HEAD=5329275のまま、ステージ/コミット/push/ブランチ操作は行っていない。
