"""XP CLI 엔트리포인트."""

import os
import sys

# Windows cp949 인코딩 문제 방지
if sys.platform == "win32":
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from xp.cli import main

if __name__ == "__main__":
    main()
