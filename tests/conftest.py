"""测试包初始化：把项目根加入 sys.path，让 `gateway` / `packs` 可导入。"""

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
