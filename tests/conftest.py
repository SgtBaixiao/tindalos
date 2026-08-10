"""pytest 公共配置：把 src/ 加入 sys.path（src 布局，独立于 pyproject 安装）。"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
