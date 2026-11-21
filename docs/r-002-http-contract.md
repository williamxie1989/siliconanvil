# R-002 最小 HTTP 和 SSE 契约

`omlx-r serve` 在 IPv4 loopback 上监听。默认由系统分配端口，`--port` 可显式指定。服务公布实际监听地址，并提供一个固定输出的 `omlx-r-mock` 模型。

- `GET /v1/models` 返回一个模型。 `POST /v1/chat/completions` 接受 `model`、`messages` 和可选的 `stream`。消息支持纯文本的 system、user 和 assistant 角色。请求需要至少一条消息。未知模型返回 404，无效请求和超出当前契约的字段返回 OpenAI 风格的 4xx 错误对象。
- 非流式响应返回 assistant 消息和 `stop`。流式响应沿用同一文本，依次提供角色、文本片段、一次 `finish_reason=stop` 和一次 `[DONE]`。单次响应使用一致的 completion ID、模型和创建时间。模拟片段由响应流按需生成。
- SSE 探针从调用 HTTP 之前用单调时钟起算至第一个可见文本 delta。响应头等待、角色帧和首段输出的延迟纳入计时。探针要求一次完成原因和一个 `[DONE]`，并检查 HTTP 响应体结束前是否还有数据帧。总耗时在收到 `[DONE]` 时取样。

实现顺序为真实 TCP 失败用例、Rust 路由与响应、历史测量口径的 SSE 探针。验收使用进程所分配的临时端口和 Python 本地假服务，覆盖头部延迟、正常及断流。模型权重、推理吞吐、原 oMLX 兼容性和真实运行时取消留给后续任务。

## 验证

```sh
cargo build --locked
OMLX_R_TEST_BINARY=target/debug/omlx-r python3 -m unittest discover -s benchmarks/rust_evolution
cargo test --locked
```

实际 TCP 测试覆盖模型列表、非流式回复、SSE 帧顺序、错误响应、客户端断开后的下一次请求。模拟服务分别延迟响应头和首段文本，验证首字计时口径，并验证提前关闭连接时探针报错。

要单独测量一次请求，可运行 `python3 benchmarks/rust_evolution/sse_probe.py "$BASE_URL" request.json`。探针输出 TTFT、总耗时、完成原因、文本片段数和拼接文本。
