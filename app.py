import asyncio
import time
import httpx
import json
import os
import datetime
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

def format_timestamp(ts):
    """টাইমস্ট্যাম্পকে মানুষের পড়ার উপযোগী তারিখ ও সময়ে রূপান্তর করার ফাংশন"""
    try:
        if not ts: return "N/A"
        return datetime.datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %I:%M:%S %p')
    except:
        return str(ts)

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
    """সম্পূর্ণ ডেটাসহ স্পেস-বিহীন API রেসপন্স ফরমেট (ডেভেলপার ফ্রেন্ডলি)"""
    basic_info = data.get("basicInfo", {})
    profile_info = data.get("profileInfo", {})
    clan_info = data.get("clanBasicInfo", {})
    captain_info = data.get("captainBasicInfo", {})
    pet_info = data.get("petInfo", {})
    social_info = data.get("socialInfo", {})
    credit_info = data.get("creditScoreInfo", {})

    return {
        "PlayerInfo": {
            "AccountName": basic_info.get("nickname", "N/A"),
            "AccountId": social_info.get("accountId", "N/A"),
            "AccountRegion": basic_info.get("region", "N/A"),
            "AccountLevel": basic_info.get("level", 0),
            "AccountLikes": basic_info.get("liked", 0),
            "AccountEXP": basic_info.get("exp", 0),
            "AccountType": basic_info.get("accountType", 0),
            "AccountAvatarId": basic_info.get("headPic", 0),
            "AccountBannerId": basic_info.get("bannerId", 0),
            "AccountBPBadges": basic_info.get("badgeCnt", 0),
            "AccountBPID": basic_info.get("badgeId", 0),
            "AccountSeasonId": basic_info.get("seasonId", 0),
            "Title": basic_info.get("title", 0),
            "ReleaseVersion": basic_info.get("releaseVersion", "N/A"),
            "AccountCreateTime": basic_info.get("createAt", "0"),
            "AccountCreateDate": format_timestamp(basic_info.get("createAt")),
            "AccountLastLogin": basic_info.get("lastLoginAt", "0"),
            "AccountLastLoginDate": format_timestamp(basic_info.get("lastLoginAt")),
            "Gender": str(social_info.get("gender", "N/A")).replace("Gender_", ""),
            "Language": str(social_info.get("language", "N/A")).replace("Language_", ""),
            "Signature": social_info.get("signature", "N/A"),
            "RankShow": social_info.get("rankShow", "N/A")
        },
        "PlayerRankInfo": {
            "BrMaxRank": basic_info.get("maxRank", 0),
            "BrRankPoint": basic_info.get("rankingPoints", 0),
            "CsMaxRank": basic_info.get("csMaxRank", 0),
            "CsRankPoint": basic_info.get("csRankingPoints", 0),
            "ShowBrRank": basic_info.get("showBrRank", False),
            "ShowCsRank": basic_info.get("showCsRank", False)
        },
        "EquippedItemsInfo": {
            "EquippedWeapon": basic_info.get("weaponSkinShows", []),
            "EquippedOutfit": profile_info.get("clothes", []),
            "EquippedSkills": profile_info.get("equipedSkills", [])
        },
        "GuildInfo": {
            "GuildName": clan_info.get("clanName", "N/A"),
            "GuildID": str(clan_info.get("clanId", "N/A")),
            "GuildLevel": clan_info.get("clanLevel", 0),
            "GuildCapacity": clan_info.get("capacity", 0),
            "GuildMember": clan_info.get("memberNum", 0),
            "GuildOwner": str(clan_info.get("captainId", "N/A"))
        },
        "GuildLeaderInfo": {
            "LeaderName": captain_info.get("nickname", "N/A"),
            "LeaderId": captain_info.get("accountId", "N/A"),
            "LeaderLevel": captain_info.get("level", 0),
            "LeaderLikes": captain_info.get("liked", 0),
            "LeaderExp": captain_info.get("exp", 0),
            "LeaderAvatarId": captain_info.get("headPic", 0),
            "LeaderBannerId": captain_info.get("bannerId", 0),
            "LeaderBadgeCount": captain_info.get("badgeCnt", 0),
            "LeaderBadgeId": captain_info.get("badgeId", 0),
            "LeaderBrMaxRank": captain_info.get("maxRank", 0),
            "LeaderBrRankPoint": captain_info.get("rankingPoints", 0),
            "LeaderCsMaxRank": captain_info.get("csMaxRank", 0),
            "LeaderCsRankPoint": captain_info.get("csRankingPoints", 0),
            "LeaderTitle": captain_info.get("title", 0),
            "LeaderPinId": captain_info.get("pinId", 0),
            "LeaderEquippedWeapon": captain_info.get("weaponSkinShows", []),
            "LeaderCreateTime": captain_info.get("createAt", "0"),
            "LeaderCreateDate": format_timestamp(captain_info.get("createAt")),
            "LeaderLastLogin": captain_info.get("lastLoginAt", "0"),
            "LeaderLastLoginDate": format_timestamp(captain_info.get("lastLoginAt"))
        },
        "PetInfo": {
            "PetId": pet_info.get("id", 0),
            "PetLevel": pet_info.get("level", 0),
            "PetExp": pet_info.get("exp", 0),
            "IsSelected": pet_info.get("isSelected", False),
            "SelectedSkillId": pet_info.get("selectedSkillId", 0),
            "SkinId": pet_info.get("skinId", 0)
        },
        "CreditScoreInfo": {
            "CreditScore": credit_info.get("creditScore", 100),
            "RewardState": credit_info.get("rewardState", "N/A"),
            "PeriodicSummaryEndTime": credit_info.get("periodicSummaryEndTime", "0"),
            "PeriodicSummaryEndDate": format_timestamp(credit_info.get("periodicSummaryEndTime"))
        }
    }

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

