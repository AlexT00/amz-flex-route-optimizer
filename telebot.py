import logging
import googlemaps
from telegram import Update
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, CallbackContext
import json
import requests
from ocr import *
# Global dictionary to store schedule context for each chat
schedule_data = {}
with open ("config.json", "r") as f:
    config = json.load(f)
    GMAPS_API_KEY = config["googlemaps_key"]
    TELEGRAM_BOT_TOKEN = config["telegram_bot_key"]
# States for the conversation
WAIT_START, WAIT_END, WAIT_PICTURES, READY, IN_TRIP = range(5)

gmaps = googlemaps.Client(key=GMAPS_API_KEY)

# === Telegram Bot Handlers ===

def newschedule(update: Update, context: CallbackContext):
    """Start a new delivery schedule."""
    chat_id = update.effective_chat.id
    schedule_data[chat_id] = {
        "state": WAIT_START,
        "start_location": None,
        "end_location": None,
        "pictures": [],
        "itinerary": [],
        "current_stop": 0,
    }
    update.message.reply_text("New schedule initiated.\nPlease provide the start delivery location (e.g., 'Toh Guan Road').")
    return

def start(update: Update, context: CallbackContext):
    update.message.reply_text(
        "Welcome to the Delivery Bot!\n"
        "Use /newschedule to start a new delivery schedule."
    )

def text_handler(update: Update, context: CallbackContext):
    """Handle text messages to capture start/end locations or additional addresses."""
    chat_id = update.effective_chat.id
    if chat_id not in schedule_data:
        update.message.reply_text("No active schedule. Start a new one with /newschedule.")
        return

    state = schedule_data[chat_id]["state"]
    text = update.message.text.strip()
    
    if state == WAIT_START:
        schedule_data[chat_id]["start_location"] = text
        schedule_data[chat_id]["state"] = WAIT_END
        update.message.reply_text(f"Start location recorded as: '{text}'.\nNow, please provide the end delivery location (e.g., 'Yishun Ave 1').")
    elif state == WAIT_END:
        schedule_data[chat_id]["end_location"] = text
        schedule_data[chat_id]["state"] = WAIT_PICTURES
        update.message.reply_text(f"End location recorded as: '{text}'.\nNow, please send your delivery pictures. You can also type additional addresses if needed. When finished, use /endpictures.")
    elif state == WAIT_PICTURES:
        # Treat incoming text as an additional address.
        schedule_data[chat_id]["pictures"].append(text)
        update.message.reply_text("Additional address recorded: " + text)
    else:
        update.message.reply_text("Text received, but not expected at this stage.")
    return

def photo_handler(update: Update, context: CallbackContext):
    """Handle photo messages by running OCR on the image and extracting all addresses."""
    chat_id = update.effective_chat.id
    if chat_id not in schedule_data or schedule_data[chat_id]["state"] != WAIT_PICTURES:
        update.message.reply_text("Please start a new schedule with /newschedule and follow the instructions.")
        return

    photo = update.message.photo[-1]
    file = photo.get_file()
    file_path = f"/tmp/{file.file_id}.jpg"
    file.download(file_path)

    extracted_addresses = extract_addresses_from_image(file_path)
    if extracted_addresses:
        schedule_data[chat_id]["pictures"].extend(extracted_addresses)
        update.message.reply_text("Picture processed. Addresses extracted: " + ", ".join(extracted_addresses) +
                                    "\nIf you need to add more addresses manually, please type them now.")
    else:
        update.message.reply_text("No address could be extracted from the picture. You may type an address manually.")
    return

def endpictures(update: Update, context: CallbackContext):
    """End picture submission and optimize itinerary."""
    chat_id = update.effective_chat.id
    if chat_id not in schedule_data or schedule_data[chat_id]["state"] != WAIT_PICTURES:
        update.message.reply_text("No active picture session. Please start with /newschedule.")
        return

    picture_addresses = schedule_data[chat_id]["pictures"]
    if not picture_addresses:
        update.message.reply_text("No pictures or additional addresses received. Please send pictures containing delivery addresses or type them in manually.")
        return

    start = schedule_data[chat_id]["start_location"]
    end = schedule_data[chat_id]["end_location"]
    stops = picture_addresses

    itinerary = optimize_itinerary(start, end, stops)
    schedule_data[chat_id]["itinerary"] = itinerary
    schedule_data[chat_id]["state"] = READY
    update.message.reply_text("Itinerary optimized and ready. Use /starttrip to begin your delivery.")
    return

def starttrip(update: Update, context: CallbackContext):
    """Begin the trip by sending the first stop's location (excluding the start location)."""
    chat_id = update.effective_chat.id
    if chat_id not in schedule_data or schedule_data[chat_id]["state"] != READY:
        update.message.reply_text("No optimized schedule found. Please start a new schedule with /newschedule.")
        return

    itinerary = schedule_data[chat_id]["itinerary"]
    if not itinerary or len(itinerary) < 2:
        update.message.reply_text("Itinerary is empty or incomplete.")
        return

    # Skip the start location (itinerary[0]) as the user is assumed to already be there.
    schedule_data[chat_id]["state"] = IN_TRIP
    schedule_data[chat_id]["current_stop"] = 1  # Start with the first delivery stop
    update.message.reply_text("Trip started. Sending the first delivery location:")
    send_location(update, itinerary[1])
    return

def nextstop(update: Update, context: CallbackContext):
    """Advance to the next stop in the itinerary."""
    chat_id = update.effective_chat.id
    if chat_id not in schedule_data or schedule_data[chat_id]["state"] != IN_TRIP:
        update.message.reply_text("Trip is not in progress. Use /starttrip to begin.")
        return

    schedule_data[chat_id]["current_stop"] += 1
    itinerary = schedule_data[chat_id]["itinerary"]
    current_stop = schedule_data[chat_id]["current_stop"]
    if current_stop >= len(itinerary):
        update.message.reply_text("Trip complete. Well done!")
        del schedule_data[chat_id]
    else:
        update.message.reply_text("Proceeding to the next delivery location:")
        send_location(update, itinerary[current_stop])
    return

def endtrip(update: Update, context: CallbackContext):
    """End the trip and clear the schedule context."""
    chat_id = update.effective_chat.id
    if chat_id in schedule_data:
        del schedule_data[chat_id]
    update.message.reply_text("Trip ended and context wiped.")
    return

def send_location(update: Update, address: str):
    # First, send the address as a text message
    update.message.reply_text(f"Address: {address}")
    # Then, geocode the address and send the location
    lat, lng = geocode_address(address + " Singapore")
    if lat is None or lng is None:
        update.message.reply_text(f"Could not geocode address: {address}")
    else:
        update.message.reply_location(latitude=lat, longitude=lng)

def geocode_address(address: str):
    """Geocode an address using the Google Maps API."""
    try:
        geocode_result = gmaps.geocode(address)
        if geocode_result:
            loc = geocode_result[0]['geometry']['location']
            return loc['lat'], loc['lng']
    except Exception as e:
        logging.error("Geocoding error: %s", e)
    return None, None

def optimize_itinerary(start: str, end: str, stops: list):
    """Optimize the itinerary using the Routes API with optimized waypoints."""
    if not stops:
        return [start, end]
    payload = {
        "origin": {
            "address": start + " Singapore"
        },
        "destination": {
            "address": end + " Singapore"
        },
        "intermediates": [
            {"address": stop + " Singapore",} for stop in stops
        ],
        "travelMode": "DRIVE",
        "optimizeWaypointOrder": "true"
    }
    headers = {
        "content-type": "application/json",
        "X-Goog-Api-Key": GMAPS_API_KEY,
        "X-Goog-FieldMask": "routes,geocodingResults.intermediates.intermediateWaypointRequestIndex",
    }
    try:
        print(payload)
        response = requests.post("https://routes.googleapis.com/directions/v2:computeRoutes", headers=headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            routes = data['routes'][0]
            if not routes:
                logging.error("No routes returned in response: %s", json.dumps(data, indent=2))
                return [start] + stops + [end]
            optimized_order = routes.get("optimizedIntermediateWaypointIndex")
            if not optimized_order:
                logging.warning(
                    "No optimized waypoint order returned; falling back to original order."
                )
                return [start] + stops + [end]
            # Reorder stops according to the optimized order.
            optimized_stops = [stops[i] for i in optimized_order]
            itinerary = [start] + optimized_stops + [end]
            print(itinerary)
            return itinerary
        else:
            logging.error("Routes API error: %s", response.text)    
    except Exception as e:
        logging.error("Optimization error: %s", e)
    return [start] + stops + [end]

def main():
    logging.basicConfig(
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        level=logging.INFO
    )
    updater = Updater(TELEGRAM_BOT_TOKEN, use_context=True)
    dp = updater.dispatcher

    # Register command handlers for the workflow
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("newschedule", newschedule))
    dp.add_handler(CommandHandler("endpictures", endpictures))
    dp.add_handler(CommandHandler("starttrip", starttrip))
    dp.add_handler(CommandHandler("nextstop", nextstop))
    dp.add_handler(CommandHandler("endtrip", endtrip))

    # Handlers for text and photo messages
    dp.add_handler(MessageHandler(Filters.photo, photo_handler))
    dp.add_handler(MessageHandler(Filters.text & ~Filters.command, text_handler))

    updater.start_polling()
    updater.idle()

if __name__ == '__main__':
    main()
