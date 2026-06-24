# Kognisant Web App — Product Site + Sync Portal

## Core Principle: The API Is Always Optional

**The Kognisant CLI works 100% offline, locally, with zero network requirements.** The web app and its API exist purely as an optional convenience layer for device sync. No auth is needed to use the CLI. No API call is ever required for local operation. The web app cannot block, degrade, or gate any CLI feature.

- User never installs Kognisant from the web app — it's `pip install kognisant`
- User never needs an account to use agents, channels, memory, jobs, or any feature
- `kognisant sync` commands gracefully no-op if user has no account linked
- If the API is down, the CLI continues operating normally with a warning: "Sync unavailable, all local features unaffected"

---

## Purpose

A Next.js web application that serves two roles:

1. **Marketing site** — Sells Kognisant to developers. Explains the product, shows features, provides easy onboarding (install → setup → first chat in under 2 minutes).
2. **Sync portal** — Authenticated dashboard where users manage device transfers, view their linked machines, and trigger encrypted sync of their `.kognisant_core` folder between devices.

The web app does NOT run Kognisant. It's a coordination layer. All AI processing stays local. The web app only handles:
- User auth (Google, GitHub via Firebase Auth)
- Encrypted blob storage for device transfers (Firebase Storage)
- Device registry (which machines are linked)
- Sync state coordination (Firestore)
- Marketing/onboarding content

---

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Framework | Next.js 14+ (App Router) |
| Language | TypeScript |
| Auth | Firebase Auth (Google, GitHub providers) |
| Database | Firebase Firestore |
| Storage | Firebase Storage (encrypted sync blobs) |
| Hosting | Vercel (or Firebase Hosting) |
| Styling | Tailwind CSS |
| Animations | Framer Motion |
| API | Next.js Route Handlers (API routes) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  Web App (Next.js on Vercel)                                         │
├─────────────────────────────────────────────────────────────────────┤
│                                                                       │
│  PUBLIC PAGES (Marketing)           AUTHENTICATED PAGES (Dashboard)   │
│  ─────────────────────────          ──────────────────────────────    │
│  /                  Landing          /dashboard         Device list    │
│  /features          Feature deep     /dashboard/sync    Transfer UI   │
│  /pricing           Free + Pro       /dashboard/devices Manage machines│
│  /docs              Quick start      /dashboard/settings Account      │
│                                      /api/sync/*        Sync API      │
│                                                                       │
└──────────────────────────────────┬──────────────────────────────────┘
                                   │
                    ┌──────────────┼──────────────────┐
                    │              │                  │
                    ▼              ▼                  ▼
          ┌──────────────┐ ┌─────────────┐  ┌──────────────────┐
          │ Firebase Auth │ │  Firestore   │  │ Firebase Storage  │
          │ (Google/GH)   │ │  (metadata)  │  │ (encrypted blobs) │
          └──────────────┘ └─────────────┘  └──────────────────┘
                                   │
                                   │ API calls from CLI
                                   │
          ┌────────────────────────┼────────────────────────┐
          │                        │                        │
          ▼                        ▼                        ▼
┌──────────────────┐   ┌──────────────────┐   ┌──────────────────┐
│ Machine A (laptop)│   │ Machine B (server)│   │ Machine C (phone) │
│ kognisant CLI     │   │ kognisant CLI     │   │ (future mobile)   │
│ .kognisant_core/  │   │ .kognisant_core/  │   │                   │
└──────────────────┘   └──────────────────┘   └──────────────────┘
```

---

## Marketing Site Pages

### Landing Page (`/`)

**Hero section:**
```
Your AI assistant that remembers everything.
Runs on your machine. Zero cloud dependency. Zero monthly fees.

[Get Started — Free] [View on GitHub]
```

**Key messaging (above the fold):**
- "Stop re-explaining your project every session"
- "Autonomous agents that work while you sleep"
- "Your data never leaves your machine"

**Sections:**
1. **Hero** — One-liner + terminal demo GIF/video
2. **Pain points** — 3 cards: "Re-explaining context", "Manual babysitting", "Cloud lock-in"
3. **How it works** — 3-step: Install → Chat → It remembers
4. **Features grid** — Memory, Agents, Channels, Background jobs, Model flexibility
5. **Terminal demo** — Embedded asciinema or video showing a real session
6. **Testimonials/stats** — GitHub stars, contributors, install count
7. **Quick start** — `pip install kognisant && kognisant init && kognisant chat`
8. **Footer** — GitHub, Docs, Discord community link

### Features Page (`/features`)

Deep dives into each major feature with code examples:
- Persistent memory (context.md demo)
- Autonomous agents (PERP swarm demo)
- Channels (remote access + SMM demo)
- Background daemon (cron jobs demo)
- Multi-model support (switch mid-session demo)
- World model (dependency graph visualization)
- Device sync (transfer between machines)

### Pricing Page (`/pricing`)

```
┌─────────────────────────────────────────────────────────────────────┐
│  Kognisant CLI is 100% free. Forever.                                │
│  Sync between devices starts at $1.99/month.                         │
└─────────────────────────────────────────────────────────────────────┘

┌───────────────────┐  ┌───────────────────┐  ┌───────────────────┐
│  Free              │  │  Standard          │  │  Premium           │
│  $0/month          │  │  $1.99/month       │  │  $4.99/month       │
├───────────────────┤  ├───────────────────┤  ├───────────────────┤
│                    │  │                    │  │                    │
│ ✅ Full CLI        │  │ ✅ Everything Free  │  │ ✅ Everything Std  │
│ ✅ All features    │  │ ✅ 5 devices        │  │ ✅ Unlimited devices│
│ ✅ 2 devices       │  │ ✅ Unlimited syncs  │  │ ✅ 100 GB+ per sync│
│ ✅ 5 syncs/month   │  │ ✅ 1 GB per sync   │  │ ✅ 90-day history  │
│ ✅ 50 MB per sync  │  │ ✅ 30-day history  │  │ ✅ 5 backup slots  │
│ ✅ 7-day history   │  │ ✅ 1 backup slot   │  │ ✅ Priority support│
│                    │  │                    │  │                    │
│ [Get Started]      │  │ [Subscribe]        │  │ [Subscribe]        │
└───────────────────┘  └───────────────────┘  └───────────────────┘

  All plans include:
  • End-to-end encryption (server never sees your data)
  • Google & GitHub sign-in
  • Web dashboard
  • Encrypted cloud backup
```

**Tier breakdown:**

| Feature | Free | Standard ($1.99/mo) | Premium ($4.99/mo) |
|---------|------|---------------------|---------------------|
| Linked devices | 2 | 5 | Unlimited |
| Syncs per month | 5 | Unlimited | Unlimited |
| Max blob size per sync | 50 MB | 1 GB | 100 GB |
| Total backup storage | None | 2 GB | 200 GB |
| Bandwidth per month | 500 MB | 10 GB | 100 GB |
| Sync history retention | 7 days | 30 days | 90 days |
| Cloud backup slots | None | 1 (latest) | 5 (rolling) |
| Priority support | ❌ | ❌ | ✅ |

**Why these tiers:**
- **Free** lets users experience the value (2 devices, 5 syncs/mo, 50 MB covers most `.kognisant_core` folders which are typically 100 KB – 5 MB). They hit the limit and upgrade.
- **Standard** is impulse-buy pricing. $1.99 removes all friction. 5 devices covers laptop + desktop + server + 2 more. 1 GB per sync handles everything except massive world models.
- **Premium** is for power users and teams. 100 GB+ handles large session histories, world models, and team-scale shared skills. Backup slots mean you can keep multiple restore points.

The CLI is always free. Agents, channels, memory, daemon, multi-model — all free. The web app monetizes only the sync convenience layer.

### Docs/Quick Start Page (`/docs`)

Interactive onboarding:
```
Step 1: Install
  pip install kognisant

Step 2: Initialize
  kognisant init
  → Creates .kognisant/ in your project

Step 3: Set up a model
  kognisant setup
  → Configure Ollama, Claude, GPT, or any OpenAI-compatible API

Step 4: Chat
  kognisant chat
  → Start talking. It remembers everything.

Step 5 (optional): Link device
  kognisant sync login
  → Links this machine to your web account for device transfers
```

---

## Sync Portal (Authenticated)

### Dashboard (`/dashboard`)

```
Welcome back, @username                          Plan: Standard ($1.99/mo)

Linked Devices (3 of 5):
  ● MacBook Pro (active, last sync: 2h ago)
  ● Ubuntu Server (active, last sync: 1d ago)
  ○ Old Laptop (offline, last seen: 7d ago)

Quick Actions:
  [Transfer to new device]  [Create backup]  [Manage devices]

Sync History (this month: 12 syncs):
  2026-06-24  MacBook → Server  (143 KB, complete)
  2026-06-20  Server → MacBook  (89 KB, complete)

Backup:
  Latest: 2026-06-23 (847 KB)  [Restore]  [Update backup]
```

### Transfer UI (`/dashboard/sync`)

```
Transfer Your Assistant

From: [MacBook Pro ▼]
To:   [Ubuntu Server ▼]

What to transfer:
  ☑ Project memory (context.md, memory-guidelines.md)
  ☑ Model pool configuration
  ☑ Skills and tools
  ☑ Session history
  ☑ Job configurations
  ☑ Channel configs (credentials re-encrypted for target)
  ☐ World model state
  ☐ Telemetry data

[Start Transfer]

Transfer is end-to-end encrypted. The server never sees your data in plaintext.
```

### Device Management (`/dashboard/devices`)

- Link new device (generates a device token)
- Unlink device (revoke access)
- View device details (OS, last active, sync status)
- Set primary device

---

## API Routes (Next.js Route Handlers)

### Auth

```
POST /api/auth/verify
  → Verify Firebase ID token, return user profile

GET /api/auth/device-token
  → Generate a one-time device linking token (6-char alphanumeric, 10min expiry)
```

### Devices

```
GET /api/devices
  → List all linked devices for the authenticated user

POST /api/devices/link
  Body: { device_token, device_name, os, machine_id }
  → Link a new device to the user's account

DELETE /api/devices/:id
  → Unlink a device

PATCH /api/devices/:id/heartbeat
  Body: { last_active, kognisant_version }
  → Update device status (called by CLI periodically)
```

### Sync

```
POST /api/sync/initiate
  Body: { from_device_id, to_device_id, components[] }
  → Create a sync job, return upload URL
  → Enforces plan limits (blob size, monthly sync count, device count)

PUT /api/sync/:job_id/upload
  Body: encrypted blob (multipart)
  → Upload encrypted .kognisant_core archive to Firebase Storage

GET /api/sync/:job_id/status
  → Check sync job status (pending, uploaded, downloaded, complete)

GET /api/sync/:job_id/download
  → Get download URL for the encrypted blob (pre-signed, 1h expiry)

POST /api/sync/:job_id/complete
  Body: { success: true }
  → Mark sync as complete, delete blob from storage

GET /api/sync/history
  → List past sync operations (retention based on plan: 7d/30d/90d)
```

### Subscription

```
GET /api/subscription
  → Current plan, limits, usage this month

POST /api/subscription/upgrade
  Body: { plan: "standard" | "premium" }
  → Upgrade plan (Stripe checkout session)

POST /api/subscription/cancel
  → Cancel subscription (downgrades to free at period end)

GET /api/subscription/portal
  → Stripe customer portal URL (manage payment method, invoices)
```

### Backups

```
POST /api/backups/create
  Body: { device_id, components[] }
  → Create a backup slot (upload URL returned)
  → Enforces plan limits:
    - Slot count (0 Free / 1 Standard / 5 Premium)
    - Per-slot size (same as sync blob limit: 50MB / 1GB / 100GB)
    - Total storage cap: Standard 2 GB total, Premium 200 GB total
  → Rejects if total storage cap would be exceeded

GET /api/backups
  → List backup slots for the user (with sizes + total usage)

GET /api/backups/:id/download
  → Pre-signed download URL (1h expiry)

DELETE /api/backups/:id
  → Delete a backup slot (frees space for a new one)
```

---

## Firestore Schema

```
users/{uid}
  ├── email: string
  ├── displayName: string
  ├── provider: "google" | "github"
  ├── plan: "free" | "standard" | "premium"
  ├── plan_started_at: timestamp
  ├── stripe_customer_id: string | null
  ├── created_at: timestamp
  └── devices_count: number

users/{uid}/devices/{device_id}
  ├── name: string (e.g., "MacBook Pro")
  ├── os: string (e.g., "darwin", "linux")
  ├── machine_id: string (hardware hash)
  ├── kognisant_version: string
  ├── linked_at: timestamp
  ├── last_active: timestamp
  ├── status: "active" | "offline"
  └── public_key: string (for E2E encryption)

users/{uid}/sync_jobs/{job_id}
  ├── from_device: string (device_id)
  ├── to_device: string (device_id)
  ├── components: string[] (what's being synced)
  ├── status: "pending" | "uploaded" | "downloaded" | "complete" | "failed"
  ├── blob_path: string (Firebase Storage path)
  ├── blob_size: number (bytes)
  ├── created_at: timestamp
  ├── completed_at: timestamp | null
  └── error: string | null

users/{uid}/backups/{backup_id}
  ├── device_id: string (source device)
  ├── components: string[]
  ├── blob_path: string (Firebase Storage)
  ├── blob_size: number (bytes)
  ├── created_at: timestamp
  └── expires_at: timestamp (based on plan retention)
```

---

## Security Model

### End-to-End Encryption for Sync

The server NEVER sees plaintext data. The flow:

```
Source Machine                     Server                      Target Machine
─────────────                     ──────                      ──────────────
1. Pack .kognisant_core
   (selected components)
2. Generate random AES-256 key
3. Encrypt archive with AES-256-GCM
4. Encrypt AES key with target
   device's public key (RSA-4096)
5. Upload encrypted blob ──────────▶ Store blob
                                     (opaque bytes,
                                      no decryption key)
6. Notify target device
                                                              7. Download blob
                                                              8. Decrypt AES key with
                                                                 own private key
                                                              9. Decrypt archive
                                                              10. Unpack into .kognisant_core
                                                              11. Confirm completion
                                     Delete blob ◀─────────── 12. Mark complete
```

**Key points:**
- Each device generates an RSA-4096 keypair on first link
- Public key stored in Firestore (for sender to encrypt the AES key)
- Private key stays on device only (never uploaded)
- Server stores opaque encrypted blobs — cannot read them
- Blobs auto-delete after 24h or on completion (whichever first)
- Transfer URLs are pre-signed with 1h expiry

### Authentication

- Firebase Auth with Google and GitHub providers
- All API routes require valid Firebase ID token in `Authorization: Bearer <token>` header
- Device tokens are one-time, 10-minute expiry, 6-character alphanumeric
- Rate limiting: 10 API calls/minute per user (anti-abuse)

### Data Minimization

The web app stores:
- User profile (email, name, provider) — from OAuth
- Device metadata (name, OS, last active) — for the dashboard
- Sync job metadata (status, size, timestamps) — for history
- Encrypted blobs (temporary, deleted after transfer)

It does NOT store:
- Project files or code
- Conversation history in plaintext
- Model API keys
- Any Kognisant-processed data

---

## Onboarding Flow (Web → CLI)

### Step 1: User lands on site

Marketing page sells the product. User clicks "Get Started."

### Step 2: Install instructions

```bash
pip install kognisant
kognisant init
kognisant setup   # Configure model
kognisant chat    # Start using
```

### Step 3: Optional — Create account for sync

```
Want to sync between devices?

[Sign in with Google]  [Sign in with GitHub]
```

### Step 4: Link device

After auth, user gets a device token:
```
Your device token: A7X2K9

Run this on your machine:
  kognisant sync login A7X2K9

Token expires in 10 minutes.
```

### Step 5: CLI links the device

```bash
$ kognisant sync login A7X2K9
  Linking device to your account...
  ✓ Device "MacBook Pro" linked to user@example.com
  
  You can now sync this machine with your other devices:
    kognisant sync push    # Upload to cloud (encrypted)
    kognisant sync pull    # Download from cloud (decrypt locally)
```

---

## Page Designs (Component Structure)

### Landing Page Components

```
<LandingPage>
  <Navbar>            Logo | Features | Pricing | Docs | GitHub | Sign In
  <Hero>              Headline + subline + CTA buttons + terminal demo
  <PainPoints>        3 cards with icons
  <HowItWorks>       3-step numbered flow
  <FeaturesGrid>     6 feature cards with code snippets
  <TerminalDemo>     Embedded video/asciinema
  <QuickStart>       Copy-paste install commands
  <CTA>              Final call-to-action
  <Footer>           Links + GitHub stars badge
</LandingPage>
```

### Dashboard Components

```
<DashboardLayout>
  <Sidebar>           Devices | Sync | Settings | Logout
  <DeviceList>        Cards for each linked machine
  <SyncPanel>         Transfer wizard (from/to/components/confirm)
  <SyncHistory>       Table of past transfers
  <SettingsPanel>     Account, plan, notifications
</DashboardLayout>
```

---

## Implementation Phases

### Phase 1: Marketing Site — 2 weeks

- [ ] Next.js project setup (App Router, TypeScript, Tailwind)
- [ ] Landing page (hero, pain points, features, quick start)
- [ ] Features page
- [ ] Pricing page
- [ ] Docs/quick-start page
- [ ] Responsive design (mobile-first)
- [ ] SEO (meta tags, OpenGraph, structured data)
- [ ] Deploy to Vercel

### Phase 2: Auth + Device Linking — 2 weeks

- [ ] Firebase project setup (Auth, Firestore, Storage)
- [ ] Google + GitHub auth providers
- [ ] Protected dashboard route
- [ ] Device linking flow (generate token → CLI consumes → link)
- [ ] Device list page
- [ ] Device heartbeat API
- [ ] API route middleware (token verification, rate limiting)

### Phase 3: Sync Portal — 3 weeks

- [ ] Sync initiation API
- [ ] Upload endpoint (Firebase Storage, presigned URLs)
- [ ] Download endpoint (presigned, 1h expiry)
- [ ] Sync status tracking (Firestore real-time)
- [ ] Transfer wizard UI (select devices, components, confirm)
- [ ] Sync history page
- [ ] Auto-delete blobs after 24h (Cloud Function or scheduled)
- [ ] E2E encryption documentation in UI

### Phase 4: Polish + Analytics — 1 week

- [ ] Analytics (Plausible or simple Firebase Analytics)
- [ ] Error handling and user feedback
- [ ] Loading states and skeleton screens
- [ ] Email notifications (sync complete, device offline alert)
- [ ] Rate limiting enforcement
- [ ] Security audit

---

## Open Decisions

1. **Annual discount**: Offer $19.99/year for Standard ($3.89 savings) and $49.99/year for Premium ($9.89 savings)?
2. **Free tier sync limit**: 5 syncs/month — is this enough to hook users, or should it be 10?
3. **Mobile app (future)**: The sync portal could become a mobile app with read-only project context. Worth scoping?
4. **Community features**: Shared skills marketplace on the web? Or keep it pure utility?
5. **Self-hosted web option**: Should users be able to self-host the web app with their own Firebase project?
6. **Team plans (future)**: $9.99/mo for 5 seats with shared skills + tools? For small teams sharing Kognisant configurations.

---

## Stripe Integration Spec

### Webhook Handling

The web app listens for Stripe webhooks at `POST /api/webhooks/stripe`:

| Event | Action |
|-------|--------|
| `checkout.session.completed` | Activate plan, update Firestore `users/{uid}.plan` |
| `invoice.payment_succeeded` | Renewal confirmed, extend `plan_expires_at` |
| `invoice.payment_failed` | Enter grace period (3 days), notify user via email |
| `customer.subscription.deleted` | Downgrade to free at period end |
| `customer.subscription.updated` | Handle mid-cycle upgrade/downgrade proration |

**Webhook security**: Verify Stripe signature using `stripe-signature` header + webhook secret. Reject unverified payloads.

### Payment Failure Grace Period

```
Day 0: Payment fails → Stripe retries automatically (3 attempts over 7 days)
Day 0: User notified: "Payment failed. Update your card to keep Standard/Premium."
Day 3: If still failing → plan features restricted (sync disabled, read-only dashboard)
Day 7: Final retry fails → downgrade to Free
       → Keep data (don't delete backups/devices)
       → But enforce Free limits on next sync attempt
```

### Mid-Cycle Upgrade/Downgrade

- **Upgrade (Free → Standard, Standard → Premium)**: Immediate. Prorate the remaining billing cycle. New limits apply instantly.
- **Downgrade (Premium → Standard, Standard → Free)**: Takes effect at end of current billing period. User retains current plan until period expires. On downgrade:
  - Excess devices: user must manually unlink extras before next billing cycle, or oldest devices are auto-unlinked
  - Excess backups: oldest backups beyond new limit are deleted 7 days after downgrade takes effect (with email warning)
  - Excess blob size: no existing syncs affected, but new syncs enforce new limit

### Account Linking (Firebase UID → Stripe Customer)

- On first subscription creation, a Stripe customer is created and `stripe_customer_id` stored in Firestore
- If user authenticates with multiple providers (Google + GitHub), Firebase Auth handles account linking — both providers point to the same Firebase UID
- **Rule**: One Firebase UID = one Stripe customer. Enforce at `POST /api/subscription/upgrade`:
  ```
  if user already has stripe_customer_id → use existing
  else → create new Stripe customer with user.email
  ```
- If a user signs in with a different email that maps to a new Firebase UID → they are a separate user. No cross-account subscription sharing.

---

## Downgrade Flow

When a user downgrades, enforce limits gracefully:

### Premium → Standard

| Resource | Premium Limit | Standard Limit | Resolution |
|----------|--------------|----------------|-----------|
| Devices | Unlimited | 5 | User has 7 days to unlink extras. After 7 days, oldest devices auto-unlinked with email notice. |
| Backups | 5 slots | 1 slot | Oldest 4 backups scheduled for deletion in 7 days. User notified to download if needed. |
| Blob size | 100 GB | 1 GB | No change to existing data. Next sync enforces 1 GB. |
| History | 90 days | 30 days | Records older than 30 days pruned after downgrade takes effect. |

### Standard → Free

| Resource | Standard Limit | Free Limit | Resolution |
|----------|---------------|-----------|-----------|
| Devices | 5 | 2 | User has 7 days to unlink extras. After 7 days, oldest auto-unlinked. |
| Backups | 1 slot | 0 | Backup deleted 7 days after downgrade. User notified. |
| Syncs/month | Unlimited | 5 | Enforced immediately on next billing cycle. |
| Blob size | 1 GB | 50 MB | Enforced on next sync. |
| History | 30 days | 7 days | Old records pruned. |

---

## Cost Ceiling Analysis

Firebase costs per user at maximum usage within plan limits:

### Standard ($1.99/mo) — Worst Case

```
User syncs 1 GB blob every day for 30 days:
  Storage:  1 GB × 2 hours avg × 30 days × $0.0002084/GB/hr = $0.013
  Uploads:  30 × 1 GB uploads = 30 GB bandwidth ≈ $3.60 (Firebase Storage egress)
  Firestore: 30 syncs × ~5 operations each = 150 writes = ~$0.00
  
  Total Firebase cost: ~$3.61/month
  Revenue: $1.99/month
  LOSS: -$1.62/month per extreme user
```

**Mitigation**: The 1 GB limit per sync blob is not the concern — it's the **bandwidth** from repeated large syncs. Fix:

1. **Bandwidth cap per tier**: Free 500 MB/mo, Standard 10 GB/mo, Premium 100 GB/mo. Exceeding = sync disabled until next month with clear message.
2. **Total backup storage cap**: Standard 2 GB total, Premium 200 GB total. Prevents unbounded storage cost growth.
3. **Blob deduplication**: If the same content is pushed twice (SHA-256 hash match), skip the upload and reuse existing blob.

### Realistic Standard User

```
Typical dev syncs 500 KB every few days:
  Storage:  0.5 MB × 2 hours × 10 syncs/month = negligible
  Bandwidth: 10 × 0.5 MB = 5 MB/month = negligible  
  Firestore: 10 syncs × 5 ops = 50 operations = negligible
  
  Total Firebase cost: < $0.01/month
  Revenue: $1.99/month
  PROFIT: ~$1.98/month
```

**Conclusion**: Vast majority of users are profitable. Add bandwidth cap (10 GB/mo Standard, 100 GB/mo Premium) to protect against the outlier who syncs 1 GB daily.

---

## Rate Limiting

### API Rate Limits

| Endpoint Group | Limit | Window | Response on Exceed |
|---------------|-------|--------|-------------------|
| Auth endpoints | 5 req | 1 min | 429 + `Retry-After: 60` |
| Device endpoints | 20 req | 1 min | 429 + `Retry-After: 30` |
| Sync endpoints | 10 req | 1 min | 429 + `Retry-After: 30` |
| Subscription endpoints | 5 req | 1 min | 429 + `Retry-After: 60` |
| Heartbeat | 2 req | 1 min | 429 (silent, daemon retries) |

### Implementation

```typescript
// middleware/rateLimit.ts
import { Ratelimit } from "@upstash/ratelimit";
import { Redis } from "@upstash/redis";

const ratelimit = new Ratelimit({
  redis: Redis.fromEnv(),
  limiter: Ratelimit.slidingWindow(10, "60 s"),
  analytics: true,
});

// Per-user rate limiting keyed by Firebase UID
export async function rateLimitMiddleware(uid: string, group: string) {
  const { success, limit, remaining, reset } = await ratelimit.limit(`${group}:${uid}`);
  if (!success) {
    return new Response("Too Many Requests", {
      status: 429,
      headers: {
        "Retry-After": String(Math.ceil((reset - Date.now()) / 1000)),
        "X-RateLimit-Limit": String(limit),
        "X-RateLimit-Remaining": "0",
      },
    });
  }
  return null; // Proceed
}
```

### CLI-Side Retry Logic

When CLI receives 429:
```python
def _api_call_with_retry(url, data, max_retries=3):
    for attempt in range(max_retries):
        response = urllib.request.urlopen(req)
        if response.status == 429:
            retry_after = int(response.headers.get("Retry-After", "30"))
            logger.warning("Rate limited, retrying in %ds", retry_after)
            time.sleep(retry_after)
            continue
        return response
    raise SyncError("API rate limit exceeded after retries")
```

---

## Onboarding Handoff (Web → CLI)

### The Flow for New Users

```
1. User discovers Kognisant (search, GitHub, word of mouth)
       ↓
2. Lands on kognisant.dev (marketing site)
       ↓
3. Reads features, sees quick start
       ↓
4. Clicks "Get Started" → /docs page with install instructions
       ↓
5. Installs CLI: pip install kognisant
       ↓
6. Uses locally: kognisant init → kognisant chat
       ↓ (days/weeks later, wants to use on second machine)
       ↓
7. Returns to kognisant.dev, clicks "Sign In"
       ↓
8. Creates account (Google/GitHub), sees dashboard
       ↓
9. Gets device token (6-char, 10-min expiry)
       ↓
10. Runs: kognisant sync login A7X2K9
       ↓
11. Device linked. User can now push/pull.
```

**Key**: Steps 1-6 happen WITHOUT any web account. The user experiences full value before ever signing up. The web app is discovered organically when they need sync.

### CLI Detection Page

After login, the dashboard shows:

```
Link Your Device

1. Make sure Kognisant is installed:
   $ pip install kognisant
   $ kognisant --version
   
   ✓ Detected: kognisant v0.1.0    ← (this is just instructional, not actual detection)

2. Run this command:
   $ kognisant sync login A7X2K9
   
   Token expires in 9:42

3. After linking, you'll see your device appear below.

Waiting for device... ⏳
```

When the device links (CLI calls POST /api/devices/link), the page updates in real-time (Firestore onSnapshot) to show:

```
✓ Device "MacBook Pro" linked!

[Go to Dashboard]
```

---

## Error & Offline States

### Dashboard Error States

| Scenario | UI |
|----------|-----|
| Device offline >24h | Grey card: "○ MacBook Pro — offline (last seen: 2d ago)" + "Remove device?" link |
| Device offline >30d | Warning banner: "MacBook Pro hasn't been seen in 30 days. Unlink?" |
| Sync upload failed | Red badge: "⚠ Sync failed (upload error). [Retry] [Dismiss]" |
| Sync download expired | Yellow badge: "Sync expired (not pulled within 24h). Push again from source device." |
| Storage quota near limit | Info bar: "Approaching bandwidth limit (8.2/10 GB this month)" |
| Payment failed (grace) | Red banner: "Payment failed. Update card within 3 days to keep your plan. [Update Payment]" |
| API down | Toast: "Sync service temporarily unavailable. Local features unaffected." |

### CLI Error Handling

```bash
# API unreachable
$ kognisant sync push
  ⚠ Cannot reach kognisant.dev (connection timeout).
  All local features continue to work normally.
  Retry with: kognisant sync push

# Account not linked
$ kognisant sync push
  You haven't linked this device to an account yet.
  To set up sync: kognisant sync login <token>
  Get a token at: https://kognisant.dev/dashboard/devices
  
  This does not affect any local features.

# Plan limit hit
$ kognisant sync push
  ⚠ Sync size (120 MB) exceeds your Free plan limit (50 MB).
  Options:
    1. Exclude large components: kognisant sync push --exclude history,world_model
    2. Upgrade to Standard ($1.99/mo): https://kognisant.dev/dashboard/settings
  
  All local features continue to work normally.
```

---

## GDPR / Account Deletion

### `DELETE /api/account`

User requests account deletion from dashboard Settings page:

```
1. User clicks "Delete Account" → confirmation modal
2. User types "DELETE" to confirm
3. API triggers cascading cleanup:
   a. Cancel Stripe subscription (if active)
   b. Delete all sync blobs from Firebase Storage
   c. Delete all Firestore documents:
      - users/{uid}
      - users/{uid}/devices/*
      - users/{uid}/sync_jobs/*
      - users/{uid}/backups/*
   d. Delete Firebase Auth account
   e. Return confirmation
4. CLI-side: sync_config.json becomes stale
   → Next heartbeat gets 401 → CLI auto-removes sync_config.json
   → All local data (.kognisant_core) is UNAFFECTED
```

**Data deletion timeline**: Immediate for Firestore docs and Storage blobs. Stripe data retained per Stripe's own retention policy (for legal/tax compliance). Firebase Auth deleted within 24h.

**Important**: Account deletion NEVER touches local `.kognisant_core` data. The CLI continues working exactly as before — just without sync capability.

---

## Monitoring & Observability

### Health Check

```
GET /api/health
  → { "status": "ok", "firebase": "connected", "stripe": "connected", "timestamp": "..." }
  → No auth required (public endpoint for uptime monitors)
```

### Alerting (via Vercel + Firebase)

| Trigger | Alert | Channel |
|---------|-------|---------|
| API error rate >5% for 5min | Critical | PagerDuty/email |
| Stripe webhook failures >3 consecutive | High | Email to admin |
| Firebase Storage >80% quota | Warning | Email to admin |
| Sync job stuck in "pending" >2h | Medium | Log + auto-cleanup |
| User reports via /api/feedback | Low | Discord webhook |

### Logging

- All API route handlers log: `{ uid, endpoint, method, status, latency_ms, error? }`
- Vercel function logs (auto-collected)
- Stripe webhook logs (Stripe dashboard)
- Firebase usage dashboard (reads/writes/storage)
