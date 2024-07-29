import asyncio
import os
import websockets

async def handle_message(websocket, path):
    async for message in websocket:
        print(f"Received message: {message}")
        await websocket.send(f"Echo: {message}")

async def start_server():
    print(f"Starting server on port 9000")
    try:
        server = await websockets.serve(handle_message, "0.0.0.0", 9000)
        print(f"Server started on ws://0.0.0.0: 9000")
        await server.wait_closed()
    except Exception as e:
        print(f"Failed to start server: {e}")

asyncio.get_event_loop().run_until_complete(start_server())
asyncio.get_event_loop().run_forever()
