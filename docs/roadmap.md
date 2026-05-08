# JobRadar — Product Roadmap & Future Vision

> Last updated: May 2026
> Current status: Personal tool (v1, functional)
> Vision: AI-powered job discovery SaaS for Indian freshers and career switchers

---

## Honest Assessment of Where We Are

Before the roadmap, a clear-eyed view of the current state:

**Strengths:**
- Working E2E pipeline (fetch → score → notify) — rare for a solo project
- Differentiated concept: AI-scored + multi-source + Telegram delivery
- Groq integration keeps API costs near-zero
- Profile-driven personalization is the right architecture for multi-user

**Gaps to fix before going public:**
- 2 of 7 sources currently return 0 jobs (Cutshort CSS selectors broken, Instahyre API 404)
- SQLite must be replaced with PostgreSQL for multi-user
- No web interface, no auth, no billing
- HN thread ID requires monthly manual update
- Source reliability is inconsistent — needs health monitoring

**Conclusion:** The foundation is solid. The product is not ready. That's fine — it means there's a clear path.

---

## Phase 0 — Stabilize (Personal Use) ← YOU ARE HERE

**Timeline:** 2–4 weeks
**Goal:** Make the tool run reliably every single day without babysitting

### Tasks
- [ ] Fix Cutshort scraper (CSS selectors are broken — returns 0 jobs)
- [ ] Fix Instahyre (API 404 — find correct endpoint or replace with scraping)
- [ ] Add LinkedIn via RapidAPI (`JSearch` or `LinkedIn Jobs Search` API — ~$10/month)
- [ ] Add Naukri RSS feed or API (biggest source for Indian freshers)
- [ ] Set up EC2 t2.micro with cron job (8 AM IST daily)
- [ ] Add health check alert — if 0 jobs scored for a run, send a Telegram warning
- [ ] Auto-discover HN thread ID monthly (already partially implemented)
- [ ] Add a weekly digest: top 10 jobs of the week, re-surfaced on Sunday

### Success metric
Tool runs 7 days unattended, finds ≥3 relevant jobs/day, zero crashes.

---

## Phase 1 — Backend Foundation (Multi-User Ready)

**Timeline:** 6–10 weeks
**Goal:** Rebuild the core to support multiple users without rewriting everything later

### Architecture shift

```
Current (personal):                 Target (multi-user):
profile.yaml (1 user)      →        PostgreSQL users table (N users)
SQLite (local)             →        PostgreSQL (managed, Render)
python -m main (manual)    →        FastAPI server + job queue (Celery + Redis)
1 cron job                 →        Per-user scheduled jobs
Telegram only              →        Telegram + Email + WhatsApp (extensible)
```

### Tasks

**Database migration**
- [ ] Replace SQLite with PostgreSQL
- [ ] Schema: `users`, `jobs`, `user_jobs`, `run_logs`, `subscriptions`
- [ ] Each user has their own `profile` stored in DB (not YAML)
- [ ] Job deduplication now scoped to user (not global)

**API layer**
- [ ] FastAPI backend: `POST /run` (trigger a run), `GET /jobs` (fetch results)
- [ ] User profile CRUD: `PUT /profile` to update roles, skills, location
- [ ] Run history: `GET /runs` — show past runs with stats
- [ ] Health endpoints for monitoring

**Job queue**
- [ ] Celery + Redis for async job execution (so HTTP requests don't time out)
- [ ] Per-user job locks (prevent duplicate runs)
- [ ] Playwright workers run in isolated containers
- [ ] Rate limiting per user (can't trigger more than 1 run per 6 hours on free tier)

**Deployment**
- [ ] Dockerize the app
- [ ] Deploy on Render.com:
  - Web service (FastAPI)
  - Worker service (Celery — runs the pipeline)
  - PostgreSQL (managed)
  - Redis (managed)
- [ ] Environment secrets via Render dashboard
- [ ] CI/CD: push to main → auto-deploy

### Success metric
One API call triggers a job search for a specific user and delivers results to their Telegram. Second user can run simultaneously without interference.

---

## Phase 2 — Website & User Onboarding

**Timeline:** 6–8 weeks (can overlap with Phase 1)
**Goal:** A beautiful website where users can sign up and configure JobRadar in minutes

### Landing page
- Hero: "Your AI job agent. Runs every morning. Finds what matters."
- Live demo: show a sample Telegram message with a scored job
- How it works: 3-step visual (Connect → Configure → Receive)
- Sources list: logos of all job sources we poll
- Pricing section (see Phase 3)
- Testimonials section (placeholder until you have real users)

**Tech stack recommendation:**
- Next.js 14 (App Router) + Tailwind CSS
- Deployed on Vercel (free, fast, perfect for Next.js)
- Connects to your FastAPI backend via REST

### Onboarding flow
1. User signs up (email + password OR Google OAuth)
2. **Profile wizard** — 5-step form:
   - Step 1: Target roles (multi-select from common options)
   - Step 2: Tech stack (skills I know / learning)
   - Step 3: Location preferences
   - Step 4: Industries (fintech, SaaS, etc.)
   - Step 5: Connect Telegram (bot link + chat ID verification)
3. Trigger first free run immediately — user sees results in 15 minutes
4. Upsell: "Loved it? Set up daily delivery — upgrade to Pro"

### Dashboard (logged-in users)
- Today's jobs: cards with score, company, title, apply button
- Run history: timeline of past runs with job counts
- Profile editor: update skills, roles, location anytime
- Source toggles: enable/disable sources
- Notification settings: time, frequency, platform

### Success metric
A user can go from signup → first Telegram job alert in under 10 minutes with zero technical knowledge.

---

## Phase 3 — Monetization (Freemium + Subscriptions)

**Timeline:** 4–6 weeks (after Phase 2 is live)
**Goal:** Start generating revenue

### Pricing model

| Tier | Price | Limits | Value prop |
|------|-------|--------|-----------|
| **Free** | ₹0 | 3 manual runs/week, Telegram only, last 7 days history | Try it, get hooked |
| **Pro** | ₹299/month | Daily cron at chosen time, Telegram + Email, 90 days history, priority support | Power users |
| **Pro Annual** | ₹2,499/year (30% off) | Everything in Pro | Retention + cash flow |

### What the free tier does NOT include (conversion levers)
- Scheduled daily runs (must manually trigger each time)
- Email delivery
- WhatsApp delivery (future)
- Job history beyond 7 days
- Multiple profiles (e.g., you + a friend sharing one account)
- Priority in the job queue (free tier users wait longer during peak)

### Payment integration
- [ ] Stripe (international) — for future global expansion
- [ ] Razorpay (India-first) — UPI, cards, net banking — much better conversion for Indian users
- [ ] Webhook: payment confirmed → flip `subscription_active` flag in DB → cron enabled
- [ ] Auto-cancel: Razorpay subscription webhook → disable cron on failure

### Notifications for subscription
- 3 days before renewal: "Your Pro subscription renews in 3 days"
- On cancellation: "Your daily runs will stop on [date]. Resubscribe to keep them going."
- On payment failure: "We couldn't process your payment — update your card to avoid losing Pro access"

### Success metric
50 paying users at ₹299/month = ₹14,950/month (~$180). This covers all infrastructure costs and proves willingness to pay.

---

## Phase 4 — Growth & Expansion

**Timeline:** 3–6 months after Pro launch
**Goal:** Scale to 500+ paying users, expand notification channels

### New sources (each adds significant value)
- [ ] **LinkedIn Jobs API** (via RapidAPI) — biggest job board, huge conversion
- [ ] **Naukri.com** — dominant in India, especially for fresher roles
- [ ] **AngelList / Wellfound** — startup jobs, Go-heavy companies
- [ ] **YCombinator job board** — high-quality startup postings
- [ ] **Company Telegram channels** — many Indian startups post jobs in Telegram groups
- [ ] **Twitter/X job posts** — `#hiring` + `#golang` + `#india` monitoring

### New notification channels
- [ ] **Email digest** — beautiful HTML email with top 5 jobs, sent at 8 AM
- [ ] **WhatsApp** (via Twilio or official WhatsApp Business API) — biggest channel in India
- [ ] **Slack** (for team accounts) — companies using JobRadar for their recruiting pipeline
- [ ] **Discord** — developer communities

### AI improvements
- [ ] **Feedback loop**: Telegram inline buttons — "Applied ✅" / "Not relevant ❌"
  - Data trains a user-specific scoring model over time
  - After 20 feedback signals, scoring accuracy improves significantly
- [ ] **Cover letter draft**: for score ≥ 8 jobs, generate a personalized cover letter draft
- [ ] **Company research**: auto-pull Crunchbase/LinkedIn data on the hiring company
- [ ] **Interview prep**: if user marks a job as "Applied", send relevant interview questions

### Team/Enterprise tier (₹999/month for 5 users)
- College placement cells buying for their graduating batch
- Coding bootcamps giving it to their students
- This is where real revenue density comes from

---

## Phase 5 — Platform Vision (12–24 months)

**Timeline:** Long-term
**Goal:** Become the definitive job discovery tool for Indian tech freshers

### The real moat
The scoring model gets better with every user's feedback. By the time you have 10,000 users giving feedback, the AI knows:
- Which job titles are actually worth applying to
- Which companies have good response rates
- Which dork queries surface the best jobs
- Which sources have the highest signal-to-noise ratio

This data is the moat. No other job board has personalized AI scoring with explicit user feedback at this granularity.

### Revenue projections (realistic, not hype)

| Users | Conversion | MRR | Timeline |
|-------|-----------|-----|---------|
| 1,000 registered | 5% paying | ₹14,950 (~$180) | Month 6 after launch |
| 5,000 registered | 7% paying | ₹1,04,650 (~$1,260) | Month 12 |
| 20,000 registered | 8% paying | ₹4,78,400 (~$5,750) | Month 24 |

At 20k registered users + 8% conversion you're looking at ₹4.8L/month — that's a real business.

### Potential exit/partnership paths
- Acquisition by a job board (Naukri, Unstop, Internshala) once you have proven user data
- B2B pivot: sell the scoring engine to companies for their inbound applicant filtering
- White-label for coding bootcamps / college placement cells

---

## Technical Debt to Address Before Scaling

These will kill you at scale if not fixed:

1. **Playwright at scale** — browser automation is memory-hungry. At 100 concurrent users, you need a Playwright pool or a headless browser service (Browserless.io — $30/month for managed browsers)

2. **Per-user API key management** — currently you share one Groq/Serper key. Multi-user needs either:
   - Users bring their own keys (friction, but free infrastructure)
   - You absorb the cost and price accordingly (simpler UX, needs margin)

3. **Source reliability monitoring** — need a dashboard showing which sources are returning 0 jobs and why. Currently you find out through logs after the fact.

4. **GDPR/data compliance** — once you store user job history and profile data, you need a privacy policy, data deletion endpoint, and cookie consent. Not optional in India either (DPDP Act 2023).

5. **Rate limiting and abuse prevention** — free tier users will try to run 50 times/day. Need hard enforcement at the API layer, not just the UI.

---

## Recommended Build Order

```
RIGHT NOW (weeks 1-4):
  Fix broken sources → Add LinkedIn/Naukri → EC2 deployment → Stable personal tool

PHASE 1 (weeks 5-14):
  FastAPI backend → PostgreSQL → Celery workers → Render.com deployment

PHASE 2 (weeks 10-20, overlap):
  Next.js landing page → Profile wizard → Dashboard → Telegram connect flow

PHASE 3 (weeks 18-26):
  Razorpay subscription → Free vs Pro enforcement → Email digest

PHASE 4+ (month 7 onward):
  Growth, new sources, feedback loop, WhatsApp, enterprise
```

---

## Key Advice

1. **Don't build the website before the backend works multi-user.** A beautiful UI on a broken foundation is a waste.

2. **Get 10 real users before writing the subscription code.** Find 10 friends or strangers, give them free access, watch what they do. You'll rewrite the onboarding completely.

3. **Telegram is your distribution.** Most job search SaaS acquire users through SEO. You can acquire through Telegram communities — developer groups, fresher groups, coding bootcamp channels. This is a massive unfair advantage.

4. **Razorpay over Stripe for India.** UPI has 70%+ payment share in India. Stripe's UPI support is limited. Razorpay is the right call.

5. **The pricing is right.** ₹299/month is the sweet spot — cheap enough to not think about it, expensive enough to be sustainable. Test ₹199 vs ₹299 vs ₹399 with your first 100 users.

6. **Your crypto exchange project is a marketing asset.** "Built by a guy who built a full crypto exchange in Go" is a better story than "built by a developer." Use it.

---

*This roadmap is a living document. Revisit and update after each phase completion.*
