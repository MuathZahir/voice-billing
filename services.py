import requests
import os
import json
import logging # Import logging
from io import BytesIO
from openai import OpenAI, APIError, APIConnectionError, RateLimitError
from config import (
    WHATSAPP_API_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_API_VERSION,
    OPENAI_API_KEY, OPENAI_MODEL_STT, OPENAI_MODEL_NLU, KNOWN_BRANCHES,
    DEFAULT_CURRENCY, ERROR_MSG_API_DOWN
)
import datetime # Keep datetime import

# Get a logger specific to this module
logger = logging.getLogger(__name__)

# --- Initialize OpenAI Client ---
if not OPENAI_API_KEY:
    # Log critical error if key is missing
    logger.critical("CRITICAL: OPENAI_API_KEY environment variable not set.")
    raise ValueError("OPENAI_API_KEY environment variable not set.")
openai_client = OpenAI(api_key=OPENAI_API_KEY)
logger.info("OpenAI client initialized.")


# --- WhatsApp Media Download ---
def get_whatsapp_media_url(media_id):
    """Gets the downloadable URL for WhatsApp media."""
    logger.debug(f"Fetching media URL for media_id: {media_id}")
    api_url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}/"
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        media_url = data.get('url')
        if media_url:
            logger.debug(f"Successfully fetched media URL for {media_id}.")
            return media_url
        else:
            logger.error(f"WhatsApp API response for {media_id} missing 'url' key. Response: {data}")
            return None
    except requests.exceptions.RequestException as e:
        logger.error(f"Error fetching media URL from WhatsApp for {media_id}: {e}")
        if e.response is not None:
            logger.error(f"WhatsApp API Response Status: {e.response.status_code}, Body: {e.response.text[:200]}...") # Log partial body
        return None

def download_whatsapp_media(media_url):
    """Downloads media content from the provided URL using the WhatsApp token."""
    logger.debug(f"Downloading media from URL: {media_url[:50]}...")
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(media_url, headers=headers, timeout=30)
        response.raise_for_status()
        logger.debug(f"Successfully downloaded media (Size: {len(response.content)} bytes).")
        return BytesIO(response.content)
    except requests.exceptions.RequestException as e:
        logger.error(f"Error downloading media content from WhatsApp URL: {e}")
        if e.response is not None:
            logger.error(f"Download Response Status: {e.response.status_code}, Body: {e.response.text[:200]}...")
        return None

# --- Speech-to-Text (Whisper API) ---
def transcribe_audio(media_id):
    """Downloads audio from WhatsApp and transcribes it using OpenAI Whisper."""
    logger.info(f"Starting STT process for media ID: {media_id}")
    media_url = get_whatsapp_media_url(media_id)
    if not media_url:
        # Error already logged by get_whatsapp_media_url
        return None

    audio_content_stream = download_whatsapp_media(media_url)
    if not audio_content_stream:
        # Error already logged by download_whatsapp_media
        return None

    logger.debug(f"Sending audio stream for {media_id} to OpenAI Whisper ({OPENAI_MODEL_STT})...")
    try:
        audio_content_stream.name = "audio.ogg" # Provide filename hint
        transcript = openai_client.audio.transcriptions.create(
            model=OPENAI_MODEL_STT,
            file=audio_content_stream,
            language="ar",
            response_format="text"
        )
        # Avoid logging full transcript if potentially long/sensitive
        logger.info(f"STT Success for media ID: {media_id}.")
        # logger.debug(f"Transcription result: {transcript[:100]}...") # Log partial transcript at DEBUG
        return transcript

    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.error(f"OpenAI API error during transcription for {media_id}: {e}")
        return None
    except Exception as e:
        logger.exception(f"Unexpected error during transcription for {media_id}: {e}") # Log with traceback
        return None

# --- NLU using OpenAI GPT with Function Calling ---
# Function definitions (tools) remain the same
tools = [
    # ... (keep tool definitions as before) ...
     {
        "type": "function",
        "function": {
            "name": "record_transfer",
            "description": "Saves a record of a monetary transfer between two gold store branches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": { "type": "number", "description": "The numerical amount transferred." },
                    "currency": { "type": "string", "description": f"Currency (e.g., JOD). Default {DEFAULT_CURRENCY}." },
                    "source_branch": { "type": "string", "description": f"Source branch name. Must be one of: {', '.join(KNOWN_BRANCHES)}." },
                    "destination_branch": { "type": "string", "description": f"Destination branch name. Must be one of: {', '.join(KNOWN_BRANCHES)}." },
                }, "required": ["amount", "source_branch", "destination_branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_branch_total",
            "description": "Retrieves total amount transferred FROM a specific branch today.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_branch": { "type": "string", "description": f"Branch name to query. Must be one of: {', '.join(KNOWN_BRANCHES)}." },
                    "date_range": { "type": "string", "description": "Time period (e.g., 'today'). Currently only 'today' is processed.", "enum": ["today"] },
                }, "required": ["query_branch"],
            },
        },
    }
]

def get_intent_and_entities_from_llm(text):
    """Uses OpenAI Chat Completion with function calling for NLU."""
    # Avoid logging full text if sensitive, maybe log length or hash?
    logger.info(f"Starting NLU process (Text length: {len(text)} chars)")
    if not text:
        logger.warning("NLU called with empty text.")
        return None

    system_prompt = f"""You are an assistant for a gold store business in Jordan... (rest of prompt as before)""" # Keep prompt

    try:
        logger.debug(f"Sending request to OpenAI ChatCompletion ({OPENAI_MODEL_NLU})")
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL_NLU,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            tools=tools,
            tool_choice="auto",
            temperature=0.1,
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            tool_call = tool_calls[0] # Process first call
            function_name = tool_call.function.name
            function_args_raw = tool_call.function.arguments
            logger.info(f"LLM decided to call function: {function_name}")
            # Be careful logging raw arguments if they might contain sensitive interpretations
            logger.debug(f"LLM raw function arguments: {function_args_raw}")

            try:
                function_args = json.loads(function_args_raw)
                # ... (perform normalization/validation as before) ...
                if 'amount' in function_args:
                     try: function_args['amount'] = float(function_args['amount'])
                     except (ValueError, TypeError): logger.warning(f"LLM provided non-numeric amount: {function_args['amount']}")

                if 'currency' in function_args:
                     curr = str(function_args['currency']).upper()
                     if "DINAR" in curr or "JOD" in curr: function_args['currency'] = "JOD"
                elif function_name == 'record_transfer': function_args['currency'] = DEFAULT_CURRENCY

                for key in ['source_branch', 'destination_branch', 'query_branch']:
                    if key in function_args:
                        normalized = function_args[key].replace("فرع ", "").strip()
                        if normalized in KNOWN_BRANCHES:
                             function_args[key] = normalized
                        else:
                             logger.warning(f"LLM extracted branch '{function_args[key]}' not in known list during NLU. Validation occurs later.")


                result = {"intent": function_name, "entities": function_args}
                logger.info(f"NLU Success. Intent: {result['intent']}, Entities identified.") # Avoid logging full entity dict here if sensitive
                logger.debug(f"NLU Entities: {result['entities']}") # Log full entities at DEBUG level
                return result

            except json.JSONDecodeError:
                logger.error(f"NLU Error: LLM function arguments were not valid JSON: {function_args_raw}")
                return {"intent": "error_parsing_llm", "entities": {}}
            except Exception as e:
                 logger.exception(f"NLU Error processing LLM function arguments: {e}") # Use exception for traceback
                 return {"intent": "error_processing_llm", "entities": {}}

        else:
            # LLM didn't call a function
            llm_reply = response_message.content
            logger.warning(f"NLU: LLM did not call a function. User text: '{text[:100]}...'. LLM reply: '{llm_reply[:100]}...'")
            return {"intent": "unclear_request", "entities": {}}

    except (APIError, APIConnectionError, RateLimitError) as e:
        logger.error(f"OpenAI API error during NLU: {e}")
        return {"intent": "error_api_down", "entities": {}}
    except Exception as e:
        logger.exception(f"Unexpected error during NLU processing: {e}") # Log with traceback
        return {"intent": "error_generic_nlu", "entities": {}}


# --- Send WhatsApp Message (Meta Cloud API) ---
def send_whatsapp_message(recipient_number, message_text):
    """Sends a text message using the WhatsApp Business Cloud API."""
    logger.info(f"Attempting to send WhatsApp message to {recipient_number}")
    # Avoid logging full message text if sensitive: logger.debug(f"Message content: {message_text[:100]}...")
    api_url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}", "Content-Type": "application/json"}
    payload = {"messaging_product": "whatsapp", "to": recipient_number, "type": "text", "text": {"body": message_text}}

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=20)
        response.raise_for_status()
        logger.info(f"WhatsApp message sent successfully to {recipient_number}. Response: {response.json()}")
        return True
    except requests.exceptions.Timeout:
        logger.error(f"Error sending WhatsApp message to {recipient_number}: Request timed out.")
        return False
    except requests.exceptions.RequestException as e:
        logger.error(f"Error sending WhatsApp message to {recipient_number}: {e}")
        if e.response is not None:
            logger.error(f"WhatsApp Send API Response Status: {e.response.status_code}, Body: {e.response.text[:200]}...")
        return False
    except Exception as e:
        logger.exception(f"Unexpected error sending WhatsApp message to {recipient_number}: {e}") # Log with traceback
        return False