# Deployment and integration runbook

Chosen topology: Lovable frontend + Render Python API + Supabase Postgres. No services have been provisioned by this document.

## Why this split

Supabase supplies hosted Postgres; its Edge Functions use a Deno-compatible TypeScript runtime, so they are not the host for the specified FastAPI process. Render documents Python/FastAPI web-service deployment. Lovable supports calling a custom API from the application. Keeping those roles separate preserves the Python source/analysis stack while giving the frontend a small stable interface. Sources: [Supabase Functions](https://supabase.com/docs/guides/functions), [Render FastAPI](https://render.com/docs/deploy-fastapi), [Lovable integrations](https://docs.lovable.dev/integrations/introduction).

Use two repositories: `atlas` backend and `atlas-web` frontend. A team-owned Supabase project belongs to the backend; do not also create a Lovable Cloud database. No frontend Supabase integration is needed for this public read-only demo.

## Environment and secrets

| Setting | Location | Purpose |
|---|---|---|
| `VITE_ATLAS_API_BASE_URL` | Lovable build configuration, public | Actual API base including `/v1` |
| `VITE_ATLAS_DATA_MODE` | Lovable build configuration, public | `mock` or `live` |
| `ATLAS_DATA_MODE` | Render/API environment | Explicit `real` or `synthetic_fixture` |
| `ATLAS_DATABASE_URL` | Render secret, real mode only | TLS Postgres connection for custom read-only role |
| `ATLAS_SNAPSHOT_ID` | Render/API environment, real mode | Complete validated published snapshot ID |
| `ATLAS_ALLOWED_ORIGINS` | Render/API environment | JSON array of exact origins, e.g. `["http://localhost:5173"]` |
| `PORT` | Render platform | Provided listener port; do not hard-code |
| `ATLAS_PUBLISH_DATABASE_URL` | Offline pipeline secret | Separate writer/publisher connection |
| `OPENAI_API_KEY` | Offline pipeline secret only | Extraction/checking, never browser/API |
| `BRIGHTDATA_API_KEY`, `BRIGHTDATA_UNLOCKER_ZONE` | Optional offline capture secrets | Approved URL fetch backend only |

`.env.example` has placeholders, never real values. The provider/budget/source pins live in pipeline config as specified in the master; credentials are loaded privately. Do not print database URLs or keys in logs, tests or handoff files. Public VITE values are visible to all users and must never contain secrets. Changing them requires rebuilding/redeploying the frontend.

## 1. Local and fixture integration first

Backend implements the documented module and commands. Start the API in explicit synthetic fixture mode without a database or model key. Frontend starts in mock mode, then live transport against the local fixture API if its environment can reach it. Hosted Lovable cannot generally reach a developer's localhost; use the deployed fixture API for that test, not a fabricated public URL.

Commit schema/fixtures and run the offline contract checks. A fixture deployment remains visibly synthetic. It proves the interface and deployment, not the real scientific pipeline.

## 2. Create the database

The human/team creates or selects a Supabase project in its own account and provides access through the deployment environment. Do not buy a plan or create billable resources automatically. Apply the backend's reviewed migrations with a migration/admin identity.

Create private `atlas` schema and snapshot-aware tables from the master plan. Keep it outside the Supabase exposed Data API schemas. Revoke public/anon/authenticated access to these tables/schema; verify effective permissions. A custom API role receives schema usage and SELECT only. It receives no write, create, DDL, ownership or bypass-RLS privileges. A separate publisher role gets only publication privileges required by the implementation; migrations use a separate admin identity. Lock down default privileges for future tables. Do not use an owner/admin connection in the API simply because it works.

Validate with the actual API role: SELECT permitted; INSERT/UPDATE/DELETE/CREATE rejected. Validate the publisher can insert a complete transaction but cannot make accidental updates through application publication methods. Enforce immutability in database permissions/constraints/triggers as needed, and test it. Raw caches, model responses, review reasoning and private full text stay outside public projection tables.

Choose the exact connection string from Supabase's current connection panel for the appropriate custom role. Use direct connection when Render networking supports its address family; otherwise use the session pooler for a persistent service needing IPv4. Do not invent hostnames or pooler usernames. Configure TLS using the supported driver options; use certificate verification when supported by the supplied connection configuration. A small bounded application pool (for example 3 connections plus no unbounded overflow) is sufficient for this demo. Do not choose transaction pooling without checking driver/prepared-statement constraints. [Supabase connection guidance](https://supabase.com/docs/guides/database/connecting-to-postgres).

## 3. Build and publish the reviewed snapshot

Run source ingestion/extraction/review/assembly in the controlled pipeline environment with the approved budget. The human reviews the relevant artifacts. Validation must pass before the publisher inserts the complete canonical snapshot transaction. Record the returned full `snap_<sha256>` ID and retain the source/run manifest privately as appropriate.

The API cannot publish. Its startup does not fetch sources, generate outputs, choose a “latest” snapshot or use a writable filesystem as durable storage. Render service files are not the source-cache persistence strategy. Keep raw caches locally or in a separately selected private archive; the minimal demo requires no object-storage integration.

## 4. Deploy the Python service

Backend provides `requirements.txt` with pinned tested dependencies, a supported Python runtime pin, and `render.yaml`. Team connects the backend Git repository to a Render Python web service.

- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn atlas.api.main:app --host 0.0.0.0 --port $PORT`
- Health check path: `/readyz`
- Secret/environment configuration: the API rows from the table above only.
- Begin with one service instance; no worker queue or autoscaling design is needed.

Set `ATLAS_DATA_MODE=real`, the read-only DSN and exact published snapshot for the real service. Readiness means that the pinned snapshot and dependencies were validated, not merely that a TCP port opened. On missing/invalid snapshot or unavailable DB, remain unready and return 503 for product requests. Liveness can still indicate the process is running. [Render health checks](https://render.com/docs/health-checks).

A Render free web service can spin down after 15 idle minutes. Use it for early integration if desired, but decide explicitly whether to choose a non-sleeping plan for judging or accept/test cold-start behavior. This handoff authorizes no purchase. Test the actual setup before the demo; retain a recorded walkthrough and a local fallback. Do not present non-cold latency as cold-start performance. [Render free-service limits](https://render.com/docs/free).

## 5. Connect Lovable

Frontend supplies the exact browser origins used by preview and published builds. Backend sets `ATLAS_ALLOWED_ORIGINS` to those values plus only needed local development origins, then redeploys. Lovable origin means the address bar's app origin, not the editor's general website. Preview origins may differ from the published site; verify in the real browser.

Frontend sets `VITE_ATLAS_DATA_MODE=live` and `VITE_ATLAS_API_BASE_URL=https://<actual-service-host>/v1`, rebuilds and republishes. Request `/meta`, select a returned example context, and complete the journey. Check both success and error CORS behavior. If the request fails, diagnose actual URL, network status, origin and server readiness; do not turn off CORS checks or silently fall back to mock data.

No Authorization header, API key, cookie or Supabase connection string is sent from the browser. The demo data is public; CORS does not prevent non-browser callers. Server bounds and basic rate limiting protect availability, not confidentiality.

## 6. Non-secret handoff records

Backend writes `handoff/backend_ready.json` after actual checks. Placeholder example below is a TEMPLATE, not evidence of deployment:

```json
{
  "contract_version": "1.0.0",
  "contract_sha256": "<checksum of contracts/openapi.json>",
  "api_base_url": "https://<actual-api-host>/v1",
  "openapi_url": "https://<actual-api-host>/openapi.json",
  "readiness_url": "https://<actual-api-host>/readyz",
  "snapshot_id": "<actual served snapshot>",
  "data_mode": "synthetic_fixture",
  "git_commit": "<actual backend commit>",
  "allowed_origins": ["https://<actual-frontend-host>"],
  "example_context_ids": ["<id returned by meta>"],
  "checks_passed": [],
  "known_limitations": []
}
```

Frontend writes or reports `handoff/frontend_ready.json`:

```json
{
  "contract_version": "1.0.0",
  "contract_sha256": "<same checksum>",
  "frontend_url": "https://<actual-frontend-host>",
  "preview_origins": [],
  "published_origin": "https://<actual-frontend-host>",
  "api_base_url": "https://<actual-api-host>/v1",
  "frontend_data_mode": "live",
  "observed_server_data_mode": "synthetic_fixture",
  "observed_snapshot_id": "<actual served snapshot>",
  "git_commit": "<actual frontend commit if available>",
  "checks_passed": [],
  "known_limitations": []
}
```

Do not fill a check as passed before running it. For real release, both observed server mode and backend mode must be real; retain exact source/coverage limits. Human relays these records and genuine blocking questions between the two coding environments. Routine internal progress does not require meetings or a new protocol service.

## Release and rollback

Run the joint acceptance journey in the communication contract. Measure non-cold latency separately and report it rather than promising it. Freeze source/prompt changes, snapshot and service deployments before judging. Footer shows the served snapshot; keep the real-data limitations visible.

To roll back, redeploy a known good backend commit configured with a previously validated immutable snapshot, then reload the frontend/clear caches. Keep the matching frontend commit and contract version. Do not modify a published snapshot in place. No client-wide automatic 409 restart mechanism is needed for this fixed demonstration.
