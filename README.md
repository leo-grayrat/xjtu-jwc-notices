# xjtu-jwc-notices

西安交通大学教务处“教学通知”本地归档与只读 MCP 服务。采集器每天低频、顺序地
读取 `https://jwc.xjtu.edu.cn/jxxx/jxtz2.htm` 及其分页，把 2026 年以来的通知
网页正文保存到 SQLite。AI 查询只访问本地数据库，不会在 MCP 调用期间访问教务处。

## 安装

需要 Windows 和 Python 3.12 或更高版本。推荐使用 [uv](https://docs.astral.sh/uv/)：

```powershell
uv sync --python 3.12 --extra dev
```

安装依赖本身不会创建计划任务，也不会自动访问网站。

## 同步与维护

首次同步会从 2026-01-01 开始建立本地库；任务可中断并在下次继续：

```powershell
uv run xjtu-notices sync
uv run xjtu-notices status
uv run xjtu-notices check
```

默认数据目录为 `%LOCALAPPDATA%\xjtu-jwc-notices`，包含 `notices.db` 和
`cookies.json`。可在命令前加 `--data-dir D:\somewhere` 覆盖；也可以设置
`XJTU_JWC_NOTICES_DATA_DIR` 环境变量（MCP 服务读取自定义归档时尤其适用）。

安装每天 08:00 运行的当前用户 Windows 任务：

```powershell
uv run xjtu-notices schedule install
uv run xjtu-notices schedule status
uv run xjtu-notices schedule uninstall
```

其他时间可用 `schedule install --time 21:30`。安装命令会替换同名任务
`XJTU JWC Notices Sync`，并把安装时选定的数据目录写入任务命令，因此
`--data-dir` 不会在计划任务中丢失。

## MCP

如果仓库位于 `<仓库绝对路径>`，可直接使用其中的虚拟环境入口：

```powershell
<仓库绝对路径>\.venv\Scripts\xjtu-notices-mcp.exe
```

通用 MCP 客户端配置示例：

```json
{
  "mcpServers": {
    "xjtu-jwc-notices": {
      "command": "uv",
      "args": [
        "--directory",
        "<仓库绝对路径>",
        "run",
        "xjtu-notices-mcp"
      ]
    }
  }
}
```

提供四个只读工具：

- `search_notices`：按关键词、日期、分类搜索或列出最新通知；
- `get_notice`：读取一条通知的完整网页正文；
- `list_categories`：列出分类、数量和日期范围；
- `get_sync_status`：检查本地数据量和最近同步结果。

## 边界与排错

- 只抓取教学通知列表下的网页正文；不下载附件，不解析图片，也不做 OCR。
- 网站验证结构发生变化时同步会明确失败，不会把验证页写入数据库。
- `status` 显示最近同步是否成功；`check` 检查 SQLite 与全文索引一致性。
- 单条正文为空或抓取失败时会在以后重试；已有成功正文不会被空响应覆盖。
- 若任务计划运行失败，先在终端执行 `uv run xjtu-notices sync` 查看具体错误。
