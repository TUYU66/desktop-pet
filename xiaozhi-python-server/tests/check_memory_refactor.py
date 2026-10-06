"""当前memory_v2固定隔离验收入口；不调用真实模型或数据库。

旧v3测试文件保留用于历史方法回归，不代表新的生产写入协议。
运行方式（服务目录）：python tests/check_memory_refactor.py
"""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
MODULES=("test_memory_v2",)

if __name__=="__main__":
    suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromName(name) for name in MODULES)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
