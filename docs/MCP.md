# MCP Server v0 — tick / recall / state

> 代码：`mcp_server.py`；回放：`scripts/replay_trace.py` + 仪表盘 `POST /api/replay`。
> 传输：MCP stdio，每行一条 JSON-RPC 2.0 消息；零第三方依赖。

## 1. 启动

```bash
# 默认角色、默认 memory.db
python mcp_server.py

# 指定关系记忆库与默认关系域
python mcp_server.py --db memory.db --character-id furina --user-id player
```

stdout 只写协议消息；错误与日志写 stderr。没有 API key 时 `tick` 仍可工作
（纯本地模板表达）；只有 `use_llm=true` 才会尝试调用 LLM。

Claude Code 的 `.mcp.json` 示例：

```json
{
  "mcpServers": {
    "emotion-engine": {
      "command": "python",
      "args": [
        "C:\\Users\\Administrator\\Desktop\\emotion-engine\\mcp_server.py",
        "--db",
        "C:\\Users\\Administrator\\Desktop\\emotion-engine\\memory.db"
      ]
    }
  }
}
```

## 2. 协议

支持：

- `initialize`：回显客户端 `protocolVersion`，声明 `tools` 能力；
- `notifications/initialized`：通知，不返回响应；
- `ping`：返回 `{}`；
- `tools/list`：返回三个工具及 JSON Schema；
- `tools/call`：执行工具，结果放在 `content[0].text`（JSON 字符串）。

错误约定：

| 场景 | 返回 |
|---|---|
| 未知方法 | JSON-RPC `-32601` |
| 参数不是对象 | `-32602` |
| 工具内部异常 / 参数错误 | `result.isError = true`，文本为原因 |
| 无法解析的 JSON 行 | `-32700` |

## 3. 工具

### `tick`

推进一轮情绪。

| 参数 | 类型 | 说明 |
|---|---|---|
| `text` | string | 自然语言事件；默认用 JEV 生成 appraisal |
| `appraisal` | object | 直接传六维；与 `text` 二选一 |
| `boundary_violation` | boolean | 显式边界判定；不传则用 Noul 检测 |
| `use_llm` | boolean | 是否走 LLM 表达路径，默认 false |
| `trace` | boolean | 是否返回 StepTrace，默认 true |
| `character_id` / `user_id` | string | 覆盖默认关系域 |

返回：`state`、`atmosphere`、`tolerance`、`expression`、`action`、
`utterance`、`shock_channels`、`blends`、`jev`、`boundary`、`trace`、
`llm_used`、`llm_backend`。

`character_id` 只用于选择表达模板与记忆域；不改变引擎公式。

### `recall`

按 `(character_id, user_id)` 召回关系域记忆。

| 参数 | 类型 | 说明 |
|---|---|---|
| `query` | string | 必填 |
| `character_id` / `user_id` | string | 覆盖默认关系域 |
| `budget_chars` | integer | prompt 记忆区块上限，默认 1200，范围 100–4000 |

返回：`context`、`relationship`、`cards`、`facts`、`open_loops`。
记忆库不可用时返回空 `context` 与 `error`，不会让工具崩溃。

### `state`

读取当前 10 通道、氛围、耐受窗与记忆统计。不推进 tick。

## 4. Trace 回放

命令行：

```bash
python scripts/replay_trace.py --text "没想到你居然离开了"
python scripts/replay_trace.py --text "被真正理解" --character furina
python scripts/replay_trace.py --appraisal '{"goal_relevance":0.8,"goal_conduciveness":-0.7}'
```

每次命令都新建引擎，只读回放，不写快照/记忆。输出：
`steps[].trace`、`final_state`、`atmosphere`、`tolerance`。

仪表盘：

```bash
POST /api/replay
{"text": "没想到你居然离开了", "boundary_violation": false}
```

服务端复制当前 `state` / `tolerance` / `scars`，用内存记忆回放一轮，
返回 `trace` 与回放后的 `state`；真实引擎、快照和记忆库都不变。

## 5. 安全与边界

- 服务只在本机 stdio 运行，不监听端口；
- 默认纯本地，不访问网络；
- `use_llm=true` 时才可能调用 DeepSeek / Kimi；
- LLM 输出只进入 `utterance`，不写回通道、人格或核心公式；
- 记忆仍按 `(character_id, user_id)` 隔离，不存整段聊天；
- `mcp_server.py` 不导入 dashboard，也不修改引擎内部状态结构。

## 6. 验收

```text
test_l6_mcp.py               16 tests（协议 / 工具 / stdio 子进程）
全量                         365 tests OK（L7 后）
ruff check .                 All checks passed
```

`test_l6_mcp.StdioTest` 会真实启动子进程，发送 `initialize` 与 `tools/list`，
校验两行 JSON-RPC 响应。
