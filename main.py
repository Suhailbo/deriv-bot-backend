import os
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from deriv_api import DerivAPI

APP_ID = 1089  # public demo app_id
TOKEN = os.environ.get("DERIV_API_TOKEN", "")

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False}
api = None

async def get_api():
    global api
    if api is None:
        api = DerivAPI(app_id=APP_ID)
        if TOKEN:
            auth = await api.authorize(TOKEN)
            if "error" in auth:
                raise HTTPException(401, auth["error"]["message"])
    return api

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN)}

@app.get("/api/account")
async def account():
    client = await get_api()
    bal = await client.balance()
    return {
        "balance": bal.get("balance", {}).get("balance"),
        "currency": bal.get("balance", {}).get("currency"),
        "loginid": bal.get("balance", {}).get("loginid"),
    }

@app.get("/api/ticks")
async def ticks(symbol: str = "R_100"):
    client = await get_api()
    # One-shot tick fetch (for the dashboard price card)
    resp = await client.ticks_history({"ticks_history": symbol, "count": 1, "end": "latest"})
    if "error" in resp:
        raise HTTPException(400, resp["error"]["message"])
    return {"symbol": symbol, "quote": resp["history"]["prices"][-1], "time": resp["history"]["times"][-1]}

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
