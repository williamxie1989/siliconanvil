# oMLX-R 架构和发行决策

## 仓库与首版定位

项目以 Rust 编写推理服务为长期目标，使用独立仓库、独立版本和独立发布节奏。oMLX 作为兼容性实现参考。若目标转为持续分发现有 Python 版并叠加少量 Rust 扩展，共享 Git 历史的 fork 更容易吸收上游提交。当前 Rust 服务会逐步形成自己的 API、运行和打包边界，适合独立历史。

阶段顺序是 Rust HTTP/SSE 与模拟后端、固定 oMLX 参考行为、一个原生 MLX 小模型、独立发行。开发阶段可以用隔离的 oMLX 实例做外部对照。首个声称具备 Rust 推理的公开版本以至少一个真实模型走 Rust 入口和原生 MLX 路径为准。DeepSeek V4.1 的量化、MTP、SSD offload 和长上下文逐项验证。初期路径采用独立二进制、配置目录、端口和缓存写入位置。

R-002 的首个增量是临时 loopback 端口上的 `GET /v1/models` 和 `POST /v1/chat/completions`。用固定文本和脚本化延迟驱动模拟后端，验证首个可见输出、一次正常终止和异常断流。此前 EV-002 的历史回放直接复用，剩余实机基线待 R-003 选定兼容性对照版本后补齐。

## 跟踪上游

维护三个独立事实。 `upstream/reference-commit` 是分析差异的起点。独立参考仓库的 `origin/main` 是最近抓取的上游头。通过契约或实机测试的兼容版本由证据确认。初始分析起点来自原路线图，目前没有通过验证的兼容版本。

首次在仓库外准备仅供比较的 Git 仓库：

```sh
reference="$HOME/omlx-notes/rust-evolution/upstream/omlx.git"
mkdir -p "$(dirname "$reference")"
git clone --bare --single-branch --branch main --no-tags \
  https://github.com/jundot/omlx.git "$reference"
git -C "$reference" config remote.origin.fetch '+refs/heads/main:refs/remotes/origin/main'
```

每轮、上游发布或底层依赖升级时，先抓取再查看固定起点之后的差异：

```sh
reference="$HOME/omlx-notes/rust-evolution/upstream/omlx.git"
git -C "$reference" fetch --no-tags origin
cargo run --locked -- upstream-status "$reference"
```

对涉及 API、模型、缓存、MLX/Metal 或其他依赖的提交，在 [上游变更决策](../upstream/decisions.md) 登记上游 SHA、受影响契约和处理决定。API 变化优先写协议或回放用例。模型适配要绑定 checkpoint、格式与版本。ABI 变化要绑定 MLX/Metal 与绑定库版本，再做实机校验。选定变更通过对应验收后，再用一次可审查的提交更新基线或迁移内容。若 Git 报告分叉或参考引用回退，先核对远端历史及基线。

## 跨会话项目管理

```sh
python3 tools/rust_evolution/project.py resume
python3 tools/rust_evolution/project.py status
python3 tools/rust_evolution/project.py history --query upstream
```

默认沿用 `~/omlx-notes/rust-evolution/progress.sqlite3`。Rust 项目任务使用 R 编号，原 EV 任务保留历史证据及依赖。每次记录用 `note --summary ... --evidence ... --next ...`。任务完成用 `finish --summary ... --evidence ...`。外部工具脚本记录另一个检出时，放在子命令前的 `--repo PATH` 指定 Git HEAD 来源。证据路径按本地工作目录解析。

## 公开发布前

- `oMLX-R` 是工作名。确定公开名称和视觉标识前，核对上游项目名的使用授权。公开介绍明确独立维护及渊源。Apache-2.0 第 6 条的项目名和商标授权范围需单独处理。
- 引入代码、测试、模型配方或绑定时，逐项记录精确来源和许可证。分发第三方代码时保留适用的版权、许可证、修改说明和 NOTICE。模型权重的分发条款需独立核对。
- 兼容表按客户端、流式事件、使用量、工具调用、取消、错误语义和模型配方记录实测范围。对照环境区分 Rust 原生推理与上游参考版本。
- 硬件 CI 与发布分别验证。通用 Rust 构建和模拟测试适合普通 CI。Metal、统一内存和性能验证绑定真实 Apple Silicon、OS、MLX、模型和设置。首次分发前明确支持架构、最低 macOS 版本、二进制格式与签名安排。
- 新服务用独立的默认目录和配置格式。缓存格式若需要互通，先验证版本与只读行为，再单独设计写入迁移。

GitHub 可作为代码、公开 Issues 和 Releases 的协作入口。本地台账继续保留运行证据和会话状态。公开条目通过稳定的 R/EV 编号关联，公开报告使用脱敏摘录。
