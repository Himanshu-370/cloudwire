# CloudWire

<p align="center">
  <a href="https://pypi.org/project/cloudwire/"><img src="https://img.shields.io/pypi/v/cloudwire?color=blue&label=PyPI" alt="PyPI version"></a>
  <a href="https://pypi.org/project/cloudwire/"><img src="https://img.shields.io/pypi/pyversions/cloudwire" alt="Python versions"></a>
  <a href="https://pypi.org/project/cloudwire/"><img src="https://img.shields.io/pypi/dm/cloudwire?color=green" alt="PyPI downloads"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Himanshu-370/cloudwire" alt="MIT License"></a>
</p>

<p align="center">
  <strong>Scan and visualize your AWS infrastructure as an interactive dependency graph — in your browser, in seconds.</strong>
</p>

<p align="center">
  No external services. No data leaves your machine. Read-only AWS access.
</p>

---

CloudWire connects to your AWS account, discovers resources across the services you choose, and draws them as a live interactive graph. Each node is an AWS resource. Each edge is a real relationship — an event trigger, a queue subscription, an API integration, a database connection.

The result is a map of how your infrastructure is actually wired together.

![CloudWire graph visualization](docs/cloudgraph.svg)

---

## Features

- **21 built-in AWS service scanners** — Lambda, API Gateway, SQS, SNS, EventBridge, DynamoDB, EC2, ECS, S3, RDS, Step Functions, Kinesis, IAM, Cognito, CloudFront, Route 53, ElastiCache, Redshift, Glue, AppSync, VPC
- **Tag-based scanning** — discover resources by AWS tags instead of by service
- **Terraform import** — drag and drop `.tfstate` or `.tf` files to visualize infrastructure without AWS credentials
- **VPC network topology** — CloudMapper-style subnet/AZ/SG diagrams with internet exposure detection
- **Three layout modes** — Circular, Flow (left-to-right data flow), and Swimlane (grouped by role)
- **Blast radius highlighting** — see exactly what breaks if a resource goes down
- **Quick and Deep scan modes** — fast list-only or full describe enrichment
- **Real-time progress** — graph builds live as each service scan completes
- **100% local** — runs on `localhost`, credentials never leave your terminal

---

## Installation

```bash
pip install cloudwire
```

**Recommended** — use `pipx` to keep it isolated from your project environments:

```bash
pipx install cloudwire
```

**Requirements:** Python 3.9+, AWS credentials configured

---

## Quick Start

```bash
cloudwire
```

The server starts on `http://localhost:8080` and your browser opens automatically.

1. Select the AWS services you want to scan from the dropdown
2. Choose your AWS region
3. Click **Scan** and watch the graph build in real time

### CLI options

```bash
cloudwire --profile production          # use a named AWS profile
cloudwire --region eu-west-1            # set the default region
cloudwire --port 9000                   # use a different port
cloudwire --no-browser                  # start server without opening browser
```

Full CLI reference: [`docs/USAGE.md`](docs/USAGE.md#cli-reference)

---

## AWS Credentials

CloudWire reads credentials from the standard AWS credential chain — any of these work:

```bash
# AWS SSO
aws sso login --profile my-profile && cloudwire --profile my-profile

# saml2aws
saml2aws login && cloudwire

# aws-vault
aws-vault exec my-profile -- cloudwire

# Standard profile
aws configure --profile staging && cloudwire --profile staging

# Environment variables
export AWS_ACCESS_KEY_ID=... AWS_SECRET_ACCESS_KEY=... && cloudwire
```

---

## Required AWS Permissions

CloudWire needs **read-only** access. The quickest way is to attach the `ReadOnlyAccess` AWS managed policy. For a least-privilege setup, grant only the permissions for the services you scan:

| Service | Permissions |
|---------|-------------|
| All scans | `sts:GetCallerIdentity` |
| Tag-based scanning | `tag:GetResources` |
| Lambda | `lambda:ListFunctions`, `lambda:ListEventSourceMappings` |
| API Gateway | `apigateway:GET` |
| SQS | `sqs:ListQueues`, `sqs:GetQueueAttributes` |
| SNS | `sns:ListTopics`, `sns:ListSubscriptions` |
| EventBridge | `events:ListRules`, `events:ListTargetsByRule` |
| DynamoDB | `dynamodb:ListTables`, `dynamodb:DescribeTable` |
| EC2 | `ec2:DescribeInstances` |
| VPC | `ec2:DescribeVpcs`, `ec2:DescribeSubnets`, `ec2:DescribeSecurityGroups`, `ec2:DescribeInternetGateways`, `ec2:DescribeNatGateways`, `ec2:DescribeRouteTables` |
| ECS | `ecs:ListClusters`, `ecs:ListServices`, `ecs:DescribeServices`, `ecs:DescribeTaskDefinition` |
| S3 | `s3:ListAllMyBuckets`, `s3:GetBucketNotification` |
| RDS | `rds:DescribeDBInstances`, `rds:DescribeDBClusters` |
| Step Functions | `states:ListStateMachines` |
| Kinesis | `kinesis:ListStreams` |
| IAM | `iam:ListRoles`, `iam:ListRolePolicies`, `iam:GetRolePolicy`, `iam:ListAttachedRolePolicies`, `iam:GetPolicy`, `iam:GetPolicyVersion` |
| Cognito | `cognito-idp:ListUserPools` |
| CloudFront | `cloudfront:ListDistributions`, `cloudfront:GetDistribution` |
| Route 53 | `route53:ListHostedZones`, `route53:ListResourceRecordSets` |
| Redshift | `redshift:DescribeClusters` |
| ElastiCache | `elasticache:DescribeCacheClusters` |
| Glue | `glue:ListJobs`, `glue:GetCrawlers`, `glue:GetTriggers` |
| AppSync | `appsync:ListGraphqlApis` |

> Services not in this list (EMR, ELB, KMS, Secrets Manager, etc.) are scanned via the Resource Groups Tagging API (`tag:GetResources`) and only discover tagged resources.

<details>
<summary>Minimal IAM policy JSON</summary>

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": [
        "sts:GetCallerIdentity",
        "tag:GetResources",
        "lambda:ListFunctions",
        "lambda:ListEventSourceMappings",
        "apigateway:GET",
        "sqs:ListQueues",
        "sqs:GetQueueAttributes",
        "sns:ListTopics",
        "sns:ListSubscriptions",
        "events:ListRules",
        "events:ListTargetsByRule",
        "dynamodb:ListTables",
        "dynamodb:DescribeTable",
        "ec2:DescribeInstances",
        "ec2:DescribeVpcs",
        "ec2:DescribeSubnets",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeNatGateways",
        "ec2:DescribeRouteTables",
        "ecs:ListClusters",
        "ecs:ListServices",
        "ecs:DescribeServices",
        "ecs:DescribeTaskDefinition",
        "s3:ListAllMyBuckets",
        "s3:GetBucketNotification",
        "rds:DescribeDBInstances",
        "rds:DescribeDBClusters",
        "states:ListStateMachines",
        "kinesis:ListStreams",
        "iam:ListRoles",
        "iam:ListRolePolicies",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:GetPolicy",
        "iam:GetPolicyVersion",
        "cognito-idp:ListUserPools",
        "cloudfront:ListDistributions",
        "cloudfront:GetDistribution",
        "route53:ListHostedZones",
        "route53:ListResourceRecordSets",
        "redshift:DescribeClusters",
        "elasticache:DescribeCacheClusters",
        "glue:ListJobs",
        "glue:GetCrawlers",
        "glue:GetTriggers",
        "appsync:ListGraphqlApis"
      ],
      "Resource": "*"
    }
  ]
}
```

</details>

---

## Supported Services

| Service | Relationships discovered |
|---------|-------------------------|
| Lambda | → DynamoDB, SQS, S3, Kinesis, ECS via env vars; ← event source mappings |
| API Gateway | → Lambda, Step Functions, SQS, SNS, Kinesis; ← Cognito authorizers |
| SQS | ← Lambda triggers, ← SNS subscriptions, → dead letter queues |
| SNS | → SQS subscriptions, → Lambda subscriptions |
| EventBridge | → Lambda, SQS, Step Functions, and any ARN target |
| DynamoDB | ← Lambda streams, → DynamoDB Streams, → global table replicas |
| EC2 | → VPC, Subnet, Security Group, IAM Instance Profile |
| ECS | → task definitions, → load balancers, → service roles |
| S3 | → Lambda notifications; ← CloudFront origins, ← Glue crawlers |
| RDS | → VPC, Subnet, Security Group |
| Step Functions | ← EventBridge targets, ← API Gateway integrations |
| Kinesis | ← Lambda event sources, ← API Gateway integrations |
| IAM | → Lambda (role-to-function edges), policy-based service inference |
| Cognito | → API Gateway authorizer edges |
| CloudFront | → S3, API Gateway, ALB/ELB, Lambda@Edge |
| Route 53 | → API Gateway, S3, ELB alias targets |
| ElastiCache | ← Lambda env var references |
| Redshift | → VPC, Subnet, Security Group |
| Glue | → S3/DynamoDB crawler targets, → trigger actions |
| AppSync | — |
| VPC Network | Subnets, SGs, IGWs, NAT GWs, route tables + internet exposure detection |
| Everything else | Tagged resources via Resource Groups Tagging API |

---

## Local Development

```bash
git clone https://github.com/Himanshu-370/cloudwire
cd cloudwire

# Install Python package in editable mode + start backend + frontend
make install-dev
make dev
```

This starts the FastAPI backend on `http://localhost:8000` and the Vite frontend on `http://localhost:5173` with hot reload.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full dev guide, project structure, and how to add a new service scanner.

---

## Documentation

| Doc | What's in it |
|-----|-------------|
| [`docs/USAGE.md`](docs/USAGE.md) | Full installation, CLI reference, UI guide, troubleshooting |
| [`docs/FEATURES.md`](docs/FEATURES.md) | Complete feature list with detail on every capability |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Backend and frontend architecture overview |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | Dev setup, project structure, adding a new scanner, PR process |
| [`CHANGELOG.md`](CHANGELOG.md) | Version history |

---

## License

MIT — see [`LICENSE`](LICENSE).
