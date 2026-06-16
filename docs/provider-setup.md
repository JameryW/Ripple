# Provider Setup Guide

Ripple 的 DataSource Provider 系统为 ConfidenceGate 提供真实数据支撑，使置信度评估更准确。

## 概览

Provider 分为 4 类：

| 类别 | 作用 | 对 ConfidenceGate 的影响 |
|------|------|------------------------|
| `historical` | 历史表现数据 | historical_deviation 因子 |
| `topology` | 社交图谱数据 | topology_calibration 因子 |
| `embedding` | 内容嵌入向量 | （预留） |
| `ambient` | 环境上下文 | （预留） |

**不配置 Provider 时**：系统使用 Stub（返回 None），LLM 自行推理。ConfidenceGate 中相关因子保持中性（不降级也不提升）。

**配置 Provider 后**：因子产出实际评估结果，置信度更精确。

## 配置方式

### 方式 1：llm_config.yaml `_providers` 段（推荐）

在 `llm_config.yaml` 中添加 `_providers` 段：

```yaml
# LLM 配置（已有）
_default:
  model_platform: anthropic
  model_name: claude-sonnet-4-20250514
  api_key: ${RIPPLE_LLM_API_KEY}

# Provider 配置
_providers:
  historical:
    impl: FileHistoricalProvider
    path: data/providers/historical_sample.json
  topology:
    impl: FileTopologyProvider
    path: data/providers/topology_sample.json
```

### 方式 2：API runtime overrides

通过 `simulate()` 的 `providers` 参数注入：

```python
from ripple.providers.historical_loaders import FileHistoricalProvider
from ripple.providers.topology_loaders import FileTopologyProvider
from ripple.api.simulate import simulate

result = await simulate(
    event={"title": "测试内容", "description": "..."},
    providers={
        "historical": FileHistoricalProvider("data/providers/historical_sample.json"),
        "topology": FileTopologyProvider("data/providers/topology_sample.json"),
    },
)
```

### 方式 3：ProviderRegistry 构造

```python
from ripple.providers.registry import ProviderRegistry
from ripple.providers.historical_loaders import FileHistoricalProvider
from ripple.providers.topology_loaders import FileTopologyProvider

registry = ProviderRegistry(
    runtime_overrides={
        "historical": FileHistoricalProvider("data/providers/historical_sample.json"),
        "topology": FileTopologyProvider("data/providers/topology_sample.json"),
    },
)
```

## 数据格式

### Historical Provider (JSON)

```json
[
  {
    "platform": "xiaohongshu",
    "event_type": "post",
    "title": "夏日穿搭分享",
    "metrics": {
      "impressions": 85000,
      "engagement": 3200,
      "likes": 2800,
      "comments": 180,
      "shares": 220,
      "saves": 450
    },
    "performance_rating": "above_average",
    "created_at": "2025-06-01"
  }
]
```

也支持 `{"records": [...]}` 包装格式，以及 CSV 格式。

字段说明：
- `platform`（可选）：平台标识，用于过滤
- `event_type`（可选）：事件类型，用于过滤
- `metrics`：表现指标，供 HistoricalCalibrator 计算偏差
- `performance_rating`：定性评级，供参考

### Topology Provider (JSON)

```json
{
  "nodes": [
    {"id": "star_1", "type": "star", "platform": "xiaohongshu", "follower_count": 85000},
    {"id": "sea_1", "type": "sea", "platform": "xiaohongshu", "follower_count": 500}
  ],
  "edges": [
    {"source": "star_1", "target": "sea_1", "weight": 0.8}
  ]
}
```

字段说明：
- `nodes`：节点列表，每个节点必须有 `id` 和 `type`（"star" 或 "sea"）
- `edges`：边列表，每条边必须有 `source`、`target`、`weight`（0-1）

也支持 CSV、GraphML、GML 格式。

## 验证

### CLI doctor 检查

```bash
ripple-cli doctor
```

输出中会包含 provider 健康状态：

```
Provider Health:
  historical: ✓ available (file-historical)
  topology: ✓ available (file-topology)
  embedding: ✗ stub (no data)
  ambient: ✗ stub (no data)
```

### Python 验证

```python
from ripple.providers.registry import ProviderRegistry

registry = ProviderRegistry(yaml_path="llm_config.yaml")
health = await registry.health_check_all()
print(health)
# {'historical': True, 'topology': True, 'embedding': False, 'ambient': False}
```

## Ensemble Runs

`ensemble_runs` 默认为 2，每次模拟自动运行 2 次取聚合结果。这使 `ensemble_stability` 因子（kappa、agreement_rate）有实际数据支撑。

可传 `ensemble_runs=1` 回退到单次运行模式。

## ConfidenceGate 因子对照

| 因子 | 无 Provider | 有 Provider |
|------|-----------|------------|
| provider_availability | MEDIUM（降级） | HIGH |
| historical_deviation | 中性（无数据） | 实际偏差值 |
| topology_calibration | 中性（无数据） | 实际校准结果 |
| ensemble_stability | 中性（单次运行） | kappa + agreement_rate |
| evidence_balance | 梯度 3 级 | 不变 |
| evidence_silent | 梯度 3 级 | 不变 |
| tribunal_audit | 多数投票 | 不变 |
