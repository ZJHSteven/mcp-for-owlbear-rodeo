"""
docs_server.search
==================

提供极简全文检索能力, 通过对文档词频的余弦式评分, 在无需第三方
搜索引擎的情况下即可满足“根据关键字找文档”的需求。
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Iterable, Sequence

from .doc_store import DocumentRecord


def _tokenize_query(text: str) -> Counter[str]:
    """与 doc_store 一致的分词策略, 保证评分公平。"""

    tokens = re.findall(r"[a-z0-9]+", text.lower())
    return Counter(tokens)


@dataclass(slots=True)
class SearchHit:
    """
    搜索命中结果。

    Attributes
    ----------
    name: str
        文档唯一名称, 供 open_doc 使用。
    title: str
        命中文档标题, 有利于 UI 展示。
    uri: str
        资源 URI, 供资源链接直接引用。
    description: str
        文档简介, 直接来源于 Markdown 首段。
    score: float
        匹配得分, 方便调试排序逻辑。
    """

    name: str
    title: str
    uri: str
    description: str
    score: float


class SearchIndex:
    """
    只读搜索索引, 在进程启动时构建, 运行期共享。

    Parameters
    ----------
    documents: Sequence[DocumentRecord]
        已加载的文档列表, 将在初始化阶段完成向量化。
    """

    def __init__(self, documents: Sequence[DocumentRecord]):
        self._documents = documents
        self._norm_cache = {doc.name: self._vector_norm(doc) for doc in documents}

    def search(self, query: str, top_k: int = 5) -> list[SearchHit]:
        """
        根据查询词返回得分最高的 `top_k` 条结果。

        简化的余弦相似度实现:
        - 查询向量采用 `Counter`
        - 文档向量使用 `DocumentRecord.token_counts`
        """

        tokens = _tokenize_query(query)
        if not tokens:
            return []
        query_norm = math.sqrt(sum(count * count for count in tokens.values()))
        if query_norm == 0:
            return []

        hits: list[SearchHit] = []
        for doc in self._documents:
            numerator = 0.0
            for term, q_weight in tokens.items():
                if term not in doc.token_counts:
                    continue
                numerator += doc.token_counts[term] * q_weight
            denominator = self._norm_cache.get(doc.name, 1.0) * query_norm
            if denominator == 0:
                continue
            score = numerator / denominator
            if score == 0:
                continue
            hits.append(
                SearchHit(
                    name=doc.name,
                    title=doc.title,
                    uri=doc.uri,
                    description=doc.description,
                    score=score,
                )
            )

        hits.sort(key=lambda item: item.score, reverse=True)
        return hits[:top_k]

    @staticmethod
    def _vector_norm(doc: DocumentRecord) -> float:
        """预计算文档向量的范数, 提升查询速度。"""

        return math.sqrt(sum(freq * freq for freq in doc.token_counts.values())) or 1.0


def build_search_index(documents: Sequence[DocumentRecord]) -> SearchIndex:
    """工厂函数, 方便主流程初始化, 同时保留扩展点。"""

    return SearchIndex(documents)
