"""pytest 公共夹具：每个测试方法使用独立的 DATA_ROOT 与全新 store。

两个测试文件原本都在模块导入期设置 DATA_ROOT，同进程运行时后导入者会
覆盖环境变量，导致跨文件共享数据目录、执行回执撞号。这里在每个测试
运行前切换到独立临时目录，并重置内存仓库，保证测试互不干扰。
"""
import os
import shutil
import tempfile

import pytest


@pytest.fixture(autouse=True)
def isolated_data_root():
    from app.seed import SEED_ROWS
    from app.store import store

    tmp_dir = tempfile.mkdtemp(prefix="cc10026_isolated_")
    previous = os.environ.get("DATA_ROOT")
    os.environ["DATA_ROOT"] = tmp_dir
    store._tables = {name: [dict(row) for row in rows] for name, rows in SEED_ROWS.items()}
    try:
        yield tmp_dir
    finally:
        if previous is None:
            os.environ.pop("DATA_ROOT", None)
        else:
            os.environ["DATA_ROOT"] = previous
        shutil.rmtree(tmp_dir, ignore_errors=True)
