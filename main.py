import re
import os
import sys
import json
import datetime
import requests
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import matplotlib.ticker as ticker
import asyncio
import aiohttp


# ---------------------------------------------------
# CONFIG
# ---------------------------------------------------
STYLES = {
    ("Buy", "Up"):   ("#008f00", "x", "Buy YES"),   # strong green
    ("Sell", "Up"):  ("#00c800", "o", "Sell YES"),  # vivid lime
    ("Buy", "Down"): ("#d000d0", "x", "Buy NO"),    # strong magenta
    ("Sell", "Down"):("#d40000", "o", "Sell NO")    # strong red
}

SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
EVENTS_URL = "https://gamma-api.polymarket.com/events"
TRADES_URL = "https://data-api.polymarket.com/trades"
PRICE_RESOLUTION_THRESHOLD = 0.95


# ---------------------------------------------------
# REPORT GENERATION
# ---------------------------------------------------
def write_stats_report(
    report_path,
    target_market,
    user_address,
    resolved_side,
    trade_count,
    count_maker,
    count_taker,
    remaining_yes,
    remaining_no,
    final_value,
    total_spent,
    pnl,
    yes_buy_sh,
    yes_buy_cost,
    yes_sell_sh,
    yes_sell_cost,
    no_buy_sh,
    no_buy_cost,
    no_sell_sh,
    no_sell_cost,
    cum_yes_total,
    cum_no_total,
    cum_yes_cost_total,
    cum_no_cost_total,
    yes_curve,
    no_curve,
    net_curve,
    yes_sh_curve,
    no_sh_curve,
    net_sh_curve,
    prices,
    trades,
):
    # Safety checks for empty data
    if len(yes_curve) > 0:
        yes_peak_idx = int(np.argmax(yes_curve))
        no_peak_idx = int(np.argmax(no_curve))
        yes_sh_peak_idx = int(np.argmax(yes_sh_curve))
        no_sh_peak_idx = int(np.argmax(no_sh_curve))

        yes_peak_val = yes_curve[yes_peak_idx]
        no_peak_val = no_curve[no_peak_idx]
        yes_sh_peak_val = yes_sh_curve[yes_sh_peak_idx]
        no_sh_peak_val = no_sh_curve[no_sh_peak_idx]

        final_yes_exp = yes_curve[-1]
        final_yes_sh = yes_sh_curve[-1]
        final_no_exp = no_curve[-1]
        final_no_sh = no_sh_curve[-1]
        final_net_exp = net_curve[-1]
        final_net_sh = net_sh_curve[-1]
    else:
        yes_peak_idx = no_peak_idx = 0
        yes_sh_peak_idx = no_sh_peak_idx = 0
        yes_peak_val = no_peak_val = 0
        yes_sh_peak_val = no_sh_peak_val = 0
        final_yes_exp = final_yes_sh = 0
        final_no_exp = final_no_sh = 0
        final_net_exp = final_net_sh = 0

    # Calculate time range
    start_time = "N/A"
    end_time = "N/A"
    if trades:
        start_time = datetime.datetime.fromtimestamp(trades[0]['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        end_time = datetime.datetime.fromtimestamp(trades[-1]['timestamp']).strftime('%Y-%m-%d %H:%M:%S')

    min_price = min(prices) if prices else 0
    max_price = max(prices) if prices else 0

    lines = [
        f"MARKET: {target_market}",
        f"WALLET: {user_address if user_address else 'N/A'}",
        f"RESOLUTION: {resolved_side if resolved_side else 'ACTIVE (market not resolved yet)'}",
        f"TRADES: {trade_count} (Maker: {count_maker}, Taker: {count_taker})",
        f"TIME RANGE: {start_time} to {end_time}",
        f"PRICE RANGE: {min_price:.2f} - {max_price:.2f}",
        "",
        "--- Position ---" if not resolved_side else "--- Position at resolution ---",
        f"YES shares: {remaining_yes:.2f}",
        f"NO shares:  {remaining_no:.2f}",
        f"{'Current value' if not resolved_side else 'Final value'}: $ {final_value:.2f}",
        f"Total spent (net exposure): $ {total_spent:.2f}",
        f"{'UNREALIZED PNL' if not resolved_side else 'FINAL PNL'}: $ {pnl:.2f}",
        "",
        "--- Buy/Sell totals ---",
        f"YES buys:  {yes_buy_sh:.2f} sh / $ {yes_buy_cost:.2f}",
        f"YES sells: {yes_sell_sh:.2f} sh / $ {yes_sell_cost:.2f}",
        f"NO buys:   {no_buy_sh:.2f} sh / $ {no_buy_cost:.2f}",
        f"NO sells:  {no_sell_sh:.2f} sh / $ {no_sell_cost:.2f}",
        "",
        "--- Cumulative buys ---",
        f"YES cumulative: {cum_yes_total:.2f} sh / $ {cum_yes_cost_total:.2f}",
        f"NO cumulative:  {cum_no_total:.2f} sh / $ {cum_no_cost_total:.2f}",
        "",
        "--- Exposure peaks (trade index: earliest → latest) ---",
        f"YES dollar peak: $ {yes_peak_val:.2f} at trade #{yes_peak_idx + 1}",
        f"NO dollar peak:  $ {no_peak_val:.2f} at trade #{no_peak_idx + 1}",
        f"YES share peak:  {yes_sh_peak_val:.2f} sh at trade #{yes_sh_peak_idx + 1}",
        f"NO share peak:   {no_sh_peak_val:.2f} sh at trade #{no_sh_peak_idx + 1}",
        "",
        "--- Final exposure ---",
        f"YES exposure: $ {final_yes_exp:.2f} | {final_yes_sh:.2f} sh",
        f"NO exposure:  $ {final_no_exp:.2f} | {final_no_sh:.2f} sh",
        f"NET exposure: $ {final_net_exp:.2f} | {final_net_sh:.2f} sh",
    ]

    lines.append("")
    lines.append("--- Trades (Sorted by Timestamp) ---")
    lines.append("Idx | Time                | Role  | Type | Side | Price(c) |   Shares   |      Bank YES      |      Bank NO       | Total avg | Total")
    lines.append("    |                     |       |      |      |          |            | sh  / avg  / spent | sh  / avg  / spent |   price   | Spent($)")
    lines.append("----+---------------------+-------+------+------+----------+------------+--------------------+--------------------+-----------+---------")

    bank_up_shares = 0.0
    bank_up_spent = 0.0
    bank_down_shares = 0.0
    bank_down_spent = 0.0
    prev_side = None

    for i, t in enumerate(trades):
        dt_str = datetime.datetime.fromtimestamp(t['timestamp']).strftime('%Y-%m-%d %H:%M:%S')
        role = t.get('role', 'N/A')

        if prev_side is not None and prev_side != t['side']:
            lines.append("")
        prev_side = t['side']

        if t['type'] == 'Buy':
            if t['side'] == 'Up':
                bank_up_shares += t['shares']
                bank_up_spent += t['cost']
            else:
                bank_down_shares += t['shares']
                bank_down_spent += t['cost']

        avg_price_up = (bank_up_spent / bank_up_shares) if bank_up_shares > 0 else 0.0
        avg_price_down = (bank_down_spent / bank_down_shares) if bank_down_shares > 0 else 0.0
        total_avg_price = avg_price_up + avg_price_down
        total_spent = bank_up_spent + bank_down_spent

        side_label = "YES" if t['side'] == "Up" else "NO"
        lines.append(
            f"{i+1:3d} | {dt_str} | {role:<5} | {t['type']:<4} | {side_label:<4} | "
            f"{t['price']:8.2f} | {t['shares']:10.2f} | "
            f"{bank_up_shares:4.0f}/{avg_price_up:5.3f}${bank_up_spent:6.2f} | "
            f"{bank_down_shares:4.0f}/{avg_price_down:5.3f}${bank_down_spent:6.2f} | "
            f"{total_avg_price:9.3f} | ${total_spent:7.2f}"
        )

    with open(report_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


# ---------------------------------------------------
# DATA FETCHING
# ---------------------------------------------------
def lookup_event_by_slug(slug):
    """Return (event, market) by exact event slug via the events endpoint."""
    try:
        resp = requests.get(EVENTS_URL, params={"slug": slug}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"Error looking up event by slug: {exc}")
        return None, None

    events = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
    for event in events:
        markets = event.get("markets") or []
        if markets:
            return event, _best_market(markets, slug)
    return None, None


def _best_market(markets, query):
    """Pick the market whose question best matches query; fall back to first."""
    query_lower = query.lower()
    # Exact match first
    for m in markets:
        if (m.get("question") or "").lower() == query_lower:
            return m
    # Most word overlap
    query_words = set(query_lower.split())
    best, best_score = markets[0], -1
    for m in markets:
        words = set((m.get("question") or "").lower().split())
        score = len(query_words & words)
        if score > best_score:
            best, best_score = m, score
    return best


def search_market(query):
    """Return (event, market) for the best-matching search result."""
    try:
        resp = requests.get(SEARCH_URL, params={"q": query}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        print(f"Error searching market: {exc}")
        return None, None

    events = data.get("events", []) if isinstance(data, dict) else []
    for event in events:
        markets = event.get("markets") or []
        if markets:
            return event, _best_market(markets, query)
    return None, None


def fetch_trades(condition_id, user_address, page_limit=100, taker_only=False):
    """
    Fetch trades for a condition/user with simple pagination.
    taker_only: If True, fetches only taker trades. If False, fetches ALL trades (maker + taker).
    """
    all_trades = []
    offset = 0
    taker_param = "true" if taker_only else "false"

    while True:
        params = {
            "limit": page_limit,
            "offset": offset,
            "takerOnly": taker_param,
            "market": condition_id,
            "user": user_address,
        }
        try:
            resp = requests.get(TRADES_URL, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            print(f"Error fetching trades (takerOnly={taker_param}): {exc}")
            return []

        if isinstance(data, dict):
            batch = data.get("trades", [])
        elif isinstance(data, list):
            batch = data
        else:
            batch = []

        all_trades.extend(batch)

        if len(batch) < page_limit:
            break
        offset += page_limit

    return all_trades


# ---------------------------------------------------
# ASYNC VERSIONS FOR BATCH PROCESSING
# ---------------------------------------------------
async def search_market_async(session, query):
    """Async version of search_market. Returns (event, market) for the first matching search result."""
    try:
        async with session.get(SEARCH_URL, params={"q": query}, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            resp.raise_for_status()
            data = await resp.json()
    except Exception as exc:
        return None, None

    events = data.get("events", []) if isinstance(data, dict) else []
    for event in events:
        markets = event.get("markets") or []
        if markets:
            return event, markets[0]
    return None, None


async def fetch_trades_async(session, condition_id, user_address, page_limit=100, taker_only=False):
    """
    Async version of fetch_trades.
    Fetch trades for a condition/user with simple pagination.
    """
    all_trades = []
    offset = 0
    taker_param = "true" if taker_only else "false"

    while True:
        params = {
            "limit": page_limit,
            "offset": offset,
            "takerOnly": taker_param,
            "market": condition_id,
            "user": user_address,
        }
        try:
            async with session.get(TRADES_URL, params=params, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                resp.raise_for_status()
                data = await resp.json()
        except Exception as exc:
            return []

        if isinstance(data, dict):
            batch = data.get("trades", [])
        elif isinstance(data, list):
            batch = data
        else:
            batch = []

        all_trades.extend(batch)

        if len(batch) < page_limit:
            break
        offset += page_limit

    return all_trades


def prompt_resolved_side(current=None):
    """Return a valid resolved side, prompting if needed."""
    if current in {"YES", "NO"}:
        return current
    while True:
        side = input("Enter resolved side (YES/NO, blank = skip): ").strip().upper()
        if not side:
            return None
        if side in {"YES", "NO"}:
            return side
        print("Please enter YES or NO.")


def normalize_resolved_arg(value):
    if not value:
        return None
    value = value.strip().upper()
    if value in {"YES", "NO", "AUTO"}:
        return value
    return None


def _resolve_from_metadata(market):
    """
    Determine resolution and current prices from market API metadata.
    Returns (resolved_side, yes_price, no_price).
    resolved_side is 'YES', 'NO', or None (active/unknown).
    """
    closed = market.get("closed", False)
    outcomes_raw = market.get("outcomes", '["Yes", "No"]')
    prices_raw = market.get("outcomePrices", '["0.5", "0.5"]')

    try:
        outcomes = json.loads(outcomes_raw) if isinstance(outcomes_raw, str) else list(outcomes_raw)
        prices = [float(p) for p in (json.loads(prices_raw) if isinstance(prices_raw, str) else prices_raw)]
    except Exception:
        return None, None, None

    yes_idx = next((i for i, o in enumerate(outcomes) if o.lower() in {"yes", "up"}), None)
    no_idx = next((i for i, o in enumerate(outcomes) if o.lower() in {"no", "down"}), None)

    yes_price = prices[yes_idx] if yes_idx is not None and yes_idx < len(prices) else None
    no_price = prices[no_idx] if no_idx is not None and no_idx < len(prices) else None

    if not closed:
        return None, yes_price, no_price

    # Closed market: winner has outcomePrices == "1"
    if yes_price is not None and yes_price >= 0.99:
        return "YES", yes_price, no_price
    if no_price is not None and no_price >= 0.99:
        return "NO", yes_price, no_price

    return None, yes_price, no_price


def infer_resolved_side_from_trades(trades, threshold=PRICE_RESOLUTION_THRESHOLD):
    """Infer resolved side from the most recent trade price."""
    if not trades:
        return None, None
    latest = max(trades, key=lambda t: t.get("timestamp", 0))
    price = float(latest.get("price", 0))
    outcome = latest.get("outcome", "").lower()

    # Map known outcome names to up/down
    UP_OUTCOMES = {"up", "yes"}
    DOWN_OUTCOMES = {"down", "no"}

    if outcome in UP_OUTCOMES:
        norm = "up"
    elif outcome in DOWN_OUTCOMES:
        norm = "down"
    else:
        # Unknown outcome label — fall back to price only
        norm = "up" if price >= threshold else "down"

    if price >= threshold:
        inferred = "YES" if norm == "up" else "NO"
    else:
        inferred = "NO" if norm == "up" else "YES"
    return inferred, latest


# ---------------------------------------------------
# CORE RENDER: compute stats + chart + report
# ---------------------------------------------------
def _render(raw_data, market_title, user_address, resolved_side, output_dir,
            yes_price=None, no_price=None):
    """
    Parse sorted raw trades, compute all stats, generate chart and report.
    resolved_side: 'YES', 'NO', or None (active market).
    yes_price/no_price: current market prices (0..1) for unrealized PnL.
    Returns dict with stats and file paths (chart_path, report_path).
    """
    report_path = os.path.join(output_dir, "report.txt")
    chart_path = os.path.join(output_dir, "chart.png")
    target_market = market_title

    parsed = []
    for item in raw_data:
        entry = {
            "type": "Buy" if item.get("side", "BUY").upper() == "BUY" else "Sell",
            "market": item.get("title", ""),
            "side": "Up" if item.get("outcome", "").lower() in {"up", "yes"} else "Down",
            "price": float(item.get("price", 0)) * 100.0,
            "shares": float(item.get("size", 0)),
            "cost": float(item.get("price", 0)) * float(item.get("size", 0)),
            "timestamp": int(item.get("timestamp", 0)),
            "role": item.get("role", "N/A"),
        }
        parsed.append(entry)

    if not parsed:
        raise ValueError("No entries found.")

    count_maker = sum(1 for e in parsed if e.get("role") == "Maker")
    count_taker = sum(1 for e in parsed if e.get("role") == "Taker")
    prices = [e["price"] for e in parsed]

    # ---------------------------------------------------
    # EXPOSURE CURVES
    # ---------------------------------------------------
    yes_curve = []
    no_curve = []
    net_curve = []
    yes_sh_curve = []
    no_sh_curve = []
    net_sh_curve = []
    yes_exp = no_exp = yes_sh_exp = no_sh_exp = 0

    for e in parsed:
        if e["side"] == "Up":
            yes_exp += e["cost"] if e["type"] == "Buy" else -e["cost"]
            yes_sh_exp += e["shares"] if e["type"] == "Buy" else -e["shares"]
        else:
            no_exp += e["cost"] if e["type"] == "Buy" else -e["cost"]
            no_sh_exp += e["shares"] if e["type"] == "Buy" else -e["shares"]
        yes_curve.append(yes_exp)
        no_curve.append(no_exp)
        net_curve.append(yes_exp + no_exp)
        yes_sh_curve.append(yes_sh_exp)
        no_sh_curve.append(no_sh_exp)
        net_sh_curve.append(yes_sh_exp + no_sh_exp)

    # ---------------------------------------------------
    # FINAL PNL CALC
    # ---------------------------------------------------
    remaining_yes = yes_sh_curve[-1]
    remaining_no = no_sh_curve[-1]
    total_spent = net_curve[-1]

    is_active = resolved_side is None

    if is_active:
        yp = yes_price if yes_price is not None else 0.5
        np_ = no_price if no_price is not None else 0.5
        final_value = remaining_yes * yp + remaining_no * np_
        pnl = final_value - total_spent
        pnl_text = (
            f"MARKET ACTIVE\n"
            f"YES price: {yp:.3f} | NO price: {np_:.3f}\n\n"
            f"YES shares: {remaining_yes:.2f}\n"
            f"NO shares:  {remaining_no:.2f}\n\n"
            f"Current value: $ {final_value:.2f}\n"
            f"Total spent:   $ {total_spent:.2f}\n\n"
            f"UNREALIZED PNL: $ {pnl:.2f}"
        )
    else:
        final_value = remaining_yes if resolved_side == "YES" else remaining_no
        pnl = final_value - total_spent
        pnl_text = (
            f"MARKET RESOLVED: {resolved_side}\n\n"
            f"Remaining YES shares: {remaining_yes:.2f}\n"
            f"Remaining NO shares:  {remaining_no:.2f}\n\n"
            f"Final Value: $ {final_value:.2f}\n"
            f"Total Spent (net exposure): $ {total_spent:.2f}\n\n"
            f"FINAL PNL: $ {pnl:.2f}"
        )

    # ---------------------------------------------------
    # BUY/SELL TOTALS
    # ---------------------------------------------------
    yes_buy_sh = yes_buy_cost = 0
    yes_sell_sh = yes_sell_cost = 0
    no_buy_sh = no_buy_cost = 0
    no_sell_sh = no_sell_cost = 0
    raw_vol_yes, raw_vol_no, raw_cost_yes, raw_cost_no = [], [], [], []

    for e in parsed:
        is_yes = (e["side"] == "Up")
        is_buy = (e["type"] == "Buy")
        if is_buy:
            if is_yes:
                yes_buy_sh += e["shares"]
                yes_buy_cost += e["cost"]
                raw_vol_yes.append(e["shares"]); raw_vol_no.append(0)
                raw_cost_yes.append(e["cost"]); raw_cost_no.append(0)
            else:
                no_buy_sh += e["shares"]
                no_buy_cost += e["cost"]
                raw_vol_yes.append(0); raw_vol_no.append(e["shares"])
                raw_cost_yes.append(0); raw_cost_no.append(e["cost"])
        else:
            raw_vol_yes.append(0); raw_vol_no.append(0)
            raw_cost_yes.append(0); raw_cost_no.append(0)
            if is_yes:
                yes_sell_sh += e["shares"]
                yes_sell_cost += e["cost"]
            else:
                no_sell_sh += e["shares"]
                no_sell_cost += e["cost"]

    cum_yes = np.cumsum(raw_vol_yes)
    cum_no = np.cumsum(raw_vol_no)
    cum_yes_cost = np.cumsum(raw_cost_yes)
    cum_no_cost = np.cumsum(raw_cost_no)
    cum_yes_total = cum_yes[-1] if len(cum_yes) > 0 else 0
    cum_no_total = cum_no[-1] if len(cum_no) > 0 else 0
    cum_yes_cost_total = cum_yes_cost[-1] if len(cum_yes_cost) > 0 else 0
    cum_no_cost_total = cum_no_cost[-1] if len(cum_no_cost) > 0 else 0

    # ---------------------------------------------------
    # PLOT SETUP
    # ---------------------------------------------------
    unique_timestamps = sorted(set(t['timestamp'] for t in parsed))
    ts_map = {ts: i for i, ts in enumerate(unique_timestamps)}
    x_indices = [ts_map[e['timestamp']] for e in parsed]

    fig, (ax1, ax2, ax3, ax4) = plt.subplots(
        4, 1, figsize=(16, 14.5),
        gridspec_kw={'height_ratios': [3, 1.3, 1.1, 1.1]}
    )
    fig.subplots_adjust(hspace=0.45, bottom=0.2)

    # ---------------------------------------------------
    # TOP: BUY/SELL SCATTER (GROUPED BUBBLE VIEW)
    # ---------------------------------------------------
    grouped_trades = {}
    for i, e in enumerate(parsed):
        x_idx = ts_map[e['timestamp']]
        if x_idx not in grouped_trades:
            grouped_trades[x_idx] = []
        grouped_trades[x_idx].append(e)

    next_up = True

    for x_idx in sorted(grouped_trades.keys()):
        group = grouped_trades[x_idx]
        avg_price = sum(t["price"] for t in group) / len(group)

        if len(group) == 1:
            e = group[0]
            style_key = (e["type"], e["side"])
            if style_key in STYLES:
                color, marker, label = STYLES[style_key]
            else:
                color, marker, label = ("gray", "o", "Unknown")

            ax1.scatter(x_idx, e["price"], color=color, marker=marker,
                        s=60, linewidths=2.5 if marker == "x" else 1.0,
                        alpha=0.9, zorder=5)

            direction = 1 if next_up else -1
            next_up = not next_up
            candle_len = 15 * 0.7
            end_y = e["price"] + direction * candle_len

            ax1.vlines(x_idx, e["price"], end_y, colors=color, linewidth=1.5, alpha=0.6)
            ax1.annotate(
                f"{e['shares']:.2f}sh\n${e['cost']:.2f}",
                xy=(x_idx, end_y),
                xytext=(0, direction * 2),
                textcoords="offset points",
                ha="center", va="bottom" if direction > 0 else "top",
                fontsize=7,
                bbox=dict(boxstyle="round,pad=0.2", fc="white", alpha=0.7, ec="none")
            )
        else:
            count = len(group)
            first_e = group[0]
            same_side = all((t["type"] == first_e["type"] and t["side"] == first_e["side"]) for t in group)

            if same_side:
                style_key = (first_e["type"], first_e["side"])
                color, _, _ = STYLES.get(style_key, ("gray", "o", ""))
            else:
                color = "#1f77b4"

            ax1.scatter(x_idx, avg_price, color="white", marker="o", s=300, edgecolors=color, linewidth=2, zorder=5)
            ax1.text(x_idx, avg_price, str(count), ha="center", va="center", fontsize=9, fontweight="bold", color=color, zorder=6)

            direction = 1 if next_up else -1
            next_up = not next_up

            info_lines = []
            for idx, t in enumerate(group):
                if idx < 5:
                    side_lbl = "YES" if t["side"] == "Up" else "NO"
                    info_lines.append(f"{t['shares']:.2f}sh ${t['cost']:.2f} ({side_lbl})")
                else:
                    info_lines.append(f"...+ {len(group) - 5} more")
                    break

            raw_len = 25 + (len(info_lines) * 5)
            candle_len = raw_len * 0.7
            end_y = avg_price + direction * candle_len

            ax1.vlines(x_idx, avg_price, end_y, colors=color, linewidth=2, alpha=0.6, linestyles="dotted")
            ax1.annotate(
                "\n".join(info_lines),
                xy=(x_idx, end_y),
                xytext=(0, direction * 2),
                textcoords="offset points",
                ha="center", va="bottom" if direction > 0 else "top",
                fontsize=6,
                bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.85, ec=color)
            )

    ax1.set_title(f"Trades for {target_market}")
    ax1.set_ylabel("Price (cents)")

    def time_formatter(x, pos):
        idx = int(x)
        if 0 <= idx < len(unique_timestamps):
            ts = unique_timestamps[idx]
            return datetime.datetime.fromtimestamp(ts).strftime('%H:%M:%S')
        return ""

    ax1.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
    ax1.xaxis.set_major_formatter(ticker.FuncFormatter(time_formatter))
    ax1.set_yticks(range(0, 101, 10))
    ax1.grid(axis='y', linestyle='--', alpha=0.3)

    vol_yes_per_ts = [0.0] * len(unique_timestamps)
    vol_no_per_ts = [0.0] * len(unique_timestamps)
    for x_idx, group in grouped_trades.items():
        for t in group:
            if t["type"] == "Buy":
                if t["side"] == "Up":
                    vol_yes_per_ts[x_idx] += t["shares"]
                else:
                    vol_no_per_ts[x_idx] += t["shares"]

    x_range = np.arange(len(unique_timestamps))
    vol_ax = ax1.inset_axes([0, 0.0, 1.0, 0.2], sharex=ax1)
    vol_ax.patch.set_alpha(0)
    vol_ax.bar(x_range - 0.35 / 2, vol_yes_per_ts, width=0.35, color="green", alpha=0.18, label="Buy YES volume")
    vol_ax.bar(x_range + 0.35 / 2, vol_no_per_ts, width=0.35, color="red", alpha=0.18, label="Buy NO volume")
    vol_ax.set_yticks([])
    vol_ax.set_xticks([])
    vol_ax.set_xlim(-0.5, len(unique_timestamps) - 0.5)

    # ---------------------------------------------------
    # SECOND: CUMULATIVE BUY SHARES + COST
    # ---------------------------------------------------
    ax2.plot(x_indices, cum_yes, color="green", alpha=0.3, linewidth=1, label="Cum Buy YES (sh)")
    ax2.fill_between(x_indices, cum_yes, color="green", alpha=0.1)
    ax2.plot(x_indices, cum_no, color="red", alpha=0.3, linewidth=1, label="Cum Buy NO (sh)")
    ax2.fill_between(x_indices, cum_no, color="red", alpha=0.1)
    ax2.set_ylabel("Cumulative buy volume (sh)")
    ax2.grid(axis='y', alpha=0.2)
    ax2.set_xticks([])
    ax2.set_title("Cumulative Buys (shares + dollars)")

    max_cum = max(cum_yes.max() if len(cum_yes) else 0, cum_no.max() if len(cum_no) else 0)
    ax2.set_ylim(0, max_cum * 1.15 + 1e-6)

    ax2_cost = ax2.twinx()
    ax2_cost.plot(x_indices, cum_yes_cost, color="green", linewidth=1.8, linestyle="--", alpha=0.7, label="Cumulative Buy YES ($)")
    ax2_cost.plot(x_indices, cum_no_cost, color="red", linewidth=1.8, linestyle="--", alpha=0.7, label="Cumulative Buy NO ($)")
    ax2_cost.set_ylabel("Cumulative buy cost ($)", color="gray", fontsize=9)
    ax2_cost.tick_params(axis='y', labelsize=8, colors="gray")
    ax2_cost.spines['right'].set_alpha(0.3)
    handles2, labels2 = ax2_cost.get_legend_handles_labels()
    handles1, labels1 = ax2.get_legend_handles_labels()
    ax2.legend(handles1 + handles2, labels1 + labels2, loc="upper left")

    cum_stats_text = (
        f"YES: {cum_yes_total:.2f} sh / $ {cum_yes_cost_total:.2f}\n"
        f"NO:  {cum_no_total:.2f} sh / $ {cum_no_cost_total:.2f}"
    )
    ax2.text(
        0.01, 0.02, cum_stats_text,
        transform=ax2.transAxes,
        ha="left", va="bottom",
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.3", fc="white", alpha=0.8, ec="gray")
    )

    # ---------------------------------------------------
    # THIRD: DOLLAR EXPOSURE
    # ---------------------------------------------------
    ax3.grid(alpha=0.3)
    ax3.plot(x_indices, yes_curve, color="green", linewidth=2, label="YES Exposure ($)")
    ax3.plot(x_indices, no_curve, color="red", linewidth=2, label="NO Exposure ($)")
    ax3.plot(x_indices, net_curve, color="blue", linewidth=2, label="NET Exposure ($ total)")

    last_x = x_indices[-1]
    if len(yes_curve) > 0:
        yes_peak = int(np.argmax(yes_curve))
        no_peak = int(np.argmax(no_curve))
        ax3.annotate("YES peak $", (x_indices[yes_peak], yes_curve[yes_peak]), xytext=(0, -20),
                     textcoords="offset points", ha='center', arrowprops=dict(arrowstyle="->", color="green"), color="green")
        ax3.annotate("NO peak $", (x_indices[no_peak], no_curve[no_peak]), xytext=(0, -20),
                     textcoords="offset points", ha='center', arrowprops=dict(arrowstyle="->", color="red"), color="red")
        ax3.annotate(f"$ {yes_curve[-1]:.2f}", (last_x, yes_curve[-1]), xytext=(15, 0), textcoords="offset points", color="green")
        ax3.annotate(f"$ {no_curve[-1]:.2f}", (last_x, no_curve[-1]), xytext=(15, 0), textcoords="offset points", color="red")
        ax3.annotate(f"$ {net_curve[-1]:.2f}", (last_x, net_curve[-1]), xytext=(15, 0), textcoords="offset points", color="blue")

    ax3.set_title("Dollar Exposure")
    ax3.set_ylabel("Exposure ($)")
    ax3.set_xticks([])
    ax3.legend(loc="upper left")

    summary = (
        f"YES (Up)  Buy: {yes_buy_sh:.2f} sh ($ {yes_buy_cost:.2f})"
        f" | Sell: {yes_sell_sh:.2f} sh ($ {yes_sell_cost:.2f})\n"
        f"NO  (Down) Buy: {no_buy_sh:.2f} sh ($ {no_buy_cost:.2f})"
        f" | Sell: {no_sell_sh:.2f} sh ($ {no_sell_cost:.2f})"
    )
    fig.text(0.01, 0.01, summary, ha="left", va="bottom", fontsize=11,
             bbox=dict(facecolor="white", alpha=0.75, edgecolor="black"))
    fig.text(0.99, 0.06, pnl_text, ha="right", va="top", fontsize=12,
             bbox=dict(facecolor="white", alpha=0.75, edgecolor="black"))

    # ---------------------------------------------------
    # BOTTOM: SHARES EXPOSURE
    # ---------------------------------------------------
    ax4.grid(alpha=0.3)
    ax4.plot(x_indices, yes_sh_curve, color="green", linewidth=2, label="YES Exposure (shares)")
    ax4.plot(x_indices, no_sh_curve, color="red", linewidth=2, label="NO Exposure (shares)")
    ax4.plot(x_indices, net_sh_curve, color="blue", linewidth=2, label="NET Exposure (shares)")

    if len(yes_sh_curve) > 0:
        yes_sh_peak = int(np.argmax(yes_sh_curve))
        no_sh_peak = int(np.argmax(no_sh_curve))
        ax4.annotate("YES peak sh", (x_indices[yes_sh_peak], yes_sh_curve[yes_sh_peak]), xytext=(0, -20),
                     textcoords="offset points", ha='center', arrowprops=dict(arrowstyle="->", color="green"), color="green")
        ax4.annotate("NO peak sh", (x_indices[no_sh_peak], no_sh_curve[no_sh_peak]), xytext=(0, -20),
                     textcoords="offset points", ha='center', arrowprops=dict(arrowstyle="->", color="red"), color="red")
        ax4.annotate(f"{yes_sh_curve[-1]:.2f} sh", (last_x, yes_sh_curve[-1]), xytext=(15, 0), textcoords="offset points", color="green")
        ax4.annotate(f"{no_sh_curve[-1]:.2f} sh", (last_x, no_sh_curve[-1]), xytext=(15, 0), textcoords="offset points", color="red")
        ax4.annotate(f"{net_sh_curve[-1]:.2f} sh", (last_x, net_sh_curve[-1]), xytext=(15, 0), textcoords="offset points", color="blue")

    ax4.set_title("Shares Exposure")
    ax4.set_ylabel("Shares")
    ax4.xaxis.set_major_locator(ticker.MaxNLocator(nbins=12))
    ax4.xaxis.set_major_formatter(ticker.FuncFormatter(time_formatter))
    plt.setp(ax4.get_xticklabels(), rotation=30, ha='right')
    ax4.legend(loc="upper left")

    xlim_range = (-0.5, len(unique_timestamps) - 0.5)
    for axis in (ax1, ax2, ax3, ax4):
        axis.set_xlim(*xlim_range)

    plt.tight_layout()
    plt.savefig(chart_path, dpi=200, bbox_inches="tight")
    plt.close('all')

    write_stats_report(
        report_path, target_market, user_address, resolved_side, len(parsed),
        count_maker, count_taker, remaining_yes, remaining_no, final_value, total_spent, pnl,
        yes_buy_sh, yes_buy_cost, yes_sell_sh, yes_sell_cost,
        no_buy_sh, no_buy_cost, no_sell_sh, no_sell_cost,
        cum_yes_total, cum_no_total, cum_yes_cost_total, cum_no_cost_total,
        yes_curve, no_curve, net_curve, yes_sh_curve, no_sh_curve, net_sh_curve,
        prices, parsed,
    )

    return {
        'market_title': target_market,
        'resolved_side': resolved_side,
        'trade_count': len(parsed),
        'count_maker': count_maker,
        'count_taker': count_taker,
        'remaining_yes': remaining_yes,
        'remaining_no': remaining_no,
        'final_value': final_value,
        'total_spent': total_spent,
        'pnl': pnl,
        'chart_path': chart_path,
        'report_path': report_path,
    }


# ---------------------------------------------------
# BOT-CALLABLE FUNCTION
# ---------------------------------------------------
def process_market(market_query, address, output_dir, resolved_override=None):
    """
    Search for a market by name, fetch trades for address, render chart + report.
    Used by the Telegram bot. Raises ValueError with a descriptive message on failure.
    resolved_override: явно указанная сторона разрешения ('YES' или 'NO'), перекрывает авто-инференс.
    """
    event, market = search_market(market_query)
    if not market:
        raise ValueError(f"Рынок не найден: {market_query!r}")

    market_title = (
        market.get("question") or market.get("title") or event.get("title", "Unknown Market")
    )
    condition_id = market.get("conditionId") or ""

    all_trades_raw = fetch_trades(condition_id, address, taker_only=False)
    if not all_trades_raw:
        raise ValueError("Трейды не найдены для данного адреса/рынка.")

    taker_trades_raw = fetch_trades(condition_id, address, taker_only=True)
    taker_hashes = {t["transactionHash"] for t in taker_trades_raw if "transactionHash" in t}

    for t in all_trades_raw:
        t["role"] = "Taker" if t.get("transactionHash") in taker_hashes else "Maker"

    raw_data = sorted(all_trades_raw, key=lambda x: x.get("timestamp", 0))

    if resolved_override in {"YES", "NO"}:
        resolved_side = resolved_override
        yes_price = no_price = None
    else:
        resolved_side, yes_price, no_price = _resolve_from_metadata(market)
        # Closed market but outcomePrices не дали однозначного ответа — fallback
        if resolved_side is None and market.get("closed"):
            inferred, _ = infer_resolved_side_from_trades(raw_data)
            if inferred:
                resolved_side = inferred

    os.makedirs(output_dir, exist_ok=True)
    trades_path = os.path.join(output_dir, "trades.json")
    with open(trades_path, "w") as f:
        json.dump(all_trades_raw, f, indent=2)

    result = _render(raw_data, market_title, address, resolved_side, output_dir,
                     yes_price=yes_price, no_price=no_price)
    result['trades_path'] = trades_path
    return result


# ---------------------------------------------------
# MAIN (CLI)
# ---------------------------------------------------
def main():
    resolved_arg = normalize_resolved_arg(sys.argv[1] if len(sys.argv) > 1 else None)
    json_file = sys.argv[2] if len(sys.argv) > 2 else None

    raw_data = []
    market_title = "Unknown Market"
    user_address = None

    if json_file:
        try:
            with open(json_file, "r") as f:
                raw_data = json.load(f)
        except Exception as e:
            print(f"Error loading JSON: {e}")
            return
        if not raw_data:
            print("No trades found in JSON.")
            return
        market_title = raw_data[0].get("title", "Unknown Market")
        user_address = raw_data[0].get("proxyWallet", "N/A")
    else:
        market_query = input("Enter market name to search: ").strip()
        if not market_query:
            print("Market name is required.")
            return

        event, market = search_market(market_query)
        if not market:
            print("No market found for that query.")
            return

        market_title = (
            market.get("question")
            or market.get("title")
            or event.get("title", "Unknown Market")  # type: ignore
        )
        condition_id = market.get("conditionId") or ""
        print(f"Found market: {market_title}")
        print(f"Condition ID: {condition_id}")

        user_address = input("Enter user address to fetch trades: ").strip()
        if not user_address:
            print("User address is required.")
            return

        print("Fetching ALL trades (Maker + Taker)...")
        all_trades_raw = fetch_trades(condition_id, user_address, taker_only=False)
        if not all_trades_raw:
            print("No trades returned for that user/market.")
            return

        print("Fetching TAKER trades only...")
        taker_trades_raw = fetch_trades(condition_id, user_address, taker_only=True)
        taker_hashes = {t["transactionHash"] for t in taker_trades_raw if "transactionHash" in t}

        count_maker = count_taker = 0
        for t in all_trades_raw:
            if t.get("transactionHash") in taker_hashes:
                t["role"] = "Taker"
                count_taker += 1
            else:
                t["role"] = "Maker"
                count_maker += 1

        raw_data = all_trades_raw
        print(f"Role Analysis: {count_maker} Maker trades, {count_taker} Taker trades.")

    safe_folder_name = re.sub(r'[<>:"/\\|?*]', '_', market_title).strip() or "Market_Data"
    output_dir = safe_folder_name
    os.makedirs(output_dir, exist_ok=True)

    trade_file_path = os.path.join(output_dir, "trades.json")

    if not json_file:
        with open(trade_file_path, "w") as f:
            json.dump(raw_data, f, indent=2)
        print(f"Saved {len(raw_data)} trades to {trade_file_path}")

    raw_data.sort(key=lambda x: x.get("timestamp", 0))

    resolved_side = None
    if resolved_arg in {"YES", "NO"}:
        resolved_side = resolved_arg
    else:
        inferred, latest = infer_resolved_side_from_trades(raw_data)
        if inferred:
            resolved_side = inferred
            price = float(latest.get("price", 0))  # type: ignore
            outcome = latest.get("outcome", "")  # type: ignore
            ts = latest.get("timestamp", 0)  # type: ignore
            print(f"Inferred resolved side: {resolved_side} (latest trade outcome {outcome} at price {price:.2f}, ts {ts})")
        else:
            if resolved_arg == "AUTO":
                print("Could not infer resolved side automatically.")
                return
            resolved_side = prompt_resolved_side(None)
            if not resolved_side:
                print("Resolved side is required.")
                return

    result = _render(raw_data, market_title, user_address, resolved_side, output_dir)
    print(f"Chart saved as {result['chart_path']}")
    print(f"Report saved as {result['report_path']}")


if __name__ == "__main__":
    main()
