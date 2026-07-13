# Cloud Infrastructure Plan

## Phase A vs Phase B

| Phase | Where Gateway runs | Status |
| --- | --- | --- |
| **A — Local experiments (now)** | Your Mac | Active |
| **B — Cloud production (later)** | AU VPS Docker | After local paper works |

**End state (Phase B):** fully cloud-hosted stack; Mac off while trading.  
**Current work:** prove connection and paper orders on local IB Gateway first.

## What “fully cloud” means in Phase B

| Component | Where it runs |
| --- | --- |
| TradingView | TradingView cloud (already) |
| Signal / Risk / Executor / ATO | AU VPS |
| IBKR Gateway | Same VPS (Docker + IBC) |
| Your Mac | Optional after Phase B |

### Important IBKR reality

IBKR does **not** offer a pure SaaS API with zero process on your side. Something must stay logged in:

- **Phase A:** IB Gateway on Mac (now)
- **Phase B:** Dockerized IB Gateway on AU VPS

## Local connection status (2026-07-13)

- IB Gateway 10.48 installed and running
- Paper API port **4002** accepts connections
- First connect hit **Error 10141**: paper trading disclaimer must be accepted before API use
- Live port 4001 closed (expected in paper mode)

**Your next manual step:** accept the paper trading / API disclaimer (Client Portal with paper login, and any Gateway popup), then:

```bash
python3 scripts/test_ibkr_connect.py
```

## Phase B topology (later)

```text
TradingView --> HTTPS --> signal_generator (public)
                              |
                         risk_manager (private)
                              |
                        order_executor --> ib-gateway :4002 (private)
                              |
                           ato_tax
```

Gateway ports never public. Prefer Sydney/Melbourne VPS.

## Acceptance

### Phase A
- [ ] `scripts/test_ibkr_connect.py` prints CONNECTED + account id
- [ ] Tiny paper order from a script succeeds

### Phase B
- [ ] Full compose stack on VPS; laptop off; paper still works
- [ ] Gateway not on public internet; HTTPS webhook works

## Todos

- [x] Local Gateway install confirmed
- [ ] Accept paper API disclaimer (Error 10141) and reconnect
- [ ] Local paper order smoke test
- [ ] Phase B: VPS + docker-compose + secrets + deploy runbook
