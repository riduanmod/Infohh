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

from config import Config
from Pb2 import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2

app = Flask(__name__)
CORS(app)

app.json.sort_keys = False 

cache = TTLCache(maxsize=100, ttl=300)
cached_tokens = defaultdict(dict)

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
    try:
        if not ts: return "N/A"
        return datetime.datetime.fromtimestamp(int(ts)).strftime('%Y-%m-%d %I:%M:%S %p')
    except:
        return str(ts)

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
    if not isinstance(data, dict):
        data = {}
        
    basic_info = data.get("basicInfo") or {}
    profile_info = data.get("profileInfo") or {}
    clan_info = data.get("clanBasicInfo") or {}
    captain_info = data.get("captainBasicInfo") or {}
    pet_info = data.get("petInfo") or {}
    social_info = data.get("socialInfo") or {}
    credit_info = data.get("creditScoreInfo") or {}

    return {
        "DeveloperInfo": {
            "Developer": "Riduanul Islam",
            "TelegramBot": "https://t.me/RiduanFFBot",
            "TelegramChannel": "https://t.me/RiduanOfficialBD"
        },
        "PlayerInfo": {
            "AccountName": basic_info.get("nickname", "N/A"),
            "AccountId": social_info.get("accountId", "N/A"),
            "AccountLevel": basic_info.get("level", 0),
            "AccountLikes": basic_info.get("liked", 0),
            "AccountEXP": basic_info.get("exp", 0),
            "AccountRegion": basic_info.get("region", "N/A"),
            "Gender": str(social_info.get("gender", "N/A")).replace("Gender_", ""),
            "Language": str(social_info.get("language", "N/A")).replace("Language_", ""),
            "AccountAvatarId": basic_info.get("headPic", 0),
            "AccountBannerId": basic_info.get("bannerId", 0),
            "AccountBPBadges": basic_info.get("badgeCnt", 0),
            "AccountBPID": basic_info.get("badgeId", 0),
            "AccountSeasonId": basic_info.get("seasonId", 0),
            "Title": basic_info.get("title", 0),
            "RankShow": social_info.get("rankShow", "N/A"),
            "AccountCreateDate": format_timestamp(basic_info.get("createAt")),
            "AccountCreateTime": basic_info.get("createAt", "0"),
            "AccountLastLoginDate": format_timestamp(basic_info.get("lastLoginAt")),
            "AccountLastLogin": basic_info.get("lastLoginAt", "0"),
            "AccountType": basic_info.get("accountType", 0),
            "ReleaseVersion": basic_info.get("releaseVersion", "N/A"),
            "Signature": social_info.get("signature", "N/A")
        },
        "PlayerRankInfo": {
            "BrRankPoint": basic_info.get("rankingPoints", 0),
            "BrMaxRank": basic_info.get("maxRank", 0),
            "CsRankPoint": basic_info.get("csRankingPoints", 0),
            "CsMaxRank": basic_info.get("csMaxRank", 0),
            "ShowBrRank": basic_info.get("showBrRank", False),
            "ShowCsRank": basic_info.get("showCsRank", False)
        },
        "PetInfo": {
            "PetId": pet_info.get("id", 0),
            "PetLevel": pet_info.get("level", 0),
            "PetExp": pet_info.get("exp", 0),
            "IsSelected": pet_info.get("isSelected", False),
            "SelectedSkillId": pet_info.get("selectedSkillId", 0),
            "SkinId": pet_info.get("skinId", 0)
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
            "GuildMember": clan_info.get("memberNum", 0),
            "GuildCapacity": clan_info.get("capacity", 0),
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
            "LeaderBrRankPoint": captain_info.get("rankingPoints", 0),
            "LeaderBrMaxRank": captain_info.get("maxRank", 0),
            "LeaderCsRankPoint": captain_info.get("csRankingPoints", 0),
            "LeaderCsMaxRank": captain_info.get("csMaxRank", 0),
            "LeaderTitle": captain_info.get("title", 0),
            "LeaderPinId": captain_info.get("pinId", 0),
            "LeaderEquippedWeapon": captain_info.get("weaponSkinShows", []),
            "LeaderCreateDate": format_timestamp(captain_info.get("createAt")),
            "LeaderCreateTime": captain_info.get("createAt", "0"),
            "LeaderLastLoginDate": format_timestamp(captain_info.get("lastLoginAt")),
            "LeaderLastLogin": captain_info.get("lastLoginAt", "0")
        },
        "CreditScoreInfo": {
            "CreditScore": credit_info.get("creditScore", 100),
            "RewardState": credit_info.get("rewardState", "N/A"),
            "PeriodicSummaryEndDate": format_timestamp(credit_info.get("periodicSummaryEndTime")),
            "PeriodicSummaryEndTime": credit_info.get("periodicSummaryEndTime", "0")
        }
    }

@app.route('/')
def root_guide():
    return jsonify({
        "DeveloperInfo": {
            "Developer": "Riduanul Islam",
            "TelegramBot": "https://t.me/RiduanFFBot",
            "TelegramChannel": "https://t.me/RiduanOfficialBD"
        },
        "API_Usage_Guide": {
            "Status": "Active",
            "Message": "Welcome to Riduan FF Info API! Use the endpoint below to fetch player data.",
            "API_Format": {
                "Get_Player_Info": "/get?uid=[uid]"
            },
            "ExampleUsage": "/get?uid=2764669166"
        }
    }), 200

@app.route('/get')
def get_account_info():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        region = "ME"
        
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        return_data = loop.run_until_complete(GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow"))
        
        formatted = format_response(return_data)
        return jsonify(formatted), 200
    
    except Exception as e:
        return jsonify({
            "error": "Failed to fetch data. Server error or invalid UID.",
            "details": str(e)
        }), 500

@app.route('/refresh', methods=['GET', 'POST'])
def refresh_tokens_endpoint():
    try:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        loop.run_until_complete(initialize_tokens())
        return jsonify({'message': 'Tokens refreshed for all regions.'}), 200
    except Exception as e:
        return jsonify({'error': f'Refresh failed: {e}'}), 500

if __name__ == '__main__':
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    
    try:
        loop.run_until_complete(initialize_tokens())
    except:
        pass
        
    app.run(host='0.0.0.0', port=Config.PORT, debug=Config.DEBUG)