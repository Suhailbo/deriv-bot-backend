import os
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# This is your Personal App ID.
# If you have already replaced 1089 with your own, keep it. 
# If not, you MUST get one from developers.deriv.com and put it here.
APP_ID = "34S0dpfCnDpl2MirCfCM5"
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
REST_BASE = "https://api.derivws.com"

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
            print("Fetching accounts via REST...")
            response = await client.get(f"{REST_BASE}/trading/v1/options/accounts", headers=headers)
            
            if response.status_code != 200:
                print(f"REST Error: {response.status_code} - {response.text}")
                raise HTTPException(response.status_code, f"Deriv API Error: {response.text}")
            
            data = response.json()
            accounts = data.get("data", [])
            
            if not accounts:
                raise HTTPException(404, "No accounts found for this token.")
            
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
    # This endpoint is currently a placeholder and will be replaced in the next step.
    # It will eventually use a WebSocket connection for live data.
    return {"symbol": symbol, "quote": "123.45", "message": "Placeholder - live WS connection coming next"}

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
