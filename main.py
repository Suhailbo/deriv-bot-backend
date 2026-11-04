import os
import asyncio
import json
import httpx
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_ID = 1089
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
REST_BASE = "https://api.derivws.com"
WS_PUBLIC = "wss://api.derivws.com/trading/v1/options/ws/public"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False}

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN)}

@app.get("/api/account")
async def account():
    if not TOKEN:
        raise HTTPException(401, "DERIV_API_TOKEN environment variable is not set.")
    
    headers = {
        "Authorization": f"Bearer {TOKEN}",
        "Deriv-App-ID": str(APP_ID)  # Required for PAT authentication
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            print("Fetching accounts via REST...")
            response = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            
            if response.status_code != 200:
                print(f"REST Error: {response.status_code} - {response.text}")
                raise HTTPException(response.status_code, f"Deriv API Error: {response.text}")
            
            data = response.json()
            accounts = data.get("data", [])
            
            if not accounts:
                raise HTTPException(404, "No accounts found for this token.")
            
            # Find the demo account
            demo_account = next((acc for acc in accounts if acc.get("type") == "demo"), None)
            if not demo_account:
                raise HTTPException(404, "No demo account found. Please ensure your token has access to a demo account.")
            
            return {
                "balance": demo_account.get("balance"),
                "currency": demo_account.get("currency"),
                "loginid": demo_account.get("account_id"),
                "type": demo_account.get("type")
            }
            
    except httpx.TimeoutException:
        raise HTTPException(504, "Timeout connecting to Deriv REST API.")
    except Exception as e:
        print(f"Unexpected error: {str(e)}")
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/api/ticks")
async def ticks(symbol: str = "R_100"):
    payload = {"ticks": symbol, "subscribe": 1}
    
    try:
        print(f"Connecting to public WS for {symbol}...")
        # Public endpoint requires NO authentication headers
        async with websockets.connect(WS_PUBLIC, open_timeout=15.0) as ws:
            await ws.send(json.dumps(payload))
            
            # Wait for the first tick response
            response_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            response = json.loads(response_raw)
            
            if "error" in response:
                raise HTTPException(400, response["error"].get("message", "Unknown tick error"))
            
            tick = response.get("tick", {})
            return {
                "symbol": tick.get("symbol"),
                "quote": tick.get("quote"),
                "epoch": tick.get("epoch")
            }
            
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout waiting for tick data.")
    except Exception as e:
        print(f"WS Error: {str(e)}")
        raise HTTPException(500, f"WebSocket error: {str(e)}")

class Toggle(BaseModel):
    symbol: str = "R_100"

@app.post("/api/bot/start")
def start(body: Toggle):
    bot_state["running"] = True
    bot_state["symbol"] = body.symbol
    return bot_state

@app.post("/api/bot/stop")
def stop():
    bot_state["running"] = False
    return bot_state
