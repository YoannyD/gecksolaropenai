import os
import time
import logging
import json
import base64
import datetime
import asyncio
import openai
from websockets.client import connect
from fastapi import FastAPI, WebSocket, Request, HTTPException, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.twiml.voice_response import VoiceResponse, Connect, Say, Stream
from dotenv import load_dotenv
import requests
from twilio.rest import Client
import smtplib
from email.mime.text import MIMEText
from pydantic import BaseModel
from typing import Optional, Dict, Any

from openai_twilio_real import SYSTEM_MESSAGE

load_dotenv()

logger = logging.getLogger(__name__)

# Configuracion TWILIO
TWILIO_ACCOUNT_SID = 'AC6c9b862207e6798a5f9dc336b404584c'
TWILIO_AUTH_TOKEN = 'd5895a228eeb5929bfb288ce31661bea'

twilio_client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

TWILIO_PHONE_NUMBER = '+526644149671'

# Configuration
OPENAI_API_KEY = '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'
openai.api_key = OPENAI_API_KEY
PORT = int(os.getenv('PORT', 5050))

VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'response.audio_transcript.done', 'conversation.item.input_audio_transcription.completed', 'conversation.item.function_call'
]
SHOW_TIMING_MATH = False

sender_email = "ivr@geckosolarenergy.com"
sender_password = "IVR#crm.1"

app = FastAPI()
transcripts = {

}
number = {}
conversations = {}

if not OPENAI_API_KEY:
    raise ValueError('Missing the OpenAI API key. Please set it in the .env file.')


class CallRequest(BaseModel):
    phone_number: str
    lead_id: str
    project_type: str
    system_instructions: Optional[str] = None


class CallResponse(BaseModel):
    call_sid: str
    status: str


SYSTEM_MESSAGE_LANGUAGE = {
    'Spanish': (
        "Eres Sofia Asistente Virtual de Gecko Solar Energy, eres amigable, profesional y servicial."
        "Con un humor ligero y apropiado. "
        "Se clara , concisa y enfocada en brindar información útil "
        "Si te preguntan en espanol por los metodos de contactos Teléfono oficina: +52 664 607 1545, Email: hello@geckosolarenergy.us"
        "Si te preguntan en ingles por los metodos de contactos Office phone: +1 619 826 6600, Email: hello@geckosolarenergy.us"
        "Cuando el usuario pida agendar una cita: "
        "1. Pide nombre, fecha/hora y tipo de proyecto EN ESE ORDEN. "
        "2. CONFIRMA los datos repitiéndolos. "
        "3. AUTOMÁTICAMENTE activa schedule_appointment con los datos. "
        "Ejemplo: "
        "'Agendaré tu cita para [fecha] a las [hora] para [proyecto].' "
        "LUEGO llama a la función sin preguntar."
    ),
    'English': (
        "You are Sofia, Gecko Solar Energy's Virtual Assistant. You are friendly, professional, and helpful."
        "With a lighthearted and appropriate sense of humor."
        "Be clear, concise, and focused on providing useful information."
        "If they ask you in Spanish for contact methods, Office Phone: +52 664 607 1545, Email: hello@geckosolarenergy.us."
        "If they ask you in English for contact methods, Office Phone: +1 619 826 6600, Email: hello@geckosolarenergy.us."
        "When the user requests to schedule an appointment: "
        "1. Ask for their full name, appointment date/time, and project type IN THAT ORDER. "
        "2. CONFIRM the details by repeating them. "
        "3. AUTOMATICALLY call schedule_appointment with the collected information. "
        "Example: "
        "'I’ll schedule your appointment for [date] at [time] for a [project type] project.' "
        "Then call the function without asking."
    ),
}


@app.get("/", response_class=JSONResponse)
async def index_page():
    return {"message": "Twilio Media Stream Server is running!"}


@app.post("/make-call")
async def make_outbound_call(call_request: CallRequest, request: Request):
    """Initiate an outbound call with Twilio"""
    try:
        # Create the call
        call = twilio_client.calls.create(
            to=call_request.phone_number,
            from_=TWILIO_PHONE_NUMBER,
            url=f"{request.base_url}outbound-call-handler",
        )

        call_sid = call.sid

        conversations[call_sid] = {
            "status": "initiated",
            "lead_id": call_request.lead_id,
            "project_type": call_request.project_type,
            "stage": call_request.stage,
            "start_time": time.time(),
            "outbound": True,
        }

        return CallResponse(call_sid=call_sid, status="initiated")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error making call: {str(e)}")


@app.post("/outbound-call-handler")
async def handle_outbound_call(request: Request):
    """Handle the outbound call once it's answered"""
    response = VoiceResponse()

    form_data = (
        await request.form() if request.method == "POST" else request.query_params
    )

    call_sid = form_data.get("CallSid")
    caller_number = form_data.get("From", "Unknown")
    to_number = form_data.get("To", "Unknown")

    # Update conversation status
    if call_sid in conversations:
        conversations[call_sid]["status"] = "in-progress"
        conversations[call_sid]["caller_number"] = caller_number
        conversations[call_sid]["to_number"] = to_number
    number['number'] = caller_number
    logger.info(f"Caller: {caller_number}")

    host = request.url.hostname
    print(f"Host generado {host}")
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream/{call_sid}')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


# Función para detectar el idioma basado en la solicitud del usuario
def detect_language(user_input: str, current_language: str) -> str:
    user_input = user_input.lower()
    if "speak english" in user_input or "can we talk in english" in user_input:
        return "Inglés"
    elif "hablar en español" in user_input or "podemos hablar en español" in user_input:
        return "Español"
    return current_language


def suggest_options(user_response: str) -> str:
    if not user_response.strip():
        return ("¿Te gustaría obtener una cotización para alguno de nuestros servicios? "
                "También puedo comunicarte con el departamento de ventas, administración o construcción si deseas dejar un mensaje con tus datos de contacto.")
    return ""


# Diccionario de preguntas comunes en ambos idiomas
def get_common_questions(language="Spanish"):
    questions = {
        "Spanish": {
            "¿A Dónde hablo?": "Hablas a Gecko Solar Energy, proveedor de sistemas de energía solar.",
            "¿Dónde están ubicados?": "Nuestras oficinas están en Tijuana, Baja California."
        },
        "English": {
            "Where am I speaking to?": "You are speaking to Gecko Solar Energy, a provider of solar energy systems.",
            "Where are they located?": "Our offices are in San Diego, California."
        },
        "Spanish_lead_assigned": {
            "¿A Dónde hablo?": "Hablas a Gecko Solar Energy, proveedor de sistemas de energía solar.",
            "¿Dónde están ubicados?": "Nuestras oficinas están en Tijuana, Baja California."
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
        "English": [
            "Are you looking for a residential, commercial, agricultural, or industrial project?",
            "What type of project are you interested in? (Example: solar panel installation, off-grid solar system, electric car charger, pool heating, battery backup, maintenance)",
            "Where is the project located?",
            "If you need maintenance, do you already have a system installed?",
            "For solar panel installation, what is your average electricity consumption?",
            "For electric car chargers, are they for residential or commercial use? Do you need fast charging or 240-volt charging?",
            "For pool heating, what are the dimensions (length, width, depth)?",
            "For off-grid solar or battery backup systems, how big is the space you want to electrify?",
            "Do you have a specific budget in mind?",
            "When do you plan to carry out your project?",
            "Is there additional information we should consider for the quote?",
            "To complete your request, could you please provide your full name, preferred contact method (phone, WhatsApp, or text message), the corresponding number, and a preferred date and time to return your call?"
        ],
        "Spanish_lead_assigned": [
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
    greeting_text = "Hola"

    greetings = {
        "Spanish": "Gracias por llamar a Gecko Solar Energy. Mi nombre es Sofía. ¿En qué puedo ayudarte hoy?",
        "English": "Thank you for calling Gecko Solar Energy. My name is Sofia. How can I help you today?",
        "Spanish_lead_assigned": "Mi nombre es Sofía llamo desde Gecko Solar Energy",
    }
    contact_info = {
        "Spanish": (
            "Métodos de contacto preferidos: WhatsApp, SMS o llamada en vivo.\n"
            "Teléfono de oficina en Tijuana: +52 664 607 1545\n"
            "Ubicaciones: Tijuana, Baja California\n"
            "Área de servicio: Todo México"
        ),
        "English": (
            "Preferred contact methods: WhatsApp, SMS, or live call.\n"
            "Office phone number in San Diego: +1 619 826 6600\n"
            "Locations: San Diego, California\n"
            "Service Area: California, USA."
        ),
        "Spanish_lead_assigned": (
            "Métodos de contacto preferidos: WhatsApp, SMS o llamada en vivo.\n"
            "Teléfono de oficina en Tijuana: +52 664 607 1545\n"
            "Ubicaciones: Tijuana, Baja California\n"
            "Área de servicio: Todo México"
        ),
    }
    conversation = {
        "Spanish": (
            f"""1.- Canalizar solicitud entrante.
                        2.-Recabar información
                        3.-Agendar llamadas o mandar mensajes.
                        4.-Recabar información específica y precalificar.
                        """
        ),
        "English": '',
        "Spanish_lead_assigned": '',
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


def get_tools(language="Spanish"):
    dicc = {
        'Spanish': [
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
            },
            {"type": "function",
             "name": "grid_tied_solar",
             "description": """Paneles solares conectados a la red""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Residencial, Comercial, Industrial, Agrícola",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Es para tu casa o negocio?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Cuánto pagas actualmente en tu recibo de luz?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Dónde se encuentra ubicado el proyecto?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "offgrid_hybrid_microgrids",
             "description": """Sistemas aislados / Microrredes / Híbridos""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Rural, Sin acceso a red",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Qué espacio deseas electrificar (tamaño en m² o descripción)?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Tienes algún generador o batería actualmente?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Qué electrodomésticos o cargas deseas alimentar?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "solar_thermal",
             "description": """Sistemas solares térmicos""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Agua caliente sanitaria, Calefacción de piscinas",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Es para agua caliente sanitaria o calefacción de piscina?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Qué capacidad o tamaño tiene el sistema actual, si existe?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Qué tipo de instalación prefieres (techo/plano)?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "heat_pumps",
             "description": """Bombas de calor alimentadas por solar""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Calefacción de piscinas",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Qué tamaño tiene la alberca (largo, ancho, profundidad)?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Qué temperatura deseas mantener?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Está techada o al aire libre?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "ev_chargers",
             "description": """Estaciones de carga para vehículos eléctricos""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Residencial, Comercial",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Es para tu casa o negocio?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Qué tipo de cargador buscas? (240V o carga rápida DC)",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Qué vehículo eléctrico tienes?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "battery_backup",
             "description": """Baterías de respaldo / Sistemas de almacenamiento""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Autonomía, Soporte durante apagones",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Ya cuentas con paneles solares instalados?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Qué deseas respaldar con la batería (refrigerador, luces, etc.)?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Has tenido apagones frecuentes?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {"type": "function",
             "name": "maintenance",
             "description": """Mantenimiento y servicio de sistemas existentes""",
             "parameters": {
                 "type": "object",
                 "properties": {
                     "topic": {
                         "type": "string",
                         "description": "Corrección o mejora",
                     },
                     "output": {
                         "type": "string",
                         "description": "¿Qué tipo de sistema tienes actualmente?",
                     },
                     "output1": {
                         "type": "string",
                         "description": "¿Qué problemas has notado?",
                     },
                     "output2": {
                         "type": "string",
                         "description": "¿Cuándo fue la última vez que recibió mantenimiento?",
                     },
                 },
                 "required": ["topic", "output", "output1", "output2"]
             }
             },
            {
                "type": "function",
                "name": "schedule_appointment",
                "description": "Agenda una cita cuando el cliente lo solicita",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "full_name": {
                            "type": "string",
                            "description": "Nombre completo del cliente"
                        },
                        "phone_number": {
                            "type": "string",
                            "description": "Número de teléfono del cliente"
                        },
                        "email": {
                            "type": "string",
                            "description": "Correo electrónico del cliente"
                        },
                        "appointment_date": {
                            "type": "string",
                            "description": "Fecha deseada para la cita (formato YYYY-MM-DD)"
                        },
                        "appointment_time": {
                            "type": "string",
                            "description": "Hora deseada para la cita (formato HH:MM)"
                        },
                        "project_type": {
                            "type": "string",
                            "description": "Tipo de proyecto (residencial, comercial, etc.)"
                        },
                        "notes": {
                            "type": "string",
                            "description": "Notas adicionales"
                        }
                    },
                    "required": ["full_name", "phone_number", "appointment_date", "appointment_time"]
                }
            },
        ],
        'English': [
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
            },
            {
                "type": "function",
                "name": "grid_tied_solar",
                "description": "Grid-tied solar panels",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Residential, Commercial, Industrial, Agricultural",
                        },
                        "output": {
                            "type": "string",
                            "description": "Is this for your home or business?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "How much do you currently pay on your electricity bill?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "Where is the project located?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "offgrid_hybrid_microgrids",
                "description": "Off-grid systems / Microgrids / Hybrids",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Rural, Off-grid",
                        },
                        "output": {
                            "type": "string",
                            "description": "What space do you want to power (size in m² or description)?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "Do you currently have a generator or battery?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "What appliances or loads do you want to power?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "solar_thermal",
                "description": "Solar Thermal Systems",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Domestic Hot Water, Pool Heating",
                        },
                        "output": {
                            "type": "string",
                            "description": "Is this for domestic hot water or pool heating?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "What capacity or size is the current system, if any?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "What type of installation do you prefer (rooftop/flat)?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "heat_pumps",
                "description": "Solar-powered heat pumps",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Pool heating",
                        },
                        "output": {
                            "type": "string",
                            "description": "What size is the pool (length, width, depth)?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "What temperature do you want to maintain?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "Is it indoor or outdoor?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "ev_chargers",
                "description": "Electric Vehicle Charging Stations",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Residential, Commercial",
                        },
                        "output": {
                            "type": "string",
                            "description": "Is this for your home or business?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "What type of charger are you looking for? (240V or DC fast charging)",
                        },
                        "output2": {
                            "type": "string",
                            "description": "What electric vehicle do you have?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "battery_backup",
                "description": "Backup Batteries / Storage Systems",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Autonomy, Support during blackouts",
                        },
                        "output": {
                            "type": "string",
                            "description": "Do you already have solar panels installed?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "What do you want to back up with the battery (refrigerator, lights, etc.)?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "Have you had frequent blackouts?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
            {
                "type": "function",
                "name": "maintenance",
                "description": "Maintenance and Service of Existing Systems",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "topic": {
                            "type": "string",
                            "description": "Fix or Enhancement",
                        },
                        "output": {
                            "type": "string",
                            "description": "What type of system do you currently have?",
                        },
                        "output1": {
                            "type": "string",
                            "description": "What problems have you noticed?",
                        },
                        "output2": {
                            "type": "string",
                            "description": "When was the last time it was serviced?",
                        },
                    },
                    "required": ["topic", "output", "output1", "output2"]
                }
            },
        ]
    }
    return dicc[language]


async def schedule_appointment(
        full_name: str,
        phone_number: str,
        appointment_date: str,
        appointment_time: str,
        email: Optional[str] = None,
        project_type: Optional[str] = None,
        notes: Optional[str] = None
):
    """Envía los datos de la cita a la API de calendario/CRM"""
    appointment_data = {
        "full_name": full_name,
        "phone_number": phone_number,
        "email": email,
        "appointment_date": appointment_date,
        "appointment_time": appointment_time,
        "project_type": project_type,
        "notes": notes,
        "source": "llamada_telefonica"
    }

    try:
        # Ejemplo con requests (ajusta la URL y headers según tu API)
        api_url = "http://localhost:8069/geckosolar/twilio-calendar"
        headers = {}

        response = requests.post(api_url, json=appointment_data, headers=headers)
        response.raise_for_status()  # Lanza error si hay un status code 4XX/5XX

        return {"status": "success", "message": "Cita agendada exitosamente"}

    except Exception as e:
        logger.error(f"Error al agendar cita: {str(e)}")
        return {"status": "error", "message": "No se pudo agendar la cita"}


@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()

    form_data = (
        await request.form() if request.method == "POST" else request.query_params
    )

    call_sid = form_data.get("CallSid")
    caller_number = form_data.get("From", "Unknown")
    to_number = form_data.get("To", "Unknown")
    number['number'] = caller_number
    logger.info(f"Caller: {caller_number}")

    conversations[call_sid] = {
        "status": "initiated",
        "lead_id": '',
        "project_type": '',
        "stage": '',
        "start_time": time.time(),
        "outbound": False,
        'caller_number': caller_number,
        'to_number': to_number,
    }

    host = request.url.hostname
    print(f"Host generado {host}")
    connect = Connect()
    connect.stream(url=f'wss://{host}/media-stream/{call_sid}')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")


# 'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview',
@app.websocket("/media-stream/{call_sid}")
async def handle_media_stream(websocket: WebSocket, call_sid: str):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("Client connected")
    await websocket.accept()

    async with connect(
            'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview',
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
    ) as openai_ws:
        await initialize_session(openai_ws, call_sid)

        # Connection specific state
        stream_sid = None
        latest_media_timestamp = 0
        last_assistant_item = None
        mark_queue = []
        response_start_timestamp_twilio = None

        async def receive_from_twilio(call_sid: str):
            """Receive audio data from Twilio and send it to the OpenAI Realtime API."""
            caller_number = ''
            to_number = ''
            if call_sid in conversations:
                caller_number = conversations[call_sid]["caller_number"]
                to_number = conversations[call_sid]["to_number"]

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

                        summary = 'Numero del prospecto ' + caller_number + ".\n"
                        summary += 'Ha llamado al numero ' + to_number + ".\n"
                        for mess in transcripts.get(caller_number, {}).get('message'):
                            summary += mess + '\n'

                        # Enviar el correo con el resumen
                        # send_email("yoannydominguez84@gmail.com", "Resumen de la llamada", summary)
                        send_email("vcst128@gmail.com", "Resumen de la llamada", summary)

                        break
            except WebSocketDisconnect:
                print("Client disconnected.")
                if openai_ws.open:
                    await openai_ws.close()

        async def send_to_twilio(call_sid: str):
            """Receive events from the OpenAI Realtime API, send audio back to Twilio."""
            caller_number = ''
            to_number = ''
            if call_sid in conversations:
                caller_number = conversations[call_sid]["caller_number"]
                to_number = conversations[call_sid]["to_number"]
            language = "Spanish"
            if to_number == '+16196481404':
                language = "English"

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

                    if response['type'] == 'conversation.item.function_call':
                        print("🔥 SE DETECTÓ LLAMADA A FUNCIÓN:", response)
                        if response['function_call']['name'] == 'schedule_appointment':
                            args = json.loads(response['function_call']['arguments'])
                            result = await schedule_appointment(**args)

                            # Envía la confirmación al cliente
                            confirmation = {
                                "type": "conversation.item.create",
                                "item": {
                                    "type": "message",
                                    "role": "assistant",
                                    "content": [{
                                        "type": "text",
                                        "text": f"Cita agendada para {args['appointment_date']} a las {args['appointment_time']}"
                                    }]
                                }
                            }
                            await openai_ws.send(json.dumps(confirmation))

                    if response['type'] in ['response.audio_transcript.done',
                                            'conversation.item.input_audio_transcription.completed']:
                        if not transcripts.get(caller_number):
                            if response['type'] == 'response.audio_transcript.done':
                                transcripts[caller_number] = {
                                    'message': ['Sofia: ' + response['transcript']]
                                }
                            else:
                                transcripts[caller_number] = {
                                    'message': ['Usuario: ' + response['transcript']]
                                }
                        else:
                            if response['type'] == 'response.audio_transcript.done':
                                if transcripts[caller_number].get('message'):
                                    transcripts[caller_number]['message'].append('Sofia: ' + response['transcript'])
                                else:
                                    transcripts[caller_number] = {
                                        'message': ['Sofia: ' + response['transcript']]
                                    }
                            else:
                                if transcripts[caller_number].get('message'):
                                    transcripts[caller_number]['message'].append('Usuario: ' + response['transcript'])
                                else:
                                    transcripts[caller_number] = {
                                        'message': ['Usuario: ' + response['transcript']]
                                    }
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

        await asyncio.gather(receive_from_twilio(call_sid), send_to_twilio(call_sid))


async def send_initial_conversation_item(openai_ws, call_sid):
    """Send initial conversation item if AI talks first."""
    caller_number = ''
    to_number = ''
    if call_sid in conversations:
        caller_number = conversations[call_sid]["caller_number"]
        to_number = conversations[call_sid]["to_number"]
    language = "Spanish"
    if to_number == '+16196481404':
        language = "English"
    greeting = get_bot_personality_context(language)['greeting']
    # initial_conversation_item = {
    #     "type": "conversation.item.create",
    #     "item": {
    #         "type": "message",
    #         "role": "user",
    #         "content": [
    #             {
    #                 "type": "input_text",
    #                 "text": greeting,
    #             }
    #         ]
    #     }
    # }
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "assistant",
            "content": [
                {
                    "type": "text",  # ✅ NO usar input_text aquí
                    "text": "Gracias por llamar a Gecko Solar Energy. Mi nombre es Sofía. ¿En qué puedo ayudarte hoy?"
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))


async def initialize_session(openai_ws, call_sid):
    """Control initial session with OpenAI."""
    caller_number = ''
    to_number = ''
    if call_sid in conversations:
        caller_number = conversations[call_sid]["caller_number"]
        to_number = conversations[call_sid]["to_number"]

    language = "Spanish"
    if to_number == '+16196481404':
        language = "English"

    session_update = {
        "type": "session.update",
        "session": {
            "model": "gpt-4o",
            "turn_detection": {
                "type": "server_vad",
                "create_response": True,
                "interrupt_response": False,  # 🔥 Esto es lo que evita que se corte la respuesta
                "prefix_padding_ms": 300,
                "silence_duration_ms": 800,
                "threshold": 0.5
            },
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE_LANGUAGE[language],
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            "input_audio_transcription": {
                "model": "gpt-4o-transcribe",
            },
            "tools": get_tools(language)
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Uncomment the next line to have the AI speak first
    await send_initial_conversation_item(openai_ws, call_sid)


async def end_twilio_call(call_sid):
    logger.info("AI Assistant Ending Twilio call...")


def generate_summary(text):
    """Usa GPT-4 para generar un resumen de la conversación"""
    prompt = f"Resumen de la conversación:\n\n{text}\n\nResumen:"
    response = openai.ChatCompletion.create(
        model="gpt-4",
        messages=[{"role": "system", "content": prompt}]
    )
    return response["choices"][0]["message"]["content"]


def send_email(to_email, subject, body):
    """Envía el resumen de la conversación por correo"""
    msg = MIMEText(body)
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = to_email

    try:
        # server = smtplib.SMTP_SSL("mail.geckosolarenergy.com", 465)
        # server.login(sender_email, sender_password)
        # server.sendmail(sender_email, to_email, msg.as_string())
        # server.quit()
        print("Correo enviado con éxito.")
    except Exception as e:
        print(f"Error al enviar correo: {e}")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
