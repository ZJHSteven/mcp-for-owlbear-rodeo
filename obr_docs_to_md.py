#!/usr/bin/env python3
"""
教学向脚本：批量把 https://docs.owlbear.rodeo/extensions/apis/ 下的 API 文档页面
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
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlparse
import urllib.robotparser as robotparser

from lxml import etree, html

# ────────────────────────────── 常量配置 ──────────────────────────────

CURL_USER_AGENT = "curl/8.7.1"  # 按需求使用 curl/8.x UA，模拟正常命令行抓取
REFERER = "https://docs.owlbear.rodeo/"
SITEMAP_URL = "https://docs.owlbear.rodeo/sitemap.xml"
ROBOTS_URL = "https://docs.owlbear.rodeo/robots.txt"
APIS_INDEX_URL = "https://docs.owlbear.rodeo/extensions/apis/"

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


# ────────────────────────────── 数据结构 ──────────────────────────────

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


@dataclass
class PageResult:
    """
    用于写入 url-map.json 的结构化结果，便于后续复用。
    """

    url: str
    title: str
    slug: str
    raw_html: Path
    cleaned_html: Path
    markdown: Path


# ────────────────────────────── 工具函数 ──────────────────────────────

def ensure_command_available(cmd_name: str) -> None:
    """
    自检依赖：确认系统已安装指定命令。
    若缺失则打印中文提示并退出，避免脚本继续执行导致混乱。
    """
    if shutil.which(cmd_name) is None:
        print(f"错误：未检测到必需命令 `{cmd_name}`。请先安装后再运行。", file=sys.stderr)
        sys.exit(1)


def prepare_layout(out_root: Path) -> OutputLayout:
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
    )


def timestamp() -> str:
    """
    返回 ISO8601 UTC 时间戳，用于日志与 url-map，便于审计。
    """
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def append_line(path: Path, line: str) -> None:
    """
    通用日志写入工具：逐行追加，并确保父目录存在。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def curl_get(
    url: str,
    headers: Dict[str, str],
    cookie_jar: Optional[Path],
    timeout: int = 60,
) -> str:
    """
    使用 curl 通过子进程抓取页面。
    关键参数说明：
    - --compressed：支持 Brotli/Gzip 压缩，节省流量。
    - --fail：HTTP 40x/50x 会返回非零退出码，便于统一错误处理。
    - -b/-c：当提供 cookie_jar 时读写同一文件，实现跨请求复用。
    """
    cmd: List[str] = [
        "curl",
        "-sS",
        "-L",
        "--compressed",
        "--fail",
        "--max-time",
        str(timeout),
        "-A",
        CURL_USER_AGENT,
        "-e",
        REFERER,
    ]

    if cookie_jar is not None:
        cmd.extend(["-b", str(cookie_jar), "-c", str(cookie_jar)])

    for key, value in headers.items():
        cmd.extend(["-H", f"{key}: {value}"])

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

    return completed.stdout


def collect_api_links_from_sitemap(sitemap_url: str, cookie_jar: Optional[Path]) -> List[Tuple[str, str]]:
    """
    从 sitemap.xml 中筛选出 /extensions/apis/ 下的页面列表。
    由于 sitemap 格式稳定，解析比解析导航页更可靠。
    """
    headers = dict(CURL_HEADERS)
    headers["Accept"] = "application/xml,text/xml;q=0.9,*/*;q=0.8"
    xml_text = curl_get(sitemap_url, headers, cookie_jar)
    root = etree.fromstring(xml_text.encode("utf-8"))

    # sitemap 通常使用默认命名空间：http://www.sitemaps.org/schemas/sitemap/0.9
    ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    loc_nodes = root.xpath("//sm:url/sm:loc", namespaces=ns)

    results: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for node in loc_nodes:
        if not isinstance(node, etree._Element) or not node.text:
            continue
        url = node.text.strip()
        if "/extensions/apis/" not in url:
            continue
        url = url.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        slug = url.rsplit("/", 1)[-1]
        # 预估标题：将 slug 中的连接符转换为空格后 Title Case，为后续查重提供友好初值。
        guess_title = re.sub(r"[-_]+", " ", slug).title()
        results.append((guess_title, url))
    return results


def collect_api_links_from_index(index_url: str, cookie_jar: Optional[Path]) -> List[Tuple[str, str]]:
    """
    Fallback：当 sitemap 被拒绝或暂不可用时，从 API 索引页解析链接。
    仍然遵守只抓取 /extensions/apis/ 下的文档，避免外溢。
    """
    html_text = curl_get(index_url, CURL_HEADERS, cookie_jar)
    dom = html.fromstring(html_text)
    results: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for anchor in dom.xpath("//a[@href]"):
        href = anchor.get("href")
        if not href:
            continue
        absolute = urljoin(index_url, href).rstrip("/")
        if "/extensions/apis/" not in absolute:
            continue
        if absolute in seen:
            continue
        seen.add(absolute)
        text_fragments = [t.strip() for t in anchor.itertext()]
        link_title = " ".join(filter(None, text_fragments)).strip() or re.sub(r"[-_]+", " ", slug_from_url(absolute)).title()
        results.append((link_title, absolute))
    return results


def load_urls_from_file(path: Path) -> List[Tuple[str, str]]:
    """
    从文本文件读取 URL 列表，忽略空行与注释（# 开头）。
    返回值包含粗略标题猜测，便于命名。
    """
    urls: List[Tuple[str, str]] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        url = line.rstrip("/")
        if url in seen:
            continue
        seen.add(url)
        slug = url.rsplit("/", 1)[-1]
        guess_title = re.sub(r"[-_]+", " ", slug).title()
        urls.append((guess_title, url))
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


def normalize_links(base: str, root: html.HtmlElement) -> None:
    """
    将 href/src 等资源链接统一转换为绝对 URL，确保 Markdown 中引用可直接访问。
    """
    for anchor in root.xpath(".//a[@href]"):
        href = anchor.get("href")
        if href:
            anchor.set("href", urljoin(base, href))
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


def file_safe_name(name: str) -> str:
    """
    将标题转换为文件系统安全的名称：
    - 替换非字母数字为下划线
    - 去掉收尾的下划线，确保不会生成空文件名
    """
    safe = re.sub(r"[^0-9A-Za-z._-]+", "_", name).strip("_")
    return safe or "index"


def unique_path(base: Path) -> Path:
    """
    若目标路径已存在，则追加数字后缀避免覆盖：
    例如 foo.md -> foo_1.md -> foo_2.md ...
    """
    if not base.exists():
        return base
    stem = base.stem
    suffix = base.suffix
    counter = 1
    while True:
        candidate = base.with_name(f"{stem}_{counter}{suffix}")
        if not candidate.exists():
            return candidate
        counter += 1


def save_clean_html(title: str, slug: str, element: html.HtmlElement, layout: OutputLayout) -> Path:
    """
    将清洗后的正文节点序列化为 HTML，保存到 out/cleaned_html/slug.html。
    保留原始 class/属性，以便 Pandoc 按语言标记代码块。
    """
    html_text = html.tostring(element, encoding="unicode", with_tail=False)
    dest = layout.cleaned_dir / f"{slug}.html"
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


def load_robots(url: str, layout: OutputLayout) -> robotparser.RobotFileParser:
    """
    读取 robots.txt 并解析为 RobotFileParser，保证后续抓取遵守站点规则。
    若站点未提供 robots 或返回 404，则在标准输出提示后默认允许抓取，以免中断流程。
    """
    parser = robotparser.RobotFileParser()
    parser.set_url(url)
    try:
        text = curl_get(url, CURL_HEADERS, layout.cookie_jar)
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
        curl_get(REFERER, CURL_HEADERS, layout.cookie_jar)
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
    title_guess: str,
    url: str,
    layout: OutputLayout,
    headers: Dict[str, str],
    robots: robotparser.RobotFileParser,
    sleep_min: float,
    sleep_max: float,
) -> Optional[PageResult]:
    """
    针对单个 URL 执行抓取 → 清洗 → 转换 → 记录的完整流程。
    内部包含三次重试机制，失败会记录到 failures.txt，然后返回 None。
    """
    if not can_fetch(robots, url):
        append_line(
            layout.run_log,
            f"{timestamp()} SKIP ROBOTS {url}",
        )
        return None

    slug = slug_from_url(url)
    attempts = 3
    for attempt in range(1, attempts + 1):
        try:
            html_text = curl_get(url, headers, layout.cookie_jar)

            raw_path = layout.raw_dir / f"{slug}.html"
            raw_path.write_text(html_text, encoding="utf-8")

            dom = html.fromstring(html_text)
            main = pick_main(dom)
            remove_noise(main)
            normalize_links(url, main)

            title = extract_title(main, fallback=title_guess)
            safe_title = file_safe_name(title)

            cleaned_path = save_clean_html(title, slug, main, layout)

            md_target = layout.md_dir / f"{safe_title}.md"
            md_path = unique_path(md_target)
            run_pandoc(title, cleaned_path, md_path, layout.assets_dir)

            append_line(
                layout.run_log,
                f"{timestamp()} OK {url} -> {md_path.relative_to(layout.root)}",
            )

            return PageResult(
                url=url,
                title=title,
                slug=slug,
                raw_html=raw_path,
                cleaned_html=cleaned_path,
                markdown=md_path,
            )
        except Exception as exc:
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
            sleep_between(sleep_min, sleep_max)


def build_arg_parser() -> argparse.ArgumentParser:
    """
    构建命令行参数解析器，提供单页模式、输出目录、速率控制与外部 URL 列表支持。
    """
    parser = argparse.ArgumentParser(
        description="批量抓取 Owlbear Rodeo 扩展 API 文档并转为 Markdown（教学向实现）。",
    )
    parser.add_argument(
        "--single",
        help="仅处理单个 API 页面 URL（跳过 sitemap）。",
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
    return parser


def write_url_map(path: Path, results: List[PageResult], out_root: Path) -> None:
    """
    将抓取成果写入 url-map.json，便于后续检索或编排文档。
    内容包括生成时间、输出根路径、每个页面的标题/URL/对应文件。
    """
    data = {
        "generated_at": timestamp(),
        "output_root": str(out_root),
        "items": [
            {
                "url": item.url,
                "title": item.title,
                "slug": item.slug,
                "raw_html": str(item.raw_html.relative_to(out_root)),
                "cleaned_html": str(item.cleaned_html.relative_to(out_root)),
                "markdown": str(item.markdown.relative_to(out_root)),
            }
            for item in results
        ],
    }
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def summarize(results: List[PageResult]) -> None:
    """
    命令行总结，帮助操作者快速了解成功数量与 Markdown 输出位置。
    """
    total = len(results)
    if total == 0:
        print("执行完成，但没有成功转换的页面，请查看 logs/failures.txt。")
    else:
        print(f"成功转换 {total} 个页面，Markdown 位于 {results[0].markdown.parent}")


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
    layout = prepare_layout(out_root)

    warmup_origin(layout)

    try:
        robots = load_robots(ROBOTS_URL, layout)
    except Exception as exc:
        print(f"错误：无法读取 robots.txt：{exc}", file=sys.stderr)
        return 1

    if args.single:
        targets = [(re.sub(r"[-_]+", " ", slug_from_url(args.single)).title(), args.single.rstrip("/"))]
    elif args.urls_file:
        targets = load_urls_from_file(Path(args.urls_file))
    else:
        try:
            targets = collect_api_links_from_sitemap(SITEMAP_URL, layout.cookie_jar)
        except Exception as exc:
            print(f"警告：解析 sitemap 失败（{exc}），尝试改为解析索引页。")
            try:
                targets = collect_api_links_from_index(APIS_INDEX_URL, layout.cookie_jar)
            except Exception as secondary:
                print(f"错误：解析索引页失败：{secondary}", file=sys.stderr)
                return 1

    if not targets:
        print("未发现需要处理的 URL，任务结束。")
        return 0

    results: List[PageResult] = []
    for guess_title, url in targets:
        result = process_url(
            title_guess=guess_title,
            url=url,
            layout=layout,
            headers=CURL_HEADERS,
            robots=robots,
            sleep_min=args.sleep_min,
            sleep_max=args.sleep_max,
        )
        if result:
            results.append(result)

    write_url_map(layout.url_map, results, out_root)
    summarize(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
