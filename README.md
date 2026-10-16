# リバーシAI開発

大学の演習課題として、既存のリバーシプログラムを検証・評価しながらAIを改良するプロジェクトです。

## 最初に読む資料

| 目的 | 資料 |
|---|---|
| エージェントの作業手順 | [AGENTS.md](AGENTS.md) |
| 授業のコマンド・盤面・時間制限 | [課題仕様](docs/プログラム開発方法.md) |
| 開発の順序・完了条件 | [開発計画](docs/開発計画.md) |
| テスト・品質ゲート・対戦評価・GUIの操作 | [ハーネス](docs/ハーネス.md) |
| 現行の責務・依存・データフロー・状態 | [アーキテクチャ](docs/harness_architecture.md) |
| 現行の識別子・予算・時計・正式評価資格 | [データ契約](docs/harness_contracts.md) |
| strict範囲・移行方針・実行証拠 | [品質進捗](docs/QUALITY_PROGRESS.md)、[停止事項](docs/QUALITY_BLOCKERS.md) |
| 未実装のAI設計・強化案 | [AI分離設計](docs/リバーシAI分離設計.md)、[強化方針](docs/強化方針.md) |
| 開発支援AIの運用方針 | [AIモデル運用方針](docs/AIモデル運用方針.md) |
| 授業の記録 | [先生からのメモ](docs/先生からのメモ/) |

## 実行の入口

コマンドはすべてリポジトリのルートで実行します。品質ゲートの対応環境は、直接インストールしたCPython 3.13.5 / Windowsです。準備・制約・結果の読み方は[操作手順](docs/ハーネス.md)を参照してください。

```powershell
python -m pip install -r requirements-dev.txt
python -B tools/quality.py
```

品質ゲートは既存のstrict対象13ファイル、全pytest、独立smokeなどの必須検査を実行し、新しい`evaluation_results/quality-UUID/`へ検証証拠を保存します。品質ゲートの成功は、AIの正式評価資格や棋力向上の証明ではありません。配布GUIの`web.py`は意図的なstrict対象外ですが、既存Lint・GUI関連テストは維持します。Phase 10の新規本番モジュール対象漏れ防止は設計段階です。

GUIだけを起動する場合:

```powershell
python reversi_player/web.py
```

起動後にブラウザで`http://localhost:8888/`を開きます。

## 資料の配置

- ルートには入口の`README.md`と作業指示の`AGENTS.md`を置きます。その他の管理対象Markdownは`docs/`に集約しています。
- [docs/reports/](docs/reports/)は実行報告、[docs/archive/](docs/archive/)は旧計画・監査・整理前の本文です。過去の成功件数や「未実装」を現在の状態へ読み替えません。
- 文書内のMarkdownリンクはその文書からの相対パス、実行コマンドとコードのパスは原則ルート基準です。資料の移動はルートからの実行方法を変えません。
- 履歴本文・保存済み評価データ内のパスやhashは当時の記録として保持し、資料移動に合わせて書き換えません。履歴Markdownのリンク先だけは新しい配置へ追従しています。
