import os
import logging
import json
import base64
import sys
import asyncio
import websockets
from urllib.parse import parse_qs
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
OPENAI_API_KEY = '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'
PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "Hola mi nombre es Gecko IA, espere mientras realizo correctamente la conexion."
)
VOICE = 'alloy'
# LOG_EVENT_TYPES = [
#     'error', 'response.content.done', 'rate_limits.updated',
#     'response.done', 'input_audio_buffer.committed',
#     'input_audio_buffer.speech_stopped', 'input_audio_buffer.speech_started',
#     'session.created', 'response.audio_transcript.done'
# ]
LOG_EVENT_TYPES = [
    'response.audio_transcript.done', 'conversation.item.input_audio_transcription.completed'
]
SHOW_TIMING_MATH = False

app = FastAPI()
transcripts = {
    'ia': [],
    'user': [],
}

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


def get_bot_personality_context():
    return {
        "greeting": "Hello! Welcome to Gecko Solar Energy. My name is Sofia. How can I assist you today? Are you interested in getting a quote on any of our services?",
        "tone": "friendly, professional, and supportive",
        "humor": "light and appropriate, adding a touch of personality without being unprofessional",
        "conversational_style": "engaging, clear, and concise, with an emphasis on providing helpful and accurate information",
        "filler_phrases": ["uh", "um", "hmm", "let me check", "alright.. hmm", "like", "you know"],
        "common_expressions": ["I'm here to help", "Feel free to ask me anything", "I'm happy to assist you"],
        "languages": ["English", "Spanish"],
        "accents": {
            "English": "Texan accent",
            "Spanish": "Mexico City accent"
        },
        "communication_preferences": "Preferred communication modes: WhatsApp, SMS, or live phone call.",
        "company_info": (
            "Gecko Solar Energy is an engineering and construction contracting company (EPC) specializing in "
            "photovoltaic generators for various sectors. We have offices in Tijuana, Baja California, and "
            "San Diego, California, operating across Mexico and the United States."
        ),
        "additional_guidelines": (
            "Detect the country code from the caller and by default speak Spanish to callers with a +52 area code and English for callers with a +1 area code. "
            "// Exclusion: Do not use the words 'chronogram' or 'cronograma' in any context. "
            "// Exclusion: Avoid unrelated conversations and focus on providing assistance related to Gecko Solar Energy's services. "
            "// Exclusion: Avoid asking 'Can I help you with anything else?' after every response."
        )
    }

# Function to fetch predefined answers
defined_responses = {
    "How can I get a quote for a project?": "To provide an accurate quote, I need to ask a few questions to better understand your needs: \n"
                                    "1. Are you looking for a residential, commercial, agricultural, or industrial project?\n"
                                    "2. What type of project are you interested in? (e.g., solar panel installation, off-grid solar, EV charging, swimming pool heating, battery backup, maintenance)\n"
                                    "3. Can you provide the location of the project?\n"
                                    "4. If requesting maintenance, do you have an existing system installed?\n"
                                    "5. For solar panel installation, how much is your average electricity bill?\n"
                                    "6. For EV charging, is this for residential or commercial use, and do you need fast chargers or 240-volt chargers?\n"
                                    "7. For swimming pool heating, what is the pool size (length, width, depth)?\n"
                                    "8. For off-grid or battery backup systems, what is the size of the home or place to be electrified?\n"
                                    "9. Do you have a specific budget in mind?\n"
                                    "10. When are you planning to execute your project?\n"
                                    "11. Is there any additional information you’d like to provide to help us prepare an accurate quote?\n"
                                    "12. Now that I have the details, can you please provide your full name, preferred method of communication (phone, WhatsApp, or text message), the respective number, and a preferred date and time for a callback?"
}

# Function to fetch predefined answers
def get_answer(question: str) -> str:
    return defined_responses.get(question.strip(), "I'm sorry, I don't have an answer for that. Can I assist you with something else?")


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()

    form_data = (
        await request.form() if request.method == "POST" else request.query_params
    )
    caller_number = form_data.get("From", "Unknown")
    logger.info(f"Caller: {caller_number}")

    host = request.url.hostname
    print(f"Host generado {host}")
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream')
    # connect.stream(url=f'wss://143.198.231.197/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


@app.websocket("/media-stream")
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    async with websockets.connect(
            'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
    ) as openai_ws:
        await initialize_session(openai_ws)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None

        async def receive_from_twilio():
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            nonlocal stream_sid, latest_media_timestamp
            try:
                async for message in websocket.iter_text():
                    data = json.loads(message)
                    if data['event'] == 'media' and openai_ws.open:
                        latest_media_timestamp = int(data['media']['timestamp'])
                        audio_append = {
                            "type": "input_audio_buffer.append",
                            "audio": data['media']['payload']
                        }
                        await openai_ws.send(json.dumps(audio_append))
                    elif data['event'] == 'start':
                        stream_sid = data['start']['streamSid']
                        print(f"Incoming stream has started {stream_sid}")
                        response_start_timestamp_twilio = None
                        latest_media_timestamp = 0
                        last_assistant_item = None
                    elif data['event'] == 'mark':
                        if mark_queue:
                            mark_queue.pop(0)
                    elif data['event'] == 'stop':
                        logger.info(f"Call ended. StreamSid: {stream_sid}")
                        break
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio():
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            nonlocal stream_sid, last_assistant_item, response_start_timestamp_twilio
            try:
                async for openai_message in openai_ws:
                    response = json.loads(openai_message)

                    if response['type'] in LOG_EVENT_TYPES:
                        print(f"Received event: {response['type']}", response)

                    if response.get('type') == 'response.audio.delta' and 'delta' in response:
                        audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                        audio_delta = {
                            "event": "media",
                            "streamSid": stream_sid,
                            "media": {
                                "payload": audio_payload
                            }
                        }
                        await websocket.send_json(audio_delta)

                        if response_start_timestamp_twilio is None:
                            response_start_timestamp_twilio = latest_media_timestamp
                            if SHOW_TIMING_MATH:
                                print(f"Setting start timestamp for new response: {response_start_timestamp_twilio}ms")

                        # Update last_assistant_item safely
                        if response.get('item_id'):
                            last_assistant_item = response['item_id']

                        await send_mark(websocket, stream_sid)

                    # Trigger an interruption. Your use case might work better using `input_audio_buffer.speech_stopped`, or combining the two.
                    if response.get('type') == 'input_audio_buffer.speech_started':
                        print("Speech started detected.")
                        if last_assistant_item:
                            print(f"Interrupting response with id: {last_assistant_item}")
                            await handle_speech_started_event()

                    if response['type'] == 'response.output_item.done':
                        print(f"Received response.output_item.done: {response}")

                    if response['type'] == 'response.audio_transcript.done':
                        transcripts['ia'].append(response['transcript'])

                    if response['type'] == 'conversation.item.input_audio_transcription.completed':
                        transcripts['user'].append(response['transcript'])

                    if response['type'] == 'end':
                        print("🚀 La llamada ha finalizado. Procesando transcripción...")
            except Exception as e:
                print(f"Error in send_to_twilio: {e}")

        async def handle_speech_started_event():
            """Handle interruption when the caller's speech starts."""
            nonlocal response_start_timestamp_twilio, last_assistant_item
            print("Handling speech started event.")
            if mark_queue and response_start_timestamp_twilio is not None:
                elapsed_time = latest_media_timestamp - response_start_timestamp_twilio
                if SHOW_TIMING_MATH:
                    print(
                        f"Calculating elapsed time for truncation: {latest_media_timestamp} - {response_start_timestamp_twilio} = {elapsed_time}ms")

                if last_assistant_item:
                    if SHOW_TIMING_MATH:
                        print(f"Truncating item with ID: {last_assistant_item}, Truncated at: {elapsed_time}ms")

                    truncate_event = {
                        "type": "conversation.item.truncate",
                        "item_id": last_assistant_item,
                        "content_index": 0,
                        "audio_end_ms": elapsed_time
                    }
                    await openai_ws.send(json.dumps(truncate_event))

                await websocket.send_json({
                    "event": "clear",
                    "streamSid": stream_sid
                })

                mark_queue.clear()
                last_assistant_item = None
                response_start_timestamp_twilio = None

        async def send_mark(connection, stream_sid):
            if stream_sid:
                mark_event = {
                    "event": "mark",
                    "streamSid": stream_sid,
                    "mark": {"name": "responsePart"}
                }
                await connection.send_json(mark_event)
                mark_queue.append('responsePart')

        await asyncio.gather(receive_from_twilio(), send_to_twilio())


async def send_initial_conversation_item(openai_ws):
    """Send initial conversation item if AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text":  "Good [morning/afternoon], thank you for calling Gecko Solar Energy. This is Sofia, how may I assist you today? "
                        "My role is to provide excellent customer service, efficiently manage incoming calls, schedule appointments with different departments (Sales, Administration, and Engineering), and direct prospects to leave a detailed message including their complete name, phone number, and reason for their call. "
                        "Gecko Solar Energy is an engineering and construction contracting company (EPC) with more than 15 years of experience, specializing in photovoltaic generators for residential, commercial, industrial, agricultural, and rural electrification projects. "
                        "The company is nationally certified in Mexico by FIDE, ANCE, and FIRCO, and by the leading equipment manufacturers. With office locations in Tijuana, Baja California, and San Diego, California. "
                        "Gecko Solar Energy operates across Mexico and the United States, committed to protecting the environment and promoting solar technology as a solution to combat climate change and stimulate new job opportunities. "
                        "The company's mission is to provide sustainable, efficient, and affordable energy solutions that actively contribute to environmental preservation and community well-being. "
                        "The company's vision is to be recognized as the leading global solar energy provider, transforming the way the world generates and consumes energy. "
                        "Gecko Solar Energy values quality, responsibility, cooperation, loyalty, and innovation. "
                        "Key services include grid-tied solar panel supply, engineering, and installation, micro grids, hybrid systems, off-grid systems, solar thermal systems, heat pump systems, EV charging solutions, procurement, engineering, and construction services, and operation and maintenance services for existing renewable energy generating assets. "
                        "When asked, provide information about these products and services based on the following options: "
                        "Grid tied solar panel supply, engineering, and installation for the residential, commercial, agricultural, and industrial sectors. "
                        "Micro grids, hybrid systems, and off-grid systems for remote areas using solar panels, batteries, generators, and wind turbines. "
                        "Solar thermal systems for heating solar panels for homes, hotels, and resorts. "
                        "Heat pump systems powered by solar panels for heating swimming pools. "
                        "EV charging solutions (equipment supply, engineering, and installation) for the residential, commercial, and industrial markets, from level 1 to level 3 DCFC charging. "
                        "Procurement, engineering, and construction services. "
                        "Operation and maintenance service for existing renewable energy generating assets (distributed generation). "
                        "Direct prospects to leave a detailed message including their complete name, phone number, and reason for their call. "
                        "Our preferred communication methods are via WhatsApp, SMS, or live phone call. You can also visit our website and chat with us via WhatsApp or SMS. "
                        "For our Tijuana office, call 664 607 1545 or contact us via WhatsApp through our website geckosolarmexico.mx. "
                        "For our San Diego office, call 619 826 6600 or contact us via SMS by visiting our website geckosolarenergy.com. "
                        "Ask qualifying questions when customers are requesting a quote for any of our services or products: When are you planning to execute your project or contract our services? Use this exact question and choice of words. "
                        "For customers requesting a quote, say 'I'm happy to help you with that. To provide an accurate quote, I need to ask a few questions to better understand your needs.' "
                        "Use the following prequalifying questions: "
                        "- Are you looking for a quote for a residential, commercial, agricultural, or industrial project? "
                        "- What type of project are you interested in? For example, solar panel installation, offgrid solar systems, electric vehicle charging stations, swimming pool heating system, battery backup, or maintenance service on an existing system? "
                        "- Can you please provide the location of the project? This helps us consider any location-specific factors. "
                        "- Only ask customers requesting information about maintenance, service, or repairs if they have an existing system installed. "
                        "- For solar panel installation systems, ask the customer how much they pay in their electricity bill on a monthly or bimonthly basis. "
                        "- For electric vehicle charging stations, ask the customer if this is for a residence or commercial, and if they are looking for fast chargers or 240-volt chargers. "
                        "- For swimming pool heating systems, ask the customer the size of their swimming pool, volume of water, or total dimensions of the swimming pool (length, width, and average depth). "
                        "- For offgrid solar systems and battery backups, ask the customer for the size of their home or place they are trying to electrify. "
                        "- For maintenance service on an existing system, ask the customer for a description of the current system and what is wrong with it. "
                        "- Do you have a specific budget in mind for this project? "
                        "- When are you planning to have the system installed? "
                        "- Is there any additional information you would like to provide to help us prepare an accurate quote? "
                        "If unsure about an answer, say 'I am not sure about that, but I will find the information and get back to you.' "
                        "After greeting, wait for the customer's response before continuing the conversation to keep it natural. "
                        "Remember to speak fast. Make sure to keep a Texas accent and add some filler words like 'uh', 'um', 'hmm', 'let me check', 'alright.. hmm', 'like', 'you know', etc. to sound more natural. "
                        "Don't sound too excited, just talk in a normal, calm tone. "
                        "For customers asking for a ballpark estimate cost, say 'I don't currently have that information, but I will be more than happy to schedule a call with one of our sales agents.' "
                        "Remember to speak clearly and professionally, maintain a courteous and respectful tone at all times, and listen actively to provide accurate information. "
                        "Keep the conversation natural and avoid sounding too robotic. Use polite language and address clients by their name if known. "
                        "For handling common inquiries, provide basic information about our solar, battery, heat pumps, solar thermal, generator, and wind turbine products and services. "
                        "Route booked appointments or booked calls to the appropriate department via our CRM. "
                        "Keep the caller informed about the status of their inquiry or request. "
                        "In case of any difficulties or escalations, use problem-solving skills to resolve issues or escalate them to the appropriate person if necessary. "
                        "Speak in a calm and patient tone when dealing with upset or frustrated callers, and acknowledge their feelings. "
                        " Use a Mexico City accent for Spanish and a Texan accent for English. "
                        "The chatbot will speak Spanish by default but can switch to English if recognized in the conversation with the caller. "
                        "Detect the country code from the caller and by default speak Spanish to callers with a +52 area code and English for callers with a +1 area code. "
                        "// Exclusion: Do not use the words 'chronogram' or 'cronograma' in any context."
                        "// Exclusion: Avoid unrelated conversations and focus on providing assistance related to Gecko Solar Energy's services."
		                "//Exclusion: Avoid asking Can i help you with anything else after every response."
                        "When asking for contact information, please provide your full name, preferred method of communication (phone, WhatsApp, or text message), the respective number, and a preferred date and time for a callback."




                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "es",
            },
            "tools": [
                {
                    "type": "function",
                    "name": "end_twilio_call",
                    "description": "Ends the call if the conversation has concluded.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "call_sid": {"type": "string"}
                        }
                    }
                }
            ]
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    await send_initial_conversation_item(openai_ws)


async def end_twilio_call(call_sid):
    logger.info("AI Assistant Ending Twilio call...")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)