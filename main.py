import json
import base64
import asyncio
import time
from typing import Optional

import requests
from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from openai import AsyncOpenAI
from openai._ws import connect
from twilio.twiml.voice_response import VoiceResponse, Connect

app = FastAPI()
OPENAI_API_KEY = "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"
VOICE = "nova"
conversations = {}
transcripts = {}

SYSTEM_MESSAGE = (
    "Eres Sofia, Asistente Virtual de Gecko Solar Energy. Agenda citas, responde dudas y da seguimiento profesional. "
    "Cuando te pidan agendar una cita, pide nombre completo, fecha/hora y tipo de proyecto. "
    "Luego, llama automáticamente a schedule_appointment."
)

TOOLS = [
    {
        "type": "function",
        "name": "schedule_appointment",
        "description": "Agendar cita",
        "parameters": {
            "type": "object",
            "properties": {
                "full_name": {"type": "string"},
                "phone_number": {"type": "string"},
                "appointment_date": {"type": "string"},
                "appointment_time": {"type": "string"},
                "project_type": {"type": "string"}
            },
            "required": ["full_name", "phone_number", "appointment_date", "appointment_time"]
        }
    }
]

@app.post("/incoming-call")
async def handle_incoming_call(request: Request):
    form = await request.form()
    call_sid = form.get("CallSid")
    from_number = form.get("From")
    to_number = form.get("To")

    conversations[call_sid] = {
        "caller_number": from_number,
        "to_number": to_number,
    }

    response = VoiceResponse()
    connect = Connect()
    connect.stream(url=f"wss://{request.url.hostname}/media-stream/{call_sid}")
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

@app.websocket("/media-stream/{call_sid}")
async def media_stream(websocket: WebSocket, call_sid: str):
    await websocket.accept()
    caller = conversations[call_sid]["caller_number"]

    async with connect(
        "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview",
        extra_headers={
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1"
        }
    ) as openai_ws:

        await initialize_session(openai_ws)
        await send_initial_conversation_item(openai_ws)

        async def from_twilio():
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media':
                        await openai_ws.send(json.dumps({
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }))
            except WebSocketDisconnect:
                await openai_ws.close()

        async def to_twilio():
            async for msg in openai_ws:
                data = json.loads(msg)

                if data.get("type") == "response.audio.delta":
                    payload = base64.b64encode(base64.b64decode(data['delta'])).decode("utf-8")
                    await websocket.send_json({
                        "event": "media",
                        "streamSid": call_sid,
                        "media": {"payload": payload}
                    })

                if data.get("type") == "conversation.item.function_call":
                    if data['function_call']['name'] == "schedule_appointment":
                        args = json.loads(data['function_call']['arguments'])
                        args['phone_number'] = caller
                        await schedule_appointment(**args)
                        confirmation = {
                            "type": "conversation.item.create",
                            "item": {
                                "type": "message",
                                "role": "assistant",
                                "content": [{"type": "text", "text": f"Cita agendada para {args['appointment_date']} a las {args['appointment_time']}"}]
                            }
                        }
                        await openai_ws.send(json.dumps(confirmation))

        await asyncio.gather(from_twilio(), to_twilio())

async def initialize_session(openai_ws):
    session_update = {
        "type": "session.update",
        "session": {
            "model": "gpt-4o",
            "turn_detection": {
                "type": "server_vad",
                "create_response": True,
                "interrupt_response": False,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.7,
            "input_audio_transcription": {"model": "gpt-4o-transcribe"},
            "tools": TOOLS
        }
    }
    await openai_ws.send(json.dumps(session_update))

async def send_initial_conversation_item(openai_ws):
    greeting = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [{
                "type": "text",
                "text": "Hola, quiero agendar una cita."
            }]
        }
    }
    await openai_ws.send(json.dumps(greeting))
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def schedule_appointment(full_name, phone_number, appointment_date, appointment_time, project_type=None):
    data = {
        "full_name": full_name,
        "phone_number": phone_number,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "project_type": project_type
    }
    print("📅 Agendando cita:", data)
    # Aquí puedes guardar en una base de datos, Google Calendar, Odoo, etc.
    return {"status": "ok"}