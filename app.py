import os
import json
import datetime
import logging # Import logging module
from logging.handlers import RotatingFileHandler # Import handler
from flask import Flask, request, jsonify, abort
from database import db_session, init_db
from models import Branch, Transfer
from services import (
    transcribe_audio,
    send_whatsapp_message,
    get_intent_and_entities_from_llm
)
from utils import normalize_branch_name_strict
from config import (
    WHATSAPP_VERIFY_TOKEN, KNOWN_BRANCHES, DEFAULT_CURRENCY,
    ERROR_MSG_GENERIC, ERROR_MSG_UNDERSTAND, ERROR_MSG_BRANCH_NOT_FOUND,
    ERROR_MSG_MISSING_INFO_TRANSFER, ERROR_MSG_MISSING_INFO_QUERY,
    CONFIRM_TRANSFER_MSG, QUERY_RESULT_MSG, QUERY_NO_RESULT_MSG,
    ERROR_MSG_STT_FAILED, ERROR_MSG_API_DOWN, BRANCH_SELF_TRANSFER_MSG
)
from sqlalchemy import func, Date
from sqlalchemy.orm.exc import NoResultFound
from datetime import date

# --- Logging Setup ---
# Get log config from environment or use defaults
log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
log_file_path = os.getenv('LOG_FILE_PATH', 'instance/app.log') # Default to instance folder if not set
log_max_bytes = int(os.getenv('LOG_FILE_MAX_BYTES', 10485760))
log_backup_count = int(os.getenv('LOG_FILE_BACKUP_COUNT', 5))

# Create formatter
log_formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s [in %(pathname)s:%(lineno)d]'
)

# --- File Handler ---
# Ensure log directory exists if using default path
if log_file_path == 'instance/app.log':
     instance_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance')
     if not os.path.exists(instance_path):
          os.makedirs(instance_path)
# Or check existence for absolute path if needed (permission set outside script)

file_handler = RotatingFileHandler(
    log_file_path,
    maxBytes=log_max_bytes,
    backupCount=log_backup_count
)
file_handler.setFormatter(log_formatter)

# --- Console Handler ---
stream_handler = logging.StreamHandler()
stream_handler.setFormatter(log_formatter)

# --- Get Root Logger and Configure ---
# Get the root logger (or you could configure flask's app.logger)
# Configuring the root logger ensures libraries also use the handlers if they log
root_logger = logging.getLogger()
root_logger.setLevel(log_level)
root_logger.addHandler(file_handler)
root_logger.addHandler(stream_handler)

# You might want to silence overly verbose libraries if needed
# logging.getLogger('sqlalchemy.engine').setLevel(logging.INFO) # Example

# Get a logger specific to this module
logger = logging.getLogger(__name__) # Use __name__ for module-specific logger

# --- Flask App Creation ---
app = Flask(__name__)

# Flask's default logger will now inherit the root logger's configuration
# You can still use app.logger if you prefer, but logging.getLogger(__name__) is often clearer

# Example: Log that the app is starting
logger.info("Flask application starting up...")
logger.info(f"Log level set to: {log_level}")
logger.info(f"Logging to file: {log_file_path}")


# ... (Keep database initialization and teardown context) ...
# Ensure the instance folder exists for SQLite (if used) or logs
instance_path_check = os.path.join(app.root_path, 'instance')
if not os.path.exists(instance_path_check):
    os.makedirs(instance_path_check)

with app.app_context():
    try:
        init_db()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.exception("CRITICAL: Failed to initialize database during startup!")
        # Depending on severity, you might want to exit here

@app.teardown_appcontext
def shutdown_session(exception=None):
    if exception:
        # Log any exception passed during teardown
        logger.error(f"Exception during teardown: {exception}")
    db_session.remove()
    # logger.debug("Database session removed.") # Debug level


# --- Webhook Verification Endpoint (GET) ---
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    logger.debug("GET /webhook received verification request") # Use DEBUG for verbose info
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    logger.debug(f"Verification params - Mode: {mode}, Token: {token}, Challenge: {challenge}")

    if mode and token:
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            logger.info(f'WEBHOOK_VERIFIED: Responding with challenge.')
            return challenge, 200
        else:
            logger.warning('WEBHOOK_VERIFICATION_FAILED: Tokens do not match.')
            abort(403)
    else:
        logger.warning('WEBHOOK_VERIFICATION_FAILED: Missing parameters.')
        abort(400)


# --- Message Handling Endpoint (POST) ---
@app.route('/webhook', methods=['POST'])
def handle_message():
    # Use request ID or message ID for tracing logs related to one request
    # For simplicity, we'll just log sequentially here
    logger.info("POST /webhook received message")
    body = request.get_json()

    # Avoid logging the full body in production if it contains sensitive data
    # Log only necessary identifiers or metadata
    # logger.debug(f"Request body: {json.dumps(body)}") # DEBUG level if needed

    if body.get("object") == "whatsapp_business_account":
        try:
            entry = body['entry'][0]
            changes = entry['changes'][0]
            value = changes['value']

            if 'messages' in value:
                message_data = value['messages'][0]
                sender_number = message_data['from']
                message_type = message_data['type']
                message_id = message_data['id']

                logger.info(f"Processing message ID: {message_id} from {sender_number}, type: {message_type}")

                text_content = None
                processing_error = None

                if message_type == 'text':
                    text_content = message_data['text']['body']
                    logger.info(f"Received TEXT: '{text_content}'") # Be careful logging user content directly

                elif message_type == 'audio':
                    logger.info("Received VOICE message, attempting transcription...")
                    audio_id = message_data['audio']['id']
                    text_content = transcribe_audio(audio_id) # service should log its details
                    if not text_content:
                        logger.warning(f"STT failed for message ID: {message_id}")
                        processing_error = ERROR_MSG_STT_FAILED
                    else:
                        # Limit log length if needed: logger.info(f"STT Result: '{text_content[:100]}...'")
                        logger.info(f"STT Result obtained for message ID: {message_id}")


                else:
                    logger.warning(f"Ignoring unsupported message type: {message_type} from {sender_number}")
                    return jsonify(status="ignored", reason="Unsupported message type"), 200

                # --- Process the text ---
                if text_content and not processing_error:
                    logger.info(f"Calling NLU for message ID: {message_id}")
                    nlu_result = get_intent_and_entities_from_llm(text_content) # service should log details

                    if not nlu_result:
                        logger.error(f"NLU processing failed unexpectedly for message ID: {message_id}")
                        processing_error = ERROR_MSG_GENERIC
                    else:
                        intent = nlu_result.get("intent")
                        entities = nlu_result.get("entities", {})
                        logger.info(f"NLU Result for {message_id} - Intent: {intent}, Entities: {entities}") # Log extracted entities

                        # --- Handle Based on NLU Result ---
                        if intent == "record_transfer":
                             response_message = handle_record_transfer(entities, sender_number, text_content, message_id) # Pass msg_id
                        elif intent == "query_branch_total":
                             response_message = handle_query_branch_total(entities, message_id) # Pass msg_id
                        elif intent == "error_api_down":
                             logger.error(f"NLU reported external API down for message ID: {message_id}")
                             response_message = ERROR_MSG_API_DOWN
                        elif intent in ["unclear_request", "error_parsing_llm", "error_processing_llm", "error_generic_nlu"]:
                             logger.warning(f"NLU resulted in '{intent}' for message ID: {message_id}. Text: '{text_content}'")
                             response_message = ERROR_MSG_UNDERSTAND
                        else:
                            logger.error(f"Unhandled intent '{intent}' received from NLU for message ID: {message_id}.")
                            response_message = ERROR_MSG_UNDERSTAND

                elif processing_error:
                    response_message = processing_error
                    # Already logged the specific error (e.g., STT failed)
                else:
                     logger.error(f"Logic Error: No text content and no processing error for message ID: {message_id}")
                     response_message = ERROR_MSG_GENERIC

                # --- Send Reply ---
                if response_message:
                    logger.info(f"Sending reply to {sender_number} for message ID {message_id}: '{response_message}'")
                    send_success = send_whatsapp_message(sender_number, response_message) # service should log details
                    if not send_success:
                        logger.error(f"Failed to send WhatsApp reply to {sender_number} for message ID {message_id}")

            else:
                 logger.warning("Webhook received but no 'messages' key found in value.")

        except KeyError as e:
             logger.exception(f"KeyError processing webhook - likely missing expected field: {e}") # Log with traceback
             # Attempt to notify sender of generic failure if possible
             # ... (error notification logic - be careful not to loop) ...
        except Exception as e:
            # Catch-all for any other unexpected errors in the main try block
            logger.exception(f"FATAL: Unhandled error processing webhook: {e}") # Log with traceback
            # Attempt to notify sender of generic failure if possible
            try:
                sender_number = body['entry'][0]['changes'][0]['value']['messages'][0]['from']
                send_whatsapp_message(sender_number, ERROR_MSG_GENERIC)
            except Exception:
                logger.error("Could not determine sender to notify about fatal webhook processing error.")

        return jsonify(status="received"), 200
    else:
        logger.warning(f"Received non-WhatsApp notification: {request.data[:200]}") # Log start of unexpected data
        abort(404)


# --- Intent Handling Functions (Add Logging) ---

# Add message_id parameter for tracing
def handle_record_transfer(entities, sender_number, original_text, message_id):
    """Processes the 'record_transfer' intent using entities from LLM."""
    logger.info(f"Handling intent 'record_transfer' for message ID: {message_id}")
    amount = entities.get("amount")
    currency = entities.get("currency", DEFAULT_CURRENCY)
    source_branch_llm = entities.get("source_branch")
    dest_branch_llm = entities.get("destination_branch")

    # --- Validation ---
    errors = []
    if amount is None or not isinstance(amount, (int, float)) or amount <= 0: errors.append("المبلغ")
    if not source_branch_llm: errors.append("فرع المصدر")
    if not dest_branch_llm: errors.append("فرع الوجهة")

    if errors:
        error_detail = f"Missing/invalid fields: {', '.join(errors)}"
        logger.warning(f"Validation Error for record_transfer ({message_id}): {error_detail}")
        return ERROR_MSG_MISSING_INFO_TRANSFER

    source_branch_name = normalize_branch_name_strict(source_branch_llm)
    dest_branch_name = normalize_branch_name_strict(dest_branch_llm)
    known_branch_list_str = ", ".join(KNOWN_BRANCHES)

    if not source_branch_name:
        logger.warning(f"Validation Error ({message_id}): Source branch '{source_branch_llm}' not in known list.")
        return ERROR_MSG_BRANCH_NOT_FOUND.format(source_branch_llm, known_branch_list_str)
    if not dest_branch_name:
        logger.warning(f"Validation Error ({message_id}): Destination branch '{dest_branch_llm}' not in known list.")
        return ERROR_MSG_BRANCH_NOT_FOUND.format(dest_branch_llm, known_branch_list_str)
    if source_branch_name == dest_branch_name:
         logger.warning(f"Validation Error ({message_id}): Source and destination branches are the same ('{source_branch_name}').")
         return BRANCH_SELF_TRANSFER_MSG

    # --- Database Interaction ---
    session = db_session()
    try:
        logger.debug(f"Looking up branches '{source_branch_name}' and '{dest_branch_name}' for message ID {message_id}")
        source_branch = session.query(Branch).filter(Branch.name == source_branch_name).one()
        dest_branch = session.query(Branch).filter(Branch.name == dest_branch_name).one()
        logger.debug(f"Branches found (IDs: {source_branch.id}, {dest_branch.id})")

        new_transfer = Transfer(
            amount=amount, currency=currency, source_branch_id=source_branch.id,
            destination_branch_id=dest_branch.id, recorded_by_whatsapp_number=sender_number,
            original_message_text=original_text
        )
        session.add(new_transfer)
        session.commit()
        transfer_id = new_transfer.id
        logger.info(f"SUCCESS: Recorded transfer ID {transfer_id} for message ID {message_id}")
        return CONFIRM_TRANSFER_MSG.format(amount, currency, source_branch_name, dest_branch_name)

    except NoResultFound:
        session.rollback()
        logger.error(f"DB Error ({message_id}): Branch lookup failed for validated names '{source_branch_name}' or '{dest_branch_name}'. Check DB sync.")
        return ERROR_MSG_GENERIC
    except Exception as e:
        session.rollback()
        # Use logger.exception to include traceback
        logger.exception(f"DB Error ({message_id}) during record_transfer: {e}")
        return ERROR_MSG_GENERIC
    finally:
        session.close() # Close session explicitly here, though teardown also does


# Add message_id parameter for tracing
def handle_query_branch_total(entities, message_id):
    """Processes the 'query_branch_total' intent for today's transfers FROM a branch."""
    logger.info(f"Handling intent 'query_branch_total' for message ID: {message_id}")
    branch_name_llm = entities.get("query_branch")
    date_range = entities.get("date_range", "today")

    if not branch_name_llm:
        logger.warning(f"Validation Error for query_branch_total ({message_id}): Missing query_branch entity.")
        return ERROR_MSG_MISSING_INFO_QUERY

    branch_name = normalize_branch_name_strict(branch_name_llm)
    known_branch_list_str = ", ".join(KNOWN_BRANCHES)
    if not branch_name:
         logger.warning(f"Validation Error ({message_id}): Query branch '{branch_name_llm}' not in known list.")
         return ERROR_MSG_BRANCH_NOT_FOUND.format(branch_name_llm, known_branch_list_str)

    if date_range != "today":
        logger.info(f"Query ({message_id}) for date range '{date_range}' received, proceeding with 'today' as only supported range.")
        # No error message needed if we just proceed with 'today'

    session = db_session()
    try:
        logger.debug(f"Looking up branch '{branch_name}' for query ({message_id})")
        target_branch = session.query(Branch).filter(Branch.name == branch_name).one()
        logger.debug(f"Branch '{branch_name}' found (ID: {target_branch.id})")

        today_date = date.today()
        logger.debug(f"Querying SUM(amount) from transfers where source={target_branch.id} and date={today_date}")

        total_amount = session.query(func.sum(Transfer.amount)).filter(
            Transfer.source_branch_id == target_branch.id,
            func.cast(Transfer.timestamp, Date) == today_date
        ).scalar()

        if total_amount is not None and total_amount > 0:
            logger.info(f"SUCCESS ({message_id}): Found total {total_amount} {DEFAULT_CURRENCY} for branch '{branch_name}' today.")
            return QUERY_RESULT_MSG.format(branch_name, total_amount, DEFAULT_CURRENCY)
        else:
            logger.info(f"SUCCESS ({message_id}): No transfers found for branch '{branch_name}' today.")
            return QUERY_NO_RESULT_MSG.format(branch_name)

    except NoResultFound:
        session.rollback()
        logger.error(f"DB Error ({message_id}): Branch lookup failed for validated query branch name '{branch_name}'.")
        return ERROR_MSG_GENERIC
    except Exception as e:
        session.rollback()
        logger.exception(f"DB Error ({message_id}) during query_branch_total: {e}")
        return ERROR_MSG_GENERIC
    finally:
        session.close()


# --- Main Execution ---
if __name__ == '__main__':
    logger.info("Starting Flask application via direct execution...")
    # Make sure debug=False in production WSGI server config
    # The host/port here are mainly for local testing
    app.run(host='127.0.0.1', port=5001, debug=False)
