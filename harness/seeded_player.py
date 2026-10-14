import random
import runpy
import sys
from pathlib import Path


def main() -> None:
    """対象scriptを指定seedのPython randomで実行するための子プロセス入口。

    argvのscriptと整数seedを用い、random・sys.path・sys.argvを更新してrunpyする。
    他の乱数源・時間予算の決定性は保証せず、対象scriptの例外は伝播する。
    """
    script = Path(sys.argv[1]).resolve()
    random.seed(int(sys.argv[2]))
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()
