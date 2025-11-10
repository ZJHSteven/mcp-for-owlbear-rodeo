"""
docs_server.doc_store
=====================

该模块负责:
1. 扫描 `docs/markdown` 目录, 读取 Owlbear Rodeo 官方文档。
2. 基于文件路径生成资源 URI/唯一名称, 方便 MCP 资源列表引用。
3. 预先提取标题、简介、分词统计, 供搜索与资源描述复用。

模块显式拆分, 便于单元测试与后续替换文档来源(例如远程拉取)。
"""

from __future__ import annotations

import logging  # 统一日志接口, 方便日后与 AI 代理日志打通
import os
import re
import textwrap
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator, Sequence

from .exceptions import DocumentIndexEmptyError, DocumentNotFoundError

LOGGER = logging.getLogger(__name__)  # 模块级 logger, 方便精准控制日志级别

# 默认文档子目录常量, 避免散落 magic string
MARKDOWN_SUBDIR = Path("docs/markdown")
# 环境变量名称, 允许用户通过 `MCP_DOCS_ROOT` 覆盖文档目录
ENV_DOC_ROOT = "MCP_DOCS_ROOT"


@dataclass(slots=True)
class DocumentRecord:
    """
    表示单个 Markdown 文档的完整信息。

    Attributes
    ----------
    name: str
        资源唯一名称(例如 `apis/viewport`), 供工具/资源检索使用。
    category: str
        第一层目录名(apis/reference), 方便构建 URI。
    path: Path
        文件系统路径, 便于日志与调试。
    title: str
        Markdown H1 标题(若缺失则回退到文件名)。
    description: str
        由文件内首段正文提取的简介, 严格基于原文生成, 遵循用户约束。
    uri: str
        MCP 资源 URI, 形如 `doc://owlbear/apis/viewport`。
    text: str
        Markdown 原文, 在 resource/read 工序中返回。
    token_counts: Counter[str]
        预计算的分词统计, 供搜索引擎按词频匹配。
    """

    name: str
    category: str
    path: Path
    title: str
    description: str
    uri: str
    text: str
    token_counts: Counter[str]


def _tokenize(text: str) -> Counter[str]:
    """
    将文本拆分为小写单词并计算词频。

    使用正则提取 `[a-z0-9]+` 片段, 兼容 API 名称中的数字。
    """

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return Counter(tokens)


class DocumentStore:
    """
    文档加载与查询核心类。

    Parameters
    ----------
    doc_root: Path | None
        用户指定的文档根目录, 优先级最高。
    uri_prefix: str
        MCP 资源 URI 前缀, 默认 `doc://owlbear`.
    """

    def __init__(self, doc_root: Path | None = None, uri_prefix: str = "doc://owlbear"):
        self._uri_prefix = uri_prefix.rstrip("/")  # 统一去除末尾 `/`, 避免重复分隔符
        self._doc_root = self._resolve_doc_root(doc_root)
        LOGGER.info("使用文档目录: %s", self._doc_root)
        self._documents = self._load_records(self._doc_root)
        self._by_name = {record.name: record for record in self._documents}
        if not self._documents:
            raise DocumentIndexEmptyError(f"目录 {self._doc_root} 未发现任何 Markdown 文档")

    @property
    def doc_root(self) -> Path:
        """返回实际使用的文档根目录, 方便诊断。"""

        return self._doc_root

    @property
    def documents(self) -> Sequence[DocumentRecord]:
        """按名称排序后的文档列表, 供资源注册使用。"""

        return self._documents

    def get(self, name: str) -> DocumentRecord:
        """按唯一名称检索文档, 不存在时抛出 `DocumentNotFoundError`。"""

        try:
            return self._by_name[name]
        except KeyError:
            raise DocumentNotFoundError(name) from None

    def iter_records(self) -> Iterator[DocumentRecord]:
        """提供惰性遍历接口, 便于按需处理。"""

        yield from self._documents

    # ---------------------------- 内部工具方法 ---------------------------- #

    def _resolve_doc_root(self, override: Path | None) -> Path:
        """
        计算文档目录优先级:
        1. 显式 `override`
        2. 环境变量 `MCP_DOCS_ROOT`
        3. 包内 `docs/markdown` (wheel 安装后复制)
        4. 仓库根目录 `docs/markdown`
        """

        candidates: list[Path] = []
        if override:
            candidates.append(override)
        env_value = os.getenv(ENV_DOC_ROOT)
        if env_value:
            candidates.append(Path(env_value))
        module_path = Path(__file__).resolve()
        package_dir = module_path.parent  # docs_server 包目录
        site_root = package_dir.parent  # site-packages 根目录或项目 src
        candidates.append(package_dir / "data" / "markdown")
        candidates.append(package_dir / "docs" / "markdown")
        candidates.append(site_root / "docs" / "markdown")
        project_root = site_root.parent
        candidates.append(project_root / "docs" / "markdown")
        cwd_candidate = Path.cwd() / "docs" / "markdown"
        candidates.append(cwd_candidate)

        for candidate in candidates:
            if candidate and candidate.exists():
                return candidate

        # 如果全部候选路径不存在, 仍返回 override 或最后一个, 方便错误信息提示
        return candidates[0] if candidates else MARKDOWN_SUBDIR

    def _load_records(self, root: Path) -> list[DocumentRecord]:
        """遍历根目录下所有 Markdown 文件并构建元数据。"""

        records: list[DocumentRecord] = []
        glob_pattern = "**/*.md"
        for md_path in sorted(root.glob(glob_pattern)):
            if not md_path.is_file():
                continue
            relative = md_path.relative_to(root)
            parts = relative.parts
            if len(parts) < 2:
                LOGGER.warning("跳过文件 %s: 需至少包含一级目录以区分类别", md_path)
                continue
            category = parts[0]
            stem = md_path.stem
            name = f"{category}/{stem}"
            text = md_path.read_text(encoding="utf-8")
            title, description = self._extract_title_and_description(text, stem)
            uri = f"{self._uri_prefix}/{category}/{stem}"
            record = DocumentRecord(
                name=name,
                category=category,
                path=md_path,
                title=title,
                description=description,
                uri=uri,
                text=text,
                token_counts=_tokenize(f"{title}\n{description}\n{text}"),
            )
            records.append(record)
        return records

    @staticmethod
    def _extract_title_and_description(text: str, fallback: str) -> tuple[str, str]:
        """
        基于 Markdown 内容抽取标题与简介。

        提取策略:
        - 第一行以 `#` 开头则视为标题, 去掉 `#` 与多余空格
        - 简介取首段正文(非空且不含标题标记), 并用 `textwrap.shorten` 截断
        """

        lines = [line.strip() for line in text.splitlines()]
        title = fallback
        description = fallback

        for line in lines:
            if not line:
                continue
            if line.startswith("#"):
                candidate = line.lstrip("# ").strip()
                if candidate:
                    title = candidate
                continue
            description = textwrap.shorten(line, width=180, placeholder="…")
            break
        else:
            description = textwrap.shorten(title, width=180, placeholder="…")

        return title, description
