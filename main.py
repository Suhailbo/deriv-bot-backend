import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deriv_api import DerivAPI

APP_ID = 1089
TOKEN = os.environ.get("DERIV_API_TOKEN", "")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False}
api = None

async def get_api():
    global api
    if api is None:
        try:
            api = DerivAPI(app_id=APP_ID)
            if TOKEN:
                # Added timeout so it doesn't hang forever
                auth = await asyncio.wait_for(api.authorize(TOKEN), timeout=10.0)
                if "error" in auth:
                    api = None
                    raise HTTPException(401, auth["error"]["message"])
        except asyncio.TimeoutError:
            api = None
            raise HTTPException(504, "Timeout connecting to Deriv. Try again.")
        except Exception as e:
            api = None
            raise HTTPException(500, f"Connection error: {str(e)}")
    return api

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN)}

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
