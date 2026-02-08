import os
import asyncio
import json
import logging
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from google import genai
from google.genai import types

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GeminiLiveBridge")

app = FastAPI()

# Allow CORS for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
API_KEY = os.environ.get("GOOGLE_API_KEY")
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025" 

if not API_KEY:
    logger.error("CRITICAL: GOOGLE_API_KEY not found in environment variables!")

# Initialize Gemini Client
client = genai.Client(api_key=API_KEY, http_options={'api_version': 'v1alpha'})

@app.get("/health")
async def health_check():
    return {"status": "ok", "model": MODEL}

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    logger.info("Client connected to WebSocket")

    # 1. Configure Gemini Live Session
    # Requesting AUDIO output for the avatar
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"], 
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                    voice_name="Puck" # Options: Puck, Charon, Kore, Fenrir, Aoede
                )
            )
        )
    )

    try:
        # 2. Connect to Google
        async with client.aio.live.connect(model=MODEL, config=config) as session:
            logger.info(f"Connected to Gemini Live Session: {session.session_id}")
            
            # --- Task A: Upstream (Client -> Server -> Gemini) ---
            async def upstream_handler():
                try:
                    while True:
                        # Receive message from Client (Linly)
                        # Expecting raw bytes for audio
                        message = await websocket.receive()
                        
                        if "bytes" in message and message["bytes"]:
                            # Forward Audio Data (Raw PCM)
                            await session.send(input={"data": message["bytes"], "mime_type": "audio/pcm"}, end_of_turn=False)
                        
                        elif "text" in message and message["text"]:
                            # Forward Text/Control Messages
                            try:
                                data = json.loads(message["text"])
                                if data.get("type") == "text_input":
                                    await session.send(input=data["content"], end_of_turn=True)
                            except:
                                pass # Ignore malformed text
                except WebSocketDisconnect:
                    logger.info("Client disconnected (Upstream)")
                    raise
                except Exception as e:
                    logger.error(f"Upstream Error: {e}")

            # --- Task B: Downstream (Gemini -> Server -> Client) ---
            async def downstream_handler():
                try:
                    while True:
                        async for response in session.receive():
                            # 1. Audio Data
                            if response.data:
                                # Forward raw bytes directly to client
                                await websocket.send_bytes(response.data)
                            
                            # 2. Text/Transcript Data (Optional)
                            if response.text:
                                payload = json.dumps({
                                    "type": "text",
                                    "content": response.text
                                })
                                await websocket.send_text(payload)
                                
                            # 3. Turn Complete Signal
                            if response.server_content and response.server_content.turn_complete:
                                await websocket.send_text(json.dumps({"type": "turn_complete"}))
                                
                except Exception as e:
                    logger.error(f"Downstream Error: {e}")

            # Run both tasks until one fails/disconnects
            await asyncio.gather(upstream_handler(), downstream_handler())

    except Exception as e:
        logger.error(f"Session Error: {e}")
        try:
            await websocket.close(code=1011)
        except:
            pass
