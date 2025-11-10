"""
docs_server.exceptions
======================

该模块集中定义文档存储/检索相关的异常类型, 便于主流程将错误处理逻辑
隔离在一处, 满足“异常与主流程解耦”的约束。后续若需要接入统一日志或
上报逻辑, 可直接在此扩展。
"""

from __future__ import annotations


class DocumentNotFoundError(KeyError):
    """
    文档未找到异常。

    Attributes
    ----------
    name: str
        触发异常的文档唯一名称, 便于上层记录上下文。
    """

    def __init__(self, name: str):
        self.name = name  # 保存出错的文档名, 方便日志记录
        super().__init__(name)


class DocumentIndexEmptyError(RuntimeError):
    """
    文档索引为空异常。

    当启动服务但没有任何 Markdown 文件被加载时抛出, 提示使用者先同步
    文档目录或修正路径配置。
    """

    pass
