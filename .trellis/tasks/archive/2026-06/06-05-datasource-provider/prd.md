# DataSource Provider 扩展架构

## Goal

为 Ripple CAS 引擎引入外部数据源接入层（DataSource Provider），使模拟不再完全依赖 LLM 虚构，而是可接入真实社交图谱、历史传播数据、向量嵌入服务和实时舆情，逐步提升模拟保真度。当前所有数据（topology、seed_ripple、historical、ambient）均由 LLM 生成或用户手动传入，缺乏与外部数据系统的结构化对接。

## What I already know

* **当前数据流**：`simulate()` 接收 `event/source/historical/environment` 参数 → 组装为 `simulation_input` → Omniscient INIT 阶段 LLM 生成 topology/star_configs/sea_configs/seed_ripple/dynamic_parameters
* **拓扑**：完全由 LLM 在 INIT 阶段生成（`runtime.py:599`），无外部图数据
* **历史数据**：`historical` 为 `List[Dict]`，由用户手动传入（`api/simulate.py:253`），无自动检索
* **向量嵌入**：`Ripple.content_embedding` 已预留为 `List[float]`（`models.py:26`），但运行时始终为空列表（`runtime.py:715`）
* **环境上下文**：`Field.ambient` 为 `Dict[str,Any]`，当前无外部数据注入
* **模因池**：`Field.meme_pool` 为 `List[Meme]`，当前无外部趋势数据
* **持久化**：`JobRepoSQLite` 存作业状态，模拟结果写 JSON 文件
* **Skill 体系**：`LoadedSkill` 已有 platform/channel/vertical 画像注入机制，可复用此模式
* **LLM 路由**：`ModelRouter` 已有 adapter 抽象（chat_completions/responses/anthropic/bedrock），可参考此模式做数据源 adapter
* **依赖约束**：`pyproject.toml` 核心依赖仅 fastapi/httpx/pydantic/pyyaml，新数据源依赖应为 optional

## Assumptions (temporary)

* Provider 接口设计遵循 Python Protocol（结构化子类型），不强制继承
* 每个 Provider 为可选注入，缺失时回退到现有 LLM 生成行为（向后兼容）
* MVP: 全部四个 Provider 接口定义 + EmbeddingProvider 实现（拓扑/历史/舆情仅 Protocol+Stub）
* Provider 实例化通过配置文件或 `simulate()` 参数注入，不修改核心编排逻辑签名

## Open Questions

(none — all resolved)

## Decision (ADR-lite)

**Context**: MVP 需要验证 Provider 架构可行性，同时为后续扩展铺路。四个 Provider 对模拟的价值和实现成本不同。
**Decision**: 全部四个 Provider 接口定义 + 仅 EmbeddingProvider 实现。其余三个（Topology/Historical/Ambient）定义为 Protocol + Stub，暂不实现。
**Consequences**: 架构一次性铺完，后续新增 Provider 只需实现接口；EmbeddingProvider 改动最小（字段已预留、endpoint 已定义），适合验证 Provider 注入机制是否工作。

**Context**: Provider 配置来源有 YAML 文件和运行时参数两种，需与现有 LLM 路由模式保持一致。
**Decision**: YAML 声明默认 Provider + simulate() 参数可覆盖。优先级：运行时参数 > YAML 配置 > 无 Provider（回退到 LLM）。
**Consequences**: 用户可在 YAML 中全局配置默认 Provider，也可按调用动态切换；与 ModelRouter 三层优先级模式一致。

**Context**: EmbeddingProvider 需要 API endpoint 配置，LLM 路由层已有 `/embeddings` 路径和完整的连接配置。
**Decision**: EmbeddingProvider 与 ModelRouter 共享 endpoint 配置。调用 `ModelRouter.embed()` 获取向量，无需额外配置项。
**Consequences**: MVP 避免引入新的配置面；后续如需独立 embedding 服务商，可扩展为独立配置模式。

**Context**: TopologyProvider 需要定义返回数据格式，后续实现者和引擎侧都需要对齐。
**Decision**: Edge-list + node metadata 格式：`{"nodes": [{"id", "type", ...}], "edges": [{"source", "target", "weight", ...}]}`。与当前 LLM INIT 输出结构对齐。
**Consequences**: 迁移成本最低，edge-list 是图数据交换的事实标准；后续实现者可直接产出此格式。

## Requirements (evolving)

* 定义 `DataSourceProvider` Protocol 基础接口（所有 Provider 的公共契约）
* 定义 `TopologyProvider` Protocol + Stub（返回 edge-list + node metadata 格式）
* 定义 `HistoricalProvider` Protocol + Stub（提供历史传播数据）
* 定义 `EmbeddingProvider` Protocol + OpenAI 实现（通过 ModelRouter.embed() 获取向量）
* 定义 `AmbientProvider` Protocol + Stub（提供实时环境/舆情数据）
* Provider 注入机制：YAML 声明默认 Provider + simulate() 参数可覆盖（优先级：运行时 > YAML > 无 Provider）
* Provider 失败回退：调用失败时 log warning + 静默回退到 LLM 生成，不中断模拟
* Skill 关联：SKILL.md 可声明 `required_providers` 字段，引擎自动加载对应 Provider
* 向后兼容：无 Provider 时行为不变

## Acceptance Criteria (evolving)

* [ ] 无 Provider 注入时，simulate() 行为与当前完全一致
* [ ] 四个 Provider Protocol 已定义（TopologyProvider / HistoricalProvider / EmbeddingProvider / AmbientProvider）
* [ ] EmbeddingProvider 实现可用，注入后 seed_ripple.content_embedding 非空
* [ ] TopologyProvider/HistoricalProvider/AmbientProvider 有 Stub 实现（返回 None/空，触发 LLM 回退）
* [ ] Provider 可通过 simulate() 参数注入，也可通过 YAML 配置文件声明默认值
* [ ] simulate() 参数传入的 Provider 优先级高于 YAML 默认值
* [ ] Provider 调用失败时 log warning + 静默回退到 LLM 生成，模拟不中断
* [ ] SKILL.md 的 `required_providers` 字段可声明所需 Provider 类型，引擎据此自动加载
* [ ] 新增 Provider 不需要修改 SimulationRuntime 核心编排逻辑

## Definition of Done (team quality bar)

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Docs/notes updated if behavior changes
* Rollout/rollback considered if risky
* 向后兼容验证通过

## Out of Scope (explicit)

* TopologyProvider/HistoricalProvider/AmbientProvider 的真实实现 — 仅 Stub
* 具体第三方 API 集成（微博/小红书 API 客户端）
* 向量数据库（Milvus/Qdrant）集成 — 仅定义接口
* 持久化层扩展（PostgreSQL/MongoDB/Redis）— 独立任务
* 数据源缓存/去重/速率限制 — 后续迭代
* 异步 Provider（流式舆情）— 后续迭代
* 批量 embedding（对历史涟漪做嵌入）— 后续迭代
* embedding 维度校验/不匹配处理 — 后续迭代

## Technical Notes

* 关键文件：`ripple/api/simulate.py`（入口）、`ripple/engine/runtime.py`（编排）、`ripple/primitives/models.py`（数据模型）、`ripple/llm/router.py`（adapter 模式参考）
* `SimulationRuntime.__init__` 签名需扩展以接受 providers
* INIT 阶段 `omniscient.init()` 返回 topology — Provider 应在此阶段前注入/合并
* `Ripple.content_embedding` 已预留，SEED 阶段 `runtime.py:715` 是注入点
* TopologyProvider 返回格式：edge-list + node metadata（与 LLM INIT 输出对齐）