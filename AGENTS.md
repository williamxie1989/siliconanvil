# oMLX-R 项目规则

Write all new code comments, docstrings, and project documentation in English. When extending an existing document or comment, write the added material in English. Leave translation of existing text to the maintainer's dedicated pass.

项目从 [架构和发行决策](docs/project-workflow.md) 接续。新会话先运行 `python3 tools/rust_evolution/project.py resume`，收尾将证据和续做动作写回台账。

参考 oMLX 使用独立检出和固定 SHA。每次采纳上游变更应记录来源、移植方式、适用范围和测试结果。原 oMLX 服务、环境、设置和缓存属于独立运行环境。

新行为先用小型 fixture 或模拟服务验证。真实推理在隔离端口、独立配置目录和足够内存余量下验证。公开发布仅描述已有的模型和 API 能力。个人证据、模型权重、缓存和台账存放在仓库外。
