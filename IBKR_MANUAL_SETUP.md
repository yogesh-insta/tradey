# IBKR Manual Setup (Before Coding)

Complete these steps in Interactive Brokers **before** any programming starts.
Use this as a checklist: tick items as you finish them.

**Target for v1 experiments:** IBKR Australia → Paper trading → **local Mac** IB Gateway → US liquid stocks/ETFs.

**Phased approach:**
1. **Now — local:** Gateway on your Mac; prove API connection and paper orders.
2. **Later — cloud:** move the same stack to an AU VPS ([`plans/00_CLOUD_INFRA.md`](plans/00_CLOUD_INFRA.md)). Mac does not need to stay on for production.

Official refs:

- [How to open a paper trading account](https://www.interactivebrokers.com/campus/trading-lessons/how-to-open-an-ibkr-paper-trading-account/)
- [Install & configure TWS/Gateway for the API](https://www.interactivebrokers.com/campus/trading-lessons/installing-configuring-tws-for-the-api/)
- [IBKR Australia](https://www.interactivebrokers.com.au)

---

## Phase 0 — Prepare

- [ ] Confirm broker: **Interactive Brokers Australia (IBKR AU)**
- [ ] Have KYC docs ready (passport or driver licence, address proof, TFN if requested)
- [ ] Decide account type: usually **Individual**
- [ ] Know this rule: **Paper trading unlocks only after a live account is approved** (you do **not** need to fund the live account yet)

---

## Phase 1 — Open and approve the live account

- [ ] Go to [interactivebrokers.com.au](https://www.interactivebrokers.com.au) → **Open account**
- [ ] Complete the application (personal details, employment, experience, financials)
- [ ] Submit identity verification documents
- [ ] Wait until the account status is **approved / open**
- [ ] Log into **Client Portal** with the live username
- [ ] Enable **2FA** and store backup codes somewhere safe (not in git)

### Funding

- [ ] For coding / paper testing: funding can wait
- [ ] Before later live micro trading (~AUD 2k): deposit, then convert **AUD → USD once** if trading US symbols (avoid constant FX churn)

---

## Phase 2 — Enable trading permissions

In **Client Portal → Settings / Account Settings → Trading Permissions** (labels may vary slightly):

- [ ] Enable **US Stocks** (and ETFs if listed separately)
- [ ] Do **not** enable futures, options, or shorting yet unless you know you need them
- [ ] Skip ASX / Asia product + real-time data packs for v1 (ASX data alone is ~AUD 25/mo)
- [ ] Accept any required IBKR disclosures/agreements for the products you enabled

---

## Phase 3 — Create the Paper Trading account

In **Client Portal**:

- [ ] Open **Settings → Paper Trading Account**
- [ ] Create / enable the paper account
- [ ] Set a **separate paper username and password**
- [ ] Confirm you can see a paper account number and simulated cash (often ~USD 1,000,000 virtual)

Store privately (password manager; **never** commit to git):

| Field | Value |
| --- | --- |
| Live username | |
| Paper username | |
| Paper account ID | |
| Paper password | (password manager only) |

---

## Phase 4 — IB Gateway location

### A) Mac Gateway (required for local experiments — now)

Prefer **IB Gateway** for algo work (lighter than full TWS).

- [x] Download / install **IB Gateway** (you have 10.48)
- [ ] Launch and log in with **Paper** credentials / Paper trading mode
- [ ] Confirm Gateway fully loads (login + 2FA complete)
- [ ] **Enable API** (Phase 5) and keep Gateway running while testing
- [ ] Accept any **paper trading disclaimer** (Error 10141 blocks API until accepted)
- [ ] Verify with: `python3 scripts/test_ibkr_connect.py`

### B) Cloud Gateway (later — after local paper works)

- [ ] Provision AU VPS (see [`plans/00_CLOUD_INFRA.md`](plans/00_CLOUD_INFRA.md))
- [ ] Deploy Dockerized **IB Gateway + IBC** on that VPS (paper mode)
- [ ] Confirm Gateway is healthy on the **private** Docker network (`ib-gateway:4002`)
- [ ] Confirm Gateway is **not** exposed on a public IP
- [ ] Store IBKR paper credentials / TOTP secret in VPS secrets (not git)

### Default API ports

| App | Paper | Live |
| --- | --- | --- |
| **IB Gateway** | **4002** | 4001 |
| **TWS** | **7497** | 7496 |

**v1 default:** IB Gateway **paper** on port **4002**.

---

## Phase 5 — Enable API access (required)

Applies to **cloud Gateway** (primary) and optional Mac Gateway.

In **IB Gateway → Configure → Settings** (or via IBC/`config.ini` on the VPS):

Go to **API → Settings**:

- [ ] Check **Enable ActiveX and Socket Clients**
- [ ] **Uncheck Read-Only API** (otherwise the bot cannot place orders)
- [ ] Confirm **Socket port** = `4002` (Gateway paper) or `7497` (TWS paper)
- [ ] On cloud: allow connections from Docker network / localhost inside the compose network — **do not** open `4002` to the public internet
- [ ] On Mac (optional): prefer **Allow connections from localhost only**
- [ ] Optional: enable **Create API message log file** for debugging
- [ ] Apply / OK, and **restart** Gateway if prompted

---

## Phase 6 — Market data (keep minimal for v1)

In **Client Portal → Market Data Subscriptions**:

- [ ] Check what US stock/ETF streaming is already **complimentary** on your account
- [ ] Do **not** buy ASX / Japan / China data packs yet
- [ ] Accept that paper quotes may be delayed unless live entitlements apply — fine for plumbing tests

---

## Phase 7 — Manual smoke test (still no custom code)

While logged into **paper** Gateway/TWS:

- [ ] Look up one liquid US symbol (mega-cap or liquid ETF)
- [ ] Place a tiny **manual** paper buy (market or limit)
- [ ] Confirm the position appears
- [ ] Close the position
- [ ] Confirm balance / positions look sane in the UI

If this fails, fix it before any programming.

---

## Phase 8 — Connection facts for the future bot

Keep a **private** note (not in git):

```text
Broker: IBKR AU
Mode: paper
Host: 127.0.0.1
Port: 4002          # Gateway paper (or 7497 if TWS)
ClientId: 1
AccountId: DUxxxxxx # your paper account id
Symbols v1: (pick 1–3 US liquid names later)
```

---

## Phase 9 — TradingView (parallel; not blocking IBKR)

Do this when the Signal Generator needs live webhooks (can wait until IBKR paper API works):

- [ ] Create a TradingView account
- [ ] Subscribe to **Essential** or higher (webhooks require a paid plan)
- [ ] Enable TradingView **2FA**
- [ ] Later: add Pine strategy + alert with webhook URL pointing at Signal Generator

---

## Ready for programming when

1. Live IBKR account is approved  
2. Paper account exists and login works  
3. **Cloud** IB Gateway runs in **paper** mode on the VPS (Mac optional)  
4. API socket is enabled and **not** read-only (in cloud Gateway config / IBC)  
5. You know cloud `host` (`ib-gateway`) / `port` / `account id`  
6. One paper round-trip succeeded (Client Portal UI and/or cloud path)  
7. TradingView can reach the cloud Signal URL over HTTPS (when signals go live)  

---

## Do not do yet

- [ ] Fund and switch to live trading
- [ ] Enable complex products (futures, options, heavy margin permissions)
- [ ] Buy ASX / Asia market data packs
- [ ] Expose API ports to the public internet
- [ ] Put IBKR passwords in source code or git

---

## Notes

- Paper and live use **different logins** and usually **different ports**.
- Never commit secrets. Use a password manager + later env files ignored by git.
- Multi-market (ASX, Japan, China) comes **after** the US paper pipeline is stable.
