"""
docs_server 包入口模块
=====================

该文件集中暴露对外 API, 方便 `pyproject.toml` 的 `project.scripts`
条目引用 `docs_server:main`。为满足学习/审阅需求, 所有注释均使用
详细中文说明, 并记录当前包版本号以便日志输出。
"""

from __future__ import annotations

import importlib.metadata as _metadata  # 标准库工具: 读取已安装包的元数据
from typing import Final  # typing.Final 用于标记常量, 提升代码可读性

from .server import create_server, main  # 统一对外暴露, 供 CLI 直接使用

try:
    # 通过包名 "mcp-for-owlbear-rodeo" 获取版本, 便于日志或资源描述使用
    __version__: Final[str] = _metadata.version("mcp-for-owlbear-rodeo")
except _metadata.PackageNotFoundError:
    # 在本地源代码目录运行时, 包可能尚未安装; 回退到开发版本标记
    __version__ = "0.0.0-dev"

__all__ = ["__version__", "create_server", "main"]
