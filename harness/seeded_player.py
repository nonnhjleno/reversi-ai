import random
import runpy
import sys
from pathlib import Path


def main():
    script = Path(sys.argv[1]).resolve()
    random.seed(int(sys.argv[2]))
    sys.path.insert(0, str(script.parent))
    sys.argv = [str(script)]
    runpy.run_path(str(script), run_name='__main__')


if __name__ == '__main__':
    main()
