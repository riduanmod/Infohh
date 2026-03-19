# app.py

import json
import base64
import httpx
import logging
from flask import Flask, render_template, request, jsonify
from Crypto.Cipher import AES
from Crypto.Util.Padding import unpad
import datetime

# আপনার দেওয়া জেনারেটেড Protobuf ফাইলগুলো ইম্পোর্ট করা হচ্ছে
from pb2.AccountPersonalShow_pb2 import *
from pb2.FreeFire_pb2 import *
from pb2.zitado_pb2 import *
from pb2.main_pb2 import *
from pb2.uid_generator_pb2 import *

# কনফিগারেশন এবং ভার্সন ইম্পোর্ট
from config import KEY, REGION_CREDENTIALS, PORT
from game_version import CLIENT_PORT, CLIENT_VERSION, UNITY_VERSION

# পেশাদার লগিং কনফিগারেশন (প্রোডাকশনে সমস্যা নির্ণয়ের জন্য প্রয়োজনীয়)
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

app = Flask(__name__)

# AES-128-ECB ডিক্রিপশন ফাংশন
def aes_decrypt(ciphertext_base64, key):
    try:
        cipher = AES.new(key, AES.MODE_ECB)
        ciphertext = base64.b64decode(ciphertext_base64)
        decrypted = cipher.decrypt(ciphertext)
        decrypted_unpadded = unpad(decrypted, AES.block_size)
        return decrypted_unpadded
    except Exception as e:
        logging.error(f"Decryption failed: {e}")
        return None

# টাইমস্ট্যাম্পকে মানুষের পড়ার যোগ্য ফরম্যাটে রূপান্তর
def format_creation_date(timestamp):
    if not timestamp:
        return "Not Found"
    try:
        dt = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        return dt.strftime('%B %d, %Y, %I:%M:%S %p %Z')
    except Exception:
        return "Invalid Date"

# র‍্যাঙ্ক বা লেভেলের জন্য ছবির লিঙ্ক তৈরি
# আপনার HTML-এ নির্দিষ্ট লিঙ্কের ফাংশন এখানে প্রয়োগ করা হয়েছে।
def get_image_url(image_id):
    if not image_id or not str(image_id).strip():
        # কোনো নির্দিষ্ট ছবি না থাকলে একটি ডিফল্ট ছবি বা ফ্লাগ দিন
        return "https://media.discordapp.net/attachments/1118671501659938887/1169315585093013584/default_avatar.png" 
    
    # আপনার HTML-এর উদাহরণ অনুযায়ী লিঙ্ক ফরম্যাট
    return f"https://id-static.riduanff.com/images/ff_items/{image_id}.png"

# অ্যাসিঙ্ক্রোনাসভাবে ডেটা ফেচ করার জন্য httpx ব্যবহার করা হয়েছে
async def fetch_user_data(uid, region_info):
    url = f"https://accountshow.ff.{region_info['ext']}.garena.com/AccountShow"
    headers = {
        "X-Garena-Auth": region_info['auth'],
        "User-Agent": f"UnityPlayer/{UNITY_VERSION} (Unity/5.x {UNITY_VERSION} platform:Andriod)",
        "X-Garena-Client-Port": CLIENT_PORT,
        "X-Garena-Client-Version": CLIENT_VERSION,
        "Connection": "Keep-Alive"
    }
    
    # Protobuf রিকোয়েস্ট পে-লোড তৈরি
    acc_req = GetPlayerInfoRequest()
    acc_req.uid = int(uid)
    
    payload = acc_req.SerializeToString()
    
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(url, headers=headers, content=payload)
            response.raise_for_status()
            
            acc_resp = GetPlayerInfoResponse()
            acc_resp.ParseFromString(response.content)
            
            return acc_resp
            
        except httpx.HTTPStatusError as e:
            logging.error(f"HTTP error {e.response.status_code} for URL {url}")
            return None
        except Exception as e:
            logging.error(f"Request failed: {e}")
            return None

@app.route('/', methods=['GET', 'POST'])
async def index():
    player_data = None
    error_message = None
    
    if request.method == 'POST':
        uid = request.form.get('player_id')
        if uid and uid.strip().isdigit():
            # সব রিজিউনের জন্য ডেটা খোঁজার চেষ্টা (সমান্তরালে নয়, একে একে)
            found = False
            for region, region_info in REGION_CREDENTIALS.items():
                logging.info(f"Checking region {region} for UID {uid}")
                data = await fetch_user_data(uid, region_info)
                
                if data and data.account.nickname: # যদি ব্যবহারকারী পাওয়া যায়
                    player_data = data
                    found = True
                    break # পাওয়া গেলে লুপ শেষ করুন
            
            if not found:
                error_message = f"Player with UID {uid} not found in any region."
                logging.warning(error_message)
        else:
            error_message = "Please enter a valid Player ID (Digits only)."
            logging.warning(error_message)

    # জেনারেটেড ছবি/লিঙ্কের জন্য টেমপ্লেট ফিল্টার হিসেবে ফাংশনগুলো পাস করা হয়েছে
    return render_template('index.html', player_data=player_data, 
                           error=error_message, 
                           get_image_url=get_image_url, 
                           format_creation_date=format_creation_date)

# প্রোডাকশন লেভেলের জন্য ফ্লস্ক-এর ডিফল্ট এরর পেজগুলো হ্যান্ডেল করা
@app.errorhandler(404)
def page_not_found(e):
    return render_template('index.html', error="404: Page Not Found"), 404

@app.errorhandler(500)
def internal_server_error(e):
    logging.critical(f"Server Error: {e}")
    return render_template('index.html', error="500: Internal Server Error. Please try again later."), 500

if __name__ == '__main__':
    # আপনার কনফিগারেশন অনুযায়ী পোর্টে রান করুন
    app.run(port=PORT, debug=True)
