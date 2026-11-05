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
WS_TRADING = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

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
        async with websockets.connect(WS_TRADING) as ws:
            await ws.send(json.dumps({"authorize": TOKEN}))
            await ws.recv()
            
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

async def run_strategy():
    symbol = bot_state["symbol"]
    print(f"Strategy started for {symbol}...")
    
    try:
        async with websockets.connect(WS_TRADING) as ws:
            await ws.send(json.dumps({"authorize": TOKEN}))
            await ws.recv() 
            
            while bot_state["running"]:
                candle_req = {"ticks_history": symbol, "adjust_start_time": 1, "count": 50, "end": "latest", "start": 1, "style": "candles", "granularity": 60}
                await ws.send(json.dumps(candle_req))
                resp = json.loads(await ws.recv())
                
                if "candles" in resp:
                    df = pd.DataFrame(resp["candles"])
                    df['close'] = df['close'].astype(float)

                    # Calculate EMA 9 and EMA 21 using standard Pandas
                    df['EMA_9'] = df['close'].ewm(span=9, adjust=False).mean()
                    df['EMA_21'] = df['close'].ewm(span=21, adjust=False).mean()
                    
                    # Calculate RSI 14 using standard Pandas
                    delta = df['close'].diff()
                    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
                    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
                    rs = gain / loss
                    df['RSI'] = 100 - (100 / (1 + rs))
                    
                    current = df.iloc[-1]
                    prev = df.iloc[-2]
                    current_price = current['close']

                    # Check Signals
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
        print(f"Strategy Error: {e}")
        bot_state["running"] = False

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
