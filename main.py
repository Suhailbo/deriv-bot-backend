import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deriv_api import DerivAPI

APP_ID = 1089
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
# Explicitly set the current WebSocket endpoint
ENDPOINT = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False}
api = None
authorized = False

async def get_api():
    global api, authorized
    if api is None or not authorized:
        try:
            if api is None:
                # Pass the explicit endpoint here
                print(f"Attempting to connect to: {ENDPOINT}")
                api = DerivAPI(endpoint=ENDPOINT, app_id=APP_ID)
            
            if TOKEN:
                print("Attempting to authorize with token...")
                auth = await asyncio.wait_for(api.authorize(TOKEN), timeout=15.0)
                print(f"Auth response: {auth}") # Log the full auth response
                
                if "error" in auth:
                    authorized = False
                    error_msg = auth['error'].get('message', 'Unknown error')
                    print(f"Deriv Auth Error: {error_msg}")
                    raise HTTPException(401, f"Deriv Auth Error: {error_msg}")
                else:
                    authorized = True
                    print("Successfully authorized with Deriv.")
        except asyncio.TimeoutError:
            api = None
            authorized = False
            print("CRITICAL: Timeout connecting or authorizing with Deriv.")
            raise HTTPException(504, "Timeout connecting to Deriv. The server may be blocked from accessing the endpoint.")
        except Exception as e:
            api = None
            authorized = False
            print(f"CRITICAL: Connection error: {str(e)}")
            raise HTTPException(500, f"Connection error: {str(e)}")
    return api

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN), "authorized": authorized}

@app.get("/api/account")
async def account():
    client = await get_api()
    try:
        bal = await asyncio.wait_for(client.balance(), timeout=10.0)
        return {
            "balance": bal.get("balance", {}).get("balance"),
            "currency": bal.get("balance", {}).get("currency"),
            "loginid": bal.get("balance", {}).get("loginid"),
        }
    except Exception as e:
        print(f"Error fetching balance: {str(e)}")
        raise HTTPException(500, f"Error fetching balance: {str(e)}")

@app.get("/api/ticks")
async def ticks(symbol: str = "R_100"):
    client = await get_api()
    try:
        resp = await asyncio.wait_for(
            client.ticks_history({"ticks_history": symbol, "count": 1, "end": "latest"}), 
            timeout=10.0
        )
        if "error" in resp:
            raise HTTPException(400, resp["error"]["message"])
        return {"symbol": symbol, "quote": resp["history"]["prices"][-1], "time": resp["history"]["times"][-1]}
    except Exception as e:
        print(f"Error fetching tick: {str(e)}")
        raise HTTPException(500, f"Error fetching tick: {str(e)}")

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
