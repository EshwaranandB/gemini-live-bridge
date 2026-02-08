import asyncio
import websockets
import pyaudio
import json

# REPLACE WITH YOUR RAILWAY URL AFTER DEPLOYMENT
# URL = "ws://localhost:8080/ws" 
URL = "wss://gemini-live-service-production.up.railway.app/ws"

# Audio Config
FORMAT = pyaudio.paInt16
CHANNELS = 1
RATE_IN = 16000  # Mic input
RATE_OUT = 24000 # Gemini output (usually 24k)
CHUNK = 512

async def run_client():
    p = pyaudio.PyAudio()
    
    # Input Stream (Mic)
    mic_stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE_IN, input=True, frames_per_buffer=CHUNK)
    
    # Output Stream (Speaker)
    speaker_stream = p.open(format=FORMAT, channels=CHANNELS, rate=RATE_OUT, output=True)

    print(f"Connecting to {URL}...")
    try:
        async with websockets.connect(URL) as ws:
            print("✅ Connected! Start speaking into your mic...")

            async def send_audio():
                while True:
                    try:
                        data = mic_stream.read(CHUNK, exception_on_overflow=False)
                        await ws.send(data)
                        await asyncio.sleep(0.01)
                    except Exception as e:
                        print(f"Send Error: {e}")
                        break

            async def receive_audio():
                while True:
                    try:
                        response = await ws.recv()
                        if isinstance(response, bytes):
                            # Play audio directly
                            speaker_stream.write(response)
                        else:
                            print(f"Text: {response}")
                    except Exception as e:
                        print(f"Receive Error: {e}")
                        break

            await asyncio.gather(send_audio(), receive_audio())
    except Exception as e:
        print(f"❌ Connection failed: {e}")
    finally:
        mic_stream.stop_stream()
        mic_stream.close()
        speaker_stream.stop_stream()
        speaker_stream.close()
        p.terminate()

if __name__ == "__main__":
    # pip install pyaudio websockets
    asyncio.run(run_client())
