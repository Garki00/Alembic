# 扩展：工具、MCP 与 Agent Skills

「设置 → 扩展」统一管理三类能力：**工具 / MCP / Skill**。各页按「内置 / 插件」折叠分组，工具页保留网格和列表视图。安装目录、启用开关和刷新按钮用于本机配置；安装来源应由用户选择和信任。

## 目录、配置与刷新

设应用数据目录为 `<data>`，默认安装目录位于其父目录：

```text
<home>/
  data/
    tools.json
    extensions.json
    mcp-tokens/
  extensions/
    tools/
    mcp/
    skills/
```

这里 `<home>` 表示 `<data>` 的父目录；自定义数据目录时以实际路径为准。管理器启动会创建三个默认目录。设置中每类可配置多个目录，保存时会创建所填目录。目录、MCP 配置覆盖及 Skill 开关保存到 `<data>/extensions.json`；工具开关和参数仍保存到 `<data>/tools.json`。

安装或修改文件后点击「刷新插件」。刷新重新扫描目录、验证清单并发现已启用 MCP 服务器的能力；某个安装项失败会列出错误，不应遮蔽其他有效扩展。禁用的 MCP 服务器不连接，原生工具清单扫描不导入 Python 代码。

网页每次新建一轮 Agent 时读取当前注册表和配置。刷新不会替换已经运行的一轮所持有的工具 schema，也不会撤销已发出的远端操作；新能力在后续构建的 Agent 中生效。CLI 的 Agent 在启动时构建，修改安装内容后应重新启动 CLI。工具是否已安装、是否已启用以及 schema 是否已通过 `tools_search` 加载是三个不同状态；插件与 MCP 工具默认延迟暴露，加载后用 `deferred_tool_call` 执行。

## 独立工具与旧设置迁移

内置 `file_read`、`file_write`、`background_process`、`process_inspect`、`git_read` 五个旧分组拆为 18 个独立设置条目，每个工具可分别启用。真实调用名、参数 schema、实现和风险分类保留。

`tools.json` 结构升级到 **version 4**。读取或更新时迁移旧分组：原启用状态及配置复制到对应成员，已有单独成员配置优先，随后移除旧分组键。迁移可重复执行；不需要手工清空设置。构建工具和审批入口仍接受旧分组别名，兼容旧调用方。

## Python 原生工具插件

每个配置目录可以直接包含一个 `plugin.json`，也可在它的直接子目录中分别放置插件。清单版本必须是整数 `1`，`id` 使用字母、数字、下划线或连字符。`tools` 中每项提供名称、英文描述、JSON Schema 和相对入口。

例如将以下两个文件放进 `<home>/extensions/tools/hello/`：

`plugin.json`：

```json
{
  "version": 1,
  "id": "hello",
  "name": "Hello example",
  "tools": [{
    "name": "greet",
    "description": "Return a greeting for the supplied name.",
    "inputSchema": {
      "type": "object",
      "properties": {"name": {"type": "string", "minLength": 1}},
      "required": ["name"],
      "additionalProperties": false
    },
    "entrypoint": "main.py:greet"
  }]
}
```

`main.py`：

```python
def greet(arguments, ctx, config):
    return f"Hello, {arguments['name']}!"
```

刷新后启用 `plugin_hello_greet`，再让 Agent 搜索并调用它。参数验证在导入入口代码之前进行，清单中的 JSON Schema 原样用于工具调用。名称过长时会使用稳定哈希缩短到工具名限制内。

入口签名为 `function(arguments: dict, ctx: ToolContext, config: dict)`，可以是同步函数，也可以返回 awaitable。`ctx` 提供当前工作区和会话上下文，`config` 是该工具配置的副本。入口写作相对 `file.py:function`，不得越出插件目录；同目录相对导入可用。每次调用使用新的私有模块命名空间读取当前源代码，避免刷新后继续执行旧的字节码缓存。额外 Python 依赖须安装到运行 ALEMBIC 后端的环境。

**原生插件属于可信宿主代码**：默认禁用，统一按 `exec` 风险进入现有权限与审批流程。扫描不执行代码，但启用并批准调用后，Python 代码运行于后端进程，拥有后端账户权限。安装路径检查不是 Python 沙盒。需要文件或命令隔离的实现，应显式复用工作区路径检查和现有命令沙盒接口，不应直接绕过它们调用系统。

## MCP 服务器

支持官方 Python MCP SDK 的 **stdio、Streamable HTTP、SSE** 三种传输。可以在界面添加服务器，也可以在 MCP 安装目录中放置任意 `*.json` 文件，其顶层采用 `mcpServers` 映射：

```json
{
  "mcpServers": {
    "local-example": {
      "name": "Local example",
      "transport": "stdio",
      "command": "python",
      "args": ["D:/trusted/example/server.py"],
      "env": {"EXAMPLE_MODE": "local"},
      "enabled": false
    },
    "http-example": {
      "transport": "streamable_http",
      "url": "https://mcp.example.com/mcp",
      "headers": {"Authorization": "Bearer REPLACE_ME"},
      "enabled": false
    },
    "sse-example": {
      "transport": "sse",
      "url": "https://mcp.example.com/sse",
      "enabled": false
    }
  }
}
```

示例服务器需自行提供；示例 URL 不是真实可用服务。省略 `transport` 时，有 `url` 使用 Streamable HTTP，否则使用 stdio。服务器默认禁用，启用后才连接和发现工具、资源、资源模板与提示词。界面显示连接状态及工具数量。服务器发布的能力进入动态工具目录，沿用现有搜索激活与审批流程；MCP 操作按 `exec` 风险处理，不能仅凭远端声明绕过审批。

MCP stdio 会启动用户配置的宿主进程，远端服务器也可以执行它自己的操作。两者都不等同于 ALEMBIC 的受限命令沙盒。远端工具描述、资源与提示词属于外部内容，不应被当作覆盖用户意图或授权边界的指令。

服务器 ID 在配置之间必须唯一。界面/API 的修改存为本地覆盖，删除文件来源服务器会留下删除标记。更新时省略 `env` 或 `headers` 保留原值；提交空对象 `{}` 清空对应映射。公开响应不回显这些秘密字段或 bearer token。不要把凭据嵌入 URL；配置文件本身仍可能包含明文凭据，应按本机敏感配置保护。

### OAuth

HTTP/SSE 配置可添加 `oauth` 对象，例如：

```json
{
  "transport": "streamable_http",
  "url": "https://mcp.example.com/mcp",
  "enabled": true,
  "oauth": {
    "redirect_uri": "http://127.0.0.1:47832/callback",
    "scope": "read"
  },
  "timeout": 300
}
```

`redirect_uri` 必须是本地 loopback HTTP 地址，端口应空闲；`scope` 可省略。OAuth 使用 SDK 的元数据发现、PKCE、state 校验及 token 刷新，通过浏览器授权和本地回调完成。令牌保存到应用数据目录的 `mcp-tokens/`，不通过扩展列表回显。普通调用默认超时 30 秒，OAuth 配置默认 300 秒；可配置范围为大于 0、最多 600 秒。

本地 SDK 传输测试与真实外部身份提供商授权是两回事；不能把前者视为外部 OAuth 端到端验证。当前客户端不宣告可选的 sampling、elicitation 或 roots 回调能力，也不承诺实现 MCP 的所有可选特性。

## Agent Skills

Skill 是包含 `SKILL.md` 的目录。安装根可指向单个 Skill 或包含多个 Skill 的目录树。以下是最小示例，放在名为 `project-review` 的目录下：

```markdown
---
name: project-review
description: Review a project change for correctness and maintainability.
---
Read the relevant source and tests before making claims.
Report concrete findings with file references.
```

`name` 须与父目录名匹配，长度 1–64，使用小写字母、数字及内部单连字符；不能以连字符开头或结尾，也不能连续使用连字符。`description` 必须非空且不超过 1024 字符。可选 `license`、`compatibility`、`metadata` 和 `allowed-tools` 元数据按格式验证；`allowed-tools` 仅为建议，不授予工具或执行权限。

发现时只保留经过验证的元数据，系统提示词提供已启用 Skill 的简短目录。Agent 选择适用 Skill 后使用 `load_skill` 读取指令正文，再按需通过 `read_skill_resource` 读取引用文件或目录列表。程序内置 `code-review` 示例及其复核清单，可在 Skill 页查看。

读取使用安全 YAML 解析，拒绝重复键、别名及过深结构，并限制文件大小。资源必须在该 Skill 目录内；拒绝绝对路径、`..`、驱动器路径及逃逸链接。读取 Skill 不自动执行脚本；需要执行时仍须通过现有工具与审批流程。关闭 Skill 后不再提供它的指令与资源。

格式参考：[Agent Skills specification](https://agentskills.io/specification)。实现约束和当前规范的全部可选特性不是同一个概念，不应据此宣称支持所有未来扩展字段。

## 配置 API 与开发入口

| 方法与路由 | 用途 |
| --- | --- |
| `GET /api/extensions` | 目录、MCP、Skills 和发现错误快照 |
| `POST /api/extensions/refresh` | 重新扫描并刷新发现 |
| `PUT /api/extensions/directories` | 提交 `tools`、`mcp`、`skills` 三个路径数组 |
| `PUT /api/extensions/mcp/{id}` | 新建或部分更新服务器 |
| `DELETE /api/extensions/mcp/{id}` | 删除服务器或屏蔽文件定义 |
| `PATCH /api/extensions/skills/{id}` | 提交 `{"enabled": true}` 或 `false` |
| `GET /api/tools`、`PUT /api/tools/{name}` | 原有工具开关及参数接口 |

开发入口分别是 `extensions/manager.py`（持久化和注册）、`extensions/plugins.py`（清单及调用）、`extensions/mcp.py`（协议客户端）、`extensions/skills.py`（发现与渐进读取）。注册表仍是工具构建与风险分类的共同来源，新增扩展不应另建一套绕开审批的调用通道。
