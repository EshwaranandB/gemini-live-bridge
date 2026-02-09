import os
import asyncio
import logging
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai.types import LiveConnectConfig, PrebuiltVoiceConfig, VoiceConfig

# --- CONFIGURATION ---
API_KEY = os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise ValueError("❌ GEMINI_API_KEY not found! Set this in Railway variables.")

# Use the latest model ID from your docs
MODEL_ID = "	gemini-2.5-flash-native-audio-preview-12-2025" 

# --- LOGGING SETUP ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GeminiBridge")

# --- APP SETUP ---
app = FastAPI()

# 1. GLOBAL CORS FIX (Crucial for Hugging Face)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- ROUTES ---

@app.get("/")
async def health_check():
    """Simple health check to verify server is running."""
    return {"status": "online", "service": "Gemini Live Bridge", "model": MODEL_ID}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Bi-directional Bridge:
    Client (Hugging Face) <-> Railway <-> Gemini Live API
    """
    await websocket.accept()
    logger.info("🔌 Client Connected via WebSocket")

    # Initialize Gemini Client (using v1alpha as per advanced docs for best features)
    client = genai.Client(api_key=API_KEY, http_options={"api_version": "v1alpha"})

    # Configure the Live Session
    # We ask for AUDIO response and set a specific voice.
    config = {
        "response_modalities": ["AUDIO"],
        "speech_config": {
            "voice_config": PrebuiltVoiceConfig(voice_name="Puck")
        }
    }

    try:
        # Connect to Gemini Live
        async with client.aio.live.connect(model=MODEL_ID, config=config) as session:
            logger.info(f"✨ Connected to Gemini Live ({MODEL_ID})")

            # --- TASK A: Receive Audio from Client -> Send to Gemini ---
            async def receive_from_client():
                try:
                    while True:
                        # 1. Receive raw PCM bytes from Hugging Face
                        data = await websocket.receive_bytes()
                        if not data:
                            break
                        
                        # 2. Send to Gemini
                        # We send "end_of_turn=False" to let Gemini's VAD decide when to reply
                        await session.send(
                            input={"data": data, "mime_type": "audio/pcm"}, 
                            end_of_turn=False 
                        )
                except WebSocketDisconnect:
                    logger.info("⚠️ Client Disconnected (Receive Loop)")
                except Exception as e:
                    logger.error(f"❌ Error receiving from client: {e}")

            # --- TASK B: Receive Audio from Gemini -> Send to Client ---
            async def send_to_client():
                try:
                    while True:
                        # 1. Iterate through the session generator
                        async for response in session.receive():
                            server_content = response.server_content
                            if server_content is None:
                                continue

                            # 2. Handle Audio Output (Model Turn)
                            if server_content.model_turn:
                                for part in server_content.model_turn.parts:
                                    # Check for inline_data (Audio bytes)
                                    if part.inline_data and part.inline_data.data:
                                        # Send raw audio bytes back to Hugging Face
                                        await websocket.send_bytes(part.inline_data.data)

                            # 3. Handle Interruption (User spoke while model was talking)
                            if server_content.interrupted:
                                logger.info("🛑 Gemini was interrupted by user")
                                # Optional: Send a text flag to client to clear audio buffer
                                # await websocket.send_text("INTERRUPTED")

                            # 4. Handle Turn Complete
                            if server_content.turn_complete:
                                logger.info("✅ Gemini Turn Complete")
                except Exception as e:
                    logger.error(f"❌ Error receiving from Gemini: {e}")

            # --- RUN LOOPS CONCURRENTLY ---
            # Using wait allows us to stop if either side disconnects
            await asyncio.wait(
                [asyncio.create_task(receive_from_client()), 
                 asyncio.create_task(send_to_client())],
                return_when=asyncio.FIRST_COMPLETED
            )

    except Exception as e:
        logger.error(f"🔥 Bridge Error: {e}")
        # Try to send error to client if still connected
        try:
            await websocket.send_text(f"Error: {str(e)}")
            await websocket.close()
        except:
            pass
    finally:
        logger.info("👋 Connection Closed")
