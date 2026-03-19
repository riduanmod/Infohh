import asyncio
import time
import httpx
import json
import os
from datetime import datetime
from collections import defaultdict
from flask import Flask, request, jsonify
from flask_cors import CORS
from cachetools import TTLCache
from typing import Tuple
from google.protobuf import json_format, message
from google.protobuf.message import Message
from Crypto.Cipher import AES

# === Local Imports ===
from config import Config
from Pb2 import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2

# === Flask App Setup ===
app = Flask(__name__)
# এটি নিশ্চিত করবে যেন ব্রাউজারে JSON রেসপন্সের সিরিয়াল উল্টাপাল্টা না হয়
app.json.sort_keys = False 
CORS(app)
cache = TTLCache(maxsize=100, ttl=300)
cached_tokens = defaultdict(dict)

# === Helper Functions ===
def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

def decode_protobuf(encoded_data: bytes, message_type: message.Message) -> message.Message:
    instance = message_type()
    instance.ParseFromString(encoded_data)
    return instance

async def json_to_proto(json_data: str, proto_message: Message) -> bytes:
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

# === Data Formatting Helpers ===
def format_timestamp(timestamp_val):
    """Convert Unix timestamp string/int to readable Date & Time."""
    if not timestamp_val:
        return None
    try:
        ts = int(timestamp_val)
        return datetime.fromtimestamp(ts).strftime('%Y-%m-%d %I:%M:%S %p')
    except (ValueError, TypeError):
        return timestamp_val

def clean_empty_data(data):
    """Recursively remove empty lists, dicts, nulls, and empty strings."""
    if isinstance(data, dict):
        cleaned_dict = {}
        for k, v in data.items():
            cleaned_v = clean_empty_data(v)
            # যদি ভ্যালু ফাঁকা না হয়, তাহলেই ডিকশনারিতে যুক্ত হবে
            if cleaned_v not in (None, "", [], {}):
                cleaned_dict[k] = cleaned_v
        return cleaned_dict
    elif isinstance(data, list):
        cleaned_list = []
        for item in data:
            cleaned_item = clean_empty_data(item)
            if cleaned_item not in (None, "", [], {}):
                cleaned_list.append(cleaned_item)
        return cleaned_list
    return data

# === Token Generation ===
async def get_access_token(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip", 
        'Content-Type': "application/x-www-form-urlencoded"
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers)
        data = resp.json()
        return data.get("access_token", "0"), data.get("open_id", "0")

async def create_jwt(region: str):
    account = Config.get_account(region)
    token_val, open_id = await get_access_token(account)
    body = json.dumps({"open_id": open_id, "open_id_type": "4", "login_token": token_val, "orign_platform_type": "4"})
    proto_bytes = await json_to_proto(body, FreeFire_pb2.LoginReq())
    payload = aes_cbc_encrypt(Config.MAIN_KEY, Config.MAIN_IV, proto_bytes)
    url = "https://loginbp.ggblueshark.com/MajorLogin"
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream", 
        'Expect': "100-continue",
        'X-Unity-Version': Config.UNITY_VERSION, 
        'X-GA': "v1 1", 
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers)
        msg = json.loads(json_format.MessageToJson(decode_protobuf(resp.content, FreeFire_pb2.LoginRes)))
        cached_tokens[region] = {
            'token': f"Bearer {msg.get('token','0')}",
            'region': msg.get('lockRegion','0'),
            'server_url': msg.get('serverUrl','0'),
            'expires_at': time.time() + 25200
        }

async def initialize_tokens():
    tasks = [create_jwt(r) for r in Config.SUPPORTED_REGIONS]
    await asyncio.gather(*tasks)

async def refresh_tokens_periodically():
    while True:
        await asyncio.sleep(25200)
        await initialize_tokens()

async def get_token_info(region: str) -> Tuple[str, str, str]:
    info = cached_tokens.get(region)
    if info and time.time() < info['expires_at']:
        return info['token'], info['region'], info['server_url']
    await create_jwt(region)
    info = cached_tokens[region]
    return info['token'], info['region'], info['server_url']

async def GetAccountInformation(uid, unk, region, endpoint):
    payload = await json_to_proto(json.dumps({'a': uid, 'b': unk}), main_pb2.GetPlayerPersonalShow())
    data_enc = aes_cbc_encrypt(Config.MAIN_KEY, Config.MAIN_IV, payload)
    token, lock, server = await get_token_info(region)
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
        'Accept-Encoding': "gzip",
        'Content-Type': "application/octet-stream", 
        'Expect': "100-continue",
        'Authorization': token, 
        'X-Unity-Version': Config.UNITY_VERSION, 
        'X-GA': "v1 1",
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(server + endpoint, data=data_enc, headers=headers)
        return json.loads(json_format.MessageToJson(decode_protobuf(resp.content, AccountPersonalShow_pb2.AccountPersonalShowInfo)))

def format_response(data):
    basic = data.get("basicInfo", {})
    social = data.get("socialInfo", {})
    pet = data.get("petInfo", {})
    clan = data.get("clanBasicInfo", {})
    captain = data.get("captainBasicInfo", {})
    profile = data.get("profileInfo", {})
    credit = data.get("creditScoreInfo", {})
    
    history_ep = data.get("historyEpInfo", [])
    achievements = data.get("equippedAchievements", [])
    region_stats = social.get("regionStats", [])
    highlights = basic.get("socialHighlights", {}).get("entries", [])
    cs_rank_entries = basic.get("csRankEntries", [])
    mmr_ratings = data.get("mmrRatings", [])

    raw_response = {
        "Player Information": {
            "Player_Name": basic.get("nickname"),
            "Player_UID": basic.get("accountId"),
            "Player_Level": basic.get("level"),
            "Player_EXP": basic.get("exp"),
            "Player_Likes": basic.get("liked"),
            "Player_Signature": social.get("socialHighlight") or social.get("signature"),
            "Player_Region": basic.get("region"),
            "Player_Gender": social.get("gender"),
            "Player_Language": social.get("language"),
            "Account_Create_Time": format_timestamp(basic.get("createAt")),
            "Account_Last_Login": format_timestamp(basic.get("lastLoginAt")),
            "Player_Title": basic.get("title"),
            "Account_Type": basic.get("accountType"),
            "Player_BP_Badges": basic.get("badgeCnt"),
            "Player_BP_ID": basic.get("badgeId"),
            "Player_Hippo_Rank": basic.get("hippoRank"),         
            "Player_Hippo_Points": basic.get("hippoRankingPoints") 
        },
        "Rank Information": {
            "BR_Max_Rank": basic.get("maxRank"),
            "BR_Rank_Point": basic.get("rankingPoints"),
            "CS_Max_Rank": basic.get("csMaxRank"),
            "CS_Rank_Point": basic.get("csRankingPoints"),
            "CS_Rank_Entries": cs_rank_entries,           
            "MMR_Ratings": mmr_ratings                    
        },
        "Pet Information": {
            "Pet_Name": pet.get("petName") or pet.get("name"),
            "Pet_Level": pet.get("level"),
            "Pet_EXP": pet.get("exp"),
            "Pet_ID": pet.get("petId") or pet.get("id"),
            "Pet_Selected_Skill_ID": pet.get("selectedSkillId"),
            "Pet_Skin_ID": pet.get("skinId")
        },
        "Guild Information": {
            "Guild_Name": clan.get("clanName"),
            "Guild_ID": clan.get("clanId"),
            "Guild_Level": clan.get("clanLevel"),
            "Guild_Capacity": clan.get("maxMembers") or clan.get("capacity"),
            "Guild_Total_Members": clan.get("currentMembers") or clan.get("memberNum"),
            "Guild_Leader_UID": clan.get("captainId")
        },
        "Guild Leader Information": {
            "Guild_Leader_Name": captain.get("nickname"),
            "Guild_Leader_UID": captain.get("accountId"),
            "Guild_Leader_Level": captain.get("level"),
            "Guild_Leader_EXP": captain.get("exp"),
            "Guild_Leader_Likes": captain.get("liked"),
            "Guild_Leader_Last_Login": format_timestamp(captain.get("lastLoginAt"))
        },
        "Extended Stats & Info": {
            "Player_Region_Stats": region_stats,                 
            "Player_Social_Highlights": highlights,              
            "Player_History_EP_Stats": history_ep                
        },
        "Account Profile & Credit": {
            "Player_Credit_Score": credit.get("score") or credit.get("creditScore"),
            "Player_Credit_Status": credit.get("status"),        
            "Player_Equipped_Outfit": profile.get("cosmeticItems") or profile.get("clothes", []),
            "Player_Equipped_Skills": profile.get("equippedSkills") or profile.get("equipedSkills", []),
            "Player_Equipped_Weapon": basic.get("weaponSkinShows", []),
            "Player_Equipped_Achievements": achievements         
        }
    }

    # Pass the raw response through the cleaner function
    return clean_empty_data(raw_response)

# === API Routes ===
@app.route('/get')
async def get_account_info():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        region = "ME"
        return_data = await GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow")
        formatted = format_response(return_data)
        return jsonify(formatted), 200
    
    except Exception as e:
        return jsonify({"error": "Invalid UID or server error. Please try again."}), 500

@app.route('/refresh', methods=['GET', 'POST'])
def refresh_tokens_endpoint():
    try:
        asyncio.run(initialize_tokens())
        return jsonify({'message': 'Tokens refreshed for all regions.'}), 200
    except Exception as e:
        return jsonify({'error': f'Refresh failed: {e}'}), 500

# === Startup ===
async def startup():
    await initialize_tokens()
    asyncio.create_task(refresh_tokens_periodically())

if __name__ == '__main__':
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(startup())
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)
