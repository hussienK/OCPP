import asyncio
import os
import websockets
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Get the port from environment variables (default to 8765 if not set)
PORT = int(os.getenv("PORT", 8765))

async def handle_message(websocket, path):
    async for message in websocket:
        print(f"Received message: {message}")

start_server = websockets.serve(handle_message, "0.0.0.0", PORT)

asyncio.get_event_loop().run_until_complete(start_server)
print(f"WebSocket server started on ws://0.0.0.0:{PORT}")
asyncio.get_event_loop().run_forever()
