# search_v1

Phase 5で独立固定した探索相手。候補側のengine・評価・予算を変更しても、このディレクトリを更新しない。

起動: `python -B baselines/search_v1/reversi.py`。Python標準ライブラリのみを使う。固定depthの比較にはeval-depth2.json、参考timeにはeval-time100ms.jsonを両役の設定として明示する。通常提出mainはresponse_config.jsonの7秒目標を使うが、実機サーバーの安全保証ではない。

明示資産はreversi.py、ai_engine.py、search_trace.py、timed_search.py、response_config.jsonと使用設定。manifest.jsonに全資産hash・実行環境・用途・検証原証拠を記す。timeと固定depthを同workとして比較せず、モデル学習や提出予算の調整でこの固定版を書き換えない。
