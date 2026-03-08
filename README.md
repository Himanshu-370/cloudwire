# AWS Flow Visualizer

Fullstack app to scan AWS resources and visualize dependencies as an interactive graph.

## Stack

- Backend: FastAPI + boto3 + networkx
- Frontend: React (Vite) + Tailwind + React Flow

## Features

- AWS graph scan for:
  - API Gateway
  - Lambda
  - SQS
  - EventBridge
  - DynamoDB
- Flexible scanning for other services via generic tagged-resource discovery
- Graph endpoints:
  - `POST /scan` (async job create)
  - `GET /scan/{job_id}` (progress/status)
  - `GET /scan/{job_id}/graph` (partial/final graph)
  - `POST /scan/{job_id}/stop` (cancel running scan)
  - `GET /graph` (latest completed graph)
  - `GET /resource/{id}?job_id=...` (optional job-scoped lookup)
- Frontend:
  - Neon hacker dark theme
  - Interactive graph visualization
  - Search resources
  - Click node details panel
  - Downstream dependency highlighting

## Backend Setup

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

The API expects AWS credentials in your environment (for example via AWS CLI profile, env vars, or instance role).

## Frontend Setup

```bash
cd frontend
npm install
npm run dev
```

Frontend runs on `http://localhost:5173` and calls backend at `http://localhost:8000` by default.

Set a custom backend URL with:

```bash
VITE_API_BASE_URL=http://localhost:8000
```

## API Usage

Start a scan:

```bash
curl -X POST http://localhost:8000/scan \
  -H "Content-Type: application/json" \
  -d '{
    "region":"us-east-1",
    "services":["apigateway","lambda","sqs","eventbridge","dynamodb"],
    "mode":"quick"
  }'
```

Check job status:

```bash
curl http://localhost:8000/scan/<job_id>
```

Get job graph snapshot (partial while running, full when complete):

```bash
curl http://localhost:8000/scan/<job_id>/graph
```

Stop a running scan:

```bash
curl -X POST http://localhost:8000/scan/<job_id>/stop
```

Get one resource scoped to a job:

```bash
curl "http://localhost:8000/resource/lambda:arn%3Aaws%3Alambda%3Aus-east-1%3A123456789012%3Afunction%3Amy-fn?job_id=<job_id>"
```
