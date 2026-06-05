# TopologyProvider 真实实现

## Goal

为 TopologyProvider Protocol 提供具体实现，使 Ripple 可以从真实数据源加载社交网络拓扑，并在 INIT 阶段对 LLM 生成的拓扑进行后置验证，增加数据可信度。

## Requirements

* 实现 `FileTopologyProvider`: 加载 SNAP/JSON/GraphML/CSV/GML 文件 → TopologyData
* 实现 `SyntheticTopologyProvider`: 基于 NetworkX 生成 BA/WS/SBM/ER 合成拓扑
* 实现拓扑后置验证框架: 规模/结构/类型分布三项校验，结果仅记录不干预
* 在 `_PROVIDER_IMPLEMENTATIONS["topology"]` 注册所有实现
* 支持 YAML 配置和 `simulate(providers={"topology": ...})` 运行时注入
* 拓扑缓存: 同一 provider 实例内相同参数返回缓存结果
* 失败 fallback: Provider 异常 → logger.warning → LLM fallback path
* NetworkX 作为 optional dependency

## Acceptance Criteria

- [ ] `FileTopologyProvider` 可加载 SNAP edge list 并返回有效 TopologyData
- [ ] `FileTopologyProvider` 可加载 JSON (node-link) 拓扑文件
- [ ] `FileTopologyProvider` 可加载 GraphML 文件
- [ ] `FileTopologyProvider` 可加载 CSV edge list
- [ ] `FileTopologyProvider` 可加载 GML 文件
- [ ] `SyntheticTopologyProvider` 可生成 BA (scale-free) 拓扑
- [ ] `SyntheticTopologyProvider` 可生成 WS (small-world) 拓扑
- [ ] `SyntheticTopologyProvider` 可生成 SBM (社区结构) 拓扑
- [ ] `SyntheticTopologyProvider` 可生成 ER (随机基线) 拓扑
- [ ] 后置验证: 规模校验 (节点/边数偏差) 记录到日志
- [ ] 后置验证: 结构校验 (连通性/孤立节点/度分布) 记录到日志
- [ ] 后置验证: 类型分布校验 (star/sea 比例) 记录到日志
- [ ] 所有 Provider 通过 YAML 配置实例化
- [ ] 所有 Provider 通过 `simulate(providers={"topology": ...})` 运行时参数注入
- [ ] Provider 异常时 fallback 到 LLM 生成
- [ ] 拓扑缓存: 重复调用返回缓存
- [ ] 单元测试覆盖所有 Provider 和边界情况
- [ ] NetworkX 为 optional dependency，import 失败时 graceful degradation

## Definition of Done

* Tests added/updated (unit/integration where appropriate)
* Lint / typecheck / CI green
* Code-spec updated (provider-architecture.md) with topology provider details
* Rollback considered: new providers are additive, no existing behavior changed

## Technical Approach

### 文件加载 (FileTopologyProvider)

```python
# ripple/providers/topology_loaders.py
class FileTopologyProvider:
    """Load topology from file (SNAP/JSON/GraphML/CSV/GML)."""

    def __init__(self, path: str | Path, format: str = "auto", node_type_map: Dict | None = None): ...
    async def get_topology(self, *, skill_id=None, platform=None, constraints=None) -> TopologyData | None: ...
```

- `format="auto"` 时根据文件扩展名推断
- NetworkX 读取 → `node_link_data()` → key rename `links` → `edges`
- SNAP node IDs (int) → 映射为 `agent_N` 字符串
- 使用 `asyncio.to_thread()` 包装 NetworkX sync I/O
- `node_type_map`: 可选映射指定节点类型 (star/sea)

### 合成生成 (SyntheticTopologyProvider)

```python
class SyntheticTopologyProvider:
    """Generate synthetic topology using NetworkX graph models."""

    def __init__(self, model: str = "ba", n: int = 50, **model_kwargs): ...
    async def get_topology(self, *, skill_id=None, platform=None, constraints=None) -> TopologyData | None: ...
```

- `model`: "ba" | "ws" | "sbm" | "er"
- `n`: 节点数
- `model_kwargs`: 模型特定参数 (m=2 for BA, k=4/p=0.3 for WS, etc.)
- `asyncio.to_thread()` 包装

### 后置验证 (TopologyValidator)

```python
# ripple/providers/topology_validator.py
class TopologyValidator:
    """Post-hoc validation of LLM-generated topology against provider data."""

    def validate(self, llm_topology: TopologyData, provider_topology: TopologyData) -> ValidationReport: ...

@dataclass
class ValidationReport:
    scale: ScaleCheck      # node/edge count deviation
    structure: StructCheck  # connectivity, isolated nodes, degree distribution
    type_dist: TypeCheck    # star/sea ratio comparison
    warnings: List[str]
```

- 校验结果仅 `logger.info/warning`，不修改 LLM 输出
- 设计预留 `auto_correct: bool = False` 扩展点

### 集成点: runtime.py

在 INIT phase 完成后（`_run_init_subcall("topology")` 返回后），调用:
```python
if providers and providers.topology and providers.topology.is_available():
    provider_data = await providers.topology.get_topology(...)
    if provider_data:
        report = validator.validate(llm_topology, provider_data)
        report.log()
```

### Registry 注册

```python
_PROVIDERS_IMPLEMENTATIONS = {
    "topology": {
        "file": ("ripple.providers.topology_loaders", "FileTopologyProvider"),
        "synthetic": ("ripple.providers.topology_loaders", "SyntheticTopologyProvider"),
    }
}
```

YAML 示例:
```yaml
_providers:
  topology:
    impl: file
    path: data/snap_facebook.txt
    format: snap
```

## Decision (ADR-lite)

**Context**: TopologyProvider 如何与 INIT 阶段集成
**Decision**: 后置验证模式 — INIT:topology LLM 正常执行，Provider 数据用于校验 LLM 输出的可信度
**Consequences**: 不省 LLM 成本但增加数据可信度；验证框架可复用于其他 Provider；预留自动修正扩展点

**Context**: 文件格式支持范围
**Decision**: 全格式支持 (SNAP/JSON/GraphML/CSV/GML)
**Consequences**: NetworkX 原生支持所有格式，实现成本低；用户无需转换数据

**Context**: 合成拓扑生成器范围
**Decision**: BA + WS + SBM + ER 四种模型
**Consequences**: 覆盖社交网络主要特征（无标度、小世界、社区结构、随机基线）；研究场景价值高

## Out of Scope

* 实时 API 类 Provider (Bluesky, Mastodon, Twitter/X)
* 拓扑可视化
* 拓扑编辑/修改 API
* 大规模图数据库集成 (Neo4j, etc.)
* python-igraph / graph-tool (license/install concerns)
* 自动修正模式 (预留扩展点但不实现)

## Technical Notes

* NetworkX `node_link_data()` 输出 `{"nodes": [...], "links": [...]}` — key rename `links` → `edges`
* SNAP edge list node IDs 为整数，需映射为字符串 agent IDs (e.g. `1` → `agent_1`)
* NetworkX 是 sync 库，需 `asyncio.to_thread()` 包装
* NetworkX optional: import 失败时 FileTopologyProvider/SyntheticTopologyProvider 的 `is_available()` 返回 False
* 文件: `ripple/providers/topology.py` (已有 Protocol), 新增 `ripple/providers/topology_loaders.py`, `ripple/providers/topology_validator.py`
- 参考: `.trellis/tasks/06-05-topologyprovider/research/topology-data-sources.md`
- INIT phase sub-call 3 是 `_build_init_topology_prompt` → LLM → topology dict

## Research References

* [`research/topology-data-sources.md`](research/topology-data-sources.md) — SNAP/NetworkX/API data sources, format comparison, implementation patterns
