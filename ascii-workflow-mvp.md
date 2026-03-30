# ASCII Workflow MVP

This document captures the phase-1 realtime architecture for a `1-5 user` deployment.

Assumptions:
- One user maps to one Alpaca account.
- Two users never share the same broker account.
- Market data is shared globally.
- Account state is isolated per user.
- API is stateless.
- Worker is the only execution path.
- Redis is used as a fast shared cache for realtime market data.
- DB is the durable source of truth.

## Components

```text
[Browser]  user dashboard
[API]      auth + reads + client realtime channel
[Worker]   per-user account sync + strategy + execution
[Streamer] shared market-data ingestion
[Redis]    latest quotes / pubsub / fast cache
[DB]       durable user/account/trade state
[Alpaca]   market data + trading/account APIs
```

## System Topology

```text
                  +----------------------+
                  |      Browser         |
                  +----------+-----------+
                             |
                       HTTPS / WS / SSE
                             |
                  +----------v-----------+
                  |         API          |
                  +-----+-----------+----+
                        |           |
                        |           |
                  +-----v--+    +---v----+
                  |   DB    |    | Redis |
                  +-----+---+    +---+---+
                        ^            ^
                        |            |
                 +------+----+  +----+------+
                 |  Worker   |  | Streamer  |
                 +------+----+  +----+------+
                        |            |
                        +------+-----+
                               |
                            +--v---+
                            |Alpaca|
                            +------+
```

## Scenario 1: User logs in and loads dashboard

```text
Browser            API               DB              Redis
   |                |                 |                |
1. |--POST /login-->|                 |                |
2. |<--JWT----------|                 |                |
3. |--GET /me------>|--read user----->|                |
4. |<--profile------|                 |                |
5. |--GET dashboard>|--read acct----->|                |
6. |                |--read positions->|                |
7. |                |--read trades---->|                |
8. |                |--read snapshot-->|                |
9. |                |--read quotes--------------------->|
10.|<--initial page-|                 |                |
11.|--connect WS/SSE>|                |                |
12.|<--live stream---|                |                |
```

## Scenario 2: Two users want same ticker

```text
User A wants AAPL -----\
                        \
User B wants AAPL -------> [symbol union] -> AAPL subscribed once
                        /
User C wants AAPL -----/

Streamer -> Redis quote:AAPL
              |
              +--> API pushes AAPL to User A
              +--> API pushes AAPL to User B
              +--> API pushes AAPL to User C
              +--> Worker reads AAPL for all users holding/watching it
```

## Scenario 3: Shared market-data stream for many users

```text
Alpaca Market WS
      |
      v
  [Streamer]
      |
      +--> normalize quote/bar
      |
      +--> write Redis latest quote
      |
      +--> publish "AAPL updated"
                  |
                  +--> API realtime fanout
                  +--> Worker reads latest values
```

## Scenario 4: Live price update reaches active users

```text
Alpaca           Streamer          Redis            API            Browsers
  |                 |                |               |                |
1 |--AAPL tick----->|                |               |                |
2 |                 |--SET quote---->|               |                |
3 |                 |--PUBLISH------>|               |                |
4 |                 |                |--notify------>|                |
5 |                 |                |               |--push A------->|
6 |                 |                |               |--push B------->|
7 |                 |                |               |--push C------->|
```

## Scenario 5: Worker loop uses shared quote cache

```text
Worker                 Redis                  DB                 Alpaca Trading
  |                      |                    |                       |
1 |--load active users----------------------->|                       |
2 |<--users/accounts--------------------------|                       |
3 |--GET quote:AAPL------>|                   |                       |
4 |<--latest quote--------|                   |                       |
5 |--read user settings---------------------->|                       |
6 |<--risk/strategy---------------------------|                       |
7 | evaluate decision     |                   |                       |
8 |--if order needed----------------------------------------------->|
9 |<--order accepted------------------------------------------------|
10|--persist order/tracking------------------>|                       |
```

## Scenario 6: User watches dashboard while worker updates account

```text
Worker                 DB                   API                 Browser
  |                    |                     |                    |
1 |--sync account----->|                     |                    |
2 |--update snapshot-->|                     |                    |
3 |--update positions->|                     |                    |
4 |--update orders---->|                     |                    |
5 |                    |--change observed--->|                    |
6 |                    |                     |--push update------>|
7 |                    |                     |<--UI refreshes-----|
```

## Scenario 7: Order is submitted

```text
Browser          API            Worker            Alpaca            DB
  |               |               |                 |               |
1 |--toggle trade>|               |                 |               |
2 |               |--save intent->|                 |               |
3 |               |               |--submit order-->|               |
4 |               |               |<--accepted------|               |
5 |               |               |--write order------------------->|
6 |<--pending-----|               |                 |               |
```

## Scenario 8: Order fill/update arrives

```text
Alpaca Trading Stream      Worker / Event Consumer         DB            API         Browser
         |                           |                      |             |             |
1        |--trade_update------------>|                      |             |             |
2        |                           |--update order------->|             |             |
3        |                           |--update position---->|             |             |
4        |                           |--update snapshot---->|             |             |
5        |                           |                      |--notify---->|             |
6        |                           |                      |             |--push------>|
```

## Scenario 9: User refreshes page mid-session

```text
Browser             API                DB              Redis
   |                 |                  |                |
1. |--reload-------->|                  |                |
2. |--GET snapshot-->|--read latest---->|                |
3. |--GET positions->|--read latest---->|                |
4. |--GET trades---->|--read latest---->|                |
5. |--GET quotes---->|---------------------------------->|
6. |<--page rebuilt--|                  |                |
7. |--reconnect live>|                  |                |
8. |<--resume stream-|                  |                |
```

## Scenario 10: User logs out

```text
Browser            API             DB
   |                |               |
1. |--logout------->|               |
2. | clear token    |               |
3. | disconnect live|               |
```

Important:

```text
Logout does NOT stop:
- worker
- account sync
- market-data stream
- DB persistence
```

## Scenario 11: Streamer disconnects from Alpaca

```text
Alpaca WS X----disconnect----X Streamer
                               |
                               +--> reconnect loop starts
                               +--> mark feed stale in Redis
                               +--> API pushes "data delayed"
                               +--> Worker may pause trade decisions if stale
```

```text
[Alpaca] --X--> [Streamer]
                 |
                 +--> Redis: feed_status = STALE
                 +--> API -> Browser: "quotes delayed"
                 +--> Worker: skip trading if quote age > threshold
```

## Scenario 12: Worker crashes and comes back

```text
Worker dies
  |
  X

State remains in DB:
- users
- linked accounts
- positions
- orders
- snapshots
- strategy settings

New worker starts
  |
  +--> load active users from DB
  +--> read latest quotes from Redis
  +--> resync account state from Alpaca
  +--> continue
```

## Scenario 13: Multiple users active, different tickers

```text
User A symbols: AAPL, NVDA
User B symbols: TSLA, SPY
User C symbols: NVDA, QQQ

Union = AAPL, NVDA, TSLA, SPY, QQQ

Streamer subscribes once to:
AAPL NVDA TSLA SPY QQQ

Redis stores:
quote:AAPL
quote:NVDA
quote:TSLA
quote:SPY
quote:QQQ
```

## Scenario 14: Multiple users active, same ticker, different positions

```text
Shared market data:
AAPL quote = 220.15

User A:
- owns 10 shares
- avg price 200

User B:
- owns 2 shares
- avg price 230

Same quote, different account state:
- User A unrealized PnL = +201.5
- User B unrealized PnL = -19.7
```

```text
             quote:AAPL = 220.15
                    |
        +-----------+-----------+
        |                       |
        v                       v
  User A position          User B position
  qty 10 @ 200             qty 2 @ 230
        |                       |
        v                       v
   PnL for A only          PnL for B only
```

## Scenario 15: User changes settings

```text
Browser              API                 DB                Worker
  |                   |                   |                  |
1 |--save settings--->|--write----------->|                  |
2 |<--ok--------------|                   |                  |
3 |                   |                   |--next loop reads>|
4 |                   |                   |<--new config-----|
```

This applies only to that user's account settings.

## Scenario 16: API serves stale-safe page

```text
API reads:
- DB for durable account state
- Redis for live quotes

If Redis quote is fresh:
  show live quote

If Redis quote is stale:
  show last quote + stale indicator

If Redis unavailable:
  page still loads from DB
```

```text
            +--> Redis fresh --> show live
API request-|
            +--> Redis stale --> show stale badge
            |
            +--> Redis down --> fallback to DB-only page
```

## Scenario 17: End-to-end realtime summary

```text
                 MARKET DATA PATH
Alpaca Market WS -> Streamer -> Redis -> API -> Browser

                 ACCOUNT STATE PATH
Alpaca Trading/API -> Worker -> DB -> API -> Browser

                 STRATEGY PATH
Redis quotes + DB settings + Alpaca account sync -> Worker -> orders -> DB
```

## Most Important Principle

```text
Shared:
- market quotes
- market bars
- symbol subscriptions

Per-user:
- credentials
- account snapshots
- positions
- orders
- trades
- risk settings
- strategy settings
```

## Worker Implementation

The worker is a long-running backend process responsible for:
- loading active users/accounts from DB
- reading latest market data from Redis
- syncing broker account state from Alpaca
- persisting snapshots, positions, orders, and trades
- evaluating strategy and risk rules
- placing orders when allowed
- recovering safely after crashes or partial failures

### Worker Role

```text
API       = user-facing web service
Streamer  = shared market-data service
Worker    = account/trading engine
DB        = durable state
Redis     = live quote cache
```

### Worker Main Loop

```text
start worker
   |
   +--> load active users/accounts from DB
   |
   +--> reconcile each account with Alpaca
   |
   +--> loop forever:
           1. load active users/accounts
           2. load latest quotes from Redis
           3. sync Alpaca account state
           4. persist snapshots / orders / positions
           5. evaluate strategy + risk
           6. place orders if needed
           7. sleep for N seconds
```

### Worker Architecture

```text
                    +----------------------+
                    |      Worker          |
                    |  long-running proc   |
                    +----------+-----------+
                               |
         +---------------------+----------------------+
         |                     |                      |
         v                     v                      v
  account sync          strategy engine        order execution
  from Alpaca           using Redis quotes     to Alpaca
         |                     |                      |
         +---------------------+----------------------+
                               |
                               v
                              DB
```

### Per-User Processing Model

```text
Worker loop
   |
   +--> for each active user/account:
           |
           +--> fetch current Alpaca account state
           +--> fetch open orders
           +--> fetch positions
           +--> reconcile DB with Alpaca truth
           +--> read latest quotes from Redis
           +--> read user/account settings from DB
           +--> evaluate strategy
           +--> if allowed, submit order
           +--> persist results
```

### Recommended Worker Boundaries

```text
Worker should own:
- account synchronization
- strategy evaluation
- risk evaluation
- order submission
- trade/order reconciliation
- snapshot persistence

Worker should not own:
- user login
- browser sessions
- frontend rendering
- direct browser websocket fanout
```

### Worker Crash Cases

#### Case 1: Worker dies before writing anything

```text
Worker -> reads quote/account state
Worker dies
```

Effect:
- nothing persisted
- next worker run retries safely

#### Case 2: Worker dies after partial DB update

```text
Worker -> update snapshot
Worker -> update positions
Worker dies before finishing cycle
```

Effect:
- DB may contain an incomplete cycle

Mitigation:
- use DB transactions for each logical update unit
- commit only consistent groups
- reconcile from Alpaca on restart

#### Case 3: Worker dies after order submission but before DB write

```text
Worker -> submit order to Alpaca
Worker dies before saving order locally
```

Effect:
- Alpaca has the order
- DB does not yet know about it

Mitigation:
- reconcile open orders on restart
- use `client_order_id`
- rebuild local order state from Alpaca truth

#### Case 4: Worker dies after DB write but before UI notification

```text
Worker -> DB updated
Worker dies before API/browser push
```

Effect:
- UI may miss the live event
- page refresh still shows correct durable state

### Worker Restart Recovery

```text
Worker dies
   |
   X

Supervisor restarts worker
   |
   v
Load active users from DB
   |
   v
For each user:
  fetch Alpaca account
  fetch Alpaca open orders
  fetch Alpaca positions
  compare with DB
  repair DB if needed
  resume normal loop
```

### Reconciliation Principle

```text
Broker truth     = Alpaca account/orders/positions
App durable state = DB
Worker memory     = disposable
```

This means:
- worker memory is never the recovery source
- Alpaca is used to restore account/order truth
- DB is repaired during startup or periodic reconciliation

### Safe Order Submission Pattern

```text
Worker
  |
  +--> generate client_order_id
  |
  +--> submit order to Alpaca
  |
  +--> persist order row with client_order_id
  |
  +--> consume trade updates or reconcile later
```

If the worker crashes after submission:

```text
restart worker
   |
   +--> fetch Alpaca open orders
   +--> find order by client_order_id
   +--> insert/repair missing DB row
```

### Transaction Model

Recommended:

```text
transaction group A:
- account snapshot
- positions sync

transaction group B:
- order persistence
- trade persistence

transaction group C:
- strategy checkpoint / worker heartbeat
```

Avoid:
- one giant transaction across all users
- holding transactions open across network calls to Alpaca

### Worker Health Model

Persist lightweight worker health fields:
- `last_worker_heartbeat`
- `last_reconciled_at`
- `last_quote_age_ms`
- `last_successful_sync_at`
- `sync_status`

ASCII:

```text
Worker ----writes heartbeat/status----> DB
   |
   +--> API/admin dashboard can display worker health
```

### Worker Process Model for Phase 1

For `1-5 users`, run exactly one worker process/container.

```text
+-------------------+
| 1 Worker Process  |
| handles all users |
+-------------------+
```

This avoids:
- distributed locks
- duplicate order execution
- multi-worker race conditions

### Worker and Redis Interaction

```text
Redis stores:
- latest quotes
- feed freshness timestamps
- optional pub/sub events

Worker reads:
- quote:AAPL
- quote:SPY
- feed_status
- last_update_ts
```

If quote age exceeds threshold:

```text
Worker -> mark data stale
Worker -> skip trading decision
Worker -> persist reason in DB/logs
```

### Worker and DB Interaction

```text
DB stores durable state:
- users
- broker accounts
- user settings
- account settings
- snapshots
- positions
- orders
- trades
- audit trail
```

The worker must continuously write durable state, not wait for logout.

### Implementation Guidance

Suggested structure:

```text
src/worker/main.py
src/worker/loop.py
src/worker/reconcile.py
src/worker/orders.py
src/worker/sync.py
src/worker/health.py
```

Suggested startup flow:

```text
main.py
  |
  +--> init DB
  +--> init Redis
  +--> init Alpaca clients
  +--> run startup reconciliation
  +--> enter loop
```

### End-to-End Failure-Safe Model

```text
Redis failure:
- worker can fall back to DB or skip trading

DB failure:
- worker should stop making trade decisions

Alpaca failure:
- worker records sync failure and retries

Worker crash:
- restart and reconcile
```

### Summary

```text
The worker should be:
- long-running
- restart-safe
- idempotent
- reconciliation-driven
- account-isolated per user
- independent from browser login/logout
```
