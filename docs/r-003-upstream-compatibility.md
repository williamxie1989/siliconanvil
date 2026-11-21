# R-003 oMLX 协议对照

对照版本选用 oMLX `v0.8.2`，发布标签解析为 `5eeec7757f83bfc99eeb87abe49686d00a0bba1f`。截至 2026-11-21，独立参考仓库的 `origin/main` 为 `ade9f20a`。本页记录源码核对。通过兼容性验收后再登记实测版本。

| 范围 | oMLX v0.8.2 源码及测试 | oMLX-R 现状和对照重点 |
| --- | --- | --- |
| 模型列表 | [`list_models`](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L3786) 读取引擎池，应用模型可见性设置 | 已验证固定单模型的列表格式。用同一列表契约比较 `object`、模型 ID 和类型 |
| 非流式纯文本 | [`create_chat_completion`](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L5016) 接收统一聊天请求 | 已验证单条 assistant 回复和完成原因。比较协议结构，忽略响应 ID、时间及模型生成的具体文本 |
| SSE 纯文本正常完成 | [上游流式测试](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/tests/integration/test_e2e_streaming.py#L265) 检查 SSE MIME、首帧和末尾 `[DONE]` | 已对 Rust mock 做 TCP 验证。比较角色、文本、完成原因和终止标志，以拼接后的文本和事件语义为准 |
| SSE 错误和关闭 | [`stream_chat_completion` 异常路径](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L6905) 发错误事件并关闭引擎迭代器 | Rust mock 已验证客户端断开后的下一次请求。对照需覆盖错误加 `[DONE]` 和 socket 提前 EOF 两种情形 |
| 请求扩展和用量 | [请求字段](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/api/openai_models.py#L333) 支持采样参数、token 上限和 `stream_options`。[用量帧](https://github.com/jundot/omlx/blob/5eeec7757f83bfc99eeb87abe49686d00a0bba1f/omlx/server.py#L7280) 可以紧随完成帧 | 当前 mock 按最小字段校验。收集实际客户端使用的字段后确定扩展顺序，工具调用、推理内容和用量单列验收 |

下一项交付是版本化的双方响应 fixture 和可重复的对照 runner。先使用上游 mock engine 验证 HTTP 契约，分别记录进程版本、请求、响应形状和差异。隔离实例使用临时端口和独立配置目录。确认某项需要真实模型后再评估模型、内存余量及启动方式。现有 oMLX 服务作为独立工作负载继续运行。
