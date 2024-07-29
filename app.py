import asyncio
import os
import websockets
import logging

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

async def handle_message(websocket, path):
    async for message in websocket:
        logger.info(f"Received message: {message}")
        await websocket.send(f"Echo: {message}")

async def start_server():
    logger.info(f"Starting server on port 9000")
    try:
        server = await websockets.serve(handle_message, "0.0.0.0", 9000)
        logger.info(f"Server started on ws://0.0.0.0:9000}")
        await server.wait_closed()
    except Exception as e:
        logger.error(f"Failed to start server: {e}", exc_info=True)

if __name__ == "__main__":
    asyncio.run(start_server())
