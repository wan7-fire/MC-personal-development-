# System Prompt Instruction Pipeline

这是一个按三份任务文档落地的开发原型，目标是把全局指令模块化、把稳定内容与动态上下文分离，并用运行时补充标签控制会话级行为。

## 已实现能力

1. **全局指令模块化**
   - `InstructionModule` 定义模块元数据：`id`、`priority`、`stability`、`cacheable`、`version`、`injection_scope` 等。
   - `InstructionAssembler` 按优先级拼装模块，并校验依赖和冲突。
   - 默认模块覆盖安全、身份、工具、行为、交付、代码规范、记忆、任务模式、输出风格。

2. **稳定内容缓存与动态上下文通道**
   - `CacheBundle` 只纳入可缓存的 stable / semi_stable 模块。
   - `EnvironmentSnapshot` 生成会话首条系统级环境补充消息。
   - `RequestPayload` 分别计算稳定缓存哈希和动态负载哈希。

3. **运行时补充注入与行为评估**
   - `RuntimeReminder` 生成 `runtime-reminder` XML 标签，并声明其不是用户请求。
   - `mode_reminder()` 支持 Plan / Ask / Craft 按轮次注入：首轮完整、每 5 轮完整、其余精简、状态变化完整。
   - `EvaluationScenario` 和 `EvaluationRecord` 支持缓存与行为联合评估记录。

## 目录结构

```text
src/instruction_pipeline.py       核心实现
examples/build_demo.py            生成示例 payload 和拼装后的全局指令
tests/test_instruction_pipeline.py 单元测试
pytest.ini                        测试配置
dist/                             示例输出目录，运行 demo 后生成
```

## 运行示例

```bash
C:/Users/Legion677/.workbuddy/binaries/python/versions/3.13.12/python.exe examples/build_demo.py
```

运行后会生成：

- `dist/demo_payload.json`
- `dist/assembled_global_instruction.md`

## 运行测试

如果当前环境已有 `pytest`：

```bash
C:/Users/Legion677/.workbuddy/binaries/python/versions/3.13.12/python.exe -m pytest
```

如果没有 `pytest`，可在隔离虚拟环境中安装后再执行。不要全局安装依赖。
