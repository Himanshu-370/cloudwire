# Cloudwire -- Comprehensive Codebase Deep-Dive

> **One-liner:** A Python CLI tool that scans your AWS account via the AWS API and displays all your services and their connections as an interactive graph in your browser.

---

## Table of Contents

1. [What Is Cloudwire?](#1-what-is-cloudwire)
2. [Tech Stack](#2-tech-stack)
3. [Project Structure](#3-project-structure)
4. [Entry Points & Startup Flow](#4-entry-points--startup-flow)
5. [System Architecture](#5-system-architecture)
6. [Module Dependency Graph](#6-module-dependency-graph)
7. [End-to-End Data Flow](#7-end-to-end-data-flow)
8. [Core Modules -- Detailed Analysis](#8-core-modules--detailed-analysis)
   - [GraphStore](#81-graphstore---thread-safe-graph-with-caching)
   - [AWSGraphScanner](#82-awsgraphscanner---mixin-composition-pattern)
   - [ScanJobStore](#83-scanjobstore---job-lifecycle-management)
   - [Service Registry](#84-service-registry-and-normalization)
9. [Key Algorithms & Logic](#9-key-algorithms--logic)
   - [Two-Phase Parallel Scan Orchestration](#91-two-phase-parallel-scan-orchestration)
   - [Network Exposure Algorithm](#92-network-exposure-algorithm)
   - [ARN-Based Graph Filtering](#93-arn-based-graph-filtering)
   - [Frontend Layout Algorithms](#94-frontend-layout-algorithms)
   - [Graph Analysis Algorithms](#95-graph-analysis-algorithms)
10. [AWS Service Scanners](#10-aws-service-scanners)
    - [Lambda Scanner](#101-lambda-scanner)
    - [VPC Scanner](#102-vpc-scanner)
    - [API Gateway Scanner](#103-api-gateway-scanner)
    - [Step Functions ASL Parser](#104-step-functions-asl-parser)
    - [Other Scanners](#105-other-scanners)
11. [Terraform Parsing](#11-terraform-parsing)
    - [.tfstate Parser](#111-tfstate-parser)
    - [.tf HCL Parser](#112-tf-hcl-parser)
12. [API Layer & Routes](#12-api-layer--routes)
13. [Frontend Architecture](#13-frontend-architecture)
    - [State Management](#131-state-management)
    - [Graph Pipeline](#132-graph-pipeline-usegraphpipeline)
    - [Component Structure](#133-component-structure)
14. [Cross-Cutting Concerns](#14-cross-cutting-concerns)
    - [Error Handling](#141-error-handling)
    - [Security](#142-security)
    - [Performance](#143-performance)
    - [Concurrency & Thread Safety](#144-concurrency--thread-safety)
15. [Design Patterns Used](#15-design-patterns-used)
16. [Architectural Trade-offs & Decisions](#16-architectural-trade-offs--decisions)
17. [Conventions & Naming](#17-conventions--naming)
18. [Configuration & Environment](#18-configuration--environment)
19. [Architectural Strengths](#19-architectural-strengths)
20. [Potential Improvements](#20-potential-improvements)
21. [Glossary](#21-glossary)
22. [FAQ](#22-faq)

---

## 1. What Is Cloudwire?

Cloudwire is a **read-only AWS infrastructure visualization tool**. You run it from your terminal, it opens a browser tab, you pick which AWS services to scan, click Scan, and within seconds you see a live interactive graph of your Lambda functions, DynamoDB tables, SQS queues, VPCs, and everything else -- with arrows showing which resources talk to which.

**The core insight**: Understanding AWS architectures is hard when you only have the AWS console, which shows each service in isolation. Cloudwire pulls all those services together into one graph so you can see that your API Gateway calls Lambda, which reads from DynamoDB, which is inside a VPC with a subnet and a security group.

**Key design principles:**
- **Single-user, local-only** -- nothing leaves your machine except AWS API calls
- **No authentication** -- relies entirely on your existing AWS credentials
- **Single package** -- `pip install cloudwire`, React frontend is pre-compiled and bundled
- **Dual mode** -- live AWS scanning OR Terraform file upload (no credentials needed)

**Current version:** 0.2.5 (Beta)

---

## 2. Tech Stack

| Technology | Role | Why It's Here |
|------------|------|---------------|
| Python 3.9+ | Backend language | Wide availability; boto3 is the standard AWS SDK |
| FastAPI | HTTP API framework | Async, auto-generates OpenAPI docs, easy routing |
| uvicorn | ASGI server | Lightweight; starts in-process from the CLI |
| boto3 / botocore | AWS API calls | Official SDK; handles auth chain, retries, pagination |
| networkx | In-memory graph | Directed graph with node/edge attribute storage |
| pydantic v2 | Request validation | Auto-validates all API inputs |
| click | CLI framework | Handles CLI options, help text, version flag |
| python-hcl2 | Terraform HCL parsing | Parses `.tf` files without a Terraform binary |
| React 18 | Frontend UI | Component model; hooks make the data pipeline clean |
| Vite | Frontend build tool | Bundles JS/CSS into `cloudwire/static/` |
| Tailwind CSS | Frontend styling | Utility classes; dark theme |
| ThreadPoolExecutor | Concurrency | Runs service scanners in parallel |

**Total Python:** ~6,336 lines across 37 files

---

## 3. Project Structure

```
cloudwire/                 --> Python package (ships in PyPI package)
|-- __init__.py            --> Version number only
|-- cli.py                 --> CLI entry point (click command, starts uvicorn)
+-- app/                   --> FastAPI application
    |-- main.py            --> FastAPI app factory, middleware, route wiring, SPA fallback
    |-- models.py          --> Pydantic request/response models, validation
    |-- services.py        --> Canonical service registry (names, colors, grouping)
    |-- graph_store.py     --> Thread-safe in-memory graph (wraps networkx)
    |-- scanner.py         --> Main scan orchestrator (AWSGraphScanner class)
    |-- scan_jobs.py       --> Job lifecycle management (queue, polling, cache, cancel)
    |-- aws_clients.py     --> Shared boto3 client factory, STS account ID lookup
    |-- errors.py          --> Custom exceptions, error payloads, AWS error translation
    |-- terraform_parser.py --> Parses .tfstate v4 files into graph nodes and edges
    |-- hcl_parser.py      --> Parses .tf (HCL) files into graph nodes and edges
    |-- scanners/          --> One mixin file per AWS service
    |   |-- _utils.py      --> Shared constants (ARN regex, env var conventions)
    |   |-- lambda_.py     --> Lambda functions, event sources, IAM inference
    |   |-- vpc.py         --> VPCs, subnets, SGs, IGWs, NAT GWs, route tables
    |   |-- ec2.py         --> EC2 instances, VPC placement, instance profiles
    |   |-- iam.py         --> IAM roles (first 200)
    |   |-- apigateway.py  --> REST + HTTP APIs, Lambda/SQS/SNS integrations
    |   |-- eventbridge.py --> Rules and targets
    |   |-- dynamodb.py    --> Tables, streams, replicas
    |   |-- sqs.py         --> Queues, DLQ edges
    |   |-- sns.py         --> Topics and subscriptions
    |   |-- s3.py          --> Buckets, notification edges
    |   |-- ecs.py         --> Clusters, services, task definitions, LB edges
    |   |-- rds.py         --> DB instances and clusters
    |   |-- cloudfront.py  --> Distributions, origin edges
    |   |-- route53.py     --> Hosted zones, records, alias targets
    |   |-- cognito.py     --> User pools
    |   |-- kinesis.py     --> Streams
    |   |-- stepfunctions.py --> State machines
    |   |-- elasticache.py --> Cache clusters
    |   |-- glue.py        --> Jobs, crawlers, triggers
    |   |-- appsync.py     --> GraphQL APIs
    |   +-- redshift.py    --> Clusters
    |-- routes/            --> FastAPI route handlers
    |   |-- scan.py        --> /api/scan, /api/graph, /api/resource/*
    |   |-- tags.py        --> /api/tags/keys, values, resources
    |   +-- terraform.py   --> /api/terraform/parse
    +-- cost/              --> (empty placeholder)

frontend/src/              --> React application source
|-- main.jsx               --> React root mount
|-- App.jsx                --> App wrapper
|-- pages/CloudWirePage.jsx --> Single-page app; all state lives here
|-- hooks/
|   |-- useScanPolling.js  --> Manages scan jobs, polling, graph state
|   |-- useGraphPipeline.js --> Applies filters, clustering, layout in sequence
|   |-- usePathFinder.js   --> Shortest-path mode state machine
|   |-- useTagDiscovery.js --> Tag key/value/resource discovery
|   |-- useTerraformUpload.js --> File staging and upload
|   +-- useGraphViewport.js --> Pan/zoom/fit viewport math
|-- lib/
|   |-- api.js             --> fetchApi() -- all HTTP calls go through here
|   |-- serviceVisuals.jsx --> Icons (SVG), colors, labels for each service
|   |-- graphTransforms.js --> Barrel re-export of graph modules
|   +-- graph/
|       |-- normalize.js   --> normalizeGraph(), region filter, connectivity partition
|       |-- layout.js      --> Flow, circular, swimlane, hybrid layout algorithms
|       |-- clustering.js  --> Collapse services into cluster nodes; focus subgraph
|       |-- analysis.js    --> Shortest path (BFS), blast radius, pattern detection
|       +-- annotations.js --> VPC/AZ/subnet bounding-box overlay annotations
+-- components/
    |-- graph/
    |   |-- GraphCanvas.jsx --> SVG canvas with pan/zoom, node drag, minimap
    |   |-- GraphNode.jsx   --> Individual node rendering (icon + label + ring)
    |   |-- GraphEdge.jsx   --> Directed edge rendering with flow animation
    |   |-- GraphLegend.jsx --> Color legend
    |   +-- Minimap.jsx     --> Overview minimap
    +-- layout/
        |-- TopBar.jsx      --> Region picker, service selector, Scan button
        |-- ServiceSidebar.jsx --> Service list, counts, search, cluster toggles
        |-- InspectorPanel.jsx --> Node detail panel (attributes, edges)
        |-- LayoutDropdown.jsx --> Flow/Circular/Swimlane switcher
        |-- TagFilterBar.jsx   --> Tag key/value filter UI
        |-- TerraformDropZone.jsx --> Drag-and-drop area for TF files
        |-- TerraformFilePanel.jsx --> File list + parse button
        +-- WarningsPanel.jsx     --> Scan warning display

cloudwire/static/          --> Pre-compiled frontend (checked into package)
```

---

## 4. Entry Points & Startup Flow

**When a user runs `cloudwire`:**

1. `pyproject.toml` declares `cloudwire = "cloudwire.cli:main"` as the installed script
2. **`cli.py:main()`** is a click command. It:
   - Checks dependencies and validates port availability
   - Starts a background thread to check PyPI for updates
   - Prints the startup message
   - Schedules a browser open in 1.5 seconds
   - Calls `uvicorn.run("cloudwire.app.main:app", ...)`
3. uvicorn imports `cloudwire.app.main` and creates the FastAPI application
4. **`main.py`** creates the `job_store = ScanJobStore(max_workers=4)` singleton (line 33), registers all route groups under `/api`, mounts `cloudwire/static/assets` as static files, and adds a wildcard SPA fallback route serving `static/index.html`
5. The browser opens to `http://localhost:8080`, loads `index.html`, which loads the bundled React app

**Frontend bootstrap:**
1. `main.jsx` mounts `<App />` into the DOM
2. `App.jsx` renders `<CloudWirePage />`
3. `CloudWirePage.jsx` initializes all state, `useScanPolling()` sets `graphData = EMPTY_GRAPH`

---

## 5. System Architecture

```
Browser (React SPA)
    |  HTTP (same origin)
    v
FastAPI app  ---- SecurityHeadersMiddleware
    |
    |-- /api/scan/*         <-- routes/scan.py
    |-- /api/tags/*         <-- routes/tags.py
    |-- /api/terraform/*    <-- routes/terraform.py
    +-- /assets/*, /*       <-- static files (Vite build)
            |
    ScanJobStore (in-memory, 4 workers)
            |
    AWSGraphScanner (mixin class, 21 services)
            |
    GraphStore (NetworkX DiGraph, thread-safe)
            |
    AWS APIs (boto3, read-only)
```

Cloudwire is a **single-process, single-user desktop tool** running as a local web server. There is no database, no message broker, no external service dependencies beyond AWS itself.

---

## 6. Module Dependency Graph

```
services.py <-- models.py <-- routes/*.py
errors.py   <-- aws_clients.py <-- routes/*.py
graph_store.py <-- scan_jobs.py <-- routes/scan.py
                                <-- scanner.py <-- routes/scan.py
scanners/_utils.py <-- scanners/*.py <-- scanner.py
terraform_parser.py <-- hcl_parser.py <-- routes/terraform.py
```

**Key observations:**
- `services.py` and `errors.py` are **foundation modules** with no internal dependencies
- `graph_store.py` depends only on `networkx` -- cleanly isolated
- `scanner.py` has the highest fan-in (pulls in all 21 mixins)
- **No circular dependencies** -- the dependency graph is a DAG

---

## 7. End-to-End Data Flow

### Live AWS Scan (Example: Lambda + SQS + DynamoDB)

```
1. TopBar "Scan" button calls runScan({ region, services, mode })
   --> useScanPolling.js:runScan()

2. POST /api/scan with body {region, services, mode, force_refresh}
   --> scan.py:create_scan_job()
   --> resolve_account_id() calls STS GetCallerIdentity
   --> Builds cache_key from account_id + region + sorted services + mode
   --> Checks ScanJobStore for cached result (avoids duplicate scans)
   --> Creates new ScanJob with status="queued"
   --> Submits _run_scan_job() to ThreadPoolExecutor
   --> Returns {job_id, status="queued", status_url, graph_url}

3. Frontend receives job_id, starts polling loop

4. Background thread runs _run_scan_job()
   --> Creates AWSGraphScanner(job.graph_store, options)
   --> Calls scanner.scan(region, services, ...)

5. PHASE 1: Non-VPC scanners run in parallel (ThreadPoolExecutor, 5 workers)
   --> Each worker: _scan_service(session, service_name)
   --> Dispatches to correct mixin (e.g., _scan_lambda)
   --> Creates nodes via self._node(), edges via self.store.add_edge()
   --> Lambda/EC2/ECS create VPC stub nodes (no VPC API calls yet)

6. After each scanner: _fetch_and_apply_tags()
   --> ResourceGroupsTaggingAPI batch fetch
   --> Matches tags to nodes by ARN

7. PHASE 2: VPC scan (scoped to referenced VPCs only)
   --> _collect_referenced_vpc_ids() finds stub VPC nodes from Phase 1
   --> _scan_vpc() fetches VPCs -> subnets -> SGs -> IGWs -> NAT GWs -> route tables
   --> Creates "contains", "protects", "routes_via" edges
   --> Creates synthetic "Internet" anchor nodes

8. POST-SCAN: _compute_network_exposure()
   --> Traces Internet -> IGW -> RTB -> Subnet -> SG -> Resource paths
   --> Marks internet-exposed resources

9. GraphStore serialized to {nodes, edges, metadata}
   --> scan_jobs.mark_completed() caches result with 5-min TTL

10. Frontend polling: GET /api/scan/{id}/graph
    --> normalizeGraph() strips dangling edges, normalizes service names

11. useGraphPipeline() recomputes (all useMemo stages):
    a. filterGraphByRegion()
    b. Service visibility filter (hiddenServices)
    c. partitionByConnectivity() (connected vs isolated)
    d. buildClusteredGraph() (collapse services >8 nodes)
    e. collapseContainerNodes() (VPC/AZ/subnet collapse)
    f. computeFocusSubgraph() (N-hop neighborhood in focus mode)
    g. layoutHybridGraph() | layoutSwimlane() (x,y positions)
    h. computeNetworkAnnotations() (bounding-box overlays)

12. GraphCanvas renders SVG:
    --> Annotations as styled rects
    --> Edges as paths with flow-animation
    --> Nodes as circles/squares with service icons
    --> Viewport transform for pan/zoom
```

### Terraform Parse Flow

```
POST /api/terraform/parse (multipart files)
  |-- Rate limit check (deque-based sliding window)
  |-- File type validation (.tf | .tfstate | .json)
  |-- Size validation (25 MB/file, 50 MB total)
  |-- Per .tfstate: validate_tfstate_content() -> parse JSON
  |-- Per .tf: validate_hcl_content() -> parse HCL
  |
  |-- TerraformParser(GraphStore).parse(state_dicts)
  |     |-- Pass 1: _register_resource() per aws_* resource
  |     +-- Pass 2: _infer_edges() per resource
  |
  |-- HCLParser(GraphStore).parse(hcl_dicts)  [if .tf files]
  |     |-- Pass 1: _register_resources()
  |     +-- Pass 2: _infer_edges() via regex reference sweep
  |
  |-- Return {job_id, graph, resource_count, edge_count, warnings}
```

### Error Path

- **AWS AccessDenied** --> scanner catches it, adds warning `"[permission] {service}: access denied"`. Scan continues.
- **Unhandled scanner exception** --> `mark_failed(job_id, message)`. Frontend shows error, stops polling.
- **User cancellation** --> `request_cancel(job_id)` sets flag. Running scanners check `_is_cancelled()` before each API call, raise `ScanCancelledError`. Partial graph preserved.

---

## 8. Core Modules -- Detailed Analysis

### 8.1 GraphStore -- Thread-Safe Graph with Caching

**File:** `cloudwire/app/graph_store.py` (234 lines)

**What:** The central data structure -- a NetworkX `DiGraph` wrapped in a thread-safe class with a single `threading.Lock` and an invalidation-based serialization cache.

**Why:** Multiple scanner threads write nodes/edges concurrently. The frontend polls for graph state. Thread safety is mandatory. Caching avoids repeated serialization of unchanged graphs.

**How -- Key Design Decisions:**

**Merge-on-write semantics** (lines 50-56):
```python
def add_node(self, node_id: str, **attrs: Any) -> None:
    with self._lock:
        current = self.graph.nodes[node_id] if self.graph.has_node(node_id) else {}
        merged = {**current, **attrs}
        merged["id"] = node_id
        self.graph.add_node(node_id, **merged)
        self._invalidate_cache()
```
Adding a node that already exists **merges** attributes -- it never clears existing fields. This is critical because multiple scanners may contribute attributes to the same node (e.g., Lambda scanner creates a VPC stub, VPC scanner later enriches it).

**Lazy serialization cache** (lines 75-89): `get_graph_payload()` converts the entire graph to `{nodes, edges, metadata}` dict. Cached in `_cached_payload`, invalidated on any mutation.

**Snapshot for safe traversal** (lines 100-103): `snapshot_graph()` returns `self.graph.copy()` under the lock -- used by `_compute_network_exposure` to avoid holding the lock during long traversals.

**Batch updates** (lines 105-118): `batch_update_nodes()` applies attribute dicts to multiple nodes atomically under one lock acquisition.

**Key methods:**
| Method | Purpose |
|--------|---------|
| `add_node(node_id, **attrs)` | Merge-on-write node creation/update |
| `add_edge(source, target, **attrs)` | Same merge semantics for edges |
| `get_graph_payload()` | Cached serialization to JSON-ready dict |
| `filter_by_arns(allowed_arns)` | Destructive 4-phase graph pruning (see Section 9.3) |
| `snapshot_graph()` | Thread-safe shallow copy for read-only traversal |
| `batch_update_nodes()` | Atomic multi-node attribute update |

---

### 8.2 AWSGraphScanner -- Mixin Composition Pattern

**File:** `cloudwire/app/scanner.py` (701 lines)

**What:** Orchestrates scanning all selected AWS services in parallel and builds the graph.

**Why:** Each AWS service has unique API calls, pagination, and edge semantics. The mixin pattern keeps each scanner independently comprehensible while allowing shared access to the graph store and boto3 session.

**How:** Inherits from **21 mixin classes** (lines 102-124):

```python
class AWSGraphScanner(
    ApiGatewayScannerMixin,
    LambdaScannerMixin,
    SqsScannerMixin,
    SnsScannerMixin,
    # ... 17 more mixins
):
```

Each mixin contributes one `_scan_*` method. All mixins access shared state via `self`: `self.store` (GraphStore), `self._client()` (boto3), `self._node()` (node creation), `self._ensure_not_cancelled()` (cancellation check).

**Service dispatch table** (lines 153-175) -- Strategy pattern:
```python
self.service_scanners: Dict[str, Callable] = {
    "apigateway": self._scan_apigateway,
    "lambda": self._scan_lambda,
    "sqs": self._scan_sqs,
    # ...
}
```

**Key methods:**
| Method | Purpose |
|--------|---------|
| `scan()` | Top-level: Phase 1 + Phase 2 + exposure computation |
| `_scan_service()` | Per-service error wrapper |
| `_node()` | Add/update node + maintain `_node_attr_index` |
| `_add_arn_node()` | Create node from ARN |
| `_fetch_and_apply_tags()` | Bulk tag fetch via ResourceGroupsTaggingAPI |
| `_scan_generic_service()` | Fallback for services without dedicated scanner |
| `_compute_network_exposure()` | Post-scan internet reachability analysis |
| `_drain_futures()` | Cancellation-aware future drain (200ms polling) |

---

### 8.3 ScanJobStore -- Job Lifecycle Management

**File:** `cloudwire/app/scan_jobs.py` (414 lines)

**What:** Manages creation, status updates, caching, cancellation, and pruning of all scan jobs.

**Why:** Scans take seconds to minutes. The frontend needs to poll for progress. Multiple scans may run concurrently. Caching avoids re-scanning identical parameters.

**How:**

**Cache key** (lines 391-414): Deterministic string: `account_id|region|sorted_services|mode|iam=N|describe=N`. For tag-filtered scans, ARN list is SHA-256 hashed:
```python
if tag_arns:
    arns_str = ",".join(sorted(tag_arns))
    parts.append(f"tags={hashlib.sha256(arns_str.encode()).hexdigest()[:16]}")
```

**Concurrency controls:**
- `_MAX_IN_FLIGHT_JOBS = 8` -- hard cap on concurrent scans
- `_MAX_RETAINED_TERMINAL_JOBS = 50` -- LRU pruning of completed jobs
- `ThreadPoolExecutor(max_workers=4)` for scan execution

**Cooperative cancellation** (lines 284-301): `request_cancel` sets flag; scanner checks `_is_cancelled()` at every API call, raises `ScanCancelledError`.

**Progress tracking** (lines 217-245): Composite labels like `"lambda, sqs | stop requested"` or `"5 active (apigateway, ec2...)"`.

**State machine:** `queued -> running -> completed | failed | cancelled`

---

### 8.4 Service Registry and Normalization

**File:** `cloudwire/app/services.py` (144 lines)

**33 aliases** map variant names to canonical IDs (lines 7-39): `"api-gateway"`, `"apigw"`, `"execute-api"` all normalize to `"apigateway"`. The `normalize_service_name` function is O(1) dict lookup.

**Registry** (lines 50-104): 37 services organized into 9 groups. Only 6 services enabled by default (apigateway, eventbridge, lambda, sqs, dynamodb, vpc) to keep quick scans fast.

**Important:** The same alias map exists in both Python (`services.py`) and JavaScript (`serviceVisuals.jsx`). They must be kept in sync manually.

---

## 9. Key Algorithms & Logic

### 9.1 Two-Phase Parallel Scan Orchestration

**Where:** `cloudwire/app/scanner.py`, `scan()` method, lines 195-300

**What:** A two-phase parallel execution strategy that scans services concurrently while deferring VPC topology to a scoped second pass.

**Why:** VPC topology is expensive -- fetching all subnets, SGs, route tables for an account with many VPCs could take 30+ seconds. By collecting VPC references from Phase 1, the code only fetches relevant VPCs.

**How:**

```
PHASE 1 (parallel, ThreadPoolExecutor, 5 workers):
  All non-VPC services run concurrently
  Each creates nodes/edges + VPC stub nodes

PHASE 2 (sequential, after Phase 1 drains):
  _collect_referenced_vpc_ids() finds all vpc stub nodes
  _scan_vpc(vpc_ids=[...]) fetches ONLY referenced VPCs
  Enriches stub nodes with full VPC topology

POST-SCAN:
  _compute_network_exposure() marks internet-exposed resources
```

The `_drain_futures` method (lines 391-409) is a custom loop supporting cooperative cancellation:
```python
def _drain_futures(self, future_map, on_result):
    pending = set(future_map)
    cancel_attempted = False
    while pending:
        if self._is_cancelled() and not cancel_attempted:
            cancel_attempted = True
            for future in list(pending):
                if future.cancel():
                    pending.remove(future)
        done, pending = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
        for future in done:
            on_result(future, future_map[future])
    self._ensure_not_cancelled()
```

---

### 9.2 Network Exposure Algorithm

**Where:** `cloudwire/app/scanner.py`, `_compute_network_exposure()`, lines 508-587

**What:** Traces the path `Internet -> IGW -> Route Table -> Subnet -> Security Group -> Resource` to determine which resources are internet-exposed.

**Why:** This is the most security-relevant computation in the codebase. It answers: "Which of my resources can be reached from the public internet?"

**How (step-by-step):**

1. **Snapshot** the graph (no lock held during traversal)
2. **Find** all Internet Gateway nodes
3. **Build** `subnet_resources` dict: subnet -> resources it contains (via "contains" edges)
4. **Build** `resource_sgs` dict: resource -> protecting security groups (via "protects" edges)
5. **Map** `igw_to_internet`: IGW node -> Internet anchor node
6. **Traverse** for each IGW: `IGW --(routes_via)--> RTB --(routes)--> Subnet`, recording the path
7. **Check** for each resource in an internet-reachable subnet: does any protecting SG have `has_open_ingress=True`?
8. **Mark** exposed resources with:
   - `exposed_internet=True`
   - `internet_path="igw-abc -> rtb-main -> subnet-public -> sg-default"` (human-readable)
   - `internet_path_nodes=[...]` (node IDs for frontend highlighting)
9. **Write** results atomically via `batch_update_nodes()`

---

### 9.3 ARN-Based Graph Filtering

**Where:** `cloudwire/app/graph_store.py`, `filter_by_arns()`, lines 133-218

**What:** A 4-phase graph pruning algorithm for tag-based filtering.

**Why:** When users filter by tags, they want to see only matching resources -- but also their immediate connections and VPC context for the graph to make sense.

**How:**

```
Phase 1 (Seed):     O(N) scan -- match nodes by real_arn, arn, or embedded ARN in node_id
Phase 2 (Expand):   O(seeds * avg_degree) -- 1-hop BFS via predecessors + successors
Phase 3 (VPC walk): BFS over service=="vpc" nodes reachable from kept set
Phase 4 (Prune):    graph.remove_node() for all unmatched nodes
```

Proximity-kept nodes are marked with `kept_by_proximity=True` so the frontend can visually distinguish them.

---

### 9.4 Frontend Layout Algorithms

**Where:** `frontend/src/lib/graph/layout.js`

#### Flow Layout (`layoutFlowGroup`)
- Uses **modified Kahn's algorithm** (`buildLevels`, lines 13-52) for topological sort
- Computes **longest-path level** (not shortest) for each node
- Assigns nodes to columns by dependency level, rows within columns by in-degree
- Column 0 = entry points (no incoming edges), Column N = leaf resources
- Multiple "lanes" when >6 nodes share a level

#### Circular Layout (`layoutCircularGroup`, lines 211-251)
- Level 0 = single center node
- Each subsequent level placed on concentric rings, radius grows by `circularLevelSpacing=200px`
- Ring capacity = `floor(2*pi*r / 240)` -- overflow creates sub-rings

#### Swimlane Layout (`layoutSwimlane`, lines 343-403)
- Ignores topology entirely
- Classifies each node into one of 6 roles: trigger / queue / processor / storage / network / unknown
- Horizontal bands, nodes sorted by connection count within each band
- Labels: "TRIGGERS & ENTRY POINTS", "DATA STORES", etc.

#### Hybrid Orchestration (`layoutHybridGraph`, lines 257-316)
- BFS-based connected component detection (`splitByConnectivity()`)
- Each component gets its own flow or circular layout in a `sqrt(N) x sqrt(N)` grid
- Isolated nodes (no edges) placed to the right
- Annotation boxes: "Connected Flows" and "Unconnected Resources"

---

### 9.5 Graph Analysis Algorithms

**Where:** `frontend/src/lib/graph/analysis.js`

#### Shortest Path (`findShortestPath`, lines 7-42)
- Standard **BFS** on directed graph, O(V+E)
- Returns ordered node ID list from source to target, or empty array if no path exists

#### Blast Radius (`computeBlastRadius`, lines 46-77)
- **Bidirectional BFS** -- forward (downstream: what this node affects) + backward (upstream: what affects this node)
- Returns `{upstream: Set, downstream: Set}` for highlighting

#### Pattern Detection (`detectPatterns`, lines 81-155)
- O(E) edge scan recognizes 4 architectural patterns:
  - **API Backend:** apigateway -> lambda -> (dynamodb|s3|rds)
  - **Event-Driven Pipeline:** eventbridge -> lambda
  - **Queue Worker:** sqs -> lambda
  - **Fan-out:** any node with 3+ outgoing edges

#### Architecture Summary (`generateArchitectureSummary`)
- Counts resources per service, identifies entry points (no incoming edges)
- Formats a human-readable sentence

#### Clustering (`buildClusteredGraph`, `clustering.js` lines 5-51)
- When service has >8 nodes (`AUTO_COLLAPSE_THRESHOLD`), all nodes collapse to one synthetic cluster node
- Edge deduplication via Set on `src->tgt` key

#### Focus Subgraph (`computeFocusSubgraph`, lines 53-80)
- BFS frontier expansion from center node for N hops, bidirectional (follows edges both ways)

---

## 10. AWS Service Scanners

### 10.1 Lambda Scanner

**Where:** `cloudwire/app/scanners/lambda_.py` (326 lines)

The most complex scanner because Lambda is the integration hub of serverless architectures.

**Environment Variable Edge Extraction** (lines 78-131) -- Two strategies:

1. **Explicit ARN detection:** If env var value matches `_ARN_PATTERN`, create direct reference edge
2. **Naming convention inference:** Uses `_ENV_VAR_CONVENTIONS` from `_utils.py`:
   - `_TABLE_NAME` suffix -> DynamoDB
   - `_QUEUE_URL` suffix -> SQS
   - `_BUCKET` suffix -> S3

**Event Source Mapping Scan** (lines 133-166): Manual pagination across all functions globally. ARN lookup includes fallback to `_base_lambda_arn` for qualified ARNs (with version/alias suffix).

**IAM Policy Inference** (lines 168-258, deep mode only):
1. Group Lambda functions by execution role
2. Fetch inline + attached policies in parallel
3. Extract `Allow` statements
4. Map IAM action prefixes to services via `_IAM_PREFIX_TO_SERVICE`
5. Create "calls" edges from Lambda to granted resources

IAM role cache (`_iam_role_cache` with `_iam_cache_lock`) ensures shared roles are fetched only once.

---

### 10.2 VPC Scanner

**Where:** `cloudwire/app/scanners/vpc.py` (247 lines)

Builds complete network topology in 6 ordered steps:

1. **VPCs** -> nodes with CIDR, default flag
2. **Subnets** -> linked to parent VPC via "contains" edges
3. **Security Groups** -> parsed rules + `has_open_ingress` flag:
   ```python
   has_open_ingress = any(
       any(r.get("CidrIp") == "0.0.0.0/0" for r in perm.get("IpRanges", [])) or
       any(r.get("CidrIpv6") == "::/0" for r in perm.get("Ipv6Ranges", []))
       for perm in inbound_rules
   )
   ```
4. **Internet Gateways** -> attached to VPCs + synthetic "Internet" anchor nodes
5. **NAT Gateways** -> contained within subnets
6. **Route Tables** -> with subnet associations and IGW/NAT route targets

**SG edge accumulation:** `pending_sg_edges` dict accumulates `(source, target) -> [port_labels]` because NetworkX only stores one edge per direction. Port labels are joined as comma-separated strings.

---

### 10.3 API Gateway Scanner

**Where:** `cloudwire/app/scanners/apigateway.py` (274 lines)

Scans both API Gateway v2 (HTTP/WebSocket) and REST APIs. Integration target resolver (lines 80-124) uses a priority cascade:

1. Lambda (most common) -- `_parse_lambda_arn` from `/invocations` URI
2. Step Functions -- `StepFunctions` in subtype or `states:::execution` in URI
3. SQS, SNS, Kinesis, EventBridge -- same pattern
4. Generic ARN fallback

REST API integration discovery is parallelized with up to `apigw_integration_workers` (default 16) threads.

---

### 10.4 Step Functions ASL Parser

**Where:** `cloudwire/app/scanners/stepfunctions.py`, lines 68-152

Recursively traverses Amazon States Language (ASL) JSON definitions:
- `Task` states with direct Lambda ARNs
- Optimized integrations (`states:::lambda:invoke`, `states:::dynamodb`, etc.)
- `Parallel` branches (recursive traversal)
- `Map` iterators (both `Iterator` and `ItemProcessor` keys for backward compatibility)
- Child state machine execution (`states:::states:startExecution`)
- Skips JSONPath references (`$`-prefixed dynamic values)

---

### 10.5 Other Scanners

| Scanner | File | Key Behavior |
|---------|------|-------------|
| EC2 | `ec2.py` | Instances + VPC placement + instance profiles |
| IAM | `iam.py` | First 200 roles; global service (us-east-1 endpoint) |
| EventBridge | `eventbridge.py` | Rules + targets with ARN resolution |
| DynamoDB | `dynamodb.py` | Tables + streams + global replicas |
| SQS | `sqs.py` | Queues + DLQ edges (RedrivePolicy) |
| SNS | `sns.py` | Topics + subscription edges |
| S3 | `s3.py` | Buckets + notification edges |
| ECS | `ecs.py` | Clusters + services + task definitions + LB edges |
| RDS | `rds.py` | DB instances + clusters |
| CloudFront | `cloudfront.py` | Distributions + origin edges; global service |
| Route53 | `route53.py` | Hosted zones + records + alias targets via hardcoded zone ID map |
| Cognito | `cognito.py` | User pools |
| Kinesis | `kinesis.py` | Streams |
| ElastiCache | `elasticache.py` | Cache clusters |
| Glue | `glue.py` | Jobs + crawlers + triggers |
| AppSync | `appsync.py` | GraphQL APIs |
| Redshift | `redshift.py` | Clusters |

**Global service handling:** IAM, CloudFront, Route53 always query `us-east-1`. Tag discovery makes a second query to `us-east-1` when scanning other regions to catch global services.

---

## 11. Terraform Parsing

### 11.1 .tfstate Parser

**Where:** `cloudwire/app/terraform_parser.py` (839 lines)

Handles Terraform state format v4+. **Two-pass algorithm:**

**Pass 1 -- Node Registration** (lines 330-421):
- Filters `mode == "managed"` and `type.startswith("aws_")` resources
- Looks up `TF_RESOURCE_TYPE_MAP` (137 entries covering ~60 AWS resource types)
- Node ID: prefer real `arn` attribute -> `"{service}:{arn}"`; fallback -> `"terraform:{tf_address}[{index}]"`
- Runs `_redact_sensitive()` before storing
- Builds **7 O(1) secondary index dicts** to avoid N-squared lookups during edge inference:
  - `_vpc_resource_id_to_node`
  - `_s3_bucket_name_to_node`
  - `_ecs_cluster_arn_to_node`
  - `_apigw_id_to_node`
  - `_iam_role_name_to_node`
  - `_eventbridge_rule_name_to_node`
  - `_node_label` (for env var convention matching)

**Pass 2 -- Edge Inference** (lines 448-795):
- 12 type-specific extractors + generic ARN sweep
- Lambda extractor handles: execution role, env var ARNs, env var naming conventions, VPC config, DLQ config
- Step Functions extractor deserializes ASL definition JSON, recursively extracts ARNs
- Generic ARN sweep walks all non-handled attributes for ARN strings
- `_SKIP_GENERIC_ATTRS` frozenset prevents duplicate edges

**Validation** (`validate_tfstate_content`): Valid UTF-8, valid JSON, has `resources` list, version >= 4, max 10,000 resources.

**Security:**
- `_SENSITIVE_EXACT`: `password`, `secret_key`, `private_key`, etc. (14 keys)
- `_SENSITIVE_SUBSTRINGS`: `"password"`, `"secret"`, `"private_key"`, `"token"`, `"credential"`, `"cert"`
- Recursive extraction bounded: `_MAX_ARN_EXTRACT_DEPTH=16`, `_MAX_ARN_EXTRACT_ELEMENTS=2048`

---

### 11.2 .tf HCL Parser

**Where:** `cloudwire/app/hcl_parser.py` (322 lines)

Parses HCL2 source files (not state) using `python-hcl2`.

**Key differences from .tfstate parser:**
- Node IDs are always `terraform:{TYPE}.{NAME}` (no real ARNs)
- Uses regex `_HCL_REF_PATTERN` to find `aws_*.name` patterns:
  ```python
  _HCL_REF_PATTERN = re.compile(
      r"\b((?:aws_[a-z0-9_]+|random_[a-z0-9_]+)\.[a-z_][a-z0-9_]*)\b"
  )
  ```
- `_classify_relationship()` derives labels from type: `aws_iam_role` -> "assumes", `aws_sqs_queue` -> "publishes_to"
- `_unwrap_hcl2()` handles python-hcl2's quirk of wrapping values in single-element lists
- Only stores scalar attrs (no nested dicts)

---

## 12. API Layer & Routes

**File:** `cloudwire/app/main.py` (137 lines)

### Security Middleware (lines 49-57)
Adds to every response: `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, strict CSP, referrer policy.

### Exception Handler Hierarchy (lines 66-104)
1. `APIError` -> structured JSON
2. `HTTPException` -> dict or string details
3. `RequestValidationError` -> Pydantic errors
4. `Exception` -> catch-all with logging

### API Endpoints

| Endpoint | Method | Pattern | Notes |
|----------|--------|---------|-------|
| `/api/scan` | POST | 202 Accepted + job URL | Async; returns `job_id`, `status_url`, `graph_url` |
| `/api/scan/{id}` | GET | Polling | Progress %, current service, node/edge counts |
| `/api/scan/{id}/graph` | GET | Direct data | Graph JSON for specific job |
| `/api/scan/{id}/stop` | POST | 202 Accepted | Cooperative cancellation |
| `/api/graph` | GET | Shortcut | Latest completed scan graph |
| `/api/resource/{id:path}` | GET | Query | Path allows slashes (ARNs) |
| `/api/services` | GET | Config | Service registry for UI |
| `/api/tags/keys` | GET | Query | Tag key enumeration |
| `/api/tags/values?key=` | GET | Query | Tag values for a key |
| `/api/tags/resources?tag_filters=` | GET | Query | JSON-encoded filters |
| `/api/terraform/parse` | POST | Synchronous | Files -> graph inline + job_id |
| `/api/health` | GET | Health check | `{service, status}` |

### Consistent Error Format
```json
{
  "error": {
    "code": "machine_readable_snake_case",
    "message": "Human readable string for display",
    "details": null
  }
}
```

**Caching:** `POST /api/scan` is **idempotent within the TTL** -- same parameters return existing `job_id` with `cached: true`. `force_refresh: true` bypasses cache.

---

## 13. Frontend Architecture

### 13.1 State Management

No external state manager (no Redux, Zustand, Jotai). All state in `CloudWirePage.jsx` via `useState` (17 hooks).

| State Variable | Type | Purpose |
|----------------|------|---------|
| `graphData` | `{nodes, edges, metadata}` | Raw API response |
| `selectedNodeId` | `string|null` | Currently selected node |
| `hiddenServices` | `string[]` | Services filtered out |
| `collapsedServices` | `Set<string>` | Services collapsed to clusters |
| `collapsedContainers` | `Set<string>` | VPC/AZ/subnet collapsed |
| `focusModeActive` | `bool` | Show only N-hop neighborhood |
| `layoutMode` | `string` | `"flow"|"circular"|"swimlane"` |
| `scanFilterMode` | `string` | `"services"|"tags"|"terraform"` |

**Persistence:** `localStorage` for `region` and `selectedServices` only. All other state resets on refresh.

**Auto-collapse** (lines 147-156): Services with >8 resources are automatically collapsed on first scan result. A `hasAutoCollapsed` ref prevents repeated triggering.

---

### 13.2 Graph Pipeline (`useGraphPipeline`)

**Where:** `frontend/src/hooks/useGraphPipeline.js`

An 8-stage pure transformation chain, each stage a `useMemo` with explicit dependency arrays:

```
Raw graphData
  |-> 1. filterGraphByRegion()
  |-> 2. Service visibility filter (hiddenServices)
  |-> 3. partitionByConnectivity() (connected vs isolated)
  |-> 4. buildClusteredGraph() (collapse services >8 nodes)
  |-> 5. collapseContainerNodes() (VPC/AZ/subnet collapse)
  |-> 6. computeFocusSubgraph() (N-hop BFS from selected)
  |-> 7. layoutHybridGraph() | layoutSwimlane() (x,y positions)
  |-> 8. computeNetworkAnnotations() (bounding boxes)
  v
Positioned nodes + edges + annotations -> GraphCanvas (SVG)
```

Every transform is a pure function. React's memoization guarantees only changed stages re-run.

---

### 13.3 Component Structure

```
CloudWirePage.jsx
  |-- TopBar.jsx (region picker, service selector, Scan button)
  |-- TagFilterBar.jsx (tag key/value filter)
  |-- TerraformDropZone.jsx / TerraformFilePanel.jsx
  |-- ServiceSidebar.jsx (service list, counts, cluster toggles)
  |-- GraphCanvas.jsx (SVG canvas, pan/zoom, node drag, minimap)
  |   |-- GraphNode.jsx (per node: icon + label + ring)
  |   |-- GraphEdge.jsx (per edge: path + flow animation)
  |   |-- GraphLegend.jsx
  |   +-- Minimap.jsx
  |-- InspectorPanel.jsx (node detail: attributes, edges)
  |-- LayoutDropdown.jsx (layout mode switcher)
  +-- WarningsPanel.jsx (scan warnings)
```

**Error Boundaries** wrap ServiceSidebar, GraphCanvas, and InspectorPanel individually -- a crash in one panel doesn't crash the app.

**Stale request cancellation:** `resourceRequestTokenRef` (integer incremented on each new selection) prevents old fetches from overwriting new state.

---

## 14. Cross-Cutting Concerns

### 14.1 Error Handling

**Three-tier backend error handling:**

| Tier | Where | Behavior |
|------|-------|----------|
| Per-API-call | Inside mixin methods | `AccessDenied`/`ExpiredToken` -> targeted warning |
| Per-service | `_scan_service()` | `ClientError`/`BotoCoreError`/`Exception` -> warning, scan continues |
| Per-job | `_run_scan_job()` | `ScanCancelledError` -> mark cancelled; `Exception` -> mark failed |

**Error sanitization** (`scanner.py:_sanitize_exc`, lines 43-48): Never leaks AWS account IDs, ARNs, or role names to the API response.

**Frontend:** `parseErrorResponse()` in `lib/api.js` handles structured errors, validation errors, and raw text. Resource errors tracked in separate `resourceError` state (won't overwrite scan errors).

---

### 14.2 Security

| Concern | Mitigation |
|---------|-----------|
| AWS credential leakage | `_sanitize_exc()` strips ARNs/account IDs from errors |
| Terraform secrets | `_redact_sensitive()` strips passwords, tokens, keys |
| XSS / framing | CSP headers, `X-Frame-Options: DENY`, `nosniff` |
| File upload DoS | 25MB/file, 50MB total, 10K resources, depth-limited parsing |
| Rate limiting | 10 terraform parse requests per 60 seconds |
| Network exposure | Localhost-only binding; CLI warns on `0.0.0.0` |
| No authentication | Intentional for local tool; never expose to untrusted networks |

---

### 14.3 Performance

**Thread pool tuning** (`scanner.py` lines 58-68):
```python
max_service_workers: int = 5
apigw_integration_workers: int = 16
dynamodb_describe_workers: int = 16
sqs_attribute_workers: int = 16
iam_workers: int = 8
```

**Adaptive retry** (line 184-189):
```python
Config(
    retries={"mode": "adaptive", "max_attempts": 10},
    max_pool_connections=64,
    connect_timeout=3,
    read_timeout=20,
)
```

**Quick vs Deep mode:** Quick skips IAM inference and resource describes, significantly reducing API calls.

**Frontend memoization:** All graph pipeline stages are `useMemo` -- layout algorithms (O(n log n)) only re-run when inputs change.

---

### 14.4 Concurrency & Thread Safety

- All `GraphStore` mutations hold `_lock` (single `threading.Lock`)
- Each `ScanJob` owns its own `GraphStore` -- no cross-job data races
- `ScanJobStore._lock` protects job dict, cache, and in-flight tracking
- `AWSGraphScanner` per-instance state (caches, indices) -- no sharing between scans
- `_iam_role_cache` has its own `_iam_cache_lock` for fine-grained locking
- `snapshot_graph()` copies under lock, traversal happens outside lock

---

## 15. Design Patterns Used

| Pattern | Where | Notes |
|---------|-------|-------|
| **Mixin Composition** | `scanner.py` + `scanners/*.py` | 21 service mixins merged into AWSGraphScanner via Python MRO |
| **Repository/Store** | `graph_store.py`, `scan_jobs.py` | Thread-safe in-memory stores with explicit lock discipline |
| **Factory Method** | `routes/*.py` `register_routes()` | Routes receive `job_store` dependency via closure |
| **Template Method** (implicit) | Each scanner mixin | All implement `_scan_<service>(session)` called by orchestrator |
| **Strategy** | Layout selection, service dispatch | `layoutHybridGraph` vs `layoutSwimlane`; `service_scanners` dict |
| **Observer** (approximate) | `ScanJobStore.update_progress()` | Progress callback injected into scanner |
| **Two-Pass Parse** | `terraform_parser.py`, `hcl_parser.py` | Pass 1: nodes; Pass 2: edges (allows forward references) |
| **SPA Fallback** | `main.py` lines 126-137 | Catch-all route serves `index.html` after all API routes |
| **Stub/Placeholder** | VPC stub nodes | Lambda creates minimal VPC node, VPC scanner enriches later |

---

## 16. Architectural Trade-offs & Decisions

### Decision 1: Mixin Composition for Scanners
- **Pro:** New services = new file + one import + one dict entry. Zero changes to orchestration.
- **Pro:** Each scanner independently comprehensible.
- **Con:** All 21 mixins share `self` -- a buggy mixin can corrupt shared state.
- **Con:** Python MRO with 21 parents is opaque for debugging.

### Decision 2: NetworkX DiGraph as Graph Backend
- **Pro:** Rich algorithm library (BFS, DFS, topological sort).
- **Pro:** Simple serialization via `nodes(data=True)` / `edges(data=True)`.
- **Con:** Not designed for concurrent access -- all mutations through lock.
- **Note:** NetworkX is an implementation detail hidden behind `GraphStore`.

### Decision 3: In-Memory Job Store (No Persistence)
- **Pro:** Zero deployment complexity (no Redis, no DB).
- **Pro:** TTL-based caching is trivial.
- **Con:** Process restart loses all scan history.
- **Con:** Cannot scale to multiple workers (each has its own `ScanJobStore`).

### Decision 4: Two-Phase VPC Scan
- **Pro:** Only fetches VPCs referenced by scanned resources.
- **Con:** Phase 2 blocked until all Phase 1 futures drain.

### Decision 5: Frontend Graph Pipeline as Pure `useMemo` Chain
- **Pro:** Each stage is pure, independently testable, only re-runs on input change.
- **Con:** Tight dependency array management required -- upstream change cascades through all downstream stages.

### Decision 6: No External State Manager
- **Pro:** Simple, no library overhead, all state visible in one file.
- **Con:** `CloudWirePage.jsx` at 664 lines with 17 `useState` hooks is a God Component.

---

## 17. Conventions & Naming

### Node ID Format
`"{service}:{identifier}"`
- ARN-based: `"lambda:arn:aws:lambda:us-east-1:123456789:function:my-fn"`
- VPC resources: `"vpc:vpc/vpc-abc123"`, `"vpc:subnet/subnet-xyz"`, `"vpc:sg/sg-def456"`
- Terraform without ARN: `"terraform:aws_lambda_function.handler"`

### Edge Relationship Vocabulary
Lowercase strings: `"contains"`, `"protects"`, `"triggers"`, `"invokes"`, `"references"`, `"assumes"`, `"routes_via"`, `"routes"`, `"attached_to"`, `"delivers"`, `"notifies"`, `"integrates"`, `"gateway"`, `"allows"`, `"dead_letter"`, `"publishes_to"`, `"reads_writes"`.

The `via` attribute provides mechanism detail (e.g., `via="lambda_event_source_mapping"`).

### Stub Nodes
Minimal placeholder nodes created by one scanner (e.g., Lambda creates a VPC node with just the VPC ID) to be enriched by another scanner (VPC scanner). Works because `add_node` uses merge semantics.

### Cross-Scanner Node Reconciliation
Deterministic node IDs enable automatic merging:
- When Lambda creates `vpc:subnet/subnet-abc` as a stub
- And VPC scanner later calls `add_node("vpc:subnet/subnet-abc", ...)` with full attributes
- The final node has both the Lambda-assigned edges and VPC-enriched attributes

The `_node_attr_index` provides O(1) lookup by `(service, attr, value)` tuples for cross-scanner matching.

---

## 18. Configuration & Environment

### CLI Arguments
| Argument | Purpose | Default |
|----------|---------|---------|
| `--port` | Server port | 8080 |
| `--host` | Bind address | 127.0.0.1 |
| `--profile` | AWS credentials profile | default |
| `--region` | Default scan region | us-east-1 |

### AWS Credentials
Standard boto3 credential chain: env vars (`AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`), `~/.aws/credentials`, instance profiles.

### Hardcoded Operational Constants
| Constant | Value | Location |
|----------|-------|----------|
| `max_workers` | 4 | `main.py` line 33 |
| `_MAX_RETAINED_TERMINAL_JOBS` | 50 | `scan_jobs.py` |
| `_MAX_IN_FLIGHT_JOBS` | 8 | `scan_jobs.py` |
| `max_service_workers` | 5 | `scanner.py` |
| Quick scan TTL | 300s | `routes/scan.py` |
| Deep scan TTL | 1800s | `routes/scan.py` |
| Terraform TTL | 1800s | `routes/terraform.py` |
| TF max files | 10 | `routes/terraform.py` |
| TF max per file | 25MB | `routes/terraform.py` |
| TF max total | 50MB | `routes/terraform.py` |
| TF max resources | 10,000 | `terraform_parser.py` |
| TF rate limit | 10/60s | `routes/terraform.py` |

### Frontend Persistence
- `localStorage`: `cloudwire_region`, `cloudwire_services` (JSON array)
- Default services exposed via `GET /api/services` (API-driven, no frontend hardcoding)

### Build Configuration
- `pyproject.toml` -- package metadata, dependencies, entry point
- `frontend/vite.config.js` -- `outDir: "../cloudwire/static"` (build into Python package)
- `Makefile` -- `make dev`, `make frontend`, `make build`

---

## 19. Architectural Strengths

1. **Clean graph abstraction** -- `GraphStore` hides NetworkX behind a well-defined interface. Serialization, locking, caching, filtering all encapsulated.

2. **Two-phase VPC scan** -- Elegant solution to "fetch only relevant VPCs". Stub-node mechanism threads the constraint without scanner coordination.

3. **Mixin decomposition** -- 21 focused scanners, each independently comprehensible. Service dispatch dict provides clean registry.

4. **Defense-in-depth security** -- Error sanitization, sensitive data redaction, security headers, read-only AWS calls, localhost binding, bounded parsing.

5. **Frontend pipeline as pure transforms** -- Each stage independently testable, only re-runs on input change.

6. **Generic scanner fallback** -- `_scan_generic_service()` uses tagging API to discover any service without a dedicated scanner. New AWS services work immediately.

7. **No circular dependencies** -- Clean DAG module structure.

---

## 20. Potential Improvements

### Scanner Registration (Medium effort)
Replace 3-location edits with a decorator-based registry:
```python
@register_scanner("lambda")
class LambdaScannerMixin: ...
```

### Extract Network Exposure (Small effort)
Move `_compute_network_exposure()` to standalone `graph_analysis.py` -- independently testable, more visible.

### Extract Route Helpers (Medium effort)
`_run_scan_job()` and `_seed_missing_tag_arns()` are domain operations in `routes/scan.py` -- belong in `scan_orchestrator.py` or `scan_jobs.py`.

### `CloudWirePage.jsx` Decomposition (Large effort)
At 664 lines with 17 `useState` hooks, natural split into:
- `useScanState` -- region, services, scanMode, handlers
- `useViewState` -- hidden/collapsed services, layout mode, focus mode
- `useTerraformState` -- file upload, parsing, results

### Tests
No `tests/` directory exists. High-value targets: `GraphStore` (no AWS deps), Terraform parsers (no mocks needed), layout algorithms (pure functions).

### Rate Limiter Process-Safety (Small effort)
Module-level deque breaks with multiple uvicorn workers. Should document constraint or use injected instance.

---

## 21. Glossary

| Term | Meaning |
|------|---------|
| **Stub node** | Minimal placeholder node created by one scanner to be enriched by another (e.g., VPC node created by Lambda scanner) |
| **node_id** | Unique graph ID: `service:resource`. ARN-based: `service:arn`. VPC: `vpc:type/id`. Terraform: `terraform:address` |
| **Phase 1 / Phase 2** | Two-phase scan: Phase 1 runs non-VPC services in parallel; Phase 2 runs VPC scanner scoped to discovered VPC IDs |
| **cache_key** | Deterministic string: `account_id|region|services|mode`. Avoids re-running identical scans |
| **job_store** | `ScanJobStore` singleton tracking all scan jobs. Created in `main.py` |
| **Mixin** | Python class adding methods via multiple inheritance. Each scanner is a mixin adding `_scan_{service}()` |
| **Relationship** | Edge label: "contains", "triggers", "invokes", "protects", "assumes", etc. |
| **blast radius** | All resources affected if a given resource fails: downstream + upstream via BFS |
| **focus mode** | Show only selected node and its N-hop neighbors |
| **swimlane** | Layout arranging nodes in horizontal bands by role (trigger/queue/processor/storage) |
| **internet exposure** | Resource reachable from Internet via IGW -> RTB -> subnet path with open SG |
| **via** | Edge attribute recording mechanism (e.g., `via="lambda_event_source_mapping"`) |
| **cluster node** | Synthetic node replacing >8 nodes of a service for visual clarity |

---

## 22. FAQ

**Q: Why does the VPC scan run after all other scans (Phase 2)?**
A: VPC topology is expensive. By waiting for Phase 1, the code collects which VPC IDs were actually referenced and only fetches those. See `scanner.py:_collect_referenced_vpc_ids()`.

**Q: Why a separate `GraphStore` per job?**
A: So a new scan doesn't overwrite results the user is still viewing. The frontend can poll the old job while a new one runs.

**Q: What are cluster nodes?**
A: When a service has >8 nodes (`AUTO_COLLAPSE_THRESHOLD`), all nodes collapse to one synthetic node (e.g., "12 lambda"). Edges rewritten to point to the cluster. Un-collapse from sidebar.

**Q: Why duplicate service aliases in Python and JavaScript?**
A: Python aliases route to correct scanner. JS aliases look up color/icon/label. Different purposes but must stay in sync.

**Q: Why do node IDs contain the full ARN?**
A: Enables O(1) lookup by ARN. When EventBridge discovers a target ARN, it creates `service:arn` -- if Lambda already created that ID, the graphs merge automatically.

**Q: How does Lambda env var edge inference work?**
A: Two strategies: (1) If value matches ARN pattern, direct edge. (2) If name matches suffix convention (`_TABLE_NAME`, `_QUEUE_URL`, `_BUCKET`), value matched against existing nodes by label.

**Q: What happens on scan cancellation?**
A: `request_cancel` sets flag. Scanner checks `_is_cancelled()` before every API call. Raises `ScanCancelledError`. Partial graph preserved.

**Q: What is `include_iam_inference`?**
A: Deep mode reads IAM policies attached to Lambda execution roles, adds edges to specific resources the role can access. Doubles IAM API calls. Skipped in quick mode.

**Q: How is the frontend served?**
A: Production: `make frontend` compiles React into `cloudwire/static/`. FastAPI mounts `/assets` as static, serves `index.html` for all other paths (SPA fallback). Development: Vite on port 5173 proxies `/api/*` to backend on port 8000.

---

*Generated by deep analysis from codebase-explainer, software-architect, and senior-developer agents.*
*Cloudwire v0.2.5 | Python 3.9+ | FastAPI + React*
