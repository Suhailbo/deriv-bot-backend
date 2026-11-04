import os
import json
import httpx
import asyncio
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# Your App ID
APP_ID = "34S0dpfCnDpl2MirCfCM5" 
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
REST_BASE = "https://api.derivws.com"
# The exact public WS endpoint Deriv support told us to use
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
        "Deriv-App-ID": APP_ID
    }
    
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            if response.status_code != 200:
                raise HTTPException(response.status_code, f"Deriv API Error: {response.text}")
            
            accounts = response.json().get("data", [])
            demo_account = next((acc for acc in accounts if acc.get("account_type") == "demo" or acc.get("type") == "demo"), None)
            
            if not demo_account:
                raise HTTPException(404, "No demo account found.")
            
            return {
                "balance": demo_account.get("balance"),
                "currency": demo_account.get("currency"),
                "loginid": demo_account.get("account_id"),
                "type": demo_account.get("account_type", demo_account.get("type"))
            }
            
    except httpx.TimeoutException:
        raise HTTPException(504, "Timeout connecting to Deriv REST API.")
    except Exception as e:
        raise HTTPException(500, f"Error: {str(e)}")

@app.get("/api/ticks")
async def ticks(symbol: str = "R_100"):
    payload = {"ticks": symbol, "subscribe": 1}
    
    try:
        # Connect to the public WebSocket for live market data
        async with websockets.connect(WS_PUBLIC, open_timeout=10.0) as ws:
            await ws.send(json.dumps(payload))
            
            # Wait for the first tick to arrive
            response_raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
            response = json.loads(response_raw)
            
            if "error" in response:
                raise HTTPException(400, response["error"].get("message", "Unknown error"))
            
            tick = response.get("tick", {})
            return {
                "symbol": tick.get("symbol"),
                "quote": tick.get("quote"),
                "epoch": tick.get("epoch")
            }
            
    except asyncio.TimeoutError:
        raise HTTPException(504, "Timeout waiting for live tick.")
    except Exception as e:
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
