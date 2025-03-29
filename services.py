import requests
import os
import json
from io import BytesIO
from openai import OpenAI, APIError, APIConnectionError, RateLimitError # Import specific errors
from config import (
    WHATSAPP_API_TOKEN, WHATSAPP_PHONE_NUMBER_ID, WHATSAPP_API_VERSION,
    OPENAI_API_KEY, OPENAI_MODEL_STT, OPENAI_MODEL_NLU, KNOWN_BRANCHES,
    DEFAULT_CURRENCY, ERROR_MSG_API_DOWN
)
import datetime

# --- Initialize OpenAI Client ---
if not OPENAI_API_KEY:
    raise ValueError("OPENAI_API_KEY environment variable not set.")
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# --- WhatsApp Media Download ---
def get_whatsapp_media_url(media_id):
    """Gets the downloadable URL for WhatsApp media."""
    api_url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{media_id}/"
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(api_url, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get('url')
    except requests.exceptions.RequestException as e:
        print(f"Error fetching media URL from WhatsApp: {e}")
        if e.response is not None:
            print(f"WhatsApp API Response Status: {e.response.status_code}")
            print(f"WhatsApp API Response Body: {e.response.text}")
        return None

def download_whatsapp_media(media_url):
    """Downloads media content from the provided URL using the WhatsApp token."""
    headers = {"Authorization": f"Bearer {WHATSAPP_API_TOKEN}"}
    try:
        response = requests.get(media_url, headers=headers, timeout=30) # Longer timeout for download
        response.raise_for_status()
        return BytesIO(response.content) # Return as BytesIO for OpenAI library
    except requests.exceptions.RequestException as e:
        print(f"Error downloading media content from WhatsApp URL: {e}")
        if e.response is not None:
            print(f"Download Response Status: {e.response.status_code}")
            print(f"Download Response Body: {e.response.text}")
        return None

# --- Speech-to-Text (Whisper API) ---
def transcribe_audio(media_id):
    """
    Downloads audio from WhatsApp using media_id and transcribes it using OpenAI Whisper.
    Returns the transcribed text or None if an error occurs.
    """
    print(f"--- Starting STT for media ID: {media_id} ---")
    media_url = get_whatsapp_media_url(media_id)
    if not media_url:
        print("Failed to get media URL.")
        return None

    print(f"Got media URL: {media_url[:50]}...") # Log truncated URL
    audio_content_stream = download_whatsapp_media(media_url)
    if not audio_content_stream:
        print("Failed to download audio content.")
        return None

    print("Audio downloaded, sending to OpenAI Whisper...")
    try:
        # Prepare the file for the API - needs a name even from BytesIO
        audio_content_stream.name = "audio.ogg" # Assume ogg, Whisper handles various formats

        transcript = openai_client.audio.transcriptions.create(
            model=OPENAI_MODEL_STT,
            file=audio_content_stream,
            language="ar", # Specify Arabic
            response_format="text" # Get plain text directly
        )
        print(f"--- STT Success. Transcription: {transcript} ---")
        return transcript

    except (APIError, APIConnectionError, RateLimitError) as e:
        print(f"OpenAI API error during transcription: {e}")
        return None # Indicate STT failure
    except Exception as e:
        print(f"An unexpected error occurred during transcription: {e}")
        return None


# --- NLU using OpenAI GPT with Function Calling ---

# Define the functions the LLM can call
tools = [
    {
        "type": "function",
        "function": {
            "name": "record_transfer",
            "description": "Saves a record of a monetary transfer between two gold store branches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "The numerical amount of money transferred.",
                    },
                    "currency": {
                        "type": "string",
                        "description": f"The currency of the transfer (e.g., JOD, Dinar). Default is {DEFAULT_CURRENCY}.",
                    },
                    "source_branch": {
                        "type": "string",
                        "description": f"The name of the branch FROM which the money was sent. Must be one of: {', '.join(KNOWN_BRANCHES)}.",
                    },
                    "destination_branch": {
                        "type": "string",
                        "description": f"The name of the branch TO which the money was sent. Must be one of: {', '.join(KNOWN_BRANCHES)}.",
                    },
                },
                "required": ["amount", "source_branch", "destination_branch"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_branch_total",
            "description": "Retrieves the total amount transferred FROM a specific branch for a given period (defaults to 'today').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_branch": {
                        "type": "string",
                        "description": f"The name of the branch to query the total transfers FROM. Must be one of: {', '.join(KNOWN_BRANCHES)}.",
                    },
                    "date_range": {
                        "type": "string",
                        "description": "The time period for the query (e.g., 'today', 'yesterday', 'last week'). Defaults to 'today'. Currently, only 'today' is processed.",
                         "enum": ["today"] # Explicitly limit for now
                    },
                },
                "required": ["query_branch"],
            },
        },
    }
    # Add more function definitions here for other features (e.g., query_to_branch, list_recent)
]

def get_intent_and_entities_from_llm(text):
    """
    Uses OpenAI Chat Completion with function calling to extract intent and entities.
    Returns a dictionary like {'intent': '...', 'entities': {...}} or None on failure.
    """
    print(f"--- Sending text to LLM for NLU: '{text}' ---")
    if not text:
        return None

    system_prompt = f"""You are an assistant for a gold store business in Jordan. Your task is to understand employee requests (in Arabic) and extract information to call the appropriate function.
Available branches are: {', '.join(KNOWN_BRANCHES)}.
The default currency is {DEFAULT_CURRENCY}.
Today's date is {datetime.date.today().strftime('%Y-%m-%d')}.
Analyze the user's message and call the relevant function with the extracted parameters.""" # Added date for context

    try:
        response = openai_client.chat.completions.create(
            model=OPENAI_MODEL_NLU,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            tools=tools,
            tool_choice="auto",  # Let the model decide which function to call
            temperature=0.1, # Lower temperature for more predictable extraction
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            # Process the first function call (assuming one primary action per message)
            tool_call = tool_calls[0]
            function_name = tool_call.function.name
            function_args_raw = tool_call.function.arguments

            print(f"LLM decided to call function: {function_name}")
            print(f"LLM raw arguments: {function_args_raw}")

            try:
                # Parse the JSON arguments string provided by the LLM
                function_args = json.loads(function_args_raw)

                # Basic validation/normalization (optional, LLM should follow schema)
                if 'amount' in function_args:
                    try:
                        function_args['amount'] = float(function_args['amount'])
                    except (ValueError, TypeError):
                         print(f"Warning: LLM provided non-numeric amount: {function_args['amount']}")
                         # Decide how to handle: error out, or try to recover? For now, let it proceed maybe db validation catches it.
                         pass # Let handler function validate further

                # Standardize currency if mentioned or default
                if 'currency' in function_args:
                    curr = str(function_args['currency']).upper()
                    if "DINAR" in curr or "JOD" in curr:
                         function_args['currency'] = "JOD"
                    # Add other normalizations if needed
                elif function_name == 'record_transfer': # Ensure default for transfer
                    function_args['currency'] = DEFAULT_CURRENCY


                # Normalize branch names before returning (remove "فرع" etc. if LLM includes it)
                # This step might be less critical if the LLM correctly uses the provided KNOWN_BRANCHES, but good as safeguard
                for key in ['source_branch', 'destination_branch', 'query_branch']:
                     if key in function_args:
                         # You might want a more robust normalization function here if needed
                         normalized = function_args[key].replace("فرع ", "").strip()
                         if normalized in KNOWN_BRANCHES:
                              function_args[key] = normalized
                         else:
                              # If LLM hallucinates a branch not in the KNOWN_BRANCHES list provided in prompt/functions
                              print(f"Warning: LLM extracted branch '{function_args[key]}' not in known list. Will rely on downstream validation.")
                              # Keep the LLM's version for now, the handler will validate against KNOWN_BRANCHES strictly.


                result = {"intent": function_name, "entities": function_args}
                print(f"--- NLU Success. Result: {result} ---")
                return result

            except json.JSONDecodeError:
                print(f"Error: LLM function arguments were not valid JSON: {function_args_raw}")
                return {"intent": "error_parsing_llm", "entities": {}}
            except Exception as e:
                 print(f"Error processing LLM function arguments: {e}")
                 return {"intent": "error_processing_llm", "entities": {}}

        else:
            # The LLM didn't call a function, meaning it didn't understand or wasn't confident.
            llm_reply = response_message.content
            print(f"LLM did not call a function. It might have replied: {llm_reply}")
            # You could potentially forward the LLM's text reply if it's informative,
            # but for structured tasks, it's better to signal inability to act.
            return {"intent": "unclear_request", "entities": {}}

    except (APIError, APIConnectionError, RateLimitError) as e:
        print(f"OpenAI API error during NLU: {e}")
        # Return a specific intent indicating API failure
        return {"intent": "error_api_down", "entities": {}}
    except Exception as e:
        print(f"An unexpected error occurred during NLU processing: {e}")
        return {"intent": "error_generic_nlu", "entities": {}}


# --- Send WhatsApp Message (Meta Cloud API) ---
def send_whatsapp_message(recipient_number, message_text):
    """Sends a text message using the WhatsApp Business Cloud API."""
    api_url = f"https://graph.facebook.com/{WHATSAPP_API_VERSION}/{WHATSAPP_PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_API_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": recipient_number,
        "type": "text",
        "text": {
            # "preview_url": False, # Optional: disable link previews if needed
            "body": message_text,
        }
    }
    print("-" * 50)
    print(f"--- SENDING WHATSAPP MESSAGE ---")
    print(f"To: {recipient_number}")
    print(f"Message: {message_text}")
    print("-" * 50)

    try:
        response = requests.post(api_url, headers=headers, json=payload, timeout=20)
        response.raise_for_status() # Raise HTTPError for bad responses (4xx or 5xx)
        print(f"WhatsApp message sent successfully! Response: {response.json()}")
        return True
    except requests.exceptions.Timeout:
        print("Error sending WhatsApp message: Request timed out.")
        return False
    except requests.exceptions.RequestException as e:
        print(f"Error sending WhatsApp message: {e}")
        if e.response is not None:
            print(f"Response status code: {e.response.status_code}")
            print(f"Response body: {e.response.text}")
            # Check for specific error codes from Meta if needed for retry logic etc.
        return False
    except Exception as e:
        # Catch any other unexpected errors
        print(f"An unexpected error occurred while sending WhatsApp message: {e}")
        return False