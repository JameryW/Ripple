# Research: Historical Data Sources for Social Media Event Propagation

- **Query**: Public datasets and data sources for historical social media event propagation data to feed Ripple's HistoricalProvider
- **Scope**: External (public datasets, APIs, file formats) + Internal (existing provider architecture)
- **Date**: 2026-06-05

---

## Executive Summary

This research identifies viable data sources for loading historical social media event propagation records into Ripple's `HistoricalProvider`. Key findings:

1. **SNAP (Stanford)** provides the best free cascade datasets (Higgs Twitter, temporal networks)
2. **Wikipedia Pageview API** is the best free API for historical attention metrics
3. **Reddit archives** (Pushshift mirrors) provide comment cascade trees with temporal data
4. **MemeTracker** provides phrase/meme propagation across media sites (archived but available)
5. **Twitter/X API** is effectively unusable due to cost (Academic Research track discontinued)
6. **File-based loading** (JSON, CSV) is the recommended pattern, matching existing TopologyProvider design

---

## 1. Internal Context: HistoricalProvider Interface

### 1.1 Protocol Definition

**File**: `/home/admin/Ripple/ripple/providers/historical.py`

```python
class HistoricalProvider(DataSourceProvider, Protocol):
    """Provides historical propagation records for calibration."""

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        """Return historical event records, or ``None`` for LLM fallback."""
        ...
```

### 1.2 Current Usage in Ripple

**File**: `/home/admin/Ripple/ripple/api/simulate.py:253`

```python
async def simulate(
    ...
    historical: Optional[List[Dict[str, Any]]] = None,
    ...
):
    # simulation_input["historical"] = historical
```

**File**: `/home/admin/Ripple/ripple/agents/omniscient.py:745-755`

```python
has_historical = bool(simulation_input.get("historical"))
system = (
    OMNISCIENT_SYNTHESIZE_ANCHORED_SYSTEM
    if has_historical
    else OMNISCIENT_SYNTHESIZE_RELATIVE_SYSTEM
)
```

**File**: `/home/admin/Ripple/ripple/service/reporting.py:159-191`

```python
def _historical_metric_summary(historical: Sequence[dict[str, Any]]) -> str:
    """Extract numeric metrics from historical records for reporting."""
    for item in historical:
        for key, value in item.items():
            if isinstance(value, (int, float)):
                collected.setdefault(key, []).append(float(value))
```

### 1.3 Expected Record Format

Based on internal usage, historical records should contain:

| Field | Type | Description | Example |
|-------|------|-------------|---------|
| `event_id` | str | Unique identifier | `"cascade_001"` |
| `platform` | str | Platform name | `"twitter"`, `"xiaohongshu"` |
| `event_type` | str | Content type | `"post"`, `"video"`, `"note"` |
| `timestamp` | str/int | ISO timestamp or Unix epoch | `"2024-01-15T10:30:00Z"` |
| `initial_metrics` | dict | Metrics at creation | `{"views": 0, "shares": 0}` |
| `peak_metrics` | dict | Peak metrics | `{"views": 50000, "shares": 1200}` |
| `final_metrics` | dict | Final metrics | `{"views": 48000, "shares": 1150}` |
| `time_to_peak` | str/int | Time to peak | `"6h"`, `21600` (seconds) |
| `cascade_depth` | int | Reshare generations | `5` |
| `cascade_width` | int | Max breadth | `120` |
| `engagement_rate` | float | Engagement ratio | `0.045` |
| `author_type` | str | Author classification | `"star"`, `"sea"` |
| `content_category` | str | Content category | `"beauty"`, `"tech"` |

---

## 2. Public Cascade Datasets

### 2.1 SNAP Higgs Twitter Dataset (RECOMMENDED)

**URL**: https://snap.stanford.edu/data/higgs-twitter.html

**Status**: FREE, no authentication, direct download

**Description**: Twitter activity during the 2012 CERN Higgs boson discovery announcement. One of the best publicly available cascade datasets.

**Data Included**:
- 456,626 tweets
- 589,942 retweets
- 14,655 reply relationships
- 32,492 mention relationships
- Temporal data (Unix timestamps)

**Files**:
- `higgs-activity_time.txt`: User activity with timestamps
- `higgs-social_network.edgelist`: Follower network
- `higgs-retweet_network.edgelist`: Retweet network

**Format Example** (`higgs-activity_time.txt`):
```
# Format: userA userB timestamp interaction
# interaction: RT=retweet, MT=mention, RE=reply
12345 67890 1341102791 RT
12345 54321 1341102805 MT
```

**Ripple Relevance**: HIGH - Contains real cascade propagation with timestamps, can be transformed to historical records

---

### 2.2 SNAP Temporal Networks

**URL**: https://snap.stanford.edu/data/

| Dataset | Description | Nodes | Edges | Temporal |
|---------|-------------|-------|-------|----------|
| `CollegeMsg` | Private messages | 1,893 | 59,835 | Yes |
| `email-Eu-core-temporal` | Email network | 986 | 332,334 | Yes |
| `wiki-Talk-temporal` | Wikipedia talk | 2,394,385 | 5,021,149 | Yes |
| `sx-mathoverflow` | Stack Exchange | 24,818 | 506,550 | Yes |
| `soc-sign-bitcoinotc` | Bitcoin trust | 5,881 | 35,592 | Yes |

**Format**: Edge list with timestamps
```
# source target timestamp weight
1 2 1388599452 1
1 3 1388601234 1
```

**Ripple Relevance**: MEDIUM-HIGH - Temporal interaction patterns can be aggregated into propagation metrics

---

### 2.3 MemeTracker Dataset

**URL**: http://memetracker.org/ (archived, data available via academic sources)

**Paper**: Leskovec, Backstrom, Kleinberg (2009) "Meme-tracking and the Dynamics of the News Cycle"

**Data Included**:
- 96 million blog posts
- 3.9 million distinct quotes
- 1.1 million distinct phrases/memes
- Time-stamped appearances across 1.6M mainstream media sites and blogs

**Format Example**:
```
# Document ID, Timestamp, Quote, Source URL
doc_001 2008-08-24T10:00:00 "the fundamentals of our economy are strong" site_a.com
doc_002 2008-08-24T10:15:00 "the fundamentals of our economy are strong" site_b.com
```

**Ripple Relevance**: HIGH - Direct meme/phrase propagation tracking across media sources

---

### 2.4 Twitter Cascade Datasets (Academic Papers)

| Paper | Year | Description | Access |
|-------|------|-------------|--------|
| Cheng et al. "Can Cascades Be Predicted?" | 2014 | ~150K image cascades | Contact authors |
| Goel et al. "The Structure of Online Diffusion Networks" | 2012 | 2B URL mentions | Contact authors |
| Myers & Leskovec "The Bursty Dynamics of the Twitter Information Network" | 2014 | Retweet cascades | Contact authors |
| Hodas & Lerman "How Visibility and Designed Anonymity Shape Cascades" | 2014 | Digg/Twitter cascades | Contact authors |

**Note**: Most Twitter cascade datasets from academic papers require contacting authors. Twitter's API terms now prohibit sharing raw tweet data.

---

### 2.5 Reddit Datasets

**Pushshift Reddit Archive** (now defunct, but mirrors exist):
- Historical posts and comments with timestamps
- Includes: upvotes, comment counts, subreddit, author
- Comment trees form cascade structures

**Reddit Hyperlink Network** (SNAP):
- URL: https://snap.stanford.edu/data/soc-RedditHyperlinks.html
- 35,586 subreddits, 137,830 directed hyperlinks
- Includes sentiment and temporal data

**Reddit Cascade Dataset** (Horawalavithana et al.):
- Comment cascade trees with temporal data
- Used for cascade prediction research

**Ripple Relevance**: MEDIUM - Reddit comment trees are cascade-like but differ from social media repost cascades

---

### 2.6 Wikipedia Pageview API (BEST FREE API)

**URL**: https://wikitech.wikimedia.org/en/docs/Pageview_API

**Status**: FREE, no authentication required

**Description**: Hourly pageview counts for any Wikipedia article from 2015 onward.

**Endpoint**:
```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{project}/{access}/{agent}/{article}/{granularity}/{start}/{end}
```

**Example**:
```
GET https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/en.wikipedia/all-access/all-agents/Barack_Obama/daily/20240101/20240131
```

**Response**:
```json
{
  "items": [
    {
      "project": "en.wikipedia",
      "article": "Barack_Obama",
      "granularity": "daily",
      "timestamp": "2024010100",
      "views": 15234,
      "access": "all-access",
      "agent": "all-agents"
    },
    ...
  ]
}
```

**Ripple Relevance**: HIGH - Free API for historical attention metrics, can be used to calibrate "viral" thresholds

---

### 2.7 Other Notable Datasets

| Dataset | Source | Description | Access |
|---------|--------|-------------|--------|
| Weibo Cascade Data | Chinese academic papers | Repost cascades on Weibo | Limited |
| YouTube Trending | Kaggle | Daily trending videos with metrics | Free |
| Media Cloud | MIT Media Lab | News attention tracking | Free API |
| CrowdTangle | Meta | Facebook public page analytics | Discontinued 2024 |
| Twitter Academic Research | Twitter/X | Historical tweet access | **DISCONTINUED** |

---

## 3. File-Based Loading Patterns

### 3.1 Recommended Record Format (JSON)

Based on the existing TopologyProvider pattern and internal usage:

```json
{
  "records": [
    {
      "event_id": "cascade_001",
      "platform": "twitter",
      "event_type": "post",
      "timestamp": "2024-01-15T10:30:00Z",
      "initial_metrics": {
        "views": 0,
        "shares": 0,
        "comments": 0,
        "likes": 0
      },
      "peak_metrics": {
        "views": 50000,
        "shares": 1200,
        "comments": 340,
        "likes": 2800
      },
      "final_metrics": {
        "views": 48000,
        "shares": 1150,
        "comments": 335,
        "likes": 2750
      },
      "time_to_peak_hours": 6.0,
      "cascade_depth": 5,
      "cascade_width": 120,
      "engagement_rate": 0.045,
      "author_type": "star",
      "content_category": "tech",
      "tags": ["AI", "startup"]
    }
  ],
  "metadata": {
    "source": "snap_higgs_twitter",
    "collected_at": "2024-01-20T00:00:00Z",
    "record_count": 1000
  }
}
```

### 3.2 CSV Format Alternative

```csv
event_id,platform,event_type,timestamp,initial_views,peak_views,final_views,time_to_peak_hours,cascade_depth,engagement_rate,author_type
cascade_001,twitter,post,2024-01-15T10:30:00Z,0,50000,48000,6.0,5,0.045,star
cascade_002,twitter,post,2024-01-16T14:00:00Z,0,1200,1100,2.5,2,0.012,sea
```

### 3.3 Loading Pattern (Following TopologyProvider)

**Reference**: `/home/admin/Ripple/ripple/providers/topology_loaders.py`

```python
class FileHistoricalProvider:
    """Load historical propagation records from file."""

    def __init__(
        self,
        path: str | Path,
        format: str = "auto",  # "json" | "csv" | "auto"
    ):
        self._path = Path(path)
        self._format = format
        self._cache: List[Dict[str, Any]] | None = None

    @property
    def name(self) -> str:
        return f"file-historical({self._path.name})"

    def is_available(self) -> bool:
        return self._path.exists()

    async def health_check(self) -> bool:
        return self.is_available()

    async def get_historical(
        self,
        *,
        skill_id: str | None = None,
        platform: str | None = None,
        event_type: str | None = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]] | None:
        if self._cache is None:
            self._cache = await self._load_records()

        # Filter by parameters
        records = self._cache
        if platform:
            records = [r for r in records if r.get("platform") == platform]
        if event_type:
            records = [r for r in records if r.get("event_type") == event_type]

        return records[:limit]

    async def _load_records(self) -> List[Dict[str, Any]]:
        fmt = self._format
        if fmt == "auto":
            fmt = self._detect_format()

        if fmt == "json":
            return await asyncio.to_thread(self._load_json)
        elif fmt == "csv":
            return await asyncio.to_thread(self._load_csv)
        else:
            raise ValueError(f"Unknown format: {fmt}")
```

---

## 4. API-Based Historical Sources

### 4.1 Wikipedia Pageview API (FREE)

**Use Case**: Fetch historical attention metrics for topics

**Implementation**:
```python
class WikipediaPageviewProvider:
    """Fetch historical pageview data from Wikipedia API."""

    BASE_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"

    async def get_historical(
        self,
        *,
        article: str,
        start: str,
        end: str,
        granularity: str = "daily",
    ) -> List[Dict[str, Any]]:
        url = f"{self.BASE_URL}/en.wikipedia/all-access/all-agents/{article}/{granularity}/{start}/{end}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url)
            data = resp.json()

        return [
            {
                "event_id": f"pageview_{item['timestamp']}",
                "platform": "wikipedia",
                "event_type": "article_view",
                "timestamp": item["timestamp"],
                "views": item["views"],
            }
            for item in data.get("items", [])
        ]
```

### 4.2 Reddit Archive (Limited Access)

**Status**: Pushshift is defunct; some mirrors exist

**Alternative**: Use Reddit's official API for recent data (60 req/min)

### 4.3 Twitter/X API (NOT RECOMMENDED)

**Status**: Academic Research track DISCONTINUED

**Cost**: $5,000/month for 1M tweets (Basic tier insufficient for historical analysis)

**Recommendation**: Do not implement Twitter API provider

---

## 5. Transformation Patterns

### 5.1 SNAP Higgs to Historical Record

```python
def transform_higgs_to_records(filepath: str) -> List[Dict[str, Any]]:
    """Transform SNAP Higgs Twitter data to Ripple historical records."""
    import pandas as pd

    # Load activity file
    df = pd.read_csv(filepath, sep=' ', header=None, names=['user', 'target', 'timestamp', 'type'])

    # Group by cascade (retweet tree)
    cascades = df[df['type'] == 'RT'].groupby('target')

    records = []
    for root_tweet, group in cascades:
        timestamps = group['timestamp'].values
        cascade_size = len(group)

        records.append({
            "event_id": f"higgs_{root_tweet}",
            "platform": "twitter",
            "event_type": "retweet_cascade",
            "timestamp": pd.to_datetime(min(timestamps), unit='s').isoformat(),
            "cascade_size": cascade_size,
            "cascade_depth": compute_depth(group),
            "time_to_peak_hours": compute_peak_time(timestamps),
        })

    return records
```

### 5.2 Wikipedia Pageview to Historical Record

```python
def transform_pageview_to_records(data: Dict) -> List[Dict[str, Any]]:
    """Transform Wikipedia pageview API response to Ripple historical records."""
    records = []
    for item in data.get("items", []):
        records.append({
            "event_id": f"wiki_{item['article']}_{item['timestamp']}",
            "platform": "wikipedia",
            "event_type": "article_view",
            "timestamp": item["timestamp"],
            "views": item["views"],
            "article": item["article"],
        })
    return records
```

---

## 6. Recommended Implementation

### 6.1 Provider Hierarchy

```
HistoricalProvider (Protocol)
├── FileHistoricalProvider     # Load from JSON/CSV files
├── WikipediaPageviewProvider  # Fetch from Wikipedia API
├── SnapHistoricalProvider     # Load SNAP datasets (pre-transformed)
└── StubHistoricalProvider     # Returns None (LLM fallback)
```

### 6.2 YAML Configuration

```yaml
_providers:
  historical:
    impl: file
    path: data/historical_cascades.json
    format: json
```

or

```yaml
_providers:
  historical:
    impl: wikipedia
    # No path needed - uses API
```

### 6.3 Priority Resolution (Same as TopologyProvider)

| Priority | Source | Example |
|----------|--------|---------|
| 1 (highest) | `simulate(providers={...})` runtime param | `{"historical": my_provider}` |
| 2 | `llm_config.yaml` `_providers` section | YAML-declared defaults |
| 3 (lowest) | Stub (returns None) | `StubHistoricalProvider()` |

---

## 7. Related Specs

| Spec File | Description |
|-----------|-------------|
| `.trellis/spec/backend/provider-architecture.md` | DataSource Provider architecture, HistoricalProvider interface |
| `.trellis/tasks/archive/2026-06/06-05-topologyprovider/research/topology-data-sources.md` | Topology data sources (similar pattern) |
| `ripple/providers/historical.py` | HistoricalProvider Protocol definition |
| `ripple/providers/topology_loaders.py` | FileTopologyProvider implementation pattern |

---

## 8. Caveats / Not Found

### Not Researched
- Chinese social media APIs (Weibo, WeChat, Xiaohongshu) - likely require business registration
- Instagram/Facebook Graph API - requires app review, limited access
- LinkedIn API - restricted to approved partners
- TikTok API - limited historical access

### Limitations
- Twitter Academic Research track is discontinued (critical for research use)
- Most real-time APIs have rate limits that make large-scale historical extraction slow
- Public cascade datasets may not reflect current social media dynamics (most are 2012-2015 era)
- MemeTracker data is archived and may require academic access

### Open Questions for Implementation
1. Should historical records be cached in memory or re-read each simulation?
2. How to handle schema evolution (different platforms have different metrics)?
3. Should we support real-time API fetching or only file-based loading?
4. How to aggregate historical records for calibration (mean, median, distribution)?

---

## 9. Summary Recommendations

| Use Case | Recommended Source | Format |
|----------|-------------------|--------|
| Real cascade data | SNAP Higgs Twitter | Transform to JSON |
| Attention metrics | Wikipedia Pageview API | API fetch |
| Custom historical data | User-provided JSON/CSV | File load |
| Reddit cascades | Pushshift mirrors (if available) | Transform to JSON |
| Meme propagation | MemeTracker (archived) | Transform to JSON |

**Primary Recommendation**: Implement `FileHistoricalProvider` following the `FileTopologyProvider` pattern. Support JSON and CSV formats. Provide pre-transformed SNAP Higgs dataset as a sample. Add `WikipediaPageviewProvider` as an API-based option for attention metrics.
