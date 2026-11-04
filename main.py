import os
import asyncio
import json
import websockets
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

APP_ID = 1089
TOKEN = os.environ.get("DERIV_API_TOKEN", "")
# The endpoint we confirmed is correct
ENDPOINT = f"wss://ws.derivws.com/websockets/v3?app_id={APP_ID}"

app = FastAPI()
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

bot_state = {"running": False}

async def send_deriv_request(payload: dict, timeout: float = 10.0):
    """Opens a connection, sends a request, and waits for the response."""
    try:
        print(f"Connecting to Deriv: {ENDPOINT}")
        async with websockets.connect(ENDPOINT, open_timeout=timeout) as ws:
            print("WebSocket connected. Sending payload...")
            await ws.send(json.dumps(payload))
            
            print("Waiting for response...")
            response_raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            response = json.loads(response_raw)
            print(f"Received response: {response}")
            return response
            
    except asyncio.TimeoutError:
        print(f"Timeout occurred after {timeout} seconds.")
        raise HTTPException(504, "Timeout connecting to Deriv. Please try again.")
    except websockets.exceptions.WebSocketException as e:
        print(f"WebSocket error: {e}")
        raise HTTPException(500, f"WebSocket error: {str(e)}")
    except Exception as e:
        print(f"Unexpected error: {e}")
        raise HTTPException(500, f"Unexpected error: {str(e)}")

@app.get("/api/health")
def health():
    return {"ok": True, "bot": bot_state, "token_configured": bool(TOKEN)}

@app.get("/api/account")
async def account():
    if not TOKEN:
        raise HTTPException(401, "DERIV_API_TOKEN environment variable is not set.")
    
    # Manually authorize to get an active connection
    auth_payload = {"authorize": TOKEN}
    auth_response = await send_deriv_request(auth_payload)
    
    if "error" in auth_response:
        error_msg = auth_response["error"].get("message", "Unknown auth error")
        raise HTTPException(401, f"Deriv Auth Error: {error_msg}")
    
    # If authorized, get the balance
    balance_payload = {"balance": 1}
    bal_response = await send_deriv_request(balance_payload)
    
    if "error" in bal_response:
        error_msg = bal_response["error"].get("message", "Unknown balance error")
        raise HTTPException(500, f"Error fetching balance: {error_msg}")
        
    return {
        "balance": bal_response.get("balance", {}).get("balance"),
        "currency": bal_response.get("balance", {}).get("currency"),
        "loginid": bal_response.get("balance", {}).get("loginid"),
    }

@app.get("/api/ticks")
async def ticks(symbol: str = "R_100"):
    tick_payload = {
        "ticks_history": symbol,
        "count": 1,
        "end": "latest"
    }
    tick_response = await send_deriv_request(tick_payload)
    
    if "error" in tick_response:
        error_msg = tick_response["error"].get("message", "Unknown tick error")
        raise HTTPException(400, error_msg)
        
    history = tick_response.get("history", {})
    prices = history.get("prices", [])
    times = history.get("times", [])
    
    if not prices:
        raise HTTPException(500, "No price data received from Deriv.")
        
    return {
        "symbol": symbol,
        "quote": prices[-1],
        "time": times[-1]
    }

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
