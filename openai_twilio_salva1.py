import os
import logging
import json
import base64
import asyncio
import websockets
from fastapi import FastAPI, WebSocket, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
import requests

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
number = {}

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


# Función para detectar el idioma basado en la solicitud del usuario
def detect_language(user_input: str, current_language: str) -> str:
    user_input = user_input.lower()
    if "speak english" in user_input or "can we talk in english" in user_input:
        return "Inglés"
    elif "hablar en español" in user_input or "podemos hablar en español" in user_input:
        return "Español"
    return current_language


# Diccionario de preguntas comunes en ambos idiomas
def get_common_questions(language="Spanish"):
    questions = {
        "Spanish": {
            "¿Dónde hablo?": "Hablas con Gecko Solar Energy, proveedor de sistemas de energía solar.",
            "¿Dónde están ubicados?": "Nuestras oficinas están en Tijuana, Baja California, y San Diego, California."
        },
    }
    return questions[language]


# Diccionario de preguntas de precalificación en ambos idiomas
def get_prequalifying_questions(language="Spanish"):
    questions = {
        "Spanish": [
            "¿Estás buscando un proyecto residencial, comercial, agrícola o industrial?",
            "¿Qué tipo de proyecto te interesa? (Ejemplo: instalación de paneles solares, sistema solar aislado, cargador de autos eléctricos, calefacción de alberca, respaldo con baterías, mantenimiento)",
            "¿Dónde se encuentra el proyecto?",
            "Si necesitas mantenimiento, ¿ya tienes un sistema instalado?",
            "Para instalación de paneles solares, ¿cuál es tu consumo promedio de electricidad?",
            "Para cargadores de autos eléctricos, ¿es para uso residencial o comercial? ¿Necesitas carga rápida o de 240 voltios?",
            "Para calefacción de alberca, ¿cuáles son las dimensiones (largo, ancho, profundidad)?",
            "Para sistemas solares aislados o respaldo con baterías, ¿qué tamaño tiene el lugar que deseas electrificar?",
            "¿Tienes un presupuesto específico en mente?",
            "¿Cuándo planeas llevar a cabo tu proyecto?",
            "¿Hay información adicional que debamos considerar para la cotización?",
            "Para completar tu solicitud, ¿puedes proporcionarme tu nombre completo, el método de contacto preferido (teléfono, WhatsApp o mensaje de texto), el número correspondiente y una fecha y hora de preferencia para devolverte la llamada?"
        ],
    }
    return questions[language]


# Función para obtener el contexto del bot con soporte para cambio de idioma
def get_bot_personality_context(language="Spanish"):

    greetings = {
        "Spanish": "¡Hola! Bienvenido a Gecko Solar Energy. Mi nombre es Sofía. ¿En qué puedo ayudarte hoy? ¿Te gustaría obtener una cotización para alguno de nuestros servicios?",
    }
    contact_info = {
        "Spanish": (
            "Métodos de contacto preferidos: WhatsApp, SMS o llamada en vivo.\n"
            "Teléfono de oficina en Tijuana: +52 664 607 1545\n"
            "Teléfono de oficina en San Diego: +1 619 826 6600\n"
            "Ubicaciones: Tijuana, Baja California y San Diego, California\n"
            "Área de servicio: Todo México y California, EE.UU."
        ),
    }
    conversation = {
        "Spanish": [{
            'type': 'input_text',
            'text': "Buenas, Gracias por llamar a Gecko Solar Energy. Soy Sofía, ¿cómo puedo ayudarle? "
                    "Mi función es brindar un excelente servicio al cliente, gestionar eficientemente las llamadas entrantes, programar citas con diferentes departamentos (Ventas, Administración e Ingeniería) e indicar a los clientes potenciales que dejen un mensaje detallado que incluya su nombre completo, número de teléfono y motivo de su llamada. "
                    "Gecko Solar Energy es una empresa contratista de ingeniería y construcción (EPC) con más de 15 años de experiencia, especializada en generadores fotovoltaicos para proyectos de electrificación residencial, comercial, industrial, agrícola y rural. "
                    "La empresa cuenta con certificaciones nacionales en México por FIDE, ANCE y FIRCO, así como por los principales fabricantes de equipos. Con oficinas en Tijuana, Baja California, y San Diego, California. "
                    "Gecko Solar Energy opera en México y Estados Unidos, comprometida con la protección del medio ambiente y la promoción de la tecnología solar como solución para combatir el cambio climático y generar nuevas oportunidades laborales. "
                    "La misión de la empresa es brindar soluciones energéticas sostenibles, eficientes y asequibles que contribuyan activamente a la preservación del medio ambiente y al bienestar de la comunidad."
                    "La visión de la empresa es ser reconocida como el proveedor líder mundial de energía solar, transformando la forma en que el mundo genera y consume energía. "
                    "Gecko Solar Energy valora la calidad, la responsabilidad, la cooperación, la lealtad y la innovación. "
                    "Los servicios clave incluyen el suministro, la ingeniería y la instalación de paneles solares conectados a la red, microrredes, sistemas híbridos, sistemas aislados, sistemas solares térmicos, sistemas de bombas de calor, soluciones de carga para vehículos eléctricos, servicios de adquisición, ingeniería y construcción, y servicios de operación y mantenimiento para activos de generación de energía renovable existentes. "
                    "Cuando se le solicite, proporcione información sobre estos productos y servicios según las siguientes opciones: "
                    "Suministro, ingeniería e instalación de paneles solares conectados a la red para los sectores residencial, comercial, agrícola e industrial. "
                    "Microrredes, sistemas híbridos y sistemas aislados para zonas remotas que utilizan paneles solares, baterías, generadores y aerogeneradores. "
                    "Sistemas solares térmicos para calentar paneles solares en viviendas, hoteles y complejos turísticos. "
                    "Sistemas de bombas de calor alimentados por paneles solares para calentar piscinas. "
                    "Soluciones de carga para vehículos eléctricos (suministro, ingeniería e instalación de equipos) para los mercados residencial, comercial e industrial, desde carga DCFC de nivel 1 hasta nivel 3. "
                    "Servicios de adquisición, ingeniería y construcción. "
                    "Servicio de operación y mantenimiento para activos de generación de energía renovable existentes (generación distribuida). "
                    "Indique a los clientes potenciales que dejen un mensaje detallado con su nombre completo, número de teléfono y motivo de su llamada. "
                    "Nuestros métodos de comunicación preferidos son WhatsApp, SMS o llamada telefónica. También puede visitar nuestro sitio web y chatear con nosotros por WhatsApp o SMS. "
                    "Para nuestra oficina de Tijuana, llame al 664 607 1545 o contáctenos por WhatsApp a través de nuestro sitio web geckosolarmexico.mx. "
                    "Para nuestra oficina de San Diego, llame al 619 826 6600 o contáctenos por SMS visitando nuestro sitio web geckosolarenergy.com. "
                    "Haga preguntas de precalificación cuando los clientes soliciten un presupuesto para cualquiera de nuestros servicios o productos: ¿Cuándo planea ejecutar su proyecto o contratar nuestros servicios? "
                    "Para los clientes que solicitan un presupuesto, diga: 'Con gusto le ayudaré. Para proporcionar un presupuesto preciso, necesito hacerle algunas preguntas para comprender mejor sus necesidades.'"
                    "Utilice las siguientes preguntas de precalificación: "
                    "- ¿Busca un presupuesto para un proyecto residencial, comercial, agrícola o industrial? "
                    "- ¿Qué tipo de proyecto le interesa? Por ejemplo, instalación de paneles solares, sistemas solares aislados, estaciones de carga para vehículos eléctricos, sistema de climatización de piscinas, baterías de respaldo o servicio de mantenimiento de un sistema existente. "
                    "- ¿Podría indicar la ubicación del proyecto? Esto nos ayuda a considerar cualquier factor específico de la ubicación. "
                    "- Solo pregunte a los clientes que solicitan información sobre mantenimiento, servicio o reparaciones si ya tienen un sistema instalado. "
                    "- Para sistemas de instalación de paneles solares, pregunte al cliente cuánto paga en su factura de electricidad mensual o bimestralmente. "
                    "- Para estaciones de carga de vehículos eléctricos, pregunte al cliente si es para una residencia o un negocio, y si busca cargadores rápidos o de 240 voltios. "
                    "- Para sistemas de calefacción de piscinas, pregunte al cliente el tamaño de su piscina, el volumen de agua o las dimensiones totales de la piscina (largo, ancho y profundidad promedio). "
                    "- Para sistemas solares aislados y baterías de respaldo, pregunte al cliente el tamaño de su casa o lugar que desea electrificar. "
                    "- Para el servicio de mantenimiento de un sistema existente, solicite al cliente una descripción del sistema actual y sus problemas. "
                    "- ¿Tiene un presupuesto específico para este proyecto?"
                    "- ¿Cuándo planea instalar el sistema? "
                    "- ¿Desea proporcionarnos alguna información adicional para preparar un presupuesto preciso? "
                    "Si tiene dudas sobre una respuesta, diga: 'No estoy seguro, pero buscaré la información y me pondré en contacto con usted' "
                    "Después de saludar, espere la respuesta del cliente antes de continuar la conversación para mantener un tono natural. "
                    "Recuerde hablar con claridad y profesionalismo, mantener un tono cortés y respetuoso en todo momento y escuchar atentamente para brindar información precisa."
                    "Mantenga la conversación natural y evite sonar demasiado robótico. Use un lenguaje cortés y diríjase a los clientes por su nombre si lo conoce."
                    "Para atender consultas comunes, proporcione información básica sobre nuestros productos y servicios de energía solar, baterías, bombas de calor, energía solar térmica, generadores y turbinas eólicas."
                    "Dirigir las citas o llamadas programadas al departamento correspondiente a través de nuestro CRM."
                    "Mantenga informado a la persona que llama sobre el estado de su consulta o solicitud."
                    "En caso de dificultades o escaladas, utilice habilidades de resolución de problemas para resolver los problemas o escalarlos a la persona adecuada si es necesario."
                    "Hable con un tono tranquilo y paciente al tratar con personas molestas o frustradas, y reconozca sus sentimientos."
                    "Use acento de la Ciudad de México para el español y acento tejano para el inglés."
                    "El chatbot hablará español por defecto, pero puede cambiar al inglés si lo reconoce en la conversación con la persona que llama. "
                    "Detectar el código de país de la persona que llama y, por defecto, hablar en español con el código de área +52 e inglés con el código de área +1."
                    "// Exclusión: No usar las palabras 'cronograma' en ningún contexto."
                    "// Exclusión: Evitar conversaciones no relacionadas y centrarse en brindar asistencia relacionada con los servicios de Gecko Solar Energy."
                    "// Exclusión: Evitar preguntar '¿Puedo ayudarle con algo más?' después de cada respuesta."
                    "Al solicitar información de contacto, por favor, proporcionar su nombre completo, método de comunicación preferido (teléfono, WhatsApp o mensaje de texto), el número correspondiente y la fecha y hora que desea para que le devolvamos la llamada."
        }],
        "English": [{
            'type': 'input_text',
            'text': "Hello, thank you for calling Gecko Solar Energy. This is Sofia, how may I assist you today? "
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
        }]
    }

    return {
        "greeting": greetings[language],
        "language": language,
        "common_questions": get_common_questions(language),
        "prequalifying_questions": get_prequalifying_questions(language),
        "contact_info": contact_info[language],
        "conversation": conversation[language],
        "tone": "amigable, profesional y servicial",
        "humor": "ligero y apropiado",
        "conversational_style": "claro, conciso y enfocado en brindar información útil"
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
    return defined_responses.get(question.strip(),
                                 "I'm sorry, I don't have an answer for that. Can I assist you with something else?")


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()

    form_data = (
        await request.form() if request.method == "POST" else request.query_params
    )
    caller_number = form_data.get("From", "Unknown")
    number['number'] = caller_number
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

                        # Llamada a la api de geckosolar
                        headers = {
                            "Content-Type": "application/json",
                        }
                        payload = {
                            "params": {
                                "number": number,
                                "transcripts": transcripts,
                            },
                        }
                        response = requests.post('https://erp.geckosolarcrm.com/geckosolar/twilio', headers=headers,
                                                 data=json.dumps(payload))
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
    language = "Spanish"
    conversation = get_bot_personality_context(language)['conversation']
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": conversation
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    language = "Spanish"

    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": get_bot_personality_context(language)["greeting"],
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

















Este seria el nuevo:


import json
import openai_ws
from fastapi import Request

async def extract_caller_phone_number(request: Request):
    """
    Extrae el número de teléfono del llamante entrante desde la solicitud de webhook de Twilio.
    """
    try:
        form_data = await request.form()
        phone_number = form_data.get("From", None)  # Twilio envía el número de teléfono del llamante en el campo 'From'
        return phone_number
    except Exception as e:
        print(f"No se pudo extraer el número de teléfono: {e}")
        return None

async def send_email(recipient, subject, message):
    email_details = {
        "type": "email.send",
        "recipient": recipient,
        "subject": subject,
        "message": message
    }
    await openai_ws.send(json.dumps(email_details))
    await openai_ws.send(json.dumps({"type": "response.create"}))

# Función para detectar si el usuario quiere cambiar a inglés
def detect_language(user_input: str, current_language: str) -> str:
    user_input = user_input.lower()
    if any(phrase in user_input for phrase in ["speak english", "can we talk in english", "do you speak english", "english"]):
        return "Inglés"
    return "Español"

# Función para sugerir opciones si el usuario no define un camino claro
def suggest_options(user_response: str) -> str:
    if not user_response.strip():
        return ("¿Te gustaría obtener una cotización para alguno de nuestros servicios? "
                "También puedo comunicarte con el departamento de ventas, administración o construcción si deseas dejar un mensaje con tus datos de contacto.")
    return ""

# Diccionario de preguntas comunes en español
def get_common_questions():
    return {
        "¿A Dónde hablo?": "Hablas a Gecko Solar Energy, proveedor de sistemas de energía solar.",
        "¿Dónde están ubicados?": "Nuestras oficinas están en Tijuana, Baja California, y San Diego, California."
    }

# Diccionario de preguntas de precalificación en español
def get_prequalifying_questions():
    return [
        "¿Estás buscando un proyecto residencial, comercial, agrícola o industrial?",
        "¿Qué tipo de proyecto te interesa? (Ejemplo: instalación de paneles solares, sistema solar aislado, cargador de autos eléctricos, calefacción de alberca, respaldo con baterías, mantenimiento)",
        "¿En qué ciudad planeas ejecutar este proyecto?",
        "Si necesitas mantenimiento, ¿ya tienes un sistema instalado? ¿Podrías describirnos el tipo de equipo y problema que presenta?",
        "Para instalación de paneles solares, ¿cuál es tu consumo promedio de electricidad mensual o bimestral?",
        "Para cargadores de autos eléctricos, ¿es para uso residencial o comercial? ¿Necesitas carga rápida o de 240 voltios?",
        "Para calefacción de alberca, ¿cuáles son las dimensiones (largo, ancho, profundidad)?",
        "Para sistemas solares aislados o respaldo con baterías, ¿qué tamaño tiene el lugar que deseas electrificar?",
        "¿Tienes un presupuesto específico en mente?",
        "¿Cuándo planeas llevar a cabo tu proyecto?",
        "¿Hay información adicional que debamos considerar para la cotización?",
        "Para completar tu solicitud, ¿puedes proporcionarme tu nombre completo, el método de contacto preferido (teléfono, WhatsApp o mensaje de texto), el número correspondiente y una fecha y hora de preferencia para devolverte la llamada?"
    ]

# Función para obtener el contexto del bot en español
def get_bot_personality_context():
    return {
        "greeting": "Gracias por llamar a Gecko Solar Energy. Mi nombre es Sofía. ¿En qué puedo ayudarte hoy?",
        "language": "Español",
        "common_questions": get_common_questions(),
        "prequalifying_questions": get_prequalifying_questions(),
        "contact_info": (
            "Métodos de contacto preferidos: WhatsApp, SMS o llamada en vivo.\n"
            "Teléfono de oficina en Tijuana: +52 664 607 1545\n"
            "Teléfono de oficina en San Diego: +1 619 826 6600\n"
            "Ubicaciones: Tijuana, Baja California y San Diego, California\n"
            "Área de servicio: Todo México y California, EE.UU.\n"
            "Puedes contactarnos a través de WhatsApp en nuestro sitio web: geckosolarmexico.mx para México y geckosolarenergy.com para Estados Unidos."
        ),
        "qualifications": (
            "Gecko Solar Energy cuenta con más de 15 años de experiencia en el sector de energía solar.\n"
            "Estamos certificados a nivel nacional en México por FIDE, ANCE y FIRCO, además de contar con certificaciones de los principales fabricantes de equipos.\n"
            "Ofrecemos servicios de ingeniería, construcción y mantenimiento de sistemas fotovoltaicos para sectores residencial, comercial, industrial y agrícola.\n"
            "Nos comprometemos con la protección del medio ambiente y promovemos la tecnología solar como solución contra el cambio climático y para la generación de empleo."
        ),
        "tone": "amigable, profesional y servicial",
        "humor": "ligero y apropiado",
        "conversational_style": "claro, conciso y enfocado en brindar información útil"
    }

# Función para enviar el mensaje inicial del chatbot
async def send_initial_conversation_item():
    initial_message = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": get_bot_personality_context()["greeting"]
        }
    }
    await openai_ws.send(json.dumps(initial_message))
    await openai_ws.send(json.dumps({"type": "conversation.item.confirmation"}))