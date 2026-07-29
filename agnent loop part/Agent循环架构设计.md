# Agent 循环架构设计

> 基于 ReAct 范式的 Agent 循环本体设计——一轮 = 调 LLM → 解析响应 → 有工具就执行 → 结果回填 → 下一轮；没有工具调用就结束。

---

## 1. 设计目标

| 目标 | 说明 |
|------|------|
| **单循环自洽** | 一个循环实例完整覆盖"推理 → 行动 → 观察"的全流程 |
| **事件驱动** | 对外通过事件流（Channel）暴露过程，与上层（TUI/CLI）解耦 |
| **可控终止** | 多种终止路径（end_turn / 无工具调用 / max_turns / cancel / error），确保循环不会无限运行 |
| **安全优先** | 工具执行前后留拦截位，支持 plan-only 只读模式 |
| **可取消** | 响应外部 cancel，中途打断不破坏状态一致性 |

---

## 2. ReAct 循环范式

### 2.1 核心循环

一轮（Turn）的执行流程：

```
┌───────────────────────────────────────────────────────────┐
│                      Agent Loop                           │
│                                                           │
│   ┌─────────┐     ┌──────────┐     ┌───────────────┐     │
│   │  调 LLM  │────▶│ 解析响应  │────▶│ 有工具调用？   │     │
│   └─────────┘     └──────────┘     └───────┬───────┘     │
│                                             │              │
│                                  ┌─── yes ──┴── no ───┐   │
│                                  ▼                     ▼   │
│                        ┌──────────────┐        ┌──────────┐│
│                        │  执行工具     │        │  结束循环  ││
│                        └──────┬───────┘        └──────────┘│
│                               ▼                            │
│                        ┌──────────────┐                    │
│                        │  结果回填     │                    │
│                        └──────┬───────┘                    │
│                               │                             │
│                               └──── 下一轮 ────────────────▶│
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 一轮的详细步骤

1. **构造请求** — 将 system prompt（最小可用）+ 对话历史 + 工具定义（schema）组装为 LLM 请求
2. **调用 LLM** — 发送请求，获取模型响应
3. **解析响应**
   - 提取 `thinking`（模型推理过程）
   - 提取 `text`（模型文本内容）
   - 提取 `tool_calls`（工具调用列表，可能为空）
4. **判断是否终止**
   - `stop_reason == "end_turn"` → 终止
   - 没有 `tool_calls` → 终止
   - 达到 `max_turns` → 终止
   - 收到 cancel 信号 → 终止
5. **执行工具**（如果有）
   - 对 `tool_calls` 分组：读类（并发）/ 写类（串行）
   - 执行前拦截（权限检查位、plan-only 检查）
   - 执行工具
   - 执行后拦截
   - 收集工具结果
6. **结果回填** — 将工具结果作为 tool message 追加到对话历史
7. **下一轮** — 回到步骤 1

### 2.3 伪代码

```python
async def agent_loop(messages, tools, config):
    for turn in range(config.max_turns):
        # ── 检查取消信号 ──
        if config.cancel_token.is_cancelled():
            emit(Event(type="cancelled", data={"reason": "user_cancelled"}))
            break

        # ── 1. 调用 LLM ──
        response = await call_llm_with_retry(
            system=config.system_prompt,
            messages=messages,
            tools=tools,
            timeout=config.llm_timeout_ms,
        )

        # ── 2. 发射事件：thinking, text ──
        if response.thinking:
            emit(Event(type="thinking", data={"content": response.thinking}))
        if response.text:
            emit(Event(type="text", data={"content": response.text}))

        # ── 3. 判断终止条件 ──
        if response.stop_reason == "end_turn" or not response.tool_calls:
            emit(Event(type="turn_end", data={"reason": "end_turn"}))
            break

        # ── 4. 执行工具 ──
        tool_results = await execute_tools_batch(
            response.tool_calls, config, channel
        )
        for result in tool_results:
            emit(Event(type="tool_result", data=result.to_dict()))

        # ── 5. 结果回填 ──
        messages.append(ToolMessage(
            tool_calls=response.tool_calls,
            results=tool_results,
        ))

    emit(Event(type="loop_end", data={"total_turns": turn}))
```

---

## 3. 状态机设计

### 3.1 状态定义

| 状态 | 说明 |
|------|------|
| `IDLE` | 初始状态，等待用户输入 |
| `RUNNING` | 循环运行中，正在执行一轮 |
| `TOOL_EXECUTING` | 正在执行工具 |
| `PLAN_ONLY` | 只规划不执行模式，只允许读类工具 |
| `CANCELLED` | 收到取消信号，正在清理 |
| `TERMINATED` | 循环终止，最终状态 |

### 3.2 状态转换图

```
                        ┌──────────┐
            ┌───────────│   IDLE   │
            │           └─────┬────┘
            │                 │ user_message
            │                 ▼
            │           ┌──────────┐
            │     ┌─────│ RUNNING  │────┐
            │     │     └─────┬────┘    │
            │     │           │         │ tool_calls
            │     │ cancel    │         ▼
            │     ▼           │   ┌───────────────┐
            │ ┌────────┐     │   │TOOL_EXECUTING │
            │ │CANCELLED│    │   └───────┬───────┘
            │ └───┬────┘    │           │
            │     │         │           │ done
            │     │         │           ▼
            │     │         └─────▶ RUNNING (下一轮)
            │     │               或 TERMINATED
            │     ▼
            │ ┌────────────┐
            └▶│ TERMINATED │
              └────────────┘

  PLAN_ONLY 切换:  RUNNING ←──(enter_plan_only)──▶ PLAN_ONLY
                   PLAN_ONLY ←──(exit_plan_only)──▶ RUNNING
```

### 3.3 合法转换表

| 当前状态 | 动作 | 目标状态 |
|---------|------|---------|
| `IDLE` | `start` | `RUNNING` |
| `RUNNING` | `tool_call` | `TOOL_EXECUTING` |
| `TOOL_EXECUTING` | `tool_done` | `RUNNING` |
| `RUNNING` | `terminate` | `TERMINATED` |
| `RUNNING` | `cancel` | `CANCELLED` |
| `TOOL_EXECUTING` | `cancel` | `CANCELLED` |
| `CANCELLED` | `cleanup_done` | `TERMINATED` |
| `RUNNING` | `enter_plan_only` | `PLAN_ONLY` |
| `PLAN_ONLY` | `exit_plan_only` | `RUNNING` |

> 非法转换抛出 `IllegalStateTransition` 异常，防止状态错乱。

### 3.4 终止条件详解

| 终止条件 | 触发时机 | 行为 |
|---------|---------|------|
| 模型显式 `end_turn` | LLM 返回 `stop_reason="end_turn"` | 正常结束，发射 `turn_end` 事件 |
| 无工具调用 | LLM 响应中不含 `tool_calls` | 正常结束，模型认为任务完成 |
| 达到最大轮数 | `turn >= max_turns` | 发射 `turn_end(reason="max_turns")`，强制终止 |
| 用户取消 | 收到 cancel 信号 | 发射 `cancelled` 事件，清理后终止 |
| 不可恢复错误 | LLM 调用失败（鉴权、格式错误等） | 发射 `error` 事件，终止循环 |

### 3.5 取消打断时的状态一致性保证

```
1. 设置 cancel 标志位
2. 等待当前操作（LLM 调用或工具执行）完成或超时
   ├── 如果 LLM 调用中：通过 SDK 取消接口中断
   └── 如果工具执行中：不强制中断，等待其完成或自行超时
3. 清理临时状态，确保对话历史完整
4. 发射 cancelled 事件
5. 转入 TERMINATED
```

> **核心原则**：协作式取消（cooperative cancellation），不强制 kill 正在执行的工具。工具层的超时由工具自身的 `timeout_ms` 控制。

---

## 4. 最大轮数限制

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_turns` | `20` | 最大执行轮数，可配置 |

达到上限时的行为：

1. 不再调用 LLM
2. 发射 `turn_end(reason="max_turns")` 事件
3. 将当前对话历史保存，供用户查看
4. 发射 `loop_end` 事件

---

## 5. 取消与超时机制

### 5.1 取消机制

采用 **协作式取消**（cooperative cancellation）：

```
外部信号                    Agent Loop
   │                           │
   │── set cancel ───────────▶│
   │                           │
   │                    ┌──────┴──────┐
   │                    │ 检查点       │
   │                    │ (每轮开始前) │◀── 检查 cancel 标志
   │                    └──────┬──────┘
   │                           │
   │                    ┌──────┴──────┐
   │                    │ LLM 调用    │◀── 通过 SDK abort
   │                    └──────┬──────┘
   │                           │
   │                    ┌──────┴──────┐
   │                    │ 工具执行    │◀── 等待工具自身超时
   │                    └──────┬──────┘
```

### 5.2 超时机制

| 层级 | 默认超时 | 超时后行为 |
|------|---------|-----------|
| LLM 调用 | `120s` | 标记失败，发射 `error` 事件，可重试 |
| 单个工具执行 | `30s`（可按工具覆盖） | 标记失败，结果回填为 timeout error |
| 整体循环 | 无限制（受 `max_turns` 约束） | — |

### 5.3 CancelToken 实现

```python
class CancelToken:
    """线程安全的取消令牌"""
    def __init__(self):
        self._cancelled = False
        self._lock = threading.Lock()

    def cancel(self):
        with self._lock:
            self._cancelled = True

    def is_cancelled(self) -> bool:
        with self._lock:
            return self._cancelled

    def reset(self):
        with self._lock:
            self._cancelled = False
```

---

## 6. 错误处理策略

| 错误类型 | 处理方式 | 是否终止循环 |
|---------|---------|:------------:|
| LLM API 限流 (429) | 指数退避重试，最多 3 次 | 重试耗尽则终止 |
| LLM 鉴权失败 (401/403) | 不重试，发射 `error` 事件 | ✅ 终止 |
| LLM 响应格式错误 | 发射 `error` 事件 | ✅ 终止 |
| LLM 调用超时 | 发射 `error` 事件 | ✅ 终止 |
| 工具不存在 | 结果回填为 error message | ❌ 继续 |
| 工具执行异常 | 结果回填为 error message | ❌ 继续 |
| 工具执行超时 | 结果回填为 timeout error | ❌ 继续 |
| 用户取消 | 发射 `cancelled` 事件，清理 | ✅ 终止 |

> **设计原则**：工具层面的错误不终止循环，让模型有机会自我修正。只有 LLM 调用层面的不可恢复错误才终止整个循环。

---

## 7. 设计约束——明确不做（本章范围）

以下能力留给后续章节，本章不实现：

| 序号 | 能力 | 本章处理方式 |
|------|------|-------------|
| 1 | 复杂的系统提示词组装 | 使用最小可用 system prompt，仅包含基本角色定义和工具使用说明 |
| 2 | 完整的权限策略 | 仅在工具执行前后留拦截位（pre/post hook），不实现具体权限规则 |
| 3 | 递归 Agent 调用（子任务委派） | 不实现 |
| 4 | 其他后续章节能力 | 包括但不限于多 Agent 协作、长期记忆、动态工具注册等 |
