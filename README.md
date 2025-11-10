# Owlbear Rodeo 扩展文档工具集

> 现包含两个核心模块：
> 1. `mcp-docs-server`：基于 uv 发布的 MCP Python 服务端，可直接通过 `uvx mcp-docs-server` 暴露 Owlbear Rodeo 文档资源（搜索 + 打开全文）。
> 2. `obr_docs_to_md.py`：抓取官网扩展文档、生成 Markdown 的离线脚本，仍用于保持 `docs/markdown` 最新。

## MCP 文档服务器快速上手

1. **安装依赖**
   ```bash
   uv sync
   ```
2. **查看帮助**
   ```bash
   uv run mcp-docs-server --help
   ```
3. **最小可运行示例**
   ```bash
   uvx mcp-for-owlbear-rodeo mcp-docs-server --transport stdio
   ```
   - 默认从 `docs/markdown` 自动加载所有 Markdown 文件，每个文件即一个 MCP 资源 (`doc://owlbear/<category>/<slug>`)。
   - 自动注册两个工具：
     - `search_docs(query, top_k=5)`：返回资源链接列表。
     - `open_doc(name)`：返回完整 Markdown 内容（与资源 URI 对齐）。
   - 资源描述 (`description`) 直接取自文档首段正文，无人工编写，满足“不要瞎编”约束。

4. **接入自定义文档目录**
   ```bash
   uv run mcp-docs-server --docs-path D:/cache/markdown
   ```
   或通过环境变量：
   ```bash
   set MCP_DOCS_ROOT=D:/cache/markdown
   uvx mcp-docs-server
   ```

5. **在其他 MCP 客户端中测试**
   - 以 Claude MCP Tool 为例，配置 `command`: `"uvx"`, `args`: `["mcp-for-owlbear-rodeo", "mcp-docs-server"]`。
   - 客户端会自动发现全部 `doc://owlbear/...` 资源，并可调用 `search_docs`/`open_doc`。

> ⚠️默认打包会随 wheel 附带 `docs/markdown`，无需联网即可开箱即用；若要更新内容，请运行下方的抓取脚本刷新 Markdown 再重新构建。

## Owlbear Rodeo 扩展文档抓取工具使用说明

> 脚本入口：`obr_docs_to_md.py`  
> 目标：批量抓取 https://docs.owlbear.rodeo/extensions/ 下的 API 及 Reference 文档，转换为纯文本 Markdown，方便后续切分与注入 MCP。

## 环境准备

1. **Python**：推荐 Python 3.9 及以上版本。  
2. **命令行依赖**  
   - `curl`：用于抓取 HTML。  
   - `pandoc`：将清洗后的 HTML 转换为 GitHub Flavored Markdown。  
3. **Python 包**  
   - `lxml`（标准库以外）  
   - `cssselect`（本仓库近期新增，务必安装）  
   安装示例：
   ```bash
   python -m pip install lxml cssselect
   ```

> 小贴士：在 Windows 上使用本脚本时，建议通过 Git Bash 或 PowerShell 运行；确保 `curl` 与 `pandoc` 已加入 `PATH`。

## 输出结构总览

默认输出位于 `./out`，脚本会自动创建并复用目录：

```
out/
  raw_html/       # 原始抓取的 HTML（按 apis/reference 分类）
  cleaned_html/   # 清洗后的 HTML，供 Pandoc 转换
  md/             # 最终 Markdown，纯文本无 HTML 标签
  assets/         # Pandoc 提取出的媒体文件（当前已全部剔除，不再使用）
  logs/
    run.log       # 逐条处理日志（北京时间，含缓存/抓取标记）
    failures.txt  # 失败记录（包含最终错误信息，便于重试）
  url-map.json    # 成功/缺失页面概览 + 元数据
```

`url-map.json` 结构示例：

```json
{
  "generated_at": "2025-10-19T02:24:42+08:00",
  "timezone": "UTC+08:00",
  "output_root": ".../out",
  "expected_items": [
    {"url": "...", "category": "apis", "slug": "action"}
  ],
  "items": [
    {
      "url": "...",
      "category": "apis",
      "title": "Action",
      "slug": "action",
      "raw_html": "raw_html/apis/action.html",
      "cleaned_html": "cleaned_html/apis/action.html",
      "markdown": "md/apis/action.md"
    }
  ],
  "missing_items": []
}
```

> `missing_items` 非空时，请查看 `logs/failures.txt` 并考虑使用 `--force-fetch` 重试。

## 常用命令

### 1. 全量抓取（推荐初次执行）

```bash
python obr_docs_to_md.py
```

- 自动解析 `sitemap.xml`（优先）/ 各分类索引页，收集 `/extensions/apis/` 与 `/extensions/reference/` 全部页面。  
- 默认输出位置：`./out`。可通过 `--out` 自定义目录。

执行结束后，终端会输出如下概览（示例）：

```
成功转换 42/45 个页面（apis:30, reference:12），Markdown 位于 D:\...\out\md\apis
仍有以下页面未成功生成 Markdown：
- [reference] https://docs.owlbear.rodeo/extensions/reference/foo
- [reference] https://docs.owlbear.rodeo/extensions/reference/bar
```

### 2. 单页调试

```bash
python obr_docs_to_md.py --single https://docs.owlbear.rodeo/extensions/reference/manifest
```

- 仅处理指定 URL，适用于调试清洗规则。  
- 同样会更新 `url-map.json`，记录当前运行期望/缺失项。

### 3. 复用缓存 / 强制刷新

- **默认行为**：若 `out/raw_html/<category>/<slug>.html` 存在且非空，脚本直接复用，避免重复请求。  
- **强制刷新**：添加 `--force-fetch` 即可忽略缓存从远端重新抓取。  
  ```bash
  python obr_docs_to_md.py --force-fetch
  ```

### 4. 其他常用参数

| 参数 | 说明 | 默认值 |
| --- | --- | --- |
| `--out PATH` | 指定输出根目录 | `out` |
| `--sleep-min` | 连续请求的最小间隔（秒） | `0.5` |
| `--sleep-max` | 连续请求的最大间隔（秒） | `1.5` |
| `--urls-file FILE` | 从自定义列表读取 URL（每行一个） | 无 |

> 建议保持合理的间隔，避免触发远端限流。即使启用缓存，解析失败也会在下一次尝试自动重新抓取。

## 运行后如何自检

1. **命令行输出**：优先关注终端概览，确认成功/缺失数量。  
2. **`logs/failures.txt`**：若有失败条目，逐条定位原因（网络异常、Cloudflare 质询、Pandoc 转换失败等）。  
3. **`out/url-map.json`**：快速查询生成的 Markdown 路径及未覆盖页面，可供后续脚本读取。  
4. **Markdown 纯文本校验**：脚本已移除所有 `<img>/<a>` 等 HTML 标签，仅保留 Markdown 语法。可配合 `rg "<"` 检查是否仍有漏网标签。

## 常见问题

- **Cloudflare 验证导致 403**：脚本已内置通用 UA 与重试机制，但若仍无法通过，可适当增加 `--sleep-min/--sleep-max` 间隔或手动复制 HTML 至 `raw_html` 后重跑清洗。  
- **本地没有安装 Pandoc**：请从 [https://pandoc.org/installing.html](https://pandoc.org/installing.html) 下载对应平台版本，并添加到 `PATH`。  
- **输出时间看不懂**：所有日志、`url-map.json` 都使用北京时间（UTC+08:00），便于与本地排查时区一致。

## 后续扩展建议

- 在 CI 中配置定时任务，结合 `--force-fetch` 每日刷新文档。  
- 依据 `url-map.json` 中的 `expected_items` / `missing_items` 生成告警报告，确保 MCP 数据源随站点更新。  
- 如需合并 Markdown，可编写额外脚本，根据 `category` 字段聚合生成章节化文档。

> 如果后续需要新增其它目录（例如 `/extensions/tutorials/`），可以参照 `CATEGORY_CONFIGS` 增加配置项并复用现有流程。
