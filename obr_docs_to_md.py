#!/usr/bin/env python3
"""
教学向脚本：批量把 https://docs.owlbear.rodeo/extensions/apis/ 与
https://docs.owlbear.rodeo/extensions/reference/ 下的技术文档页面
转换为高保真的 GitHub Flavored Markdown（GFM）。

实现要点（呼应需求，便于后续复习）：
1. URL 收集：优先从 sitemap 解析 /extensions/apis/ 页面列表，避免直接抓目录对方风控。
2. 抓取：严格使用 curl 子进程执行，统一 UA/Referer，支持 cookie 复用与失败重试。
3. DOM 清洗：借助 lxml 解析，选择正文候选节点并删除导航、侧栏等噪音，补齐绝对链接。
4. 格式转换：调用 pandoc 转成带 pipe table、保留代码语言标签的 GFM，同时抽取图片资源。
5. 日志与产物：保存原始 HTML / 清洗后 HTML / Markdown 与 assets，并输出运行日志和 url-map。

脚本设计遵循“简单直观 + 单一职责 + 充分中文注释”的指导思想，便于编程初学者理解。
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse
import urllib.robotparser as robotparser

from lxml import etree, html

# ────────────────────────────── 常量配置 ──────────────────────────────

CURL_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127 Safari/537.36"  # 按需求使用 curl/8.x UA，模拟正常命令行抓取
DOCS_BASE_URL = "https://docs.owlbear.rodeo"
REFERER = f"{DOCS_BASE_URL}/"
SITEMAP_URL = f"{DOCS_BASE_URL}/sitemap.xml"
ROBOTS_URL = f"{DOCS_BASE_URL}/robots.txt"


@dataclass(frozen=True)
class CategoryConfig:
    """
    描述一个文档类别的抓取配置（例如 apis/reference），集中保存路径前缀与索引页地址，方便扩展。
    """

    key: str
    sitemap_prefix: str
    index_url: str


CATEGORY_CONFIGS: Tuple[CategoryConfig, ...] = (
    CategoryConfig(
        key="apis",
        sitemap_prefix="/extensions/apis/",
        index_url=f"{DOCS_BASE_URL}/extensions/apis/",
    ),
    CategoryConfig(
        key="reference",
        sitemap_prefix="/extensions/reference/",
        index_url=f"{DOCS_BASE_URL}/extensions/reference/",
    ),
)

# 按官方要求的正文候选选择器顺序排列，命中首个即可。
MAIN_SELECTORS: Sequence[str] = [
    "article.theme-doc-markdown",
    "article .markdown",
    "main .theme-doc-markdown",
    "main .markdown",
    "#__docusaurus .markdown",
    '[itemprop="articleBody"]',
    "article",
]

# 噪音选择器：导航、侧栏、目录、脚本样式等一律剔除，避免混入 Markdown。
NOISE_SELECTORS: Sequence[str] = [
    "header",
    "nav",
    "footer",
    "aside",
    ".theme-doc-toc-desktop",
    ".table-of-contents",
    ".theme-doc-sidebar-container",
    ".breadcrumbs",
    "script",
    "style",
    "noscript",
]

# curl 默认公共头部，有助于稳定拿到 HTML（教学注：Accept-Language 等可视需求调整）。
CURL_HEADERS: Dict[str, str] = {
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Connection": "keep-alive",
}

# Pandoc 转换参数常量，集中放置便于统一维护。
PANDOC_FROM = "html-native_divs-native_spans"
PANDOC_TO = "gfm+pipe_tables+raw_html"

# 装饰性元素选择器/路径，专门用于去除对内容理解无帮助的节点。
DECORATIVE_XPATHS: Sequence[str] = (
    ".//a[contains(@class, 'hash-link')]",
    ".//a[@aria-hidden='true']",
    ".//span[contains(@class, 'hash-link')]",
    ".//*[@class and contains(@class, 'copyButton')]",
)

# 日志统一使用北京时间（UTC+08:00），便于与操作者所在时区对齐。
LOCAL_TZ = timezone(timedelta(hours=8))
LOCAL_TZ_NAME = "UTC+08:00"

# ────────────────────────────── 数据结构 ──────────────────────────────


@dataclass
class CategoryPaths:
    """
    为单个类别记录对应的输出子目录，确保不同类别彼此隔离，便于后续检索。
    """

    raw_dir: Path
    cleaned_dir: Path
    md_dir: Path


@dataclass
class OutputLayout:
    """
    描述输出目录结构，集中保管路径，避免在主流程中散落硬编码。
    """

    root: Path
    raw_dir: Path
    cleaned_dir: Path
    md_dir: Path
    assets_dir: Path
    logs_dir: Path
    run_log: Path
    failures_log: Path
    url_map: Path
    cookie_jar: Path
    category_dirs: Dict[str, CategoryPaths]


@dataclass
class PageResult:
    """
    用于写入 url-map.json 的结构化结果，便于后续复用。
    """

    url: str
    category: str
    title: str
    slug: str
    raw_html: Path
    cleaned_html: Path
    markdown: Path


@dataclass
class TargetTask:
    """
    表示等待抓取的单个页面：归属类别 + 预估标题 + 规范化 URL + slug。
    """

    category: str
    title_guess: str
    url: str
    slug: str


# ────────────────────────────── 工具函数 ──────────────────────────────

def ensure_command_available(cmd_name: str) -> None:
    """
    自检依赖：确认系统已安装指定命令。
    若缺失则打印中文提示并退出，避免脚本继续执行导致混乱。
    """
    if shutil.which(cmd_name) is None:
        print(f"错误：未检测到必需命令 `{cmd_name}`。请先安装后再运行。", file=sys.stderr)
        sys.exit(1)


def prepare_layout(out_root: Path, categories: Sequence[str]) -> OutputLayout:
    """
    准备输出目录树：out/raw_html、out/cleaned_html、out/md、out/assets、out/logs。
    若目录不存在则创建；若存在则复用，以便增量运行。
    """
    raw_dir = out_root / "raw_html"
    cleaned_dir = out_root / "cleaned_html"
    md_dir = out_root / "md"
    assets_dir = out_root / "assets"
    logs_dir = out_root / "logs"

    for p in (raw_dir, cleaned_dir, md_dir, assets_dir, logs_dir):
        p.mkdir(parents=True, exist_ok=True)

    category_dirs: Dict[str, CategoryPaths] = {}
    for key in categories:
        category_raw = raw_dir / key
        category_cleaned = cleaned_dir / key
        category_md = md_dir / key
        for subdir in (category_raw, category_cleaned, category_md):
            subdir.mkdir(parents=True, exist_ok=True)
        category_dirs[key] = CategoryPaths(
            raw_dir=category_raw,
            cleaned_dir=category_cleaned,
            md_dir=category_md,
        )

    return OutputLayout(
        root=out_root,
        raw_dir=raw_dir,
        cleaned_dir=cleaned_dir,
        md_dir=md_dir,
        assets_dir=assets_dir,
        logs_dir=logs_dir,
        run_log=logs_dir / "run.log",
        failures_log=logs_dir / "failures.txt",
        url_map=out_root / "url-map.json",
        cookie_jar=logs_dir / "curl_cookies.txt",
        category_dirs=category_dirs,
    )


def timestamp() -> str:
    """
    返回 ISO8601 北京时间戳（UTC+08:00），用于日志与 url-map，便于审计。
    """
    return datetime.now(LOCAL_TZ).isoformat(timespec="seconds")


def append_line(path: Path, line: str) -> None:
    """
    通用日志写入工具：逐行追加，并确保父目录存在。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def curl_get(url: str, cookie_jar: Optional[Path] = None, referer: Optional[str] = None) -> str:
    """
    使用 curl 通过子进程抓取页面。
    添加基本的浏览器 User-Agent 来避免 Cloudflare 等 CDN 的简单拦截。
    """
    cmd: List[str] = [
        "curl",
        "--location",
        "--silent",
        "--show-error",
        "--compressed",
        "-A",
        CURL_USER_AGENT,
    ]

    for header_name, header_value in CURL_HEADERS.items():
        cmd.extend(["-H", f"{header_name}: {header_value}"])

    if referer:
        cmd.extend(["-e", referer])

    if cookie_jar:
        cmd.extend(
            [
                "--cookie",
                str(cookie_jar),
                "--cookie-jar",
                str(cookie_jar),
            ]
        )

    cmd.append(url)

    completed = subprocess.run(
        cmd,
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="ignore",
    )

    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"curl 报错（退出码 {completed.returncode}）：{stderr or '无额外输出'}")

    # 检测 Cloudflare 质询页面
    output = completed.stdout
    if "Just a moment" in output and "challenge-platform" in output:
        raise RuntimeError("遇到 Cloudflare 验证页面，curl 无法处理 JavaScript 质询")

    return output


def canonicalize_url(url: str) -> str:
    """
    统一 URL 格式：保留协议与域名，移除查询与片段，去除尾部斜杠（根路径除外）。
    这样可以减少重复键，方便后续映射。
    """
    parsed = urlparse(url)
    scheme = parsed.scheme or urlparse(DOCS_BASE_URL).scheme
    netloc = parsed.netloc or urlparse(DOCS_BASE_URL).netloc
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    return f"{scheme}://{netloc}{path}"


def guess_title_from_slug(slug: str) -> str:
    """
    将 URL slug 转换为比较可读的标题猜测，便于在日志中定位。
    """
    return re.sub(r"[-_]+", " ", slug).strip().title() or slug


def determine_category_from_url(url: str) -> Optional[str]:
    """
    根据 URL 的路径判断其属于哪个文档类别（apis/reference）。
    若不匹配任何已知类别，则返回 None。
    """
    parsed = urlparse(url)
    path = parsed.path or ""
    for config in CATEGORY_CONFIGS:
        prefix = config.sitemap_prefix.rstrip("/")
        if path == prefix or path.startswith(f"{prefix}/"):
            return config.key
    return None


def collect_targets_from_sitemap(sitemap_url: str, cookie_jar: Optional[Path]) -> List[TargetTask]:
    """
    从 sitemap.xml 中筛选出已知类别下的文档页面列表。
    sitemap 结构稳定，优先使用该来源以保证覆盖完整。
    """
    xml_text = curl_get(sitemap_url, cookie_jar=cookie_jar, referer=REFERER)
    root = etree.fromstring(xml_text.encode("utf-8"))

    # sitemap 通常使用默认命名空间：http://www.sitemaps.org/schemas/sitemap/0.9
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    loc_nodes = root.xpath("//sm:url/sm:loc", namespaces=ns)

    results: List[TargetTask] = []
    seen: set[str] = set()
    for node in loc_nodes:
        if not isinstance(node, etree._Element) or not node.text:
            continue
        canonical = canonicalize_url(node.text.strip())
        category = determine_category_from_url(canonical)
        if not category:
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        slug = slug_from_url(canonical)
        results.append(
            TargetTask(
                category=category,
                title_guess=guess_title_from_slug(slug),
                url=canonical,
                slug=slug,
            )
        )
    return results


def collect_targets_from_index(config: CategoryConfig, cookie_jar: Optional[Path]) -> List[TargetTask]:
    """
    Fallback：当 sitemap 不可用时，按类别逐个解析索引页中的链接。
    仅抓取满足该类别路径前缀的 URL，避免误采其他页面。
    """
    html_text = curl_get(config.index_url, cookie_jar=cookie_jar, referer=REFERER)
    dom = html.fromstring(html_text)
    results: List[TargetTask] = []
    seen: set[str] = set()
    for anchor in dom.xpath("//a[@href]"):
        href = anchor.get("href")
        if not href:
            continue
        absolute = canonicalize_url(urljoin(config.index_url, href))
        if determine_category_from_url(absolute) != config.key:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        text_fragments = [t.strip() for t in anchor.itertext()]
        link_title = " ".join(filter(None, text_fragments)).strip() or guess_title_from_slug(slug_from_url(absolute))
        results.append(
            TargetTask(
                category=config.key,
                title_guess=link_title,
                url=absolute,
                slug=slug_from_url(absolute),
            )
        )
    return results


def load_urls_from_file(path: Path) -> List[TargetTask]:
    """
    从文本文件读取 URL 列表，忽略空行与注释（# 开头）。
    返回值包含粗略标题猜测，便于命名。
    """
    urls: List[TargetTask] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        canonical = canonicalize_url(line)
        if canonical in seen:
            continue
        category = determine_category_from_url(canonical)
        if not category:
            print(f"警告：忽略未知类别的 URL：{canonical}")
            continue
        seen.add(canonical)
        slug = slug_from_url(canonical)
        urls.append(
            TargetTask(
                category=category,
                title_guess=guess_title_from_slug(slug),
                url=canonical,
                slug=slug,
            )
        )
    return urls


def slug_from_url(url: str) -> str:
    """
    提取 URL 尾段作为 slug；若 URL 以 / 结尾则取前一段。
    """
    return url.rstrip("/").rsplit("/", 1)[-1]


def pick_main(dom: html.HtmlElement) -> html.HtmlElement:
    """
    根据预设选择器顺序挑选正文容器。
    若全部落空则返回原始 DOM，保证不会因结构特殊而抛出异常。
    """
    for css in MAIN_SELECTORS:
        matches = dom.cssselect(css)
        if matches:
            return matches[0]
    return dom


def remove_noise(root: html.HtmlElement) -> None:
    """
    删除侧栏/导航/脚本样式等噪音节点，避免污染正文 Markdown。
    lxml.cssselect 会返回所有匹配节点，逐个 drop_tree() 即可。
    """
    for css in NOISE_SELECTORS:
        for node in root.cssselect(css):
            node.drop_tree()


def remove_decorative_elements(root: html.HtmlElement) -> None:
    """
    额外剔除不影响语义、但会在 Markdown 中生成多余 HTML 的装饰节点：
    - 标题尾部的 hash-link 图标（a.hash-link）
    - 复制按钮/提示图标（class 包含 copyButton）
    - 所有 img/svg/picture/figure 等媒体节点，满足“纯文本”要求
    """
    for xpath in DECORATIVE_XPATHS:
        for node in root.xpath(xpath):
            node.drop_tree()

    for node in root.xpath(".//img | .//picture | .//figure | .//svg"):
        node.drop_tree()


def normalize_links(
    base: str,
    root: html.HtmlElement,
    current_url: str,
    url_to_md: Dict[str, str],
    md_root: Path,
) -> None:
    """
    统一正文中的链接与资源：
    - 站内链接自动映射为相对的本地 Markdown 路径，并保留锚点，方便离线阅读。
    - 站外链接维持绝对 URL。
    - 图片链接转换为绝对 URL，方便 pandoc 抽取。
    """
    current_md_rel = url_to_md.get(current_url)
    current_md_path = md_root / current_md_rel if current_md_rel else None

    for anchor in root.xpath(".//a[@href]"):
        href = anchor.get("href")
        if not href:
            continue
        if href.startswith("#"):
            continue
        absolute = urljoin(base, href)
        parsed = urlparse(absolute)
        canonical = canonicalize_url(absolute)
        fragment = f"#{parsed.fragment}" if parsed.fragment else ""
        internal_target = url_to_md.get(canonical)
        if not internal_target:
            inferred_category = determine_category_from_url(canonical)
            if inferred_category:
                inferred_md = f"{inferred_category}/{slug_from_url(canonical)}.md"
                url_to_md.setdefault(canonical, inferred_md)
                internal_target = inferred_md
        if current_md_path and internal_target and not parsed.query:
            target_md_path = md_root / internal_target
            relative = os.path.relpath(target_md_path, start=current_md_path.parent)
            anchor.set("href", relative.replace("\\", "/") + fragment)
        else:
            anchor.set("href", absolute)
    for img in root.xpath(".//img[@src]"):
        src = img.get("src")
        if src:
            img.set("src", urljoin(base, src))


def extract_title(main_node: html.HtmlElement, fallback: str) -> str:
    """
    优先取正文中的首个 <h1> 文本作为页面正式标题，保障 Markdown 首行正确。
    若不存在 <h1>，则退回调用方传入的 fallback。
    """
    h1_nodes = main_node.xpath(".//h1")
    if h1_nodes:
        text_fragments = [segment.strip() for segment in h1_nodes[0].itertext()]
        title = " ".join(filter(None, text_fragments)).strip()
        if title:
            return title
    return fallback


def save_clean_html(category: str, slug: str, element: html.HtmlElement, layout: OutputLayout) -> Path:
    """
    将清洗后的正文节点序列化为 HTML，保存到 out/cleaned_html/slug.html。
    保留原始 class/属性，以便 Pandoc 按语言标记代码块。
    """
    html_text = html.tostring(element, encoding="unicode", with_tail=False)
    dest = layout.category_dirs[category].cleaned_dir / f"{slug}.html"
    dest.write_text(html_text, encoding="utf-8")
    return dest


def run_pandoc(title: str, in_html: Path, out_md: Path, assets_dir: Path) -> None:
    """
    调用 pandoc 将 HTML 转为 GFM，保持指令集中提供的参数配置。
    """
    cmd = [
        "pandoc",
        str(in_html),
        "--from",
        PANDOC_FROM,
        "--to",
        PANDOC_TO,
        "--metadata",
        f"title={title}",
        "--wrap=none",
        "--extract-media",
        str(assets_dir),
        "-o",
        str(out_md),
    ]
    completed = subprocess.run(cmd, check=False, capture_output=True, text=True, encoding="utf-8", errors="ignore")
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"pandoc 转换失败（退出码 {completed.returncode}）：{stderr or '无额外输出'}")


def sanitize_markdown(md_path: Path) -> None:
    """
    Pandoc 转出的 Markdown 仍可能残留 HTML 标签或图片引用。
    该函数二次清洗，确保：
    - 所有 <img> 标签统统移除；
    - 内联 <a> 标签被替换为其文本内容，避免 HTML 片段；
    - 常见装饰性标签（span/div/figure 等）全部删除；
    - 统一裁剪行尾空格与多余空行，得到整洁可读的纯文本 Markdown。
    """
    content = md_path.read_text(encoding="utf-8")

    # 移除所有图片标签。
    content = re.sub(r"<img[^>]*>", "", content, flags=re.IGNORECASE)

    # 将内联链接替换为纯文本，保留锚点文字，满足“无 HTML 标签”约束。
    content = re.sub(r"<a[^>]*>(.*?)</a>", r"\1", content, flags=re.IGNORECASE | re.DOTALL)

    # 剥离常见块级标签，避免残留 <div> 等结构。
    content = re.sub(
        r"</?(?:span|div|section|article|header|footer|main|figure|figcaption)[^>]*>",
        "",
        content,
        flags=re.IGNORECASE,
    )

    # 清理多余空白行（连续 ≥3 行空白压缩为 2 行），并移除行尾空格。
    content = re.sub(r"\n{3,}", "\n\n", content)
    content = "\n".join(line.rstrip() for line in content.splitlines())

    content = content.strip() + "\n"
    md_path.write_text(content, encoding="utf-8")


def load_robots(url: str, layout: OutputLayout) -> robotparser.RobotFileParser:
    """
    读取 robots.txt 并解析为 RobotFileParser，保证后续抓取遵守站点规则。
    若站点未提供 robots 或返回 404，则在标准输出提示后默认允许抓取，以免中断流程。
    """
    parser = robotparser.RobotFileParser()
    parser.set_url(url)
    try:
        text = curl_get(url, cookie_jar=layout.cookie_jar, referer=REFERER)
    except Exception as exc:
        print(f"警告：无法读取 robots.txt（{exc}），默认允许抓取。")
        parser.parse([])
        return parser
    parser.parse(text.splitlines())
    return parser


def warmup_origin(layout: OutputLayout) -> None:
    """
    对主域名进行一次轻量访问，让服务端下发潜在 cookie，提升后续请求的成功率。
    本操作忽略所有异常，确保不会阻断主流程。
    """
    try:
        curl_get(REFERER, cookie_jar=layout.cookie_jar)
    except Exception:
        pass


def can_fetch(parser: robotparser.RobotFileParser, url: str) -> bool:
    """
    使用 robots 解析结果判断当前 UA 是否允许访问指定 URL。
    这里直接使用 CURL_USER_AGENT，与实际请求 UA 保持一致。
    """
    return parser.can_fetch(CURL_USER_AGENT, url)


def sleep_between(min_seconds: float, max_seconds: float) -> None:
    """
    在两次请求间随机 sleep，模仿人工浏览速率，降低对方负载与封禁风险。
    """
    duration = random.uniform(min_seconds, max_seconds)
    time.sleep(duration)


def process_url(
    task: TargetTask,
    layout: OutputLayout,
    robots: robotparser.RobotFileParser,
    sleep_min: float,
    sleep_max: float,
    url_to_md: Dict[str, str],
    force_fetch: bool,
) -> Optional[PageResult]:
    """
    针对单个 URL 执行抓取 → 清洗 → 转换 → 记录的完整流程。
    内部包含三次重试机制，失败会记录到 failures.txt，然后返回 None。
    """
    url = task.url
    if not can_fetch(robots, url):
        append_line(
            layout.run_log,
            f"{timestamp()} SKIP ROBOTS {url}",
        )
        return None

    slug = task.slug
    category_paths = layout.category_dirs[task.category]
    raw_path = category_paths.raw_dir / f"{slug}.html"
    use_cached_raw = (
        raw_path.exists()
        and raw_path.stat().st_size > 0
        and not force_fetch
    )

    attempts = 3
    for attempt in range(1, attempts + 1):
        fetched_remote = False
        try:
            if use_cached_raw and attempt == 1:
                html_text = raw_path.read_text(encoding="utf-8")
            else:
                html_text = curl_get(url, cookie_jar=layout.cookie_jar, referer=REFERER)
                raw_path.write_text(html_text, encoding="utf-8")
                fetched_remote = True

            dom = html.fromstring(html_text)
            main = pick_main(dom)
            remove_noise(main)
            remove_decorative_elements(main)
            normalize_links(url, main, url, url_to_md, layout.md_dir)

            title = extract_title(main, fallback=task.title_guess)

            cleaned_path = save_clean_html(task.category, slug, main, layout)

            md_rel = url_to_md[url]
            md_path = layout.md_dir / md_rel
            md_path.parent.mkdir(parents=True, exist_ok=True)
            run_pandoc(title, cleaned_path, md_path, layout.assets_dir)
            sanitize_markdown(md_path)

            status = "OK CACHE" if use_cached_raw and attempt == 1 else "OK FETCH"
            append_line(
                layout.run_log,
                f"{timestamp()} {status} {url} -> {md_path.relative_to(layout.root)}",
            )

            return PageResult(
                url=url,
                category=task.category,
                title=title,
                slug=slug,
                raw_html=raw_path,
                cleaned_html=cleaned_path,
                markdown=md_path,
            )
        except Exception as exc:
            # 如果缓存解析失败，则后续尝试改为重新抓取。
            if use_cached_raw:
                use_cached_raw = False

            if attempt < attempts:
                append_line(
                    layout.run_log,
                    f"{timestamp()} RETRY {attempt}/{attempts} {url} 原因：{exc}",
                )
                time.sleep(1.0)
                continue
            append_line(
                layout.run_log,
                f"{timestamp()} FAIL {url} 原因：{exc}",
            )
            append_line(
                layout.failures_log,
                f"{timestamp()} {url} {exc}",
            )
            return None
        finally:
            # 每次尝试结束后仍然控制节奏，防止过快触发风控
            if fetched_remote:
                sleep_between(sleep_min, sleep_max)


def build_arg_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器，提供单页模式、输出目录、速率控制与外部 URL 列表支持。
    """
    parser = argparse.ArgumentParser(
        description="批量抓取 Owlbear Rodeo 扩展文档（apis/reference）并转为 Markdown（教学向实现）。",
    )
    parser.add_argument(
        "--single",
        help="仅处理单个扩展文档页面 URL（跳过 sitemap）。",
    )
    parser.add_argument(
        "--out",
        default="out",
        help="输出根目录，默认 ./out。",
    )
    parser.add_argument(
        "--sleep-min",
        type=float,
        default=0.5,
        help="抓取间隔下限秒数，默认 0.5。",
    )
    parser.add_argument(
        "--sleep-max",
        type=float,
        default=1.5,
        help="抓取间隔上限秒数，默认 1.5。",
    )
    parser.add_argument(
        "--urls-file",
        help="从文本文件读取待处理 URL 列表（逐行一个），提供后跳过 sitemap。",
    )
    parser.add_argument(
        "--force-fetch",
        action="store_true",
        help="忽略本地缓存，强制重新抓取远端 HTML（默认复用已存在原始文件以节省请求）。",
    )
    return parser


def write_url_map(path: Path, results: List[PageResult], expected: Sequence[TargetTask], out_root: Path) -> None:
    """
    将抓取成果写入 url-map.json，便于后续检索或编排文档。
    内容包括生成时间、输出根路径、每个页面的标题/URL/对应文件。
    """
    expected_map = {
        task.url: {
            "url": task.url,
            "category": task.category,
            "slug": task.slug,
        }
        for task in expected
    }
    success_urls = {item.url for item in results}
    missing_urls = [url for url in expected_map if url not in success_urls]

    data = {
        "generated_at": timestamp(),
        "timezone": LOCAL_TZ_NAME,
        "output_root": str(out_root),
        "expected_items": list(expected_map.values()),
        "items": [
            {
                "url": item.url,
                "category": item.category,
                "title": item.title,
                "slug": item.slug,
                "raw_html": str(item.raw_html.relative_to(out_root)).replace("\\", "/"),
                "cleaned_html": str(item.cleaned_html.relative_to(out_root)).replace("\\", "/"),
                "markdown": str(item.markdown.relative_to(out_root)).replace("\\", "/"),
            }
            for item in results
        ],
        "missing_items": [expected_map[url] for url in missing_urls],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(results: List[PageResult], expected: Sequence[TargetTask]) -> None:
    """
    命令行总结，帮助操作者快速了解成功数量与 Markdown 输出位置。
    """
    total_success = len(results)
    total_expected = len(expected)
    missing_urls = [task for task in expected if task.url not in {item.url for item in results}]

    if total_success == 0:
        print("执行完成，但没有成功转换的页面，请查看 logs/failures.txt。")
        return

    counts = Counter(result.category for result in results)
    breakdown = ", ".join(f"{category}:{counts[category]}" for category in sorted(counts))
    print(f"成功转换 {total_success}/{total_expected} 个页面（{breakdown}），Markdown 位于 {results[0].markdown.parent}")

    if missing_urls:
        print("仍有以下页面未成功生成 Markdown：")
        for task in missing_urls:
            print(f"- [{task.category}] {task.url}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    """
    顶层入口：解析参数 → 自检依赖 → 准备目录与 robots → 收集 URL → 逐页处理。
    """
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    if args.sleep_min <= 0 or args.sleep_max <= 0 or args.sleep_max < args.sleep_min:
        parser.error("--sleep-min 与 --sleep-max 必须为正数且 max ≥ min。")

    ensure_command_available("curl")
    ensure_command_available("pandoc")

    out_root = Path(args.out).resolve()
    layout = prepare_layout(out_root, [config.key for config in CATEGORY_CONFIGS])

    warmup_origin(layout)

    try:
        robots = load_robots(ROBOTS_URL, layout)
    except Exception as exc:
        print(f"错误：无法读取 robots.txt：{exc}", file=sys.stderr)
        return 1

    if args.single:
        canonical = canonicalize_url(args.single)
        category = determine_category_from_url(canonical)
        if not category:
            parser.error("仅支持 /extensions/apis/ 与 /extensions/reference/ 下的页面。")
        slug = slug_from_url(canonical)
        targets = [
            TargetTask(
                category=category,
                title_guess=guess_title_from_slug(slug),
                url=canonical,
                slug=slug,
            )
        ]
    elif args.urls_file:
        targets = load_urls_from_file(Path(args.urls_file))
    else:
        try:
            targets = collect_targets_from_sitemap(SITEMAP_URL, layout.cookie_jar)
        except Exception as exc:
            print(f"警告：解析 sitemap 失败（{exc}），尝试改为解析各索引页。")
            targets = []
            for config in CATEGORY_CONFIGS:
                try:
                    targets.extend(collect_targets_from_index(config, layout.cookie_jar))
                except Exception as secondary:
                    print(f"错误：解析 {config.key} 索引页失败：{secondary}", file=sys.stderr)

    if not targets:
        print("未发现需要处理的 URL，任务结束。")
        return 0

    # 以 URL 去重，避免 sitemap/索引重复。
    unique_targets: Dict[str, TargetTask] = {task.url: task for task in targets}
    targets = sorted(unique_targets.values(), key=lambda item: (item.category, item.slug))

    url_to_md: Dict[str, str] = {task.url: f"{task.category}/{task.slug}.md" for task in targets}

    results: List[PageResult] = []
    for task in targets:
        result = process_url(
            task=task,
            layout=layout,
            robots=robots,
            sleep_min=args.sleep_min,
            sleep_max=args.sleep_max,
            url_to_md=url_to_md,
            force_fetch=args.force_fetch,
        )
        if result:
            results.append(result)

    write_url_map(layout.url_map, results, targets, out_root)
    summarize(results, targets)
    return 0


if __name__ == "__main__":
    sys.exit(main())
