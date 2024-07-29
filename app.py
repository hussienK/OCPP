import asyncio
import os
import websockets

# Get the port from environment variables (default to 8765 if not set)

async def handle_message(websocket, path):
    async for message in websocket:
        print(f"Received message: {message}")

start_server = websockets.serve(handle_message, "0.0.0.0", 9000)

asyncio.get_event_loop().run_until_complete(start_server)
print(f"WebSocket server started on ws://0.0.0.0: 9000")
asyncio.get_event_loop().run_forever()
