import requests
import json
from datetime import datetime
from collections import defaultdict
import time
import pandas as pd
from bs4 import BeautifulSoup
import threading
import smtplib
import os
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ========================= CONFIG =========================
POLL_INTERVAL = 30
MIN_COUNT = 71
WINDOW_MIN = 5
REFRESH_WALLETS_HOUR = 1
HYPURRSCAN_URL = "https://hypurrscan.io/leaderboard"
MIN_BALANCE_USD = 20_000_000
MIN_DEPOSIT_USD = 20_000_000

# Email configuration (from environment variables)
EMAIL_SENDER = os.getenv("EMAIL_SENDER")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER")

# ===========================================================

def send_email(subject, body):
    """Send email notification for anomaly detection."""
    try:
        msg = MIMEMultipart()
        msg["From"] = EMAIL_SENDER
        msg["To"] = EMAIL_RECEIVER
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(EMAIL_SENDER, EMAIL_PASSWORD)
            server.send_message(msg)

        print(f"📧 Email sent: {subject}")
    except Exception as e:
        print(f"❌ Email error: {e}")

def scrape_top_wallets(num=100):
    headers = {'User-Agent': 'Mozilla/5.0'}
    try:
        resp = requests.get(HYPURRSCAN_URL, headers=headers, timeout=10)
        soup = BeautifulSoup(resp.text, 'lxml')
        rows = soup.find_all('tr')[1:num+1]
        wallets = []
        for row in rows:
            tds = row.find_all('td')
            if len(tds) > 1:
                addr_td = tds[1] if tds[1].get('class') == ['address'] else tds[0]
                addr = addr_td.text.strip() or addr_td.find('a')['href'].split('/')[-1]
                if addr.startswith('0x') and len(addr) == 42:
                    wallets.append(addr.lower())
        print(f"Scraped {len(wallets)} top wallets.")
        return wallets[:num]
    except Exception as e:
        print(f"Scrape error: {e}")
        return [
            "0xb317d2bc2d3d2df5fa441b5bae0ab9d8b07283ae",
            "0x2ea18c23f72a4b6172c55b411823cdc5335923f4",
            "0xc44d87a291f54a77adbae7a22becf4522b0c708e",
        ]

def get_user_state(user_address):
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "userState", "user": user_address}
    try:
        resp = requests.post(url, json=payload, timeout=5)
        data = resp.json()
        if data and isinstance(data, list) and len(data) > 0:
            return float(data[0].get('marginSummary', {}).get('accountValue', 0))
    except Exception as e:
        print(f"UserState error for {user_address}: {e}")
    return 0.0

def get_latest_deposit(user_address):
    url = "https://api.hyperliquid.xyz/info"
    payload = {"type": "userNonFundingLedgerUpdates", "user": user_address}
    try:
        resp = requests.post(url, json=payload, timeout=5)
        data = resp.json()
        if not isinstance(data, list):
            return 0.0
        deposits = [
            {'time': d.get('time', 0), 'amount': float(d.get('amount', 0))}
            for d in data if d.get('type') == 'deposit' and d.get('currency') == 'USDC'
        ]
        if not deposits:
            return 0.0
        return max(deposits, key=lambda x: x['time'])['amount']
    except Exception as e:
        print(f"Ledger error for {user_address}: {e}")
    return 0.0

def is_whale_eligible(user_address):
    balance = get_user_state(user_address)
    if balance < MIN_BALANCE_USD:
        return False
    latest_dep = get_latest_deposit(user_address)
    if latest_dep <= MIN_DEPOSIT_USD:
        return False
    print(f"✅ Whale eligible: {user_address} | Balance: ${balance:,.0f} | Dep: ${latest_dep:,.0f}")
    return True

def get_user_orders(user_address):
    url = "https://api.hyperliquid.xyz/info"
    orders = []
    payload_open = {"type": "openOrders", "user": user_address}
    try:
        resp = requests.post(url, json=payload_open, timeout=5)
        data = resp.json()
        for item in data:
            order = item.get('order', {})
            if order.get('orderType') == 'Limit':
                orders.append({
                    'timestamp': order.get('timestamp', 0),
                    'price': float(order.get('limitPx', 0)),
                    'size': float(order.get('sz', 0)),
                    'side': order.get('side', ''),
                    'coin': order.get('coin', ''),
                })
    except Exception as e:
        print(f"Open orders error: {e}")
    return orders

def detect_layering(orders):
    if len(orders) < MIN_COUNT:
        return []
    now_ms = int(time.time() * 1000)
    recent_start = now_ms - (WINDOW_MIN * 60 * 1000)
    recent_orders = [o for o in orders if o['timestamp'] >= recent_start]
    if len(recent_orders) < MIN_COUNT:
        return []
    price_groups = defaultdict(list)
    for ord in recent_orders:
        price_groups[ord['price']].append(ord)
    anomalies = []
    for price, ords in price_groups.items():
        if len(ords) >= MIN_COUNT and len(set(round(o['size'], 4) for o in ords)) > 1:
            anomalies.append({
                'price': price,
                'count': len(ords),
                'coin': ords[0]['coin'],
                'side': ords[0]['side']
            })
    return anomalies

def scan_wallet(wallet):
    if not is_whale_eligible(wallet):
        return
    orders = get_user_orders(wallet)
    anomalies = detect_layering(orders)
    if anomalies:
        for anom in anomalies:
            msg = (
                f"🚨 LIVE WHALE HIT\n"
                f"Wallet: {wallet}\n"
                f"Coin: {anom['coin']}\n"
                f"Price: ${anom['price']:.2f}\n"
                f"Count: {anom['count']}\n"
                f"Side: {anom['side']}\n"
                f"Time: {datetime.now().isoformat()}"
            )
            print(msg)
            send_email("🚨 Whale Layering Alert", msg)

if __name__ == "__main__":
    print("Starting Hyperliquid Whale Layering Scanner (Email Alerts Enabled)")
    wallets = scrape_top_wallets(100)
    last_refresh = time.time()

    while True:
        if time.time() - last_refresh > (REFRESH_WALLETS_HOUR * 3600):
            wallets = scrape_top_wallets(100)
            last_refresh = time.time()
            print("Wallet list refreshed.")
        threads = []
        for w in wallets:
            t = threading.Thread(target=scan_wallet, args=(w,))
            t.start()
            threads.append(t)
            time.sleep(0.2)
        for t in threads:
            t.join()
        print(f"Cycle complete. Sleeping {POLL_INTERVAL}s...\n")
        time.sleep(POLL_INTERVAL)
