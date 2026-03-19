import asyncio
import time
import httpx
import json
import os
from datetime import datetime
from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
from google.protobuf import json_format
from Crypto.Cipher import AES

# === Local Imports ===
from config import Config
from Pb2 import FreeFire_pb2, main_pb2, AccountPersonalShow_pb2

# === Flask App Setup ===
app = Flask(__name__)
app.json.sort_keys = False 
CORS(app)

# Vercel-এর জন্য In-memory cache
cached_tokens = {}

# === Helper Functions ===
def pad(text: bytes) -> bytes:
    padding_length = AES.block_size - (len(text) % AES.block_size)
    return text + bytes([padding_length] * padding_length)

def aes_cbc_encrypt(key: bytes, iv: bytes, plaintext: bytes) -> bytes:
    aes = AES.new(key, AES.MODE_CBC, iv)
    return aes.encrypt(pad(plaintext))

def decode_protobuf(encoded_data: bytes, message_type):
    instance = message_type()
    instance.ParseFromString(encoded_data)
    return instance

async def json_to_proto(json_data: str, proto_message):
    json_format.ParseDict(json.loads(json_data), proto_message)
    return proto_message.SerializeToString()

# === Token Generation (On-Demand) ===
async def get_access_token(account: str):
    url = "https://ffmconnect.live.gop.garenanow.com/oauth/guest/token/grant"
    payload = account + "&response_type=token&client_type=2&client_secret=2ee44819e9b4598845141067b281621874d0d5d7af9d8f7e00c1e54715b7d1e3&client_id=100067"
    headers = {
        'User-Agent': Config.USER_AGENT, 
        'Connection': "Keep-Alive", 
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
        'Content-Type': "application/octet-stream", 
        'X-Unity-Version': Config.UNITY_VERSION, 
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(url, data=payload, headers=headers)
        msg = json.loads(json_format.MessageToJson(decode_protobuf(resp.content, FreeFire_pb2.LoginRes)))
        cached_tokens[region] = {
            'token': f"Bearer {msg.get('token','0')}",
            'region': msg.get('lockRegion','0'),
            'server_url': msg.get('serverUrl','0'),
            'expires_at': time.time() + 25000 # এক্সপায়ারেশন টাইম
        }

# ব্যাকগ্রাউন্ড লুপের বদলে আমরা ডাইনামিক চেকিং ব্যবহার করছি, যা Vercel-এ ক্র্যাশ করবে না
async def get_token_info(region: str):
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
        'Content-Type': "application/octet-stream", 
        'Authorization': token, 
        'X-Unity-Version': Config.UNITY_VERSION, 
        'ReleaseVersion': Config.RELEASE_VERSION
    }
    async with httpx.AsyncClient() as client:
        resp = await client.post(server + endpoint, data=data_enc, headers=headers)
        return json.loads(json_format.MessageToJson(decode_protobuf(resp.content, AccountPersonalShow_pb2.AccountPersonalShowInfo)))

# HTML ফাইলের Javascript এর সাথে মিল রেখে Data Format করা হয়েছে
def format_response(data):
    basic = data.get("basicInfo", {})
    social = data.get("socialInfo", {})
    clan = data.get("clanBasicInfo", {})
    captain = data.get("captainBasicInfo", {})
    
    return {
        "AccountInfo": {
            "AccountName": basic.get("nickname"),
            "AccountLevel": basic.get("level"),
            "BrRankPoint": basic.get("rankingPoints", 0),
            "CsRankPoint": basic.get("csRankingPoints", 0),
            "AccountLikes": basic.get("liked", 0),
            "AccountAvatarId": basic.get("headPic")
        },
        "GuildInfo": {
            "GuildName": clan.get("clanName"),
            "GuildLevel": clan.get("clanLevel", 0),
            "GuildMember": clan.get("memberNum", 0),
            "GuildCapacity": clan.get("capacity", 0),
            "GuildID": clan.get("clanId"),
            "GuildOwner": clan.get("captainId")
        },
        "socialinfo": {
            "signature": social.get("signature", "")
        }
    }

# === API Routes ===
@app.route('/')
def home():
    # ফ্রন্টএন্ড রেন্ডার হবে এখান থেকে
    return render_template('index.html')

@app.route('/get')
async def get_account_info():
    uid = request.args.get('uid')
    if not uid:
        return jsonify({"error": "Please provide UID."}), 400
    
    try:
        region = "ME" # ডিফল্ট রিজিয়ন
        return_data = await GetAccountInformation(uid, "7", region, "/GetPlayerPersonalShow")
        formatted = format_response(return_data)
        return jsonify(formatted), 200
    
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# Serverless Environment এ app.run() এর প্রয়োজন নেই
