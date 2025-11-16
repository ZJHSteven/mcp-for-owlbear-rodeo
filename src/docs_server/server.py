"""
docs_server.server
==================

CLI 与 MCP Server 入口。负责:
1. 初始化 `DocumentStore` 与 `SearchIndex`
2. 注册资源与工具(搜索、打开文档)
3. 暴露 `main()` 供 `uvx mcp-docs-server` 直接启动
"""

from __future__ import annotations

import argparse
import logging
from pathlib import Path
from typing import Annotated, Sequence

from pydantic import Field

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.resources import TextResource

from .doc_store import DocumentRecord, DocumentStore
from .exceptions import DocumentNotFoundError
from .search import SearchIndex, build_search_index

LOGGER = logging.getLogger(__name__)

# ---------------------------- 工具参数 Schema ---------------------------- #
# 通过 Pydantic Field 在类型提示中声明参数约束, FastMCP 会据此自动生成 JSON Schema。
QueryArg = Annotated[
    str,
    Field(description="搜索关键词, 不能为空, 支持 API 名称或关键术语", min_length=1),
]
TopKArg = Annotated[
    int,
    Field(description="返回结果条数, 限制在 1-20 之间以控制响应负载", ge=1, le=20),
]
DocNameArg = Annotated[
    str,
    Field(description="文档唯一名称, 形如 apis/viewport, 由目录+文件名组成", min_length=1),
]

# ---------------------------- CLI 解析 ---------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    """
    构造命令行参数解析器, 方便 `uvx mcp-docs-server --help` 查看说明。
    """

    parser = argparse.ArgumentParser(
        prog="mcp-docs-server",
        description="将 Owlbear Rodeo Markdown 文档暴露为 MCP 资源与工具",
    )
    parser.add_argument(
        "--docs-path",
        type=Path,
        default=None,
        help="自定义 Markdown 根目录 (默认自动探测 docs/markdown)",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="指定 MCP 传输方式, 默认 stdio, 便于 AI 客户端通过管道启动",
    )
    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="调整日志级别, 便于排查路径/资源加载问题",
    )
    return parser


def _configure_logging(level: str) -> None:
    """
    设置基础日志格式, 输出到标准错误, 包含时间戳与级别信息。
    """

    logging.basicConfig(
        level=getattr(logging, level),
        format="[%(asctime)s] %(levelname)s %(name)s: %(message)s",
    )


# ---------------------------- MCP Server 构建 ---------------------------- #


def create_server(doc_root: Path | None = None) -> FastMCP:
    """
    创建并配置 FastMCP 实例。

    Parameters
    ----------
    doc_root: Path | None
        文档根目录, None 表示走自动探测。
    """

    store = DocumentStore(doc_root)
    index = build_search_index(store.documents)
    server = FastMCP(
        name="docs-server",
        instructions="提供 Owlbear Rodeo 扩展 API 与 Reference 文档查询能力。",
    )

    _register_resources(server, store)
    _register_tools(server, store, index)
    LOGGER.info("完成资源注册: %s 篇文档", len(store.documents))
    return server


def _register_resources(server: FastMCP, store: DocumentStore) -> None:
    """
    将每个 Markdown 文档注册为 `TextResource`, 供客户端列出与直接读取。
    """

    for record in store.iter_records():
        resource = TextResource(
            uri=record.uri,
            name=record.name,
            title=record.title,
            description=record.description,
            mime_type="text/markdown",
            text=record.text,
        )
        server.add_resource(resource)


def _register_tools(server: FastMCP, store: DocumentStore, index: SearchIndex) -> None:
    """
    注册 `search_docs` 与 `open_doc` 工具。
    """

    @server.tool(
        name="search_docs",
        description="按关键词或文档名搜索 Owlbear Rodeo 文档",
    )
    def search_docs(query: QueryArg, top_k: TopKArg = 5):
        """搜索匹配文档: 输入关键词与可选的返回数量, 输出 resource_link 列表。"""
        hits = index.search(query, top_k=top_k)
        if not hits:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"未找到与 '{query}' 相关的文档, 请尝试其他关键词。",
                    }
                ]
            }
        links = []
        for hit in hits:
            links.append(
                {
                    "type": "resource_link",
                    "uri": hit.uri,
                    "name": hit.name,
                    "description": hit.description,
                    "mimeType": "text/markdown",
                }
            )
        return {"content": links}

    @server.tool(
        name="open_doc",
        description="根据唯一名称读取完整 Markdown 文档",
    )
    def open_doc(name: DocNameArg):
        """打开指定文档: 根据唯一名称返回完整 Markdown 内容, 不存在则提示先搜索。"""
        try:
            record = store.get(name)
        except DocumentNotFoundError:
            return {
                "content": [
                    {
                        "type": "text",
                        "text": f"未找到名称为 '{name}' 的文档, 可先调用 search_docs 确认可用列表。",
                    }
                ]
            }
        return {
            "content": [
                {
                    "type": "resource",
                    "resource": {
                        "uri": record.uri,
                        "title": record.title,
                        "mimeType": "text/markdown",
                        "text": record.text,
                    },
                }
            ]
        }


# ---------------------------- 主入口 ---------------------------- #


def main(argv: Sequence[str] | None = None) -> None:
    """CLI 入口, 解析参数后启动 FastMCP。"""

    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    _configure_logging(args.log_level)
    server = create_server(args.docs_path)
    server.run(transport=args.transport)


__all__ = ["create_server", "main"]
