import os
import json
import datetime # Import datetime module
from flask import Flask, request, jsonify, abort
from database import db_session, init_db
from models import Branch, Transfer
# Import the NEW service functions
from services import (
    transcribe_audio,
    send_whatsapp_message,
    get_intent_and_entities_from_llm
)
# Import the utility for strict branch matching
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
from datetime import date # Keep this specific import

# Create Flask app
app = Flask(__name__)

# Ensure the instance folder exists (useful even if not using SQLite)
instance_path = os.path.join(app.root_path, 'instance')
if not os.path.exists(instance_path):
    os.makedirs(instance_path)

# Initialize DB (create tables and seed branches if needed)
# Make sure your DATABASE_URL points to PostgreSQL now
with app.app_context():
    init_db()

@app.teardown_appcontext
def shutdown_session(exception=None):
    db_session.remove()

# --- Webhook Verification Endpoint (GET) ---
# (No changes needed from the previous version)
@app.route('/webhook', methods=['GET'])
def verify_webhook():
    # ... (keep existing verification logic) ...
    print("GET /webhook received verification request")
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    print(f"Mode: {mode}, Token: {token}, Challenge: {challenge}")

    if mode and token:
        if mode == 'subscribe' and token == WHATSAPP_VERIFY_TOKEN:
            print(f'WEBHOOK_VERIFIED: Responding with challenge: {challenge}')
            return challenge, 200
        else:
            print('WEBHOOK_VERIFICATION_FAILED: Tokens do not match.')
            abort(403)
    else:
        print('WEBHOOK_VERIFICATION_FAILED: Missing parameters.')
        abort(400)

# --- Message Handling Endpoint (POST) ---
@app.route('/webhook', methods=['POST'])
def handle_message():
    body = request.get_json()
    print("-----------------------------------------")
    print("POST /webhook received message:")
    # Avoid printing potentially large/sensitive full body in prod logs long-term
    # Consider logging specific fields like message ID, sender, type
    # print(json.dumps(body, indent=2))
    print("-----------------------------------------")

    if body.get("object") == "whatsapp_business_account":
        try:
            entry = body['entry'][0]
            changes = entry['changes'][0]
            value = changes['value']

            if 'messages' in value:
                message_data = value['messages'][0]
                sender_number = message_data['from']
                message_type = message_data['type']
                message_id = message_data['id'] # Good for tracing

                print(f"Processing message ID: {message_id} from {sender_number}, type: {message_type}")

                text_content = None
                processing_error = None # To track errors before sending reply

                if message_type == 'text':
                    text_content = message_data['text']['body']
                    print(f"Received TEXT: '{text_content}'")

                elif message_type == 'voice':
                    print("Received VOICE message, attempting transcription...")
                    audio_id = message_data['voice']['id']
                    # --- Call REAL STT Service ---
                    text_content = transcribe_audio(audio_id)
                    if not text_content:
                        print("STT failed.")
                        processing_error = ERROR_MSG_STT_FAILED
                    else:
                        print(f"STT Result: '{text_content}'")

                else:
                    print(f"Ignoring unsupported message type: {message_type}")
                    # Don't send reply for unsupported types usually
                    return jsonify(status="ignored", reason="Unsupported message type"), 200

                # --- Process the text (if obtained) ---
                if text_content and not processing_error:
                    # --- Call REAL NLU Service ---
                    nlu_result = get_intent_and_entities_from_llm(text_content)

                    if not nlu_result:
                        print("NLU processing failed.")
                        processing_error = ERROR_MSG_GENERIC # Or a more specific NLU error if available
                    else:
                        intent = nlu_result.get("intent")
                        entities = nlu_result.get("entities", {})
                        print(f"NLU Intent: {intent}, Entities: {entities}")

                        # --- Handle Based on NLU Result ---
                        if intent == "record_transfer":
                             response_message = handle_record_transfer(entities, sender_number, text_content)
                        elif intent == "query_branch_total":
                             response_message = handle_query_branch_total(entities)
                        # Handle specific errors identified by NLU service wrapper
                        elif intent == "error_api_down":
                             response_message = ERROR_MSG_API_DOWN
                        elif intent in ["unclear_request", "error_parsing_llm", "error_processing_llm", "error_generic_nlu"]:
                             response_message = ERROR_MSG_UNDERSTAND
                        # Add handlers for other intents here
                        else: # Fallback for unexpected/unhandled intent from LLM
                            print(f"Warning: Unhandled intent '{intent}' received from NLU.")
                            response_message = ERROR_MSG_UNDERSTAND

                elif processing_error:
                    # Use the error message determined during STT/processing
                    response_message = processing_error
                else:
                    # Should not happen if logic is correct (text_content is None but no error?)
                     print("Warning: No text content and no processing error flagged.")
                     response_message = ERROR_MSG_GENERIC


                # --- Send Reply ---
                if response_message: # Only send if there's something to say
                    send_whatsapp_message(sender_number, response_message)

            else:
                 print("Webhook received but no 'messages' key found in value.")


        except Exception as e:
            print(f"FATAL: Unhandled error processing webhook: {e}")
            import traceback
            traceback.print_exc() # Log full traceback for debugging
            # Attempt to notify sender of generic failure if possible
            try:
                sender_number = body['entry'][0]['changes'][0]['value']['messages'][0]['from']
                send_whatsapp_message(sender_number, ERROR_MSG_GENERIC)
            except Exception:
                print("Could not determine sender to notify about fatal error.")

        # Return 200 OK to WhatsApp quickly to acknowledge receipt
        return jsonify(status="received"), 200
    else:
        print("Received non-WhatsApp notification")
        abort(404)


# --- Intent Handling Functions (Updated to use LLM entities and stricter validation) ---

def handle_record_transfer(entities, sender_number, original_text):
    """Processes the 'record_transfer' intent using entities from LLM."""
    # Extract entities using .get() for safety
    amount = entities.get("amount")
    currency = entities.get("currency", DEFAULT_CURRENCY) # Default if LLM omits it
    source_branch_llm = entities.get("source_branch")
    dest_branch_llm = entities.get("destination_branch")

    # --- Validation ---
    errors = []
    if amount is None or not isinstance(amount, (int, float)) or amount <= 0:
        errors.append("المبلغ") # Amount
    if not source_branch_llm:
        errors.append("فرع المصدر") # Source Branch
    if not dest_branch_llm:
        errors.append("فرع الوجهة") # Destination Branch

    if errors:
        print(f"Validation Error: Missing or invalid fields: {', '.join(errors)}")
        return ERROR_MSG_MISSING_INFO_TRANSFER # Return generic missing info message

    # --- Strict Branch Validation against KNOWN_BRANCHES ---
    # Use the utility to normalize/validate against our canonical list
    source_branch_name = normalize_branch_name_strict(source_branch_llm)
    dest_branch_name = normalize_branch_name_strict(dest_branch_llm)

    known_branch_list_str = ", ".join(KNOWN_BRANCHES)
    if not source_branch_name:
        print(f"Validation Error: Source branch '{source_branch_llm}' not in known list.")
        return ERROR_MSG_BRANCH_NOT_FOUND.format(source_branch_llm, known_branch_list_str)
    if not dest_branch_name:
        print(f"Validation Error: Destination branch '{dest_branch_llm}' not in known list.")
        return ERROR_MSG_BRANCH_NOT_FOUND.format(dest_branch_llm, known_branch_list_str)
    if source_branch_name == dest_branch_name:
         print(f"Validation Error: Source and destination branches are the same ('{source_branch_name}').")
         return BRANCH_SELF_TRANSFER_MSG

    # --- Database Interaction ---
    try:
        session = db_session()
        # Fetch Branch objects using the validated names
        source_branch = session.query(Branch).filter(Branch.name == source_branch_name).one()
        dest_branch = session.query(Branch).filter(Branch.name == dest_branch_name).one()

        # Create and save the transfer record
        new_transfer = Transfer(
            amount=amount,
            currency=currency, # Use currency from LLM or default
            source_branch_id=source_branch.id,
            destination_branch_id=dest_branch.id,
            recorded_by_whatsapp_number=sender_number,
            original_message_text=original_text # Store original text for audit
        )
        session.add(new_transfer)
        session.commit()
        transfer_id = new_transfer.id # Get ID after commit
        print(f"SUCCESS: Recorded transfer ID {transfer_id}")

        return CONFIRM_TRANSFER_MSG.format(amount, currency, source_branch_name, dest_branch_name)

    except NoResultFound:
        # This should NOT happen if normalize_branch_name_strict worked correctly and DB is synced
        session.rollback()
        print(f"CRITICAL ERROR: Branch lookup failed for validated names '{source_branch_name}' or '{dest_branch_name}'. Check DB sync.")
        return ERROR_MSG_GENERIC
    except Exception as e:
        session.rollback()
        print(f"ERROR during database operation for record_transfer: {e}")
        import traceback
        traceback.print_exc()
        return ERROR_MSG_GENERIC
    finally:
        # Ensure session is closed/removed properly by teardown context
        pass


def handle_query_branch_total(entities):
    """Processes the 'query_branch_total' intent for today's transfers FROM a branch."""
    branch_name_llm = entities.get("query_branch")
    date_range = entities.get("date_range", "today") # Default to today if omitted

    # --- Validation ---
    if not branch_name_llm:
        print("Validation Error: Missing query_branch entity.")
        return ERROR_MSG_MISSING_INFO_QUERY

    # --- Strict Branch Validation ---
    branch_name = normalize_branch_name_strict(branch_name_llm)
    known_branch_list_str = ", ".join(KNOWN_BRANCHES)
    if not branch_name:
         print(f"Validation Error: Query branch '{branch_name_llm}' not in known list.")
         return ERROR_MSG_BRANCH_NOT_FOUND.format(branch_name_llm, known_branch_list_str)

    # --- Date Range Handling (Currently only 'today') ---
    if date_range != "today":
        print(f"Info: Received query for date range '{date_range}', but only 'today' is supported.")
        # Inform the user clearly about the limitation
        # return f"عذراً، يمكنني فقط الاستعلام عن إجمالي تحويلات اليوم حالياً لفرع {branch_name}."
        # Or proceed with 'today' anyway if that's acceptable behaviour
        pass # For now, proceed assuming 'today' if LLM gave something else

    # --- Database Interaction ---
    try:
        session = db_session()
        target_branch = session.query(Branch).filter(Branch.name == branch_name).one()

        # Query for sum of amounts transferred FROM the target branch TODAY
        today_date = date.today()

        # Use func.date() for portability across DBs (esp. SQLite vs PG) to compare dates
        total_amount = session.query(func.sum(Transfer.amount)).filter(
            Transfer.source_branch_id == target_branch.id,
            func.cast(Transfer.timestamp, Date) == today_date # Cast timestamp to Date for comparison
            # For PostgreSQL timezone-aware timestamps, ensure comparison handles timezones correctly
            # or store timestamps consistently (e.g., UTC)
        ).scalar()

        session.close() # Close session after query

        if total_amount is not None and total_amount > 0:
             # Assuming single currency for simplicity in query response
            print(f"SUCCESS: Found total {total_amount} {DEFAULT_CURRENCY} for branch '{branch_name}' today.")
            return QUERY_RESULT_MSG.format(branch_name, total_amount, DEFAULT_CURRENCY)
        else:
            print(f"SUCCESS: No transfers found for branch '{branch_name}' today.")
            return QUERY_NO_RESULT_MSG.format(branch_name)

    except NoResultFound:
        session.rollback() # Should close session from try/except
        print(f"CRITICAL ERROR: Branch lookup failed for validated query branch name '{branch_name}'.")
        return ERROR_MSG_GENERIC
    except Exception as e:
        # Ensure session is rolled back / closed even on other errors
        try:
             session.rollback()
             session.close()
        except: pass # Ignore errors during cleanup
        print(f"ERROR during database operation for query_branch_total: {e}")
        import traceback
        traceback.print_exc()
        return ERROR_MSG_GENERIC
    finally:
       # Ensure session is closed/removed properly by teardown context
       pass


# --- Main Execution ---
if __name__ == '__main__':
    print("Starting Flask production-like app...")
    # IMPORTANT: Use a production-ready WSGI server like Gunicorn or uWSGI
    # behind a reverse proxy like Nginx instead of Flask's built-in server.
    # Example (runnable for dev, but NOT for production deployment):
    # Set debug=False for production!
    app.run(host='0.0.0.0', port=5001, debug=False) # Listen on all interfaces if needed for container/VM

    # --- Production Run Command Example (using Gunicorn) ---
    # Make sure gunicorn is installed: pip install gunicorn
    # gunicorn --bind 0.0.0.0:5001 --workers 4 app:app
    # (Adjust workers based on your server resources)