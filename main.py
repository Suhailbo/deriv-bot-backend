import os
import json
import httpx
import asyncio
import websockets
import datetime
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_ID = "34S0dpfCnDpl2MirCfCM5" 
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
REST_BASE = "https://api.derivws.com"
WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"

# --- TRADE SETTINGS ---
STAKE_AMOUNT = 1.00      
STOP_LOSS_AMT = 0.50     
TAKE_PROFIT_AMT = 1.00   
DURATION = 5             
# ----------------------

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False, "symbol": "frxXAUUSD"} 
trade_history = []
bot_task = None

# --- HELPER: Get Demo Account ID ---
async def get_demo_account_id():
    headers = {"Authorization": f"Bearer {TOKEN}", "Deriv-App-ID": APP_ID}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            accounts = response.json().get("data", [])
            demo = next((acc for acc in accounts if acc.get("account_type") == "demo" or acc.get("type") == "demo"), None)
            if demo:
                return demo.get("account_id")
            return None
    except Exception as e:
        print(f"Error fetching demo account ID: {e}")
        return None

# --- HELPER: Get Authenticated WebSocket URL via OTP ---
async def get_authenticated_ws_url():
    account_id = await get_demo_account_id()
    if not account_id:
        print("Could not find demo account ID for OTP.")
        return None
    
    headers = {"Authorization": f"Bearer {TOKEN}", "Deriv-App-ID": APP_ID}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # Request the OTP URL
            otp_response = await client.post(f"{REST_BASE}/trading/v1/options/accounts/{account_id}/otp", headers=headers)
            if otp_response.status_code == 200:
                data = otp_response.json()
                if "data" in data and "ws_url" in data["data"]:
                    return data["data"]["ws_url"]
                elif "ws_url" in data:
                    return data["ws_url"]
            print(f"OTP Error: {otp_response.status_code} - {otp_response.text}")
    except Exception as e:
        print(f"Exception during OTP request: {e}")
    return None

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN)}

@app.get("/api/account")
async def account():
    if not TOKEN:
        raise HTTPException(401, "TOKEN not set.")
    headers = {"Authorization": f"Bearer {TOKEN}", "Deriv-App-ID": APP_ID}
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            accounts = response.json().get("data", [])
            demo = next((acc for acc in accounts if acc.get("account_type") == "demo" or acc.get("type") == "demo"), None)
            if not demo: raise HTTPException(404, "No demo found.")
            return {"balance": demo.get("balance"), "currency": demo.get("currency"), "loginid": demo.get("account_id"), "type": "demo"}
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/api/ticks")
async def ticks(symbol: str = "frxXAUUSD"):
    payload = {"ticks": symbol, "subscribe": 1}
    try:
        async with websockets.connect(WS_PUBLIC, open_timeout=10.0) as ws:
            await ws.send(json.dumps(payload))
            response = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
            return {"symbol": symbol, "quote": response.get("tick", {}).get("quote")}
    except Exception as e:
        raise HTTPException(500, f"WebSocket error: {str(e)}")

@app.get("/api/trades")
def get_trades():
    return trade_history

@app.get("/api/open_trades")
async def open_trades():
    if not TOKEN:
        return []
    try:
        ws_url = await get_authenticated_ws_url()
        if not ws_url:
            return []
            
        async with websockets.connect(ws_url) as ws:
            await ws.send(json.dumps({"portfolio": 1}))
            resp = json.loads(await ws.recv())
            
            if "error" in resp:
                return []
                
            contracts = resp.get("portfolio", {}).get("contracts", [])
            open_list = []
            for c in contracts:
                open_list.append({
                    "id": c.get("contract_id"),
                    "symbol": c.get("symbol"),
                    "type": "Rise" if c.get("contract_type") == "CALL" else "Fall",
                    "buy_price": c.get("buy_price"),
                    "profit": c.get("profit", 0), # Live P&L
                })
            return open_list
    except Exception as e:
        print(f"Error fetching open trades: {e}")
        return []

# --- THE BULLETPROOF STRATEGY ENGINE ---
async def run_strategy():
    symbol = bot_state["symbol"]
    print(f"Strategy started for {symbol}...")
    
    while bot_state["running"]:
        try:
            ws_url = await get_authenticated_ws_url()
            if not ws_url:
                print("Failed to get authenticated WebSocket URL. Retrying in 5s...")
                await asyncio.sleep(5)
                continue
                
            print(f"Connecting to authenticated WS: {ws_url[:50]}...")
            async with websockets.connect(ws_url) as ws:
                # Inner loop for active connection
                while bot_state["running"]:
                    # Use the public endpoint for market data, as it doesn't require auth
                    async with websockets.connect(WS_PUBLIC) as public_ws:
                        candle_req = {"ticks_history": symbol, "adjust_start_time": 1, "count": 50, "end": "latest", "start": 1, "style": "candles", "granularity": 60}
                        await public_ws.send(json.dumps(candle_req))
                        resp = json.loads(await public_ws.recv())
                        
                        if "candles" in resp:
                            df = pd.DataFrame(resp["candles"])
                            df['close'] = df['close'].astype(float)

                            df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
                            df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
                            
                            delta = df['close'].diff()
                            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                            rs = gain / loss
                            df['RSI'] = 100 - (100 / (1 + rs))
                            
                            current = df.iloc[-1]
                            prev = df.iloc[-2]
                            current_price = current['close']

                            long_signal = (prev['EMA_9'] <= prev['EMA_21']) and (current['EMA_9'] > current['EMA_21']) and (current['RSI'] > 50)
                            short_signal = (prev['EMA_9'] >= prev['EMA_21']) and (current['EMA_9'] < current['EMA_21']) and (current['RSI'] < 50)

                            if long_signal:
                                print(f"LONG Signal on {symbol} at {current_price}")
                                await place_trade(ws, "CALL", symbol, current_price)
                                await asyncio.sleep(60) 
                            elif short_signal:
                                print(f"SHORT Signal on {symbol} at {current_price}")
                                await place_trade(ws, "PUT", symbol, current_price)
                                await asyncio.sleep(60)
                    
                    await asyncio.sleep(10) 
                    
        except Exception as e:
            print(f"Strategy Connection Lost: {e}. Reconnecting in 5 seconds...")
            await asyncio.sleep(5)

async def place_trade(ws, contract_type, symbol, entry_price):
    proposal_req = {
        "proposal": 1,
        "amount": STAKE_AMOUNT,
        "basis": "stake",
        "contract_type": contract_type,
        "currency": "USD",
        "duration": DURATION,
        "duration_unit": "m",
        "symbol": symbol
    }
    await ws.send(json.dumps(proposal_req))
    prop_resp = json.loads(await ws.recv())
    
    if "error" in prop_resp:
        print(f"Proposal Error: {prop_resp['error']['message']}")
        return

    proposal_id = prop_resp["proposal"]["id"]

    buy_req = {
        "buy": proposal_id,
        "price": STAKE_AMOUNT,
        "limit_order": {
            "stop_loss": STOP_LOSS_AMT,
            "take_profit": TAKE_PROFIT_AMT
        }
    }
    
    await ws.send(json.dumps(buy_req))
    buy_resp = json.loads(await ws.recv())
    
    if "error" not in buy_resp:
        trade = {
            "id": buy_resp["buy"]["contract_id"],
            "time": datetime.datetime.now().strftime("%H:%M:%S"),
            "type": "Rise (CALL)" if contract_type == "CALL" else "Fall (PUT)",
            "entry": str(entry_price),
            "status": f"Open (SL: ${STOP_LOSS_AMT}, TP: ${TAKE_PROFIT_AMT})"
        }
        trade_history.insert(0, trade)
        print(f"Trade placed: {trade}")

class Toggle(BaseModel):
    symbol: str = "frxXAUUSD"

@app.post("/api/bot/start")
async def start(body: Toggle):
    global bot_task
    bot_state["running"] = True
    bot_state["symbol"] = body.symbol
    if bot_task is None or bot_task.done():
        bot_task = asyncio.create_task(run_strategy())
    return bot_state

@app.post("/api/bot/stop")
def stop():
    global bot_task
    bot_state["running"] = False
    if bot_task:
        bot_task.cancel()
        bot_task = None
    return bot_state
